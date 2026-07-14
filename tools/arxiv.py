"""arXiv Atom API metadata, including comments and journal references."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html import unescape
from typing import Any

from cost import CostRecorder
from tools.http import get_text

API = "https://export.arxiv.org/api/query"
ABS = "https://arxiv.org/abs/{id}"
NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def fetch(arxiv_id: str, *, cost: CostRecorder) -> dict[str, Any] | None:
    xml = get_text(API, {"id_list": arxiv_id, "max_results": 1},
                   agent="WebAgent", cost=cost)
    if not xml:
        return _fetch_abs_page(arxiv_id, cost=cost)
    try:
        root = ET.fromstring(xml)
        entry = root.find("atom:entry", NS)
    except ET.ParseError:
        return _fetch_abs_page(arxiv_id, cost=cost)
    if entry is None:
        return None
    def val(path: str) -> str:
        node = entry.find(path, NS)
        return " ".join((node.text or "").split()) if node is not None else ""
    return {
        "id": val("atom:id"), "title": val("atom:title"),
        "comment": val("arxiv:comment"), "journal_ref": val("arxiv:journal_ref"),
        "doi": val("arxiv:doi"), "published": val("atom:published"),
        "updated": val("atom:updated"),
    }


def _fetch_abs_page(arxiv_id: str, *, cost: CostRecorder) -> dict[str, Any] | None:
    """Rate-limit fallback for the public abstract page's labeled metadata."""
    url = ABS.format(id=arxiv_id)
    html = get_text(url, None, agent="WebAgent", cost=cost)
    if not html:
        return None
    def meta(name: str) -> str:
        match = re.search(rf'<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\'](.*?)["\']',
                          html, flags=re.IGNORECASE | re.DOTALL)
        return unescape(match.group(1)).strip() if match else ""
    def table(label: str) -> str:
        match = re.search(rf'<td[^>]*class=["\'][^"\']*{label}[^"\']*["\'][^>]*>(.*?)</td>',
                          html, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return " ".join(unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())
    return {"id": url, "title": meta("citation_title"),
            "comment": table("comments"), "journal_ref": table("jref"),
            "doi": meta("citation_doi"), "published": meta("citation_date"),
            "updated": ""}


def publication_signal(record: dict[str, Any]) -> dict[str, Any]:
    """Classify claims conservatively; submissions never become publications."""
    journal_ref = str(record.get("journal_ref") or "").strip()
    comment = str(record.get("comment") or "").strip()
    text = journal_ref or comment
    low = text.casefold()
    if journal_ref:
        status, strength = "published", 0.92
    elif re.search(r"\b(accepted|to appear|published|presented at)\b", low):
        status, strength = "accepted", 0.82
    elif re.search(r"\b(submitted|submission|under review|in review)\b", low):
        status, strength = "submitted", 0.45
    else:
        status, strength = "unknown", 0.25
    return {"status": status, "text": text, "confidence": strength,
            "may_prove_publication": status in {"accepted", "published"}}


def parse_venue_hint(record: dict[str, Any]) -> dict[str, Any] | None:
    signal = publication_signal(record)
    if not signal["may_prove_publication"]:
        return None
    text = signal["text"]
    # Keep this deliberately conservative: return the clause after an explicit
    # publication verb, or the full journal-ref. Formal APIs will normalize it.
    match = re.search(
        r"(?:accepted(?:\s+and\s+presented)?|presented|to appear|published)\s+"
        r"(?:at|in|to)\s+([^.;]+)", text, re.IGNORECASE)
    name = (match.group(1) if match else text).strip(" ,")
    if not name or len(name) > 200:
        return None
    year_match = re.search(r"\b(19|20)\d{2}\b", name)
    low = name.casefold()
    vtype = "journal" if re.search(r"\b(journal|transactions|letters)\b", low) \
        else "workshop" if "workshop" in low else "conference"
    return {"name": name, "type": vtype,
            "year": int(year_match.group()) if year_match else None,
            "doi": record.get("doi") or None,
            "publication_status": signal["status"],
            "confidence": signal["confidence"], "quote": text}
