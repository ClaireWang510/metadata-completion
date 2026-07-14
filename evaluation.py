"""Gold evaluation for venue, DOI, affiliation and selective-risk metrics."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any

from tools.matching import normalize_text
from tools.venue import canonical_name


def _norm_doi(value: Any) -> str:
    return str(value or "").casefold().removeprefix("https://doi.org/").strip()


def _aff_names(items: list[Any]) -> set[str]:
    return {normalize_text(item.get("name", "") if isinstance(item, dict) else str(item))
            for item in items if item}


def evaluate_pair(completed: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    predicted_venue = completed.get("venue_completed") or {}
    gold_venue = gold.get("venue_gold") or {}
    predicted_name = canonical_name(predicted_venue.get("name", ""),
                                    predicted_venue.get("type", "unknown"),
                                    predicted_venue.get("year"))
    gold_name = canonical_name(gold_venue.get("name", ""),
                               gold_venue.get("type", "unknown"),
                               gold_venue.get("year"))
    venue_name_ok = normalize_text(predicted_name) == normalize_text(gold_name)
    doi_ok = _norm_doi(predicted_venue.get("doi")) == _norm_doi(gold_venue.get("doi"))
    status_ok = predicted_venue.get("publication_status") == gold_venue.get("status")

    predicted_authors = completed.get("affiliations_completed") or []
    gold_authors = gold.get("authors_gold") or []
    tp = fp = fn = 0
    for index, expected in enumerate(gold_authors):
        actual = predicted_authors[index] if index < len(predicted_authors) else {}
        pred, truth = _aff_names(actual.get("affiliations") or []), _aff_names(expected.get("affiliations") or [])
        tp += len(pred & truth); fp += len(pred - truth); fn += len(truth - pred)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accepted = predicted_venue.get("decision") == "accepted"
    correct = venue_name_ok and doi_ok and status_ok
    confidence = float(predicted_venue.get("confidence") or 0.0)
    return {"arxiv_id": gold.get("arxiv_id"), "venue_name_correct": venue_name_ok,
            "doi_correct": doi_ok, "status_correct": status_ok,
            "venue_correct": correct, "venue_accepted": accepted,
            "venue_confidence": confidence, "affiliation_precision": precision,
            "affiliation_recall": recall, "affiliation_f1": f1}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    accepted = [r for r in rows if r["venue_accepted"]]
    brier = sum((r["venue_confidence"] - float(r["venue_correct"])) ** 2 for r in rows) / max(n, 1)
    ece = 0.0
    for lower in (i / 10 for i in range(10)):
        bucket = [r for r in rows if lower <= r["venue_confidence"] < lower + 0.1
                  or (lower == 0.9 and r["venue_confidence"] == 1.0)]
        if bucket:
            accuracy = sum(r["venue_correct"] for r in bucket) / len(bucket)
            confidence = sum(r["venue_confidence"] for r in bucket) / len(bucket)
            ece += len(bucket) / max(n, 1) * abs(accuracy - confidence)
    ranked = sorted(rows, key=lambda r: r["venue_confidence"], reverse=True)
    curve = [{"coverage": k / max(n, 1),
              "risk": sum(not r["venue_correct"] for r in ranked[:k]) / k}
             for k in range(1, len(ranked) + 1)]
    return {"papers": n, "venue_accuracy": sum(r["venue_correct"] for r in rows) / max(n, 1),
            "venue_coverage": len(accepted) / max(n, 1),
            "selective_risk": (sum(not r["venue_correct"] for r in accepted) / len(accepted)) if accepted else None,
            "venue_brier": brier, "venue_ece_10bin": ece,
            "risk_coverage_curve": curve,
            "affiliation_macro_f1": sum(r["affiliation_f1"] for r in rows) / max(n, 1)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold_dir", default="gold")
    parser.add_argument("--output_dir", default="out")
    args = parser.parse_args()
    rows = []
    for gold_path in sorted(Path(args.gold_dir).glob("*.json")):
        completed_path = Path(args.output_dir) / gold_path.stem / "latest.completed.json"
        if completed_path.exists():
            rows.append(evaluate_pair(json.loads(completed_path.read_text(encoding="utf-8")),
                                      json.loads(gold_path.read_text(encoding="utf-8"))))
    print(json.dumps({"summary": aggregate(rows), "papers": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
