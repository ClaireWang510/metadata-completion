"""Shared blackboard: agents write Candidates, Planner adjudicates."""
from __future__ import annotations
from collections import defaultdict
from typing import Any
from schema import Candidate
import config


class Blackboard:
    def __init__(self):
        self._by_field: dict[str, list[Candidate]] = defaultdict(list)
        self._history: list[Candidate] = []

    def write(self, c: Candidate):
        self._by_field[c.field].append(c)
        self._history.append(c)

    def get(self, field: str) -> list[Candidate]:
        return list(self._by_field.get(field, []))

    def fields(self) -> list[str]:
        return list(self._by_field.keys())

    def history(self) -> list[Candidate]:
        return list(self._history)

    # ---------- adjudication ----------
    def adjudicate(self, field: str) -> Candidate | None:
        """
        Simple rule-based adjudication:
          - highest confidence wins
          - if two sources agree (same normalized value), boost confidence
          - sources without evidence get a penalty
        """
        cands = self._by_field.get(field, [])
        if not cands:
            return None

        # cluster by normalized value
        clusters: dict[str, list[Candidate]] = defaultdict(list)
        for c in cands:
            key = _claim_key(field, c.value)
            clusters[key].append(c)

        scored: list[tuple[float, Candidate]] = []
        for key, group in clusters.items():
            best = max(group, key=lambda x: x.confidence)
            # Repeating the same query in later rounds is not independent evidence.
            independent_sources = {
                c.corroboration_group or c.source.split(":", 1)[0] for c in group
            }
            agree_bonus = 0.08 * (len(independent_sources) - 1)
            evid_penalty = 0.0 if best.evidence else 0.05
            score = min(1.0, best.confidence + agree_bonus - evid_penalty)
            merged = best.model_copy(update={"confidence": score})
            scored.append((score, merged))

        scored.sort(key=lambda x: x[0], reverse=True)
        winner = scored[0][1]
        conflict = (len(scored) > 1 and scored[1][0] >= config.CONF_THRESHOLD
                    and abs(scored[0][0] - scored[1][0]) < 0.08)
        decision = "conflicted" if conflict else (
            "accepted" if scored[0][0] >= config.CONF_THRESHOLD else "abstained")
        return winner.model_copy(update={"decision": decision})

    def dump(self) -> dict[str, Any]:
        return {
            "by_field": {k: [c.model_dump() for c in v]
                          for k, v in self._by_field.items()},
            "history":  [c.model_dump() for c in self._history],
        }


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return " ".join(v.lower().split())
    if isinstance(v, list):
        return "|".join(_norm(x) for x in v)
    if isinstance(v, dict):
        return "|".join(f"{k}={_norm(v[k])}" for k in sorted(v))
    return str(v).lower()


def _claim_key(field: str, value: Any) -> str:
    """Cluster field claims without requiring byte-identical dictionaries."""
    if field == "venue" and isinstance(value, dict):
        doi = str(value.get("doi") or "").casefold()
        if doi and "10.48550/arxiv." not in doi:
            return f"doi:{doi.removeprefix('https://doi.org/')}"
        name = _norm(value.get("name"))
        year = value.get("year") or ""
        return f"venue:{name}|{year}"
    if field.endswith(".affiliations") and isinstance(value, dict):
        affs = value.get("affiliations") or []
        names = [item.get("name", "") if isinstance(item, dict) else item for item in affs]
        return "aff:" + "|".join(sorted(_norm(name) for name in names))
    return _norm(value)
