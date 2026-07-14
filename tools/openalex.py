"""OpenAlex client - authorships (with institutions) + host_venue."""
from __future__ import annotations
from typing import Any

import config
from cost import CostRecorder
from tools.http import get_json


def _params(extra: dict[str, Any]) -> dict[str, Any]:
    p = dict(extra)
    if config.OPENALEX_MAILTO:
        p["mailto"] = config.OPENALEX_MAILTO
    return p


def search_by_arxiv_id(arxiv_id: str, *, cost: CostRecorder) -> dict[str, Any] | None:
    url = f"{config.OPENALEX_BASE}/works"
    # OpenAlex has no arXiv external-id filter. The canonical arXiv DOI is stable.
    data = get_json(url, _params({"filter": f"doi:10.48550/arXiv.{arxiv_id}",
                                    "per-page": 1}), agent="WebAgent", cost=cost)
    if data and data.get("results"):
        return data["results"][0]
    return None


def search_by_title(title: str, *, cost: CostRecorder) -> list[dict[str, Any]]:
    url = f"{config.OPENALEX_BASE}/works"
    data = get_json(url, _params({"search": title, "per-page": 3}),
                    agent="WebAgent", cost=cost)
    return list(data.get("results") or []) if data else []


def parse_venue(work: dict[str, Any]) -> dict[str, Any] | None:
    """Return {name,type,year,doi} or None."""
    loc = work.get("primary_location") or {}
    src = loc.get("source") or {}
    name = src.get("display_name") or loc.get("raw_source_name")
    src_type = src.get("type") or loc.get("raw_type")
    year = work.get("publication_year")
    doi = work.get("doi")
    if not name:
        host = work.get("host_venue") or {}
        name = host.get("display_name")
        src_type = host.get("type") or src_type

    if not name:
        return None
    type_map = {"journal": "journal", "conference": "conference",
                "book series": "conference", "repository": "preprint"}
    vt = type_map.get((src_type or "").lower(), "unknown")
    if vt == "unknown":
        low_name = name.lower()
        if "workshop" in low_name:
            vt = "workshop"
        elif "conference" in low_name or "proceedings" in low_name:
            vt = "conference"
        elif any(token in low_name for token in ("journal", "transactions", "letters")):
            vt = "journal"
    # arXiv repository → preprint
    if name.lower() in {"arxiv", "arxiv.org", "arxiv preprint"}:
        vt = "preprint"
    return {"name": name, "type": vt, "year": year, "doi": doi,
            "publication_status": "preprint" if vt == "preprint" else "published"}


def arxiv_doi(arxiv_id: str) -> str | None:
    """Return arXiv's canonical DOI without conflating it with a publisher DOI."""
    clean = (arxiv_id or "").strip()
    return f"10.48550/arXiv.{clean}" if clean else None


def parse_authorships(work: dict[str, Any]) -> list[dict[str, Any]]:
    """Return author names and their institution display names."""
    out: list[dict[str, Any]] = []
    for a in work.get("authorships", []):
        au = a.get("author") or {}
        insts = a.get("institutions") or []
        normalized = [{"name": i.get("display_name"),
                       "raw_name": i.get("display_name")}
                      for i in insts if i.get("display_name")]
        known = {i["name"].casefold() for i in normalized}
        for raw in a.get("raw_affiliation_strings") or []:
            if raw and raw.casefold() not in known:
                normalized.append({"name": raw, "raw_name": raw})
        out.append({
            "author_name": au.get("display_name", ""),
            "orcid": au.get("orcid"),
            "affiliations": normalized,
        })
    return out
