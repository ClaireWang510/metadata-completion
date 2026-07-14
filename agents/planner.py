"""LLM Planner Agent: inspect the blackboard, dispatch agents, and re-plan."""
from __future__ import annotations

import json
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from llm import chat_json
from schema import Candidate, CompletedMetadata, VenueValue, AffiliationValue, LinkValue
import config

from agents import web_agent, link_agent, pdf_agent, verifier
from tools.openalex import arxiv_doi
from tools.venue import canonical_name


AGENT = "Planner"
AVAILABLE_AGENTS = {"web", "link", "pdf"}

SYSTEM = """You are the Planner in a scientific-metadata multi-agent system.
You own diagnosis, task decomposition, agent selection, re-planning, and stopping.
Inspect the paper, blackboard summaries, previous actions, and Verifier feedback.
Choose actions that can obtain missing or stronger evidence; do not follow a fixed
pipeline. WebAgent retrieves venue/affiliation records, LinkAgent semantically
classifies resource URLs, and PDFAgent reads the paper first page and LaTeX front
matter for venue/affiliations. Do not request an agent already used unless there is
a genuinely new task it can perform. Stop only when fields are verified, no useful
agent remains, or evidence is insufficient and abstention is appropriate.

Return JSON only:
{"target_fields":[str], "actions":[{
 "agent":"web"|"link"|"pdf", "tasks":["venue"|"affiliations"|"links"],
 "reason":str}], "stop":bool, "stop_reason":str, "rationale":str}

Field names must be venue, authors[i].affiliations, or
resource_links[i].link_class. Never invent an index. For resource links, request one
bulk {"agent":"link","tasks":["links"]} action; target_fields need not enumerate every
link because the runtime expands that action. Empty actions are allowed."""


def _budget_left(cost: CostRecorder) -> bool:
    return cost.summary()["total"]["total_tokens"] < config.MAX_TOTAL_TOKENS_PER_PAPER


def _valid_fields(paper: dict[str, Any]) -> set[str]:
    fields = {"venue"}
    fields.update(f"authors[{i}].affiliations" for i, _ in enumerate(paper.get("authors", [])))
    fields.update(f"resource_links[{i}].link_class"
                  for i, _ in enumerate(paper.get("resource_links", [])))
    return fields


def _planner_payload(paper: dict[str, Any], bb: Blackboard, round_id: int,
                     used_agents: set[str], verifier_report: dict[str, dict],
                     previous_rounds: list[dict[str, Any]]) -> dict[str, Any]:
    resource_links = paper.get("resource_links", []) or []
    resource_prefix = "resource_links["
    non_link_fields = [field for field in _valid_fields(paper)
                       if not field.startswith(resource_prefix)]
    link_decisions: dict[str, int] = {}
    for field, item in verifier_report.items():
        if field.startswith(resource_prefix):
            decision = str(item.get("decision") or "missing")
            link_decisions[decision] = link_decisions.get(decision, 0) + 1
    return {
        "round_id": round_id,
        "paper": {
            "arxiv_id": paper.get("arxiv_id"), "title": paper.get("title"),
            "abstract": str(paper.get("abstract") or "")[:1800],
            "venue": paper.get("venue"),
            "authors": [{"index": i, "name": author.get("name"),
                         "affiliations": author.get("affiliations", [])}
                        for i, author in enumerate(paper.get("authors", []))],
            # Planning only needs to decide whether LinkAgent is useful. Full URL
            # contexts are sent later in bounded batches by LinkAgent itself.
            "resource_links": [{"index": i, "url": str(link.get("url") or "")[:240],
                                "has_context": bool(link.get("description")),
                                "link_class": link.get("link_class")}
                               for i, link in enumerate(resource_links[:50])],
            "resource_link_count": len(resource_links),
            "resource_links_omitted": max(0, len(resource_links) - 50),
        },
        "valid_target_fields": sorted(non_link_fields),
        "resource_link_target_range": (
            f"resource_links[0..{len(resource_links) - 1}].link_class"
            if resource_links else None
        ),
        # Planner needs decision-level state, not the Verifier's full evidence packet.
        "blackboard_summary": {field: [{
            "value": c.value, "source": c.source,
            "confidence": c.confidence, "agent": c.agent,
        } for c in bb.get(field)] for field in bb.fields()
            if not field.startswith(resource_prefix)},
        "resource_link_candidate_count": sum(
            1 for field in bb.fields() if field.startswith(resource_prefix)),
        "resource_link_verification_summary": link_decisions,
        "verifier_report": {field: {
            "decision": item.get("decision"),
            "credibility": item.get("credibility"),
            "need_more_evidence": item.get("need_more_evidence"),
            "reason": str(item.get("reason") or "")[:500],
            "suggested_action": item.get("suggested_action"),
        } for field, item in verifier_report.items()
            if not field.startswith(resource_prefix)},
        "used_agents": sorted(used_agents),
        "previous_rounds": [{
            "round_id": item.get("round_id"),
            "actions": [{"agent": action.get("agent"), "tasks": action.get("tasks"),
                         "status": action.get("status")}
                        for action in item.get("actions", [])],
            "unresolved_fields": item.get("unresolved_fields", [])[:50],
            "unresolved_fields_omitted": max(
                0, len(item.get("unresolved_fields", [])) - 50),
        } for item in previous_rounds[-2:]],
        "pdf_enabled": config.ENABLE_PDF_AGENT,
    }


def plan(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder, *,
         round_id: int, used_agents: set[str], verifier_report: dict[str, dict],
         previous_rounds: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask the Planner model for the next structured action plan."""
    payload = _planner_payload(paper, bb, round_id, used_agents,
                               verifier_report, previous_rounds)
    out = chat_json(model=config.PLANNER_MODEL, system=SYSTEM,
                    user=json.dumps(payload, ensure_ascii=False), agent=AGENT,
                    cost=cost, max_tokens=1600)
    if out.get("_error"):
        return {"target_fields": [], "actions": [], "stop": True,
                "stop_reason": "planner_model_error",
                "rationale": str(out.get("_error"))}

    valid_fields = _valid_fields(paper)
    targets = [f for f in out.get("target_fields", []) if f in valid_fields]
    actions = []
    for action in out.get("actions", []):
        if not isinstance(action, dict):
            continue
        agent = action.get("agent")
        if agent not in AVAILABLE_AGENTS or agent in used_agents:
            continue
        if agent == "pdf" and not config.ENABLE_PDF_AGENT:
            continue
        allowed_tasks = {"web": {"venue", "affiliations"},
                         "link": {"links"},
                         "pdf": {"venue", "affiliations"}}[agent]
        tasks = [t for t in action.get("tasks", []) if t in allowed_tasks]
        if tasks:
            actions.append({"agent": agent, "tasks": tasks,
                            "reason": str(action.get("reason") or "")})
    # Bulk actions imply their concrete fields. This avoids asking the Planner to
    # emit hundreds of nearly identical link field names.
    for action in actions:
        if action["agent"] == "link" and "links" in action["tasks"]:
            targets.extend(f"resource_links[{i}].link_class"
                           for i, _ in enumerate(paper.get("resource_links", [])))
        if action["agent"] == "web":
            if "venue" in action["tasks"]:
                targets.append("venue")
            if "affiliations" in action["tasks"]:
                targets.extend(f"authors[{i}].affiliations"
                               for i, _ in enumerate(paper.get("authors", [])))
    targets = list(dict.fromkeys(targets))
    return {
        "target_fields": targets, "actions": actions,
        "stop": bool(out.get("stop", False)),
        "stop_reason": str(out.get("stop_reason") or "planner_stopped"),
        "rationale": str(out.get("rationale") or ""),
    }


def _dispatch(action: dict[str, Any], paper: dict[str, Any], bb: Blackboard,
              cost: CostRecorder, round_id: int) -> dict[str, Any]:
    agent = action["agent"]
    if agent == "web":
        web_agent.run(paper, bb, cost, round_id=round_id, tasks=action["tasks"])
        result = {"status": "completed"}
    elif agent == "link":
        link_agent.run(paper, bb, cost, round_id=round_id)
        result = {"status": "completed"}
    else:
        result = pdf_agent.run(paper, bb, cost, round_id=round_id)
    return {"agent": agent, "tasks": action["tasks"],
            "reason": action.get("reason", ""), **result}


def _verified_candidate(bb: Blackboard, field: str,
                        report: dict[str, dict]) -> Candidate | None:
    verification = report.get(field) or {}
    index = verification.get("candidate_index")
    candidates = bb.get(field)
    if not isinstance(index, int) or not 0 <= index < len(candidates):
        return None
    if verification.get("decision") not in {"accepted", "conflicted"}:
        return None
    return candidates[index].model_copy(update={
        "confidence": float(verification.get("credibility", 0.0)),
        "decision": verification.get("decision"),
    })


def run(paper: dict[str, Any]) -> tuple[CompletedMetadata, Blackboard, CostRecorder, dict]:
    cost, bb = CostRecorder(), Blackboard()
    used_agents: set[str] = set()
    target_fields: set[str] = set()
    verifier_report: dict[str, dict] = {}
    round_trace: list[dict[str, Any]] = []
    stopped_reason = "max_iterations"
    it = 0

    for it in range(1, config.MAX_ITERATIONS + 1):
        if not _budget_left(cost):
            stopped_reason = "token_budget_exhausted"
            break
        next_plan = plan(paper, bb, cost, round_id=it, used_agents=used_agents,
                         verifier_report=verifier_report,
                         previous_rounds=round_trace)
        target_fields.update(next_plan["target_fields"])
        trace_round = {"round_id": it, "plan": next_plan, "actions": []}
        round_trace.append(trace_round)

        if next_plan["stop"] and not next_plan["actions"]:
            stopped_reason = next_plan["stop_reason"]
            break
        for action in next_plan["actions"]:
            if not _budget_left(cost):
                stopped_reason = "token_budget_exhausted"
                break
            trace_round["actions"].append(_dispatch(action, paper, bb, cost, it))
            used_agents.add(action["agent"])

        if target_fields and _budget_left(cost):
            verifier_report = verifier.run(paper, bb, cost,
                                            fields=sorted(target_fields))
            trace_round["verifier_report"] = verifier_report
            unresolved = [f for f in target_fields
                          if verifier_report.get(f, {}).get("decision") != "accepted"]
            trace_round["unresolved_fields"] = sorted(unresolved)
            if not unresolved:
                stopped_reason = "all_verified"
                break
        if not next_plan["actions"]:
            stopped_reason = next_plan["stop_reason"] or "no_useful_actions"
            break

    completed = _build_completed(paper, bb, verifier_report, it, stopped_reason)
    trace = {"blackboard": bb.dump(), "verifier_report": verifier_report,
             "target_fields": sorted(target_fields), "rounds": round_trace,
             "used_agents": sorted(used_agents)}
    return completed, bb, cost, trace


def _build_completed(paper: dict[str, Any], bb: Blackboard,
                     report: dict[str, dict], iterations: int,
                     stopped_reason: str) -> CompletedMetadata:
    completed = CompletedMetadata(
        arxiv_id=paper.get("arxiv_id", ""),
        arxiv_doi=arxiv_doi(paper.get("arxiv_id", "")),
        iterations=iterations, stopped_reason=stopped_reason)
    venue = _verified_candidate(bb, "venue", report)
    if venue and isinstance(venue.value, dict):
        value = venue.value
        doi = value.get("doi")
        if doi and "10.48550/arxiv." in str(doi).casefold():
            doi = None
        completed.venue_completed = VenueValue(
            name=canonical_name(value.get("name", ""), value.get("type", "unknown"),
                                value.get("year")),
            type=value.get("type", "unknown"), year=value.get("year"), doi=doi,
            publication_status=value.get("publication_status", "unknown"),
            source=venue.source, evidence=venue.quote or venue.evidence,
            evidence_url=venue.evidence_url, json_path=venue.json_path,
            retrieved_at=venue.retrieved_at, confidence=venue.confidence,
            decision=venue.decision)

    for i, author in enumerate(paper.get("authors", [])):
        candidate = _verified_candidate(bb, f"authors[{i}].affiliations", report)
        completed.affiliations_completed.append(AffiliationValue(
            author_name=author.get("name", ""),
            affiliations=candidate.value.get("affiliations", []) if candidate else [],
            source=candidate.source if candidate else "",
            evidence=(candidate.quote or candidate.evidence) if candidate else "",
            evidence_url=candidate.evidence_url if candidate else "",
            json_path=candidate.json_path if candidate else "",
            retrieved_at=candidate.retrieved_at if candidate else "",
            confidence=candidate.confidence if candidate else 0.0,
            decision=candidate.decision if candidate else "abstained"))

    for i, link in enumerate(paper.get("resource_links", [])):
        candidate = _verified_candidate(bb, f"resource_links[{i}].link_class", report)
        value = candidate.value if candidate and isinstance(candidate.value, dict) else {}
        completed.resource_links_completed.append(LinkValue(
            url=link.get("url", ""), link_class=value.get("link_class", "other"),
            rationale=value.get("rationale", ""),
            source=candidate.source if candidate else "",
            evidence=candidate.evidence if candidate else "",
            retrieved_at=candidate.retrieved_at if candidate else "",
            confidence=candidate.confidence if candidate else 0.0,
            decision=candidate.decision if candidate else "abstained"))
    return completed
