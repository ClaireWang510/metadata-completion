"""Optional IEEE Xplore Metadata API enrichment (requires an application key)."""
from __future__ import annotations
from typing import Any
from urllib.parse import quote

import config
from cost import CostRecorder
from tools.http import get_json


def fetch_by_doi(doi: str, *, cost: CostRecorder) -> dict[str, Any] | None:
    if not config.IEEE_API_KEY or not doi:
        return None
    url = f"{config.IEEE_BASE}/{quote(doi, safe='')}"
    data = get_json(url, {"apikey": config.IEEE_API_KEY},
                    agent="WebAgent", cost=cost)
    articles = (data or {}).get("articles") or []
    return articles[0] if articles else None


def parse_authorships(article: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    authors = ((article.get("authors") or {}).get("authors") or [])
    for author in authors:
        affiliations = author.get("affiliation") or []
        if isinstance(affiliations, str):
            affiliations = [affiliations]
        out.append({"author_name": author.get("full_name") or "",
                    "orcid": author.get("orcid"),
                    "affiliations": [a for a in affiliations if a]})
    return out
