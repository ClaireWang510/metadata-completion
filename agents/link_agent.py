"""Link classification agent: label each resource_link into 6 classes."""
from __future__ import annotations
import json
import re
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
Return exactly one item for every supplied index. Do NOT open or visit URLs. First make a
cheap prior judgment from the URL string, host, path, regex type, and nearby context. For
example, paths containing LaTeX/template/style-file markers are very likely template
boilerplate; third-party documentation, model, dataset, and tool URLs introduced by
'we use', 'using', or 'based on' are usually cited_external. Then use the paper title and
abstract only to distinguish the paper's own official resources from external ones.
Rely on: (1) URL syntax/ownership, (2) the paper's title & abstract, and (3) the
LaTeX/description context surrounding each URL.
Prefer HIGH confidence only when the surrounding context contains a clear anchor phrase
like 'Code is available at', 'We release', 'Our dataset', 'project page', etc."""


# These patterns are deliberately high precision. Ambiguous URLs still go to the LLM.
_TEMPLATE_URL = re.compile(
    r"(?:cvpr[_-]?template|acl-style-files|"
    r"(?:latex|tex)[_./-]?(?:template|style)|"
    r"(?:template|style)[_./-]?(?:latex|tex)|"
    r"(?:neurips|nips|icml|iclr|emnlp|naacl)[^?#]*(?:template|style))",
    re.IGNORECASE,
)


def _preclassify(item: dict[str, Any]) -> dict[str, Any] | None:
    """Classify only URL forms that are safe without network or model access."""
    url = str(item.get("url") or "").replace(r"\_", "_")
    if _TEMPLATE_URL.search(url):
        return {
            "index": item["index"],
            "link_class": "template_boilerplate",
            "confidence": 0.98,
            "rationale": "URL path contains a conference/LaTeX template or style-file marker.",
        }
    return None


def _items(paper: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "url": l.get("url", ""),
            "context": (l.get("description") or "")[:400],
            "link_type_regex": l.get("link_type", ""),
        }
        for i, l in enumerate(paper.get("resource_links", []) or [])
    ]


def _build_user(paper: dict[str, Any], items: list[dict[str, Any]]) -> str:
    payload = {
        "title": paper.get("title", ""),
        "abstract": (paper.get("abstract") or "")[:1500],
        "links": items,
    }
    return json.dumps(payload, ensure_ascii=False)


def _write_candidate(result: dict[str, Any], links: list[dict[str, Any]],
                     bb: Blackboard, round_id: int, *, source: str) -> bool:
    i = result.get("index")
    if not isinstance(i, int) or i < 0 or i >= len(links):
        return False
    link_class = result.get("link_class", "other")
    if link_class not in LINK_CLASSES:
        link_class = "other"
    try:
        conf = max(0.0, min(1.0, float(result.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    rationale = str(result.get("rationale") or "")
    bb.write(Candidate(
        field=f"resource_links[{i}].link_class",
        value={"url": links[i].get("url", ""),
               "link_class": link_class, "rationale": rationale},
        source=source,
        evidence=(links[i].get("description") or "")[:120],
        confidence=conf, identity_score=1.0, extraction_score=conf,
        source_reliability=0.90 if source == "rule:url_pattern" else 0.60,
        corroboration_group="link_rule" if source == "rule:url_pattern" else "link_llm",
        agent=AGENT, round_id=round_id,
    ))
    return True


def run(paper: dict[str, Any], bb: Blackboard, cost: CostRecorder,
        round_id: int) -> None:
    links = paper.get("resource_links", []) or []
    if not links:
        return
    pending = []
    for item in _items(paper):
        prior = _preclassify(item)
        if prior:
            _write_candidate(prior, links, bb, round_id, source="rule:url_pattern")
        else:
            pending.append(item)

    # A bounded output is much less likely to time out or be truncated. Keeping the
    # original global indices also makes batches transparent to the blackboard.
    size = config.LINK_BATCH_SIZE
    for start in range(0, len(pending), size):
        batch = pending[start:start + size]
        out = chat_json(
            model=config.LINK_MODEL, system=SYSTEM,
            user=_build_user(paper, batch), agent=AGENT, cost=cost,
            max_tokens=max(500, min(1800, 180 * len(batch))),
        )
        seen: set[int] = set()
        for result in out.get("items", []):
            if not isinstance(result, dict):
                continue
            if _write_candidate(result, links, bb, round_id,
                                source="llm:LinkAgent"):
                seen.add(result["index"])
        # Record an explicit low-confidence fallback instead of silently dropping a
        # link when a provider returns truncated/malformed batch output.
        for item in batch:
            if item["index"] not in seen:
                _write_candidate({
                    "index": item["index"], "link_class": "other",
                    "confidence": 0.0,
                    "rationale": "Link classification batch returned no valid item.",
                }, links, bb, round_id, source="llm:LinkAgent:fallback")
