"""Crossref works API - fallback for DOI / venue."""
from __future__ import annotations
from typing import Any

import config
from cost import CostRecorder
from tools.http import get_json
from tools.matching import title_similarity


def search_by_bibliographic(title: str, *, cost: CostRecorder,
                            rows: int = 10) -> list[dict[str, Any]]:
    data = get_json(config.CROSSREF_BASE,
                    {"query.bibliographic": title, "rows": rows,
                     "select": "DOI,title,container-title,type,issued,author,URL,publisher"},
                    agent="WebAgent", cost=cost)
    if not data:
        return []
    items = ((data.get("message") or {}).get("items") or [])
    return sorted(items, key=lambda item: title_similarity(
        (item.get("title") or [""])[0], title), reverse=True)


def search_by_title(title: str, *, cost: CostRecorder) -> dict[str, Any] | None:
    """Backward-compatible best result; new code should inspect top-k."""
    items = search_by_bibliographic(title, cost=cost)
    return items[0] if items else None


def parse_venue(item: dict[str, Any]) -> dict[str, Any] | None:
    title = (item.get("container-title") or [None])[0]
    if not title:
        return None
    ctype = item.get("type", "")
    vt = "journal" if "journal" in ctype else "conference" if "proceedings" in ctype else "unknown"
    year = None
    issued = item.get("issued") or {}
    dp = issued.get("date-parts")
    if dp and dp[0]:
        year = dp[0][0]
    return {"name": title, "type": vt, "year": year, "doi": item.get("DOI"),
            "publication_status": "published"}


def parse_authorships(item: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for author in item.get("author") or []:
        name = " ".join(p for p in (author.get("given"), author.get("family")) if p)
        affiliations = [a.get("name") for a in author.get("affiliation") or [] if a.get("name")]
        out.append({"author_name": name, "orcid": author.get("ORCID"),
                    "affiliations": affiliations})
    return out
