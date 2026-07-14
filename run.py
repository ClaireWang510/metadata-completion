"""Single-paper entrypoint.

Usage:
  python run.py --input data/debug/2401.06806 --output_dir out
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from agents.planner import run as planner_run
from output_utils import write_run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True,
                    help="sample directory containing metadata.json, or a metadata JSON path")
    ap.add_argument("--output_dir", default="out",
                    help="output root; files are written under <output_dir>/<arxiv_id>/")
    args = ap.parse_args()

    input_path = Path(args.input)
    metadata_path = input_path / "metadata.json" if input_path.is_dir() else input_path
    with metadata_path.open("r", encoding="utf-8") as f:
        paper = json.load(f)
    paper["_sample_dir"] = str(metadata_path.parent)

    completed, bb, cost, trace = planner_run(paper)

    arxiv_id = str(paper.get("arxiv_id") or input_path.name)
    paths = write_run(args.output_dir, arxiv_id, completed, trace, cost)

    tot = cost.summary()["total"]
    print(f"[done] {metadata_path} -> {paths['run_dir']}"
          f"  tokens={tot['total_tokens']}  iters={completed.iterations}"
          f"  reason={completed.stopped_reason}")


if __name__ == "__main__":
    main()
