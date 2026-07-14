"""Evidence retrieval across arXiv, OpenAlex, DBLP and Crossref."""
from __future__ import annotations
import json
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from schema import Candidate
from tools import arxiv, openalex, dblp, crossref, ieee
from tools.matching import match_author_indices, title_similarity

AGENT = "WebAgent"


def _identity(title_score: float, source_authors: list[dict[str, Any]],
              paper_authors: list[dict[str, Any]]) -> float:
    if not source_authors or not paper_authors:
        return title_score
    matches = match_author_indices(source_authors, paper_authors)
    author_score = sum(score for _, _, score in matches) / max(
        len(source_authors), len(paper_authors), 1)
    return 0.78 * title_score + 0.22 * author_score


def _write_venue(bb: Blackboard, value: dict[str, Any], *, source: str,
                 evidence: str, evidence_url: str, json_path: str,
                 reliability: float, identity: float, group: str,
                 round_id: int, quote: str = "") -> None:
    # Repository identifiers may identify the preprint but never its venue DOI.
    doi = str(value.get("doi") or "")
    if doi:
        value = {**value, "doi": doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")}
        doi = str(value["doi"])
    if value.get("type") != "preprint" and "10.48550/arxiv." in doi.casefold():
        value = {**value, "doi": None}
    confidence = reliability * identity
    quote = quote or json.dumps(value, ensure_ascii=False, sort_keys=True)
    bb.write(Candidate(
        field="venue", value=value, source=source, evidence=evidence,
        confidence=confidence, identity_score=identity,
        extraction_score=1.0, source_reliability=reliability,
        corroboration_group=group, evidence_url=evidence_url,
        json_path=json_path, quote=quote, agent=AGENT, round_id=round_id,
    ))


def _write_affiliations(bb: Blackboard, source_authors: list[dict[str, Any]],
                        paper: dict[str, Any], *, source: str, evidence_url: str,
                        json_path: str, reliability: float, identity: float,
                        group: str, round_id: int) -> None:
    for index, author, match_score in match_author_indices(
            source_authors, paper.get("authors", [])):
        if not author.get("affiliations"):
            continue
        bb.write(Candidate(
            field=f"authors[{index}].affiliations",
            value={"author_name": paper["authors"][index].get("name"),
                   "affiliations": author["affiliations"]},
            source=source, evidence=json_path, evidence_url=evidence_url,
            json_path=json_path, confidence=reliability * identity * match_score,
            quote=json.dumps(author, ensure_ascii=False, sort_keys=True),
            identity_score=identity, extraction_score=match_score,
            source_reliability=reliability, corroboration_group=group,
            agent=AGENT, round_id=round_id,
        ))


def run(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder,
        round_id: int, tasks: list[str]) -> None:
    arxiv_id = str(paper.get("arxiv_id") or "")
    title = str(paper.get("title") or "")
    if not tasks:
        return

    # arXiv comments/journal-ref are first-class evidence, not a venue by default.
    if "venue" in tasks and arxiv_id:
        record = arxiv.fetch(arxiv_id, cost=cost)
        if record:
            hint = arxiv.parse_venue_hint(record)
            if hint:
                conf = float(hint.pop("confidence")); quote = str(hint.pop("quote"))
                _write_venue(bb, hint, source=f"arxiv:{arxiv_id}",
                    evidence="arxiv:journal_ref/comment", evidence_url=record.get("id", ""),
                    json_path="entry.arxiv:journal_ref|comment", reliability=conf,
                    identity=1.0, group="arxiv", round_id=round_id, quote=quote)

    # Fetch both the arXiv work and title candidates. A preprint hit must never
    # suppress discovery of its formally published sibling.
    works: list[dict[str, Any]] = []
    if arxiv_id:
        preprint = openalex.search_by_arxiv_id(arxiv_id, cost=cost)
        if preprint:
            works.append(preprint)
    if title:
        works.extend(openalex.search_by_title(title, cost=cost))
    unique_works = {str(w.get("id") or id(w)): w for w in works}.values()
    for work in unique_works:
        similarity = title_similarity(str(work.get("display_name") or ""), title)
        oa_authors = openalex.parse_authorships(work)
        identity = _identity(similarity, oa_authors, paper.get("authors", []))
        if title and identity < 0.82:
            continue
        oa_id = str(work.get("id") or "openalex")
        url = oa_id if oa_id.startswith("http") else ""
        if "venue" in tasks:
            venue = openalex.parse_venue(work)
            if venue:
                _write_venue(bb, venue, source=f"openalex:{oa_id}",
                    evidence="OpenAlex published location", evidence_url=url,
                    json_path="primary_location.source.display_name|raw_source_name",
                    reliability=0.86 if venue["type"] != "preprint" else 0.60,
                    identity=identity or 1.0, group="crossref_metadata",
                    round_id=round_id)
        if "affiliations" in tasks:
            _write_affiliations(bb, oa_authors, paper,
                source=f"openalex:{oa_id}", evidence_url=url,
                json_path="authorships[].institutions|raw_affiliation_strings",
                reliability=0.82, identity=identity or 1.0,
                group="openalex_authorship", round_id=round_id)

    if "venue" in tasks and title:
        for hit in dblp.search_by_title(title, cost=cost):
            similarity = title_similarity(str(hit.get("title") or ""), title)
            venue_type = dblp.normalize_venue_type(hit.get("type"))
            if similarity < 0.82 or venue_type in {"unknown", "preprint"}:
                continue
            _write_venue(bb, {
                "name": hit.get("venue"), "type": venue_type,
                "year": int(hit["year"]) if hit.get("year") else None,
                "doi": hit.get("doi"), "publication_status": "published",
            }, source=f"dblp:{hit.get('url', '')}", evidence="DBLP publication record",
                evidence_url=str(hit.get("url") or ""), json_path="result.hits[].info",
                reliability=0.88, identity=similarity, group="dblp",
                round_id=round_id)

    # Crossref is always queried and every plausible top-k item is scored.
    if title and any(task in tasks for task in ("venue", "affiliations")):
        for item in crossref.search_by_bibliographic(title, cost=cost):
            item_title = str((item.get("title") or [""])[0])
            similarity = title_similarity(item_title, title)
            crossref_authors = crossref.parse_authorships(item)
            identity = _identity(similarity, crossref_authors, paper.get("authors", []))
            if identity < 0.82:
                continue
            doi = str(item.get("DOI") or "")
            source = f"crossref:{doi}"
            url = str(item.get("URL") or (f"https://doi.org/{doi}" if doi else ""))
            if "venue" in tasks:
                venue = crossref.parse_venue(item)
                if venue:
                    _write_venue(bb, venue, source=source,
                        evidence="Crossref publication record", evidence_url=url,
                        json_path="message.items[].container-title|DOI|issued",
                        reliability=0.88, identity=identity,
                        group="crossref_metadata", round_id=round_id)
            if "affiliations" in tasks:
                _write_affiliations(bb, crossref_authors, paper,
                    source=source, evidence_url=url,
                    json_path="message.items[].author[].affiliation",
                    reliability=0.84, identity=identity,
                    group="crossref_metadata", round_id=round_id)
            ieee_record = ieee.fetch_by_doi(doi, cost=cost)
            if ieee_record and "affiliations" in tasks:
                _write_affiliations(bb, ieee.parse_authorships(ieee_record), paper,
                    source=f"ieee:{doi}", evidence_url=url,
                    json_path="articles[].authors.authors[].affiliation",
                    reliability=0.94, identity=identity,
                    group="ieee_publisher", round_id=round_id)
