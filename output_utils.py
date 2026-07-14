"""Persist one pipeline run in the per-paper output layout."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def make_run_id() -> str:
    """A sortable, filesystem-safe UTC identifier with microsecond precision."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def write_run(output_root: str | os.PathLike[str], arxiv_id: str,
              completed: Any, trace: dict[str, Any], cost: Any,
              *, run_id: str | None = None) -> dict[str, str]:
    """Write an immutable run snapshot plus convenient latest files.

    Layout:
      out/<arxiv_id>/latest.{completed,trace,cost}.json
      out/<arxiv_id>/runs/<run_id>/{completed,trace,cost}.json
    """
    safe_id = str(arxiv_id).strip()
    if not safe_id or any(c in safe_id for c in '/\\') or safe_id in {".", ".."}:
        raise ValueError(f"Invalid arxiv_id: {arxiv_id!r}")

    paper_dir = Path(output_root) / safe_id
    snapshot_dir = paper_dir / "runs" / (run_id or make_run_id())
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    completed_data = completed.model_dump() if hasattr(completed, "model_dump") else completed
    cost_data = cost.summary() if hasattr(cost, "summary") else cost
    payloads = {
        "completed.json": completed_data,
        "trace.json": trace,
        "cost.json": cost_data,
    }
    for name, payload in payloads.items():
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
        (snapshot_dir / name).write_text(text, encoding="utf-8")
        latest = paper_dir / f"latest.{name}"
        temp = latest.with_suffix(latest.suffix + ".tmp")
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, latest)
    return {
        "paper_dir": str(paper_dir),
        "run_dir": str(snapshot_dir),
        "completed": str(paper_dir / "latest.completed.json"),
        "trace": str(paper_dir / "latest.trace.json"),
        "cost": str(paper_dir / "latest.cost.json"),
    }
