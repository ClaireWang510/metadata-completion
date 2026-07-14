"""Batch entrypoint for data/<split>/<arxiv_id>/metadata.json datasets.

Usage:
  python batch.py --input_dir data/debug --output_dir out [--limit 100] [--workers 4]
"""
from __future__ import annotations
import argparse
import concurrent.futures as cf
import json
import os
import traceback
from pathlib import Path

from agents.planner import run as planner_run
from output_utils import write_run


def discover_samples(input_dir: str) -> list[tuple[str, str]]:
    """Return (arxiv_id, metadata_path) from sample directories.

    The legacy flat `<id>.json` layout remains supported for compatibility.
    """
    root = os.path.abspath(input_dir)
    samples: list[tuple[str, str]] = []
    if not os.path.isdir(root):
        return samples
    for name in sorted(os.listdir(root)):
        child = os.path.join(root, name)
        metadata = os.path.join(child, "metadata.json")
        if os.path.isdir(child) and os.path.isfile(metadata):
            samples.append((name, metadata))
    if samples:
        return samples
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.isfile(path) and name.lower().endswith(".json"):
            samples.append((os.path.splitext(name)[0], path))
    return samples


def _process_one(arxiv_id: str, inp: str, out_dir: str) -> dict:
    try:
        with open(inp, "r", encoding="utf-8") as f:
            paper = json.load(f)
        paper["_sample_dir"] = str(Path(inp).resolve().parent)
        completed, bb, cost, trace = planner_run(paper)
        # Prefer the ID declared by metadata, but keep the directory name as fallback.
        paper_id = str(paper.get("arxiv_id") or arxiv_id)
        paths = write_run(out_dir, paper_id, completed, trace, cost)
        tot = cost.summary()["total"]
        return {"id": paper_id, "ok": True, "run_dir": paths["run_dir"],
                "tokens": tot["total_tokens"],
                "iters": completed.iterations,
                "reason": completed.stopped_reason}
    except Exception as e:  # noqa: BLE001
        return {"id": arxiv_id, "ok": False, "err": f"{e}\n{traceback.format_exc()}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir",  required=True)
    ap.add_argument("--output_dir", default="out")
    ap.add_argument("--limit",  type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    samples = discover_samples(args.input_dir)
    if args.limit:
        samples = samples[:args.limit]

    results = []
    if args.workers <= 1:
        for arxiv_id, metadata in samples:
            r = _process_one(arxiv_id, metadata, args.output_dir)
            print(r); results.append(r)
    else:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(_process_one, arxiv_id, metadata, args.output_dir)
                    for arxiv_id, metadata in samples]
            for fut in cf.as_completed(futs):
                r = fut.result(); print(r); results.append(r)

    with open(os.path.join(args.output_dir, "_batch_summary.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    ok = sum(1 for r in results if r["ok"])
    tot_tokens = sum(r.get("tokens", 0) for r in results if r["ok"])
    print(f"[batch done] ok={ok}/{len(results)}  total_tokens={tot_tokens}"
          f"  avg_tokens={tot_tokens/max(ok,1):.1f}")


if __name__ == "__main__":
    main()
