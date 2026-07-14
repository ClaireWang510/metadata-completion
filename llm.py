"""Thin LLM wrapper that (a) supports JSON mode and (b) records tokens."""
from __future__ import annotations
import json
import os
import re
import time
from typing import Any

from openai import OpenAI

import config  # loads the project-level .env before the client is created
from cost import CallRecord, CostRecorder


_client: OpenAI | None = None


def _parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object from strict JSON or common Markdown/reasoning wrappers."""
    text = raw.strip()
    candidates = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*([\s\S]*?)```", text,
                                 flags=re.IGNORECASE))

    decoder = json.JSONDecoder()
    for candidate in candidates:
        candidate = candidate.strip()
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

        # Some OpenAI-compatible models emit reasoning before the JSON object.
        for match in re.finditer(r"\{", candidate):
            try:
                value, _ = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    raise json.JSONDecodeError("No JSON object found", text, 0)


def _cli() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("BASE_URL") or None,
            )
    return _client


def chat_json(
    *,
    model: str,
    system: str,
    user: str,
    agent: str,
    cost: CostRecorder,
    images: list[str] | None = None,     # optional data-url or http(s) urls
    temperature: float = 0.1,
    max_tokens: int = 1500,
) -> dict[str, Any]:
    """Call the model, expect a JSON object back. Records tokens into `cost`."""

    content: list[dict[str, Any]] | str
    if images:
        parts: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for url in images:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        content = parts
    else:
        content = user

    t0 = time.time()
    ok = False
    extra: dict[str, Any] = {}
    prompt_toks = completion_toks = 0
    try:
        resp = _cli().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        usage = resp.usage
        prompt_toks = getattr(usage, "prompt_tokens", 0)
        completion_toks = getattr(usage, "completion_tokens", 0)
        result = _parse_json_object(raw)
        ok = True
        return result
    except json.JSONDecodeError:
        extra = {
            "error": "invalid_json",
            "raw_response": raw[:4000],
        }
        return {"_error": "invalid_json", "_raw": raw}
    except Exception as e:                                # noqa: BLE001
        extra = {"error": str(e)}
        return {"_error": str(e)}
    finally:
        cost.add(CallRecord(
            agent=agent, kind="llm", model_or_url=model,
            prompt_tokens=prompt_toks, completion_tokens=completion_toks,
            latency_ms=(time.time() - t0) * 1000, ok=ok, extra=extra,
        ))
