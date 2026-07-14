"""Safe extraction of LaTeX front matter for affiliation fallback."""
from __future__ import annotations
import re
from pathlib import Path


def frontmatter_text(root: str | Path, max_chars: int = 16000) -> tuple[str, str]:
    path = Path(root)
    if not path.is_dir():
        return "", ""
    candidates: list[tuple[int, Path, str]] = []
    for tex in path.rglob("*.tex"):
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        score = sum(token in text for token in (
            "\\title", "\\author", "\\affiliation", "\\institute", "\\maketitle"))
        if score:
            candidates.append((score, tex, text))
    if not candidates:
        return "", ""
    _, selected, text = max(candidates, key=lambda item: (item[0], len(item[2])))
    # Remove comments and stop before the main body where citations can mislead.
    text = re.sub(r"(?m)(?<!\\)%.*$", "", text)
    stops = [p for marker in ("\\begin{abstract}", "\\section{")
             if (p := text.find(marker)) >= 0]
    if stops:
        text = text[:min(stops)]
    return text[:max_chars], str(selected)
