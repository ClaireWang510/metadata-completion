"""DBLP publ/api - primarily used to confirm conference/journal venue names."""
from __future__ import annotations
from typing import Any

import config
from cost import CostRecorder
from tools.http import get_json


def search_by_title(title: str, *, cost: CostRecorder) -> list[dict[str, Any]]:
    data = get_json(config.DBLP_BASE, {"q": title, "format": "json", "h": 5},
                    agent="WebAgent", cost=cost)
    if not data:
        return []
    hits = (((data.get("result") or {}).get("hits") or {}).get("hit")) or []
    out = []
    for h in hits:
        info = h.get("info") or {}
        out.append({
            "title": info.get("title"),
            "venue": info.get("venue"),
            "type": info.get("type"),          # 'Conference and Workshop Papers' / 'Journal Articles' / ...
            "year": info.get("year"),
            "doi":  info.get("doi"),
            "url":  info.get("url"),
        })
    return out


def normalize_venue_type(dblp_type: str | None) -> str:
    if not dblp_type:
        return "unknown"
    t = dblp_type.lower()
    if "journal" in t:      return "journal"
    # DBLP's generic class is literally "Conference and Workshop Papers";
    # it must not make every conference record a workshop.
    if "conference and workshop" in t: return "conference"
    if "workshop" in t:     return "workshop"
    if "conference" in t:   return "conference"
    if "informal" in t:     return "preprint"
    return "unknown"
