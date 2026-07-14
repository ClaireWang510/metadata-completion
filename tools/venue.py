"""Canonical publication-container presentation without changing identity."""
from __future__ import annotations
import re


def canonical_name(name: str, venue_type: str, year: int | None) -> str:
    value = " ".join((name or "").split())
    if venue_type not in {"conference", "workshop"} or not value:
        return value
    parenthetical = re.findall(r"\(([A-Z][A-Z0-9-]{1,15})\)", value)
    bare = re.fullmatch(r"[A-Z][A-Z0-9-]{1,15}", value)
    acronym = parenthetical[-1] if parenthetical else (value if bare else "")
    if acronym:
        result = acronym
        if year and not re.search(rf"\b{year}\b", result):
            result += f" {year}"
        if venue_type == "workshop" and "workshop" not in result.casefold():
            result += " Workshops"
        return result
    return value
