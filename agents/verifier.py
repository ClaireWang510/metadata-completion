"""Deterministic claim/evidence verification and selective abstention."""
from __future__ import annotations
import re
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from tools.matching import name_similarity
import config

AGENT = "Verifier"


def _validate(field: str, paper: dict[str, Any], candidate: Any) -> tuple[float, list[str]]:
    score = float(candidate.confidence)
    reasons: list[str] = []
    value = candidate.value
    if not candidate.evidence or not (candidate.evidence_url or candidate.quote or candidate.json_path):
        score -= 0.08; reasons.append("evidence location is incomplete")
    if candidate.identity_score < 0.82:
        score -= 0.20; reasons.append("paper identity match is weak")

    if field == "venue" and isinstance(value, dict):
        doi = str(value.get("doi") or "")
        vtype = value.get("type")
        status = value.get("publication_status", "unknown")
        if doi and not re.fullmatch(r"(?:https?://doi\.org/)?10\.\d{4,9}/\S+", doi,
                                    flags=re.IGNORECASE):
            score -= 0.25; reasons.append("DOI syntax is invalid")
        if vtype != "preprint" and "10.48550/arxiv." in doi.casefold():
            score = 0.0; reasons.append("repository DOI cannot be a publisher DOI")
        if status == "submitted" and vtype != "preprint":
            score = min(score, 0.49); reasons.append("submission does not prove publication")
        if status in {"published", "accepted"} and not value.get("name"):
            score = 0.0; reasons.append("publication container is absent")
    elif field.startswith("authors[") and isinstance(value, dict):
        index = int(field.split("[", 1)[1].split("]", 1)[0])
        expected = str((paper.get("authors") or [])[index].get("name", ""))
        if name_similarity(str(value.get("author_name", "")), expected) < 0.72:
            score = 0.0; reasons.append("author identity mismatch")
        if not value.get("affiliations"):
            score = 0.0; reasons.append("no affiliation was extracted")
    return max(0.0, min(1.0, score)), reasons


def verify(field: str, paper: dict[str, Any], candidates: list[dict[str, Any]],
           cost: CostRecorder) -> dict[str, Any]:
    """Compatibility entrypoint for callers supplying serialized candidates."""
    from schema import Candidate
    bb = Blackboard()
    for item in candidates:
        bb.write(Candidate.model_validate(item))
    return run(paper, bb, cost, fields=[field])[field]


def run(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder,
        fields: list[str] | None = None) -> dict[str, dict]:
    report: dict[str, dict] = {}
    for field in fields if fields is not None else bb.fields():
        chosen = bb.adjudicate(field)
        if chosen is None:
            report[field] = {"field": field, "chosen_value": None,
                "credibility": 0.0, "reason": "No candidate was produced.",
                "need_more_evidence": True, "decision": "abstained"}
            continue
        credibility, reasons = _validate(field, paper, chosen)
        decision = chosen.decision
        if decision == "conflicted":
            credibility = min(credibility, config.CONF_THRESHOLD - 0.01)
            reasons.append("independent high-confidence claims conflict")
        elif credibility >= config.CONF_THRESHOLD:
            decision = "accepted"
        else:
            decision = "abstained"
        report[field] = {
            "field": field, "chosen_value": chosen.value,
            "credibility": credibility,
            "reason": "; ".join(reasons) or "deterministic constraints passed",
            "need_more_evidence": decision != "accepted", "decision": decision,
            "source": chosen.source, "evidence_url": chosen.evidence_url,
            "json_path": chosen.json_path, "retrieved_at": chosen.retrieved_at,
        }
    return report
