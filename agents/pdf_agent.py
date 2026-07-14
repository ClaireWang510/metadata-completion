"""PDF Agent (optional): read arXiv PDF first page to extract author-affiliation
   pairings + 'Accepted at X' hints. Gated by config.ENABLE_PDF_AGENT."""
from __future__ import annotations
import json
import os
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from llm import chat_json
from schema import Candidate
from tools import pdf_utils
from tools import latex as latex_utils
from tools.matching import match_author_indices
import config


AGENT = "PDFAgent"

SYSTEM = """You extract two things from a scientific paper's rendered FIRST PAGE,
its extracted text, and (when available) its LaTeX front matter:
1. author-affiliation pairs, aligned by the paper's own superscripts / footnotes.
2. any 'Accepted at / To appear in / Published in / Camera-ready for ...' hints
   about the venue (conference or journal), including workshop names.

Return JSON:
{
  "authors": [{"author_name": str, "affiliations": [str], "evidence_quote": str}],
  "venue_hint": {"name": str|null, "type": "conference|journal|workshop|preprint|unknown", "year": int|null, "evidence_quote": str},
  "confidence": float
}
Venue rules (strict):
- Only return a non-null venue when the FIRST PAGE explicitly says the PAPER was
  "accepted at", "to appear in", "published in", or is a "camera-ready" paper for it.
- A challenge/competition/track name, leaderboard result, dataset name, experiment,
  citation, filename, bibliography entry, or LaTeX template/style is NOT evidence
  that the paper was published at that venue.
- Text such as "ranks 1st in the ICCV 2023 Challenge" describes participation,
  not publication. Return venue_hint.name=null unless there is separate explicit
  publication wording.
- Do not infer publication from a year embedded in a repository, dataset, or event name.
- evidence_quote must be an exact short quote supporting the publication claim.
If unsure about a field, leave it empty / null. Do NOT invent."""


def run(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder,
        round_id: int, cache_dir: str = ".pdf_cache") -> dict[str, Any]:
    if not config.ENABLE_PDF_AGENT:
        return {"status": "disabled"}
    arxiv_id = paper.get("arxiv_id")
    if not arxiv_id:
        return {"status": "missing_arxiv_id"}

    sample_dir = str(paper.get("_sample_dir") or "")
    local_pdf = os.path.join(sample_dir, f"{arxiv_id}.pdf") if sample_dir else ""
    pdf_path = local_pdf if local_pdf and os.path.exists(local_pdf) else \
        pdf_utils.download_pdf(arxiv_id, cache_dir, cost=cost)
    if not pdf_path or not os.path.exists(pdf_path):
        return {"status": "download_failed"}
    text = pdf_utils.first_page_text(pdf_path, cost=cost)
    image = pdf_utils.first_page_image(pdf_path, cost=cost)
    latex_text, latex_path = latex_utils.frontmatter_text(
        os.path.join(sample_dir, "latex") if sample_dir else "")
    if not text.strip() and not image and not latex_text.strip():
        return {"status": "empty_sources"}

    user = json.dumps({
        "title": paper.get("title", ""),
        "first_page_text": text,
        "latex_frontmatter": latex_text,
        "known_authors": [a.get("name") for a in paper.get("authors", [])],
    }, ensure_ascii=False)

    out = chat_json(model=config.VLM_MODEL, system=SYSTEM, user=user,
                    agent=AGENT, cost=cost, images=[image] if image else None,
                    max_tokens=1600)
    if out.get("_error"):
        return {"status": "llm_error", "error": out.get("_error")}

    conf = float(out.get("confidence", 0.6))
    paper_authors = paper.get("authors", [])
    written_fields: list[str] = []
    extracted_authors = out.get("authors") or []
    for i, a, match_score in match_author_indices(extracted_authors, paper_authors):
        if not a.get("affiliations"):
            continue
        field = f"authors[{i}].affiliations"
        affiliations = [{"name": value, "raw_name": value}
                        if isinstance(value, str) else value
                        for value in a["affiliations"]]
        evidence_quote = str(a.get("evidence_quote") or "").strip()
        bb.write(Candidate(
            field=field,
            value={"author_name": paper_authors[i].get("name", a.get("author_name", "")),
                    "affiliations": affiliations},
            source=f"pdf:{arxiv_id}", evidence="PDF first page / LaTeX front matter",
            evidence_url=pdf_path, json_path="page[0]",
            quote=evidence_quote, confidence=conf * match_score,
            identity_score=1.0, extraction_score=match_score,
            source_reliability=0.90, corroboration_group="paper_source",
            agent=AGENT, round_id=round_id,
        ))
        written_fields.append(field)

    vh = out.get("venue_hint") or {}
    evidence_quote = str(vh.get("evidence_quote") or "").strip()
    publication_terms = (
        "accepted at", "accepted to", "to appear in", "published in",
        "published at", "camera-ready", "camera ready",
    )
    has_publication_claim = any(term in evidence_quote.casefold()
                                for term in publication_terms)
    if vh.get("name") and evidence_quote and has_publication_claim:
        bb.write(Candidate(
            field="venue",
            value={"name": vh["name"], "type": vh.get("type", "unknown"),
                    "year": vh.get("year"), "doi": None},
            source=f"pdf:{arxiv_id}",
            evidence=evidence_quote,
            evidence_url=pdf_path, json_path="page[0]",
            quote=evidence_quote, identity_score=1.0,
            extraction_score=conf, source_reliability=0.80,
            corroboration_group="paper_source",
            confidence=min(conf, 0.65), agent=AGENT, round_id=round_id,
        ))
        written_fields.append("venue")
    return {
        "status": "ok",
        "text_chars": len(text),
        "image_used": bool(image),
        "latex_path": latex_path,
        "written_fields": written_fields,
    }
