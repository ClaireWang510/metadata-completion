"""Link classification agent: label each resource_link into 6 classes."""
from __future__ import annotations
import json
from typing import Any

from blackboard import Blackboard
from cost import CostRecorder
from llm import chat_json
from schema import Candidate
import config


AGENT = "LinkAgent"
LINK_CLASSES = {
    "official_code", "official_dataset", "official_project",
    "cited_external", "template_boilerplate", "other",
}

SYSTEM = """You classify URLs found inside a scientific paper into one of:
- official_code        : code released by the paper's authors (this paper)
- official_dataset     : dataset released by the paper's authors (this paper)
- official_project     : the paper's own project page / demo / website
- cited_external       : a link to an EXTERNAL resource (baseline model, referenced dataset, prior work, competition site, etc.) that is only *cited*, not released by this paper
- template_boilerplate : boilerplate URLs from LaTeX templates (e.g., CVPR/ICML template repo)
- other                : anything else (broken URL, personal homepage, org page not tied to the paper's contribution)

You MUST return JSON: {"items":[{"index":int,"link_class":str,"confidence":float,"rationale":str}, ...]}
Rely on: (1) the paper's title & abstract, (2) the LaTeX/description context surrounding each URL.
Prefer HIGH confidence only when the surrounding context contains a clear anchor phrase
like 'Code is available at', 'We release', 'Our dataset', 'project page', etc."""


def _build_user(paper: dict[str, Any]) -> tuple[str, list[dict]]:
    links = paper.get("resource_links", []) or []
    items = []
    for i, l in enumerate(links):
        items.append({
            "index": i,
            "url": l.get("url", ""),
            "context": (l.get("description") or "")[:400],
            "link_type_regex": l.get("link_type", ""),
        })
    payload = {
        "title": paper.get("title", ""),
        "abstract": (paper.get("abstract") or "")[:1500],
        "links": items,
    }
    return json.dumps(payload, ensure_ascii=False), items


def run(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder,
        round_id: int) -> None:
    links = paper.get("resource_links", []) or []
    if not links:
        return
    user, items = _build_user(paper)
    out = chat_json(model=config.LINK_MODEL, system=SYSTEM, user=user,
                    agent=AGENT, cost=cost, max_tokens=1200)
    for it in out.get("items", []):
        i = it.get("index")
        if not isinstance(i, int) or i < 0 or i >= len(links):
            continue
        link_class = it.get("link_class", "other")
        if link_class not in LINK_CLASSES:
            link_class = "other"
        conf = float(it.get("confidence", 0.5))
        rat  = it.get("rationale", "")
        bb.write(Candidate(
            field=f"resource_links[{i}].link_class",
            value={"url": links[i].get("url", ""),
                    "link_class": link_class, "rationale": rat},
            source="llm:LinkAgent",
            evidence=(links[i].get("description") or "")[:120],
            confidence=conf, identity_score=1.0, extraction_score=conf,
            source_reliability=0.60, corroboration_group="link_llm",
            agent=AGENT, round_id=round_id,
        ))
