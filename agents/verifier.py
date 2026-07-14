"""LLM Verifier Agent: compare claims with evidence and selectively abstain."""
from __future__ import annotations

import json
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from llm import chat_json
import config


AGENT = "Verifier"

SYSTEM = """You are the independent Verifier in a scientific-metadata multi-agent system.
For every requested field, inspect all candidate claims and their evidence. Decide whether
the evidence semantically supports a claim about THIS paper. Do not merely trust numeric
confidence or source reputation. Pay special attention to paper identity, exact author
identity, publication versus submission/preprint, publisher DOI versus repository DOI,
and whether a URL is released by this paper rather than merely cited.

Return JSON only:
{"items":[{
  "field": str,
  "candidate_index": int|null,
  "credibility": number,
  "decision": "accepted"|"conflicted"|"abstained",
  "need_more_evidence": bool,
  "reason": str,
  "evidence_assessment": str,
  "suggested_action": "web"|"pdf"|"link"|"none"
}]}

candidate_index is the zero-based index in that field's candidates. Use null when no
candidate is adequately supported. Mark conflicted when independently supported claims
cannot be reconciled. An accepted claim must have credibility >= the supplied threshold.
Never invent a claim or evidence."""


def _paper_context(paper: dict[str, Any]) -> dict[str, Any]:
    return {
        "arxiv_id": paper.get("arxiv_id"),
        "title": paper.get("title"),
        "authors": [a.get("name") for a in paper.get("authors", [])],
        "abstract": str(paper.get("abstract") or "")[:1800],
    }


def _candidate_payload(candidate: Any, index: int) -> dict[str, Any]:
    return {
        "candidate_index": index,
        "value": candidate.value,
        "source": candidate.source,
        "evidence": str(candidate.evidence or "")[:1200],
        "evidence_url": candidate.evidence_url,
        "json_path": candidate.json_path,
        "quote": str(candidate.quote or "")[:2000],
        "identity_score": candidate.identity_score,
        "extraction_score": candidate.extraction_score,
        "source_reliability": candidate.source_reliability,
        "producer_confidence": candidate.confidence,
        "producer_agent": candidate.agent,
    }


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
    requested = list(fields if fields is not None else bb.fields())
    report: dict[str, dict] = {}
    size = config.VERIFIER_BATCH_SIZE
    for start in range(0, len(requested), size):
        batch = requested[start:start + size]
        report.update(_run_batch(paper, bb, cost, batch))
    return report


def _run_batch(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder,
               requested: list[str]) -> dict[str, dict]:
    """Verify a bounded field batch so large papers cannot create one huge call."""
    payload = {
        "paper": _paper_context(paper),
        "acceptance_threshold": config.CONF_THRESHOLD,
        "fields": [
            {"field": field,
             "candidates": [_candidate_payload(c, i) for i, c in enumerate(bb.get(field))]}
            for field in requested
        ],
    }
    out = chat_json(model=config.VERIFIER_MODEL, system=SYSTEM,
                    user=json.dumps(payload, ensure_ascii=False), agent=AGENT,
                    cost=cost, max_tokens=max(700, min(2400, 190 * len(requested))))
    if out.get("_error"):
        return {field: {
            "field": field, "candidate_index": None, "chosen_value": None,
            "credibility": 0.0, "decision": "abstained",
            "need_more_evidence": True,
            "reason": f"Verifier model failed: {out.get('_error')}",
            "suggested_action": "none",
        } for field in requested}

    by_field = {item.get("field"): item for item in out.get("items", [])
                if isinstance(item, dict) and item.get("field") in requested}
    report: dict[str, dict] = {}
    for field in requested:
        item = by_field.get(field, {})
        candidates = bb.get(field)
        index = item.get("candidate_index")
        valid_index = isinstance(index, int) and 0 <= index < len(candidates)
        credibility = max(0.0, min(1.0, float(item.get("credibility", 0.0))))
        decision = item.get("decision", "abstained")
        if decision not in {"accepted", "conflicted", "abstained"}:
            decision = "abstained"
        if not valid_index or (decision == "accepted" and credibility < config.CONF_THRESHOLD):
            decision = "abstained"
        chosen = candidates[index] if valid_index else None
        report[field] = {
            "field": field,
            "candidate_index": index if valid_index else None,
            "chosen_value": chosen.value if chosen else None,
            "credibility": credibility,
            "decision": decision,
            "need_more_evidence": bool(item.get("need_more_evidence", decision != "accepted")),
            "reason": str(item.get("reason") or "Verifier did not provide a reason."),
            "evidence_assessment": str(item.get("evidence_assessment") or ""),
            "suggested_action": item.get("suggested_action", "none"),
            "source": chosen.source if chosen else "",
            "evidence_url": chosen.evidence_url if chosen else "",
            "json_path": chosen.json_path if chosen else "",
            "retrieved_at": chosen.retrieved_at if chosen else "",
        }
    return report
