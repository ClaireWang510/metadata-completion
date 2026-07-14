"""Token / API-call / latency accounting."""
from __future__ import annotations
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class CallRecord:
    agent: str
    kind: str                 # "llm" | "http" | "pdf"
    model_or_url: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    ok: bool = True
    extra: dict = field(default_factory=dict)


class CostRecorder:
    def __init__(self):
        self.records: list[CallRecord] = []
        self._t0 = time.time()

    def add(self, rec: CallRecord):
        self.records.append(rec)

    def summary(self) -> dict[str, Any]:
        by_agent: dict[str, dict[str, float]] = defaultdict(lambda: {
            "prompt_tokens": 0, "completion_tokens": 0,
            "llm_calls": 0, "http_calls": 0, "pdf_calls": 0,
            "latency_ms": 0.0,
        })
        for r in self.records:
            b = by_agent[r.agent]
            b["prompt_tokens"] += r.prompt_tokens
            b["completion_tokens"] += r.completion_tokens
            b["latency_ms"] += r.latency_ms
            if r.kind == "llm": b["llm_calls"] += 1
            elif r.kind == "http": b["http_calls"] += 1
            elif r.kind == "pdf": b["pdf_calls"] += 1

        total = {
            "prompt_tokens":     sum(r.prompt_tokens for r in self.records),
            "completion_tokens": sum(r.completion_tokens for r in self.records),
            "total_tokens":      sum(r.prompt_tokens + r.completion_tokens for r in self.records),
            "llm_calls":         sum(1 for r in self.records if r.kind == "llm"),
            "http_calls":        sum(1 for r in self.records if r.kind == "http"),
            "pdf_calls":         sum(1 for r in self.records if r.kind == "pdf"),
            "wall_ms":           (time.time() - self._t0) * 1000,
        }
        return {"by_agent": dict(by_agent), "total": total,
                "records": [asdict(r) for r in self.records]}
