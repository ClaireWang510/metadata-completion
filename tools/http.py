"""Common HTTP wrapper with retries & cost logging."""
from __future__ import annotations
import time
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

import config
from cost import CallRecord, CostRecorder


@retry(stop=stop_after_attempt(config.HTTP_RETRIES),
       wait=wait_exponential(multiplier=1, min=1, max=5),
       reraise=True)
def _get(url: str, params: dict[str, Any] | None = None) -> httpx.Response:
    with httpx.Client(timeout=config.HTTP_TIMEOUT,
                      headers={"User-Agent": "meta-completion/0.1"}) as cli:
        return cli.get(url, params=params)


def get_json(url: str, params: dict[str, Any] | None,
             *, agent: str, cost: CostRecorder) -> dict[str, Any] | None:
    t0 = time.time()
    ok = True
    data: dict[str, Any] | None = None
    extra: dict[str, Any] = {"params": params or {}}
    try:
        r = _get(url, params=params)
        extra.update({"status_code": r.status_code, "final_url": str(r.url)})
        r.raise_for_status()
        data = r.json()
    except Exception as exc:                       # noqa: BLE001
        ok = False
        extra["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        cost.add(CallRecord(
            agent=agent, kind="http", model_or_url=url,
            latency_ms=(time.time() - t0) * 1000, ok=ok,
            extra=extra,
        ))
    return data


def get_text(url: str, params: dict[str, Any] | None,
             *, agent: str, cost: CostRecorder) -> str | None:
    """GET text with the same observable error reporting as ``get_json``."""
    t0 = time.time(); ok = True; result = None
    extra: dict[str, Any] = {"params": params or {}}
    try:
        r = _get(url, params=params)
        extra.update({"status_code": r.status_code, "final_url": str(r.url)})
        r.raise_for_status()
        result = r.text
    except Exception as exc:  # noqa: BLE001
        ok = False
        extra["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        cost.add(CallRecord(agent=agent, kind="http", model_or_url=url,
                            latency_ms=(time.time() - t0) * 1000,
                            ok=ok, extra=extra))
    return result
