"""Planner (main) Agent: diagnose -> plan -> dispatch -> adjudicate -> stop."""
from __future__ import annotations
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from schema import Candidate, CompletedMetadata, VenueValue, AffiliationValue, LinkValue
import config

from agents import web_agent, link_agent, pdf_agent, verifier
from tools.openalex import arxiv_doi
from tools.venue import canonical_name


AGENT = "Planner"

def _venue_complete(value: Any) -> bool:
    """A string venue is never a complete, evidenced publication claim."""
    if not isinstance(value, dict):
        return False
    status = value.get("publication_status") or value.get("status")
    required = (value.get("name"), value.get("type"), value.get("year"), status,
                value.get("source"), value.get("evidence") or value.get("evidence_url"))
    if not all(required):
        return False
    # DOI may legitimately be absent for a preprint or some workshop proceedings.
    return bool(value.get("doi") or status in {"preprint", "accepted"})


def diagnose(paper: dict[str, Any], cost: CostRecorder) -> dict[str, Any]:
    """Deterministic routing: retrieval must not depend on an LLM opinion."""
    missing_aff = any(not _affiliations_complete(a) for a in paper.get("authors", []))
    uncategorized = any(l.get("link_class") not in _LINK_CLASSES
                        for l in paper.get("resource_links", []))
    return {
        "venue_missing_or_wrong": not _venue_complete(paper.get("venue")),
        "affiliations_missing": missing_aff,
        "links_uncategorized": uncategorized,
        "notes": "Deterministic completeness check.",
    }


def _affiliations_complete(author: dict[str, Any]) -> bool:
    affiliations = author.get("affiliations") or []
    return bool(affiliations) and all(
        isinstance(item, dict) and bool(item.get("name")) for item in affiliations
    )


def _budget_left(cost: CostRecorder) -> bool:
    return cost.summary()["total"]["total_tokens"] < config.MAX_TOTAL_TOKENS_PER_PAPER


_LINK_CLASSES = {
    "official_code", "official_dataset", "official_project",
    "cited_external", "template_boilerplate", "other",
}


def _target_fields(paper: dict[str, Any], diag: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Build explicit targets; absence of a candidate must remain visible."""
    fields: list[str] = []
    web_tasks: list[str] = []
    if diag.get("venue_missing_or_wrong"):
        fields.append("venue")
        web_tasks.append("venue")

    missing_affiliations = [
        i for i, author in enumerate(paper.get("authors", []))
        if not _affiliations_complete(author)
    ]
    if missing_affiliations:
        web_tasks.append("affiliations")
        fields.extend(f"authors[{i}].affiliations" for i in missing_affiliations)

    for i, link in enumerate(paper.get("resource_links", [])):
        # link_type such as "github" describes the URL, not our semantic class.
        if link.get("link_class") not in _LINK_CLASSES:
            fields.append(f"resource_links[{i}].link_class")
    return fields, web_tasks


def _choose_with_verifier(bb: Blackboard, field: str,
                          report: dict[str, dict]) -> Candidate | None:
    """Use a credible verifier choice only when it exactly matches a candidate."""
    verification = report.get(field) or {}
    if (float(verification.get("credibility", 0.0)) >= config.CONF_THRESHOLD
            and not verification.get("need_more_evidence")):
        chosen_value = verification.get("chosen_value")
        for candidate in bb.get(field):
            if candidate.value == chosen_value:
                return candidate.model_copy(update={
                    "confidence": float(verification.get("credibility", candidate.confidence)),
                    "decision": verification.get("decision", "accepted"),
                })
    return bb.adjudicate(field)


def run(paper: dict[str, Any]) -> tuple[CompletedMetadata, Blackboard, CostRecorder, dict]:
    cost = CostRecorder()
    bb = Blackboard()

    # ---- 1. Diagnose ----
    diag = diagnose(paper, cost)

    target_fields, tasks_web = _target_fields(paper, diag)

    # Preserve the input as a low-reliability claim so an enrichment failure is
    # explicit rather than silently turning a known venue into null.
    input_venue = paper.get("venue")
    if input_venue:
        value = input_venue if isinstance(input_venue, dict) else {
            "name": str(input_venue), "type": "preprint" if "arxiv" in str(input_venue).casefold() else "unknown",
            "year": None, "doi": None,
            "publication_status": "preprint" if "arxiv" in str(input_venue).casefold() else "unknown",
        }
        bb.write(Candidate(field="venue", value=value, source="input:metadata",
                           evidence="original metadata (unverified)", confidence=0.35,
                           source_reliability=0.35, corroboration_group="input",
                           agent=AGENT, round_id=0))

    stopped_reason = ""
    verifier_report: dict[str, dict] = {}
    round_trace: list[dict[str, Any]] = []
    web_done = link_done = pdf_done = False
    it = 0

    for it in range(1, config.MAX_ITERATIONS + 1):
        actions: list[dict[str, Any]] = []
        # ---- 2. Dispatch cheap tools first ----
        if tasks_web and not web_done and _budget_left(cost):
            web_agent.run(paper, bb, cost, round_id=it, tasks=tasks_web)
            web_done = True
            actions.append({"agent": "WebAgent", "status": "completed"})

        link_targets = [f for f in target_fields if f.startswith("resource_links[")]
        if link_targets and not link_done and _budget_left(cost):
            link_agent.run(paper, bb, cost, round_id=it)
            link_done = True
            actions.append({"agent": "LinkAgent", "status": "completed"})

        # ---- 3. Verify ----
        if not _budget_left(cost):
            stopped_reason = "token_budget_exhausted"; break
        verifier_report = verifier.run(paper, bb, cost, fields=target_fields)

        low_conf = [f for f in target_fields
                    for r in [verifier_report.get(f, {})]
                    if float(r.get("credibility", 0.0)) < config.CONF_THRESHOLD
                    or r.get("need_more_evidence")]
        round_trace.append({
            "round_id": it,
            "actions": actions,
            "low_confidence_fields": low_conf,
            "verifier_report": verifier_report,
        })

        if not low_conf:
            stopped_reason = "all_confident"; break

        # ---- 4. Escalate to PDF Agent for remaining low-conf fields ----
        need_pdf = any(f == "venue" or f.startswith("authors[") for f in low_conf)
        if (need_pdf and config.ENABLE_PDF_AGENT and not pdf_done
                and _budget_left(cost)):
            pdf_result = pdf_agent.run(paper, bb, cost, round_id=it)
            pdf_done = True
            round_trace[-1]["actions"].append({"agent": "PDFAgent", **pdf_result})
        else:
            stopped_reason = "no_more_tools"; break
    else:
        stopped_reason = "max_iterations"

    # ---- 5. Adjudicate final values ----
    completed = CompletedMetadata(arxiv_id=paper.get("arxiv_id", ""),
                                  arxiv_doi=arxiv_doi(paper.get("arxiv_id", "")),
                                  iterations=it, stopped_reason=stopped_reason)

    venue_final = _choose_with_verifier(bb, "venue", verifier_report)
    if venue_final and isinstance(venue_final.value, dict):
        v = venue_final.value
        publisher_doi = v.get("doi")
        if publisher_doi and "10.48550/arxiv." in str(publisher_doi).casefold():
            publisher_doi = None
        completed.venue_completed = VenueValue(
            name=canonical_name(v.get("name", ""), v.get("type", "unknown"),
                                v.get("year")), type=v.get("type", "unknown"),
            year=v.get("year"), doi=publisher_doi,
            publication_status=v.get("publication_status", "unknown"),
            source=venue_final.source, evidence=venue_final.quote or venue_final.evidence,
            evidence_url=venue_final.evidence_url,
            json_path=venue_final.json_path, retrieved_at=venue_final.retrieved_at,
            confidence=venue_final.confidence, decision=venue_final.decision,
        )

    # affiliations: iterate per author index
    for i, a in enumerate(paper.get("authors", [])):
        field = f"authors[{i}].affiliations"
        chosen = _choose_with_verifier(bb, field, verifier_report)
        if chosen and isinstance(chosen.value, dict):
            completed.affiliations_completed.append(AffiliationValue(
                author_name=chosen.value.get("author_name") or a.get("name", ""),
                affiliations=chosen.value.get("affiliations", []),
                source=chosen.source, evidence=chosen.quote or chosen.evidence,
                evidence_url=chosen.evidence_url, confidence=chosen.confidence,
                json_path=chosen.json_path, retrieved_at=chosen.retrieved_at,
                decision=chosen.decision,
            ))
        else:
            completed.affiliations_completed.append(AffiliationValue(
                author_name=a.get("name", ""),
                affiliations=a.get("affiliations", []),
                source="input:metadata" if a.get("affiliations") else "",
                evidence="original metadata" if a.get("affiliations") else "",
                confidence=0.35 if a.get("affiliations") else 0.0,
                decision="abstained",
            ))

    for i, l in enumerate(paper.get("resource_links", [])):
        field = f"resource_links[{i}].link_class"
        chosen = _choose_with_verifier(bb, field, verifier_report)
        if chosen and isinstance(chosen.value, dict):
            completed.resource_links_completed.append(LinkValue(
                url=l.get("url", ""),
                link_class=chosen.value.get("link_class", "other"),
                rationale=chosen.value.get("rationale", ""),
                source=chosen.source, evidence=chosen.evidence,
                retrieved_at=chosen.retrieved_at,
                confidence=chosen.confidence, decision=chosen.decision,
            ))
        else:
            completed.resource_links_completed.append(LinkValue(
                url=l.get("url", ""),
                link_class=l.get("link_class", "other")
                if l.get("link_class") in _LINK_CLASSES else "other",
                rationale=l.get("rationale", ""),
                source="input:metadata" if l.get("link_class") else "",
                evidence=l.get("description", ""),
                confidence=0.35 if l.get("link_class") else 0.0,
                decision="abstained",
            ))

    trace = {
        "diagnosis": diag,
        "blackboard": bb.dump(),
        "verifier_report": verifier_report,
        "target_fields": target_fields,
        "rounds": round_trace,
    }
    return completed, bb, cost, trace
