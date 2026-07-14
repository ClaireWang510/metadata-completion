"""Deterministic paper/author entity matching helpers."""
from __future__ import annotations

import itertools
import re
import unicodedata
from typing import Any

from rapidfuzz import fuzz


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.findall(r"[\w]+", text, flags=re.UNICODE))


def title_similarity(a: str, b: str) -> float:
    return fuzz.token_set_ratio(normalize_text(a), normalize_text(b)) / 100.0


def name_similarity(a: str, b: str) -> float:
    """Score names robustly to middle names, initials, order and accents."""
    aa, bb = normalize_text(a).split(), normalize_text(b).split()
    if not aa or not bb:
        return 0.0
    surname = 1.0 if aa[-1] == bb[-1] else fuzz.ratio(aa[-1], bb[-1]) / 100.0
    first = 1.0 if aa[0] == bb[0] else (0.9 if aa[0][0] == bb[0][0] else 0.0)
    token = fuzz.token_set_ratio(" ".join(aa), " ".join(bb)) / 100.0
    return 0.45 * surname + 0.35 * first + 0.20 * token


def match_author_indices(source_authors: list[dict[str, Any]],
                         paper_authors: list[dict[str, Any]],
                         minimum: float = 0.72) -> list[tuple[int, dict[str, Any], float]]:
    """Maximum-weight one-to-one assignment, with a small author-order prior.

    Author lists are small, so an exact dynamic program avoids a heavy scipy
    dependency while preventing greedy matching from consuming the wrong name.
    """
    n, m = len(source_authors), len(paper_authors)
    if not n or not m:
        return []
    scores = [[0.0] * m for _ in range(n)]
    for i, source in enumerate(source_authors):
        for j, paper in enumerate(paper_authors):
            name = name_similarity(str(source.get("author_name", "")),
                                   str(paper.get("name", "")))
            order = max(0.0, 1.0 - abs(i - j) / max(n, m, 1))
            orcid_a = str(source.get("orcid") or "").lower().rstrip("/")
            orcid_b = str(paper.get("orcid") or "").lower().rstrip("/")
            if orcid_a and orcid_b:
                name = 1.0 if orcid_a == orcid_b else 0.0
            scores[i][j] = 0.9 * name + 0.1 * order

    # DP over source position and used target mask; skipping is allowed.
    states: dict[int, tuple[float, list[tuple[int, int, float]]]] = {0: (0.0, [])}
    for i in range(n):
        next_states = dict(states)
        for mask, (total, pairs) in states.items():
            for j in range(m):
                if mask & (1 << j) or scores[i][j] < minimum:
                    continue
                key = mask | (1 << j)
                value = (total + scores[i][j], pairs + [(i, j, scores[i][j])])
                if key not in next_states or value[0] > next_states[key][0]:
                    next_states[key] = value
        states = next_states
    _, pairs = max(states.values(), key=lambda item: (len(item[1]), item[0]))
    return [(j, source_authors[i], score) for i, j, score in sorted(pairs)]
