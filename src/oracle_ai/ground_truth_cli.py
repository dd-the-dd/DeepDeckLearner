from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from oracle_ai.ground_truth import (
    evaluate_ground_truth_service,
    load_ground_truth_scenarios,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate an Oracle AI inference service against human ground truths.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Ground truth JSON file. Defaults to .local-app/ground-truth-scenarios.json.",
    )
    parser.add_argument(
        "--service-url",
        default="http://127.0.0.1:8791",
        help="Oracle AI inference service URL. ia-in-training defaults to port 8791.",
    )
    parser.add_argument(
        "--controller-id",
        default=None,
        help="Optional model/controller id, e.g. ia-gt-0 when using the registry on port 8790.",
    )
    parser.add_argument(
        "--minimum-confidence",
        type=int,
        default=1,
        help="Ignore human labels below this confidence (1-10).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON file to write the complete evaluation report.",
    )
    parser.add_argument(
        "--append-jsonl",
        type=Path,
        default=None,
        help="Optional JSONL history file. Appends one compact evaluation per run.",
    )
    return parser


def compact_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": report["schemaVersion"],
        "evaluatedAt": report["evaluatedAt"],
        "controllerId": report.get("controllerId"),
        "model": report.get("model"),
        "trainingStep": report.get("trainingStep"),
        "scenarioCount": report["scenarioCount"],
        "decisionCount": report["decisionCount"],
        "coverage": report["coverage"],
        "metrics": report["metrics"],
        "errorCount": len(report.get("errors", [])),
    }


def main() -> None:
    args = build_parser().parse_args()
    scenarios = load_ground_truth_scenarios(
        args.dataset,
        minimum_confidence=args.minimum_confidence,
    )
    if not scenarios:
        raise SystemExit("No ground truth scenarios matched the requested confidence threshold.")

    report = evaluate_ground_truth_service(
        scenarios,
        service_url=args.service_url,
        controller_id=args.controller_id,
    )
    compact = compact_report(report)
    print(json.dumps(compact, indent=2, sort_keys=True))

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            f"{json.dumps(report, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )

    if args.append_jsonl is not None:
        args.append_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.append_jsonl.open("a", encoding="utf-8") as handle:
            handle.write(f"{json.dumps(compact, sort_keys=True)}\n")


if __name__ == "__main__":
    main()
