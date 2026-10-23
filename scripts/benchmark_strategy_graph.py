from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deepdeck_examples.strategic_graph import (
    STRATEGY_GRAPH_IMPLEMENTATION,
    STRATEGY_GRAPH_VERSION,
    analyze_deck,
)

BENCHMARK_DECKS = (
    ("0938a478-b9e4-4e78-a6f6-9abde6905731", ("tempo",)),
    ("14f4a767-830e-4ac1-9dcf-567b7b2cf8a4", ("combo",)),
    ("1a8e8160-dc8a-4698-b59b-6bcbdb6c034d", ("aggro", "midrange")),
    ("1e827971-d6ad-4c1d-89ee-732e03100dce", ("combo",)),
    ("26724ec8-932e-4662-85d3-cc7947d8e657", ("control", "combo")),
    ("290d312b-7f4d-4648-9f61-591441dbbbb4", ("combo",)),
    ("36e40983-73fe-4c1a-9dab-9e4f45fb7734", ("aggro", "midrange")),
    ("6f9f5fb8-a7f1-4915-bdf7-a1af73d6c385", ("aggro", "tempo", "midrange")),
    ("bd1c6029-58a3-4cd4-9244-39a8114afbde", ("combo",)),
    ("f0ed6629-7fb4-440d-bf5e-deac7c2bcf3b", ("tempo",)),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the versioned strategic graph against ten downloaded decks."
    )
    parser.add_argument("--deck-root", type=Path, default=Path(".deepdeck/decks"))
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with failure when a deck is missing or its primary family is unexpected.",
    )
    return parser.parse_args()


def run(deck_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for version_id, expected in BENCHMARK_DECKS:
        path = deck_root / f"{version_id}.json"
        if not path.is_file():
            records.append(
                {
                    "deckVersionId": version_id,
                    "expected": list(expected),
                    "status": "missing",
                }
            )
            continue
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        analysis = analyze_deck(snapshot.get("cards", []))
        records.append(
            {
                "deckVersionId": version_id,
                "name": snapshot.get("name", version_id),
                "expected": list(expected),
                "primary": analysis.primary_archetype,
                "match": analysis.primary_archetype in expected,
                "averageManaValue": round(analysis.average_mana_value, 3),
                "landRatio": round(analysis.land_ratio, 3),
                "archetypeMix": {
                    key: round(value, 4) for key, value in analysis.archetype_mix.items()
                },
                "roleCounts": dict(sorted(analysis.role_counts.items())),
                "status": "evaluated",
            }
        )
    evaluated = [record for record in records if record["status"] == "evaluated"]
    matched = sum(bool(record["match"]) for record in evaluated)
    return {
        "schemaVersion": "deepdeck-strategy-benchmark/v1",
        "modelVersion": STRATEGY_GRAPH_VERSION,
        "implementationRevision": STRATEGY_GRAPH_IMPLEMENTATION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "requested": len(BENCHMARK_DECKS),
            "evaluated": len(evaluated),
            "matched": matched,
            "accuracy": round(matched / len(evaluated), 4) if evaluated else 0.0,
        },
        "decks": records,
    }


def main() -> int:
    arguments = _arguments()
    report = run(arguments.deck_root)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    summary = report["summary"]
    failed = (
        summary["evaluated"] != summary["requested"] or summary["matched"] != summary["evaluated"]
    )
    return 1 if arguments.strict and failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
