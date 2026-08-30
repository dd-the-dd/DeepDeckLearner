from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

GROUND_TRUTH_SCHEMA_VERSION = 1
GROUND_TRUTH_EVALUATION_SCHEMA_VERSION = "oracle-ai-ground-truth-evaluation/v1"
AI_DECISION_SCHEMA_VERSION = "ai-decision/v1"


def default_ground_truth_path() -> Path:
    learner_root = Path(__file__).resolve().parents[2]
    return learner_root / ".deepdeck" / "ground-truth-scenarios.json"


def _deterministic_root_record(
    scenario: Mapping[str, Any],
    *,
    decision_type: str,
) -> dict[str, Any] | None:
    identifier = str(scenario.get("id", "")).strip()
    session = scenario.get("initialSession")
    proof = scenario.get("proof")
    if not identifier or not isinstance(session, Mapping) or not isinstance(proof, Mapping):
        return None
    state = session.get("state")
    decision = session.get("decision")
    if not isinstance(state, Mapping) or not isinstance(decision, Mapping):
        return None
    options = decision.get("options")
    if not isinstance(options, list) or not options:
        return None

    selected_action_id = ""
    winning_line = proof.get("winningLine")
    if isinstance(winning_line, list) and winning_line:
        first_choice = winning_line[0]
        if isinstance(first_choice, Mapping) and first_choice.get("choiceKind") == "action":
            selected_action_id = str(first_choice.get("choice", ""))
    if not selected_action_id:
        action_trace = proof.get("actionTrace")
        if isinstance(action_trace, list) and action_trace:
            first_action = action_trace[0]
            if isinstance(first_action, Mapping):
                selected_action_id = str(first_action.get("actionId", ""))
    selected_index = next(
        (
            index
            for index, option in enumerate(options)
            if isinstance(option, Mapping)
            and str(option.get("id", "")) == selected_action_id
        ),
        None,
    )
    if selected_index is None:
        configured_index = proof.get("firstActionIndex")
        if (
            isinstance(configured_index, int)
            and not isinstance(configured_index, bool)
            and 0 <= configured_index < len(options)
        ):
            selected_index = configured_index
    if selected_index is None:
        return None

    selected_action = options[selected_index]
    final_signature = f"deterministic-win:{identifier}"
    return {
        "schemaVersion": GROUND_TRUTH_SCHEMA_VERSION,
        "id": f"deterministic:{identifier}:root",
        "confidence": 10,
        "initialSession": dict(session),
        "decision": dict(decision),
        "legalActions": list(options),
        "humanChoice": {
            "actionId": selected_action.get("id"),
            "actionIndex": selected_index,
            "actionKind": selected_action.get("kind"),
            "actionLabel": selected_action.get("label"),
        },
        "scenario": {
            "scenarioSequenceId": identifier,
            "lineId": identifier,
            "lineIndex": 0,
            "linePoints": 100,
            "lineFinalStateSignature": final_signature,
            "resolvedDecisionType": decision_type,
            "sourceSchemaVersion": scenario.get("schemaVersion"),
        },
        "terminalBoardSignature": final_signature,
    }


def _load_deterministic_root_records(
    paths: Sequence[str | Path],
    *,
    scenario_ids: Sequence[str] | None,
    decision_type: str,
) -> list[dict[str, Any]]:
    selected_ids = {str(identifier) for identifier in scenario_ids or []}
    records: list[dict[str, Any]] = []
    for raw_path in paths:
        dataset_path = Path(raw_path)
        if not dataset_path.exists():
            raise FileNotFoundError(
                f"Deterministic ground truth dataset does not exist: {dataset_path}"
            )
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        scenarios = payload.get("scenarios", []) if isinstance(payload, dict) else []
        if not isinstance(scenarios, list):
            raise ValueError(
                f"Deterministic ground truth dataset has no scenarios list: {dataset_path}"
            )
        for scenario in scenarios:
            if not isinstance(scenario, Mapping):
                continue
            identifier = str(scenario.get("id", ""))
            if selected_ids and identifier not in selected_ids:
                continue
            record = _deterministic_root_record(
                scenario,
                decision_type=decision_type,
            )
            if record is not None:
                records.append(record)
    if selected_ids:
        found_ids = {
            str(record.get("scenario", {}).get("scenarioSequenceId", ""))
            for record in records
        }
        missing_ids = sorted(selected_ids - found_ids)
        if missing_ids:
            raise ValueError(
                "Deterministic ground truth scenarios have no replayable root decision: "
                + ", ".join(missing_ids)
            )
    return records


def load_ground_truth_scenarios(
    path: str | Path | None = None,
    *,
    minimum_confidence: int = 1,
    deterministic_paths: Sequence[str | Path] | None = None,
    deterministic_scenario_ids: Sequence[str] | None = None,
    deterministic_decision_type: str = "fastDeterministicWin",
) -> list[dict[str, Any]]:
    dataset_path = Path(path) if path is not None else default_ground_truth_path()
    if not dataset_path.exists():
        return []

    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios", []) if isinstance(payload, dict) else payload
    if not isinstance(scenarios, list):
        raise ValueError("Ground truth dataset must contain a scenarios list.")

    minimum = max(1, min(int(minimum_confidence), 10))
    records = [
        scenario
        for scenario in scenarios
        if isinstance(scenario, dict)
        and scenario.get("schemaVersion") == GROUND_TRUTH_SCHEMA_VERSION
        and minimum <= int(scenario.get("confidence", 0) or 0) <= 10
    ]
    if deterministic_paths:
        records.extend(
            _load_deterministic_root_records(
                deterministic_paths,
                scenario_ids=deterministic_scenario_ids,
                decision_type=deterministic_decision_type,
            )
        )
    return records


def _scenario_identity(record: Mapping[str, Any], index: int) -> str:
    metadata = record.get("scenario", {})
    sequence_id = (
        metadata.get("scenarioSequenceId")
        if isinstance(metadata, Mapping)
        else None
    )
    return str(sequence_id or record.get("id") or f"record:{index}")


def ground_truth_scenario_count(records: Sequence[Mapping[str, Any]]) -> int:
    return len({
        _scenario_identity(record, index)
        for index, record in enumerate(records)
    })


def ground_truth_decision_request(
    scenario: Mapping[str, Any],
    *,
    controller_id: str | None = None,
) -> dict[str, Any]:
    session = scenario.get("initialSession")
    if not isinstance(session, Mapping):
        raise ValueError("Ground truth scenario is missing initialSession.")
    state = session.get("state")
    decision = session.get("decision")
    if not isinstance(state, Mapping) or not isinstance(decision, Mapping):
        raise ValueError("Ground truth scenario must contain state and decision snapshots.")

    decision_id = str(decision.get("id", ""))
    player_id = str(decision.get("playerId", ""))
    options = decision.get("options")
    if not decision_id or not player_id or not isinstance(options, list) or not options:
        raise ValueError("Ground truth decision snapshot is incomplete.")

    payload: dict[str, Any] = {
        "schemaVersion": AI_DECISION_SCHEMA_VERSION,
        "requestId": decision_id,
        "playerId": player_id,
        "state": dict(state),
        "decision": dict(decision),
        "deterministic": True,
    }
    if controller_id:
        payload["controllerId"] = controller_id
    return payload


def _prediction_index(prediction: Any) -> int | None:
    if isinstance(prediction, bool):
        return None
    if isinstance(prediction, int):
        return prediction
    if isinstance(prediction, Mapping):
        value = prediction.get("actionIndex", prediction.get("action_index"))
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _prediction_probabilities(prediction: Any) -> list[float] | None:
    if not isinstance(prediction, Mapping):
        return None
    values = prediction.get("actionProbabilities", prediction.get("action_probabilities"))
    if not isinstance(values, list):
        return None
    try:
        return [float(value) for value in values]
    except (TypeError, ValueError):
        return None


def _line_identity(record: Mapping[str, Any], index: int) -> str:
    metadata = record.get("scenario", {})
    if isinstance(metadata, Mapping):
        return str(
            metadata.get("lineId")
            or metadata.get("scenarioSequenceId")
            or record.get("id")
            or f"record:{index}"
        )
    return str(record.get("id") or f"record:{index}")


def _line_points(record: Mapping[str, Any]) -> float:
    metadata = record.get("scenario", {})
    value = metadata.get("linePoints", 100) if isinstance(metadata, Mapping) else 100
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _line_final_state_signature(record: Mapping[str, Any]) -> str:
    metadata = record.get("scenario", {})
    if isinstance(metadata, Mapping):
        value = metadata.get("lineFinalStateSignature")
        if value:
            return str(value)
    return str(record.get("terminalBoardSignature") or "")


def _decision_type(record: Mapping[str, Any]) -> str:
    metadata = record.get("scenario", {})
    return (
        str(metadata.get("resolvedDecisionType", "unknown"))
        if isinstance(metadata, Mapping)
        else "unknown"
    )


def _prediction_matches_human_choice(
    record: Mapping[str, Any],
    prediction: Any,
) -> bool:
    predicted_index = _prediction_index(prediction)
    human_choice = record.get("humanChoice", {})
    human_index = human_choice.get("actionIndex") if isinstance(human_choice, Mapping) else None
    return predicted_index is not None and isinstance(human_index, int) and predicted_index == human_index


def _sequence_point_metrics(
    scenarios: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_lines: dict[str, dict[str, list[tuple[int, Mapping[str, Any]]]]] = {}
    scenario_types: dict[str, set[str]] = {}
    for index, record in enumerate(scenarios):
        scenario_id = _scenario_identity(record, index)
        line_id = _line_identity(record, index)
        scenario_lines.setdefault(scenario_id, {}).setdefault(line_id, []).append((index, record))
        scenario_types.setdefault(scenario_id, set()).add(_decision_type(record))

    evaluated_scenarios = 0
    point_score_total = 0.0
    perfect_score_count = 0
    by_decision_type: dict[str, dict[str, float | int]] = {}

    for scenario_id, lines in scenario_lines.items():
        line_summaries = []
        signature_points: dict[str, float] = {}
        for entries in lines.values():
            ordered = sorted(entries, key=lambda item: item[0])
            records = [record for _, record in ordered]
            points = max(_line_points(record) for record in records)
            signature = next(
                (
                    value
                    for value in (_line_final_state_signature(record) for record in reversed(records))
                    if value
                ),
                "",
            )
            if signature:
                signature_points[signature] = max(signature_points.get(signature, 0.0), points)
            line_summaries.append({
                "records": records,
                "points": points,
                "signature": signature,
            })

        maximum_points = max(
            [summary["points"] for summary in line_summaries] + list(signature_points.values()) + [0.0]
        )
        if maximum_points <= 0:
            continue

        matched_points = 0.0
        has_complete_prediction = False
        for summary in line_summaries:
            records = summary["records"]
            predictions_for_line = [
                predictions.get(str(record.get("id", "")))
                for record in records
            ]
            if any(prediction is None for prediction in predictions_for_line):
                continue
            has_complete_prediction = True
            if all(
                _prediction_matches_human_choice(record, prediction)
                for record, prediction in zip(records, predictions_for_line)
            ):
                signature = str(summary["signature"])
                matched_points = max(
                    matched_points,
                    signature_points.get(signature, float(summary["points"])),
                )

        if not has_complete_prediction:
            continue

        evaluated_scenarios += 1
        score = matched_points / maximum_points
        point_score_total += score
        perfect_score_count += int(matched_points == maximum_points)
        for decision_type in scenario_types.get(scenario_id, {"unknown"}):
            bucket = by_decision_type.setdefault(
                decision_type,
                {
                    "sequenceEvaluatedScenarios": 0,
                    "sequencePointScoreTotal": 0.0,
                    "sequencePerfectScoreCount": 0,
                },
            )
            bucket["sequenceEvaluatedScenarios"] = int(bucket["sequenceEvaluatedScenarios"]) + 1
            bucket["sequencePointScoreTotal"] = float(bucket["sequencePointScoreTotal"]) + score
            bucket["sequencePerfectScoreCount"] = int(bucket["sequencePerfectScoreCount"]) + int(
                matched_points == maximum_points
            )

    decision_summary = {}
    for decision_type, bucket in by_decision_type.items():
        count = int(bucket["sequenceEvaluatedScenarios"])
        decision_summary[decision_type] = {
            "sequenceEvaluatedScenarios": count,
            "sequencePointScore": (
                float(bucket["sequencePointScoreTotal"]) / count if count else 0.0
            ),
            "sequencePerfectScoreRate": (
                float(bucket["sequencePerfectScoreCount"]) / count if count else 0.0
            ),
        }

    return {
        "sequenceEvaluatedScenarios": evaluated_scenarios,
        "sequencePointScore": point_score_total / evaluated_scenarios if evaluated_scenarios else 0.0,
        "sequencePerfectScoreRate": (
            perfect_score_count / evaluated_scenarios if evaluated_scenarios else 0.0
        ),
        "byDecisionType": decision_summary,
    }


def ground_truth_agreement(
    scenarios: Sequence[Mapping[str, Any]],
    predictions: Mapping[str, Any],
) -> dict[str, Any]:
    matched = 0
    evaluated = 0
    weighted_matched = 0.0
    total_weight = 0.0
    probability_count = 0
    human_probability_total = 0.0
    weighted_human_probability = 0.0
    weighted_probability_weight = 0.0
    by_decision_type: dict[str, dict[str, float | int]] = {}
    scenario_buckets: dict[str, dict[str, float | int]] = {}

    for index, scenario in enumerate(scenarios):
        identity = _scenario_identity(scenario, index)
        confidence = int(scenario.get("confidence", 0) or 0)
        bucket = scenario_buckets.setdefault(
            identity,
            {
                "total": 0,
                "evaluated": 0,
                "matched": 0,
                "confidence": max(1, min(confidence, 10)),
            },
        )
        bucket["total"] = int(bucket["total"]) + 1

    for index, scenario in enumerate(scenarios):
        scenario_id = str(scenario.get("id", ""))
        prediction = predictions.get(scenario_id)
        predicted_index = _prediction_index(prediction)
        human_choice = scenario.get("humanChoice", {})
        human_index = human_choice.get("actionIndex") if isinstance(human_choice, Mapping) else None
        if predicted_index is None or not isinstance(human_index, int):
            continue

        confidence = int(scenario.get("confidence", 0) or 0)
        weight = max(1, min(confidence, 10)) / 10.0
        is_match = predicted_index == human_index
        evaluated += 1
        matched += int(is_match)
        total_weight += weight
        weighted_matched += weight if is_match else 0.0

        sequence_bucket = scenario_buckets[_scenario_identity(scenario, index)]
        sequence_bucket["evaluated"] = int(sequence_bucket["evaluated"]) + 1
        sequence_bucket["matched"] = int(sequence_bucket["matched"]) + int(is_match)

        probabilities = _prediction_probabilities(prediction)
        human_probability = None
        if probabilities is not None and 0 <= human_index < len(probabilities):
            human_probability = probabilities[human_index]
            probability_count += 1
            human_probability_total += human_probability
            weighted_human_probability += human_probability * weight
            weighted_probability_weight += weight

        decision_type = _decision_type(scenario)
        bucket = by_decision_type.setdefault(
            decision_type,
            {
                "evaluated": 0,
                "matched": 0,
                "probabilityCount": 0,
                "humanProbability": 0.0,
                "weight": 0.0,
                "weightedMatched": 0.0,
                "weightedHumanProbability": 0.0,
                "weightedProbabilityWeight": 0.0,
            },
        )
        bucket["evaluated"] = int(bucket["evaluated"]) + 1
        bucket["matched"] = int(bucket["matched"]) + int(is_match)
        bucket["weight"] = float(bucket["weight"]) + weight
        bucket["weightedMatched"] = float(bucket["weightedMatched"]) + (
            weight if is_match else 0.0
        )
        if human_probability is not None:
            bucket["probabilityCount"] = int(bucket["probabilityCount"]) + 1
            bucket["humanProbability"] = float(bucket["humanProbability"]) + human_probability
            bucket["weightedHumanProbability"] = (
                float(bucket["weightedHumanProbability"]) + human_probability * weight
            )
            bucket["weightedProbabilityWeight"] = (
                float(bucket["weightedProbabilityWeight"]) + weight
            )

    decision_summary: dict[str, dict[str, float | int]] = {}
    for decision_type, bucket in by_decision_type.items():
        bucket_evaluated = int(bucket["evaluated"])
        bucket_weight = float(bucket["weight"])
        bucket_probability_count = int(bucket["probabilityCount"])
        bucket_probability_weight = float(bucket["weightedProbabilityWeight"])
        decision_summary[decision_type] = {
            "evaluated": bucket_evaluated,
            "exactAgreement": (
                float(bucket["matched"]) / bucket_evaluated if bucket_evaluated else 0.0
            ),
            "confidenceWeightedAgreement": (
                float(bucket["weightedMatched"]) / bucket_weight if bucket_weight else 0.0
            ),
            "meanHumanActionProbability": (
                float(bucket["humanProbability"]) / bucket_probability_count
                if bucket_probability_count
                else 0.0
            ),
            "confidenceWeightedHumanActionProbability": (
                float(bucket["weightedHumanProbability"]) / bucket_probability_weight
                if bucket_probability_weight
                else 0.0
            ),
        }

    completed_scenarios = [
        bucket
        for bucket in scenario_buckets.values()
        if int(bucket["evaluated"]) == int(bucket["total"])
    ]
    scenario_exact_total = 0
    scenario_decision_agreement_total = 0.0
    scenario_weight_total = 0.0
    weighted_scenario_exact_total = 0.0
    weighted_scenario_decision_agreement_total = 0.0
    for bucket in completed_scenarios:
        total = int(bucket["total"])
        bucket_matched = int(bucket["matched"])
        confidence_weight = float(bucket["confidence"]) / 10.0
        exact = bucket_matched == total
        decision_agreement = bucket_matched / total if total else 0.0
        scenario_exact_total += int(exact)
        scenario_decision_agreement_total += decision_agreement
        scenario_weight_total += confidence_weight
        weighted_scenario_exact_total += confidence_weight if exact else 0.0
        weighted_scenario_decision_agreement_total += decision_agreement * confidence_weight

    completed_count = len(completed_scenarios)
    total_scenario_count = len(scenario_buckets)

    sequence_metrics = _sequence_point_metrics(scenarios, predictions)
    for decision_type, sequence_bucket in sequence_metrics["byDecisionType"].items():
        decision_summary.setdefault(decision_type, {
            "evaluated": 0,
            "exactAgreement": 0.0,
            "confidenceWeightedAgreement": 0.0,
            "meanHumanActionProbability": 0.0,
            "confidenceWeightedHumanActionProbability": 0.0,
        }).update(sequence_bucket)

    return {
        "evaluated": evaluated,
        "exactAgreement": matched / evaluated if evaluated else 0.0,
        "confidenceWeightedAgreement": (
            weighted_matched / total_weight if total_weight else 0.0
        ),
        "meanHumanActionProbability": (
            human_probability_total / probability_count if probability_count else 0.0
        ),
        "confidenceWeightedHumanActionProbability": (
            weighted_human_probability / weighted_probability_weight
            if weighted_probability_weight
            else 0.0
        ),
        "evaluatedScenarios": completed_count,
        "scenarioCoverage": (
            completed_count / total_scenario_count if total_scenario_count else 0.0
        ),
        "scenarioExactAgreement": (
            scenario_exact_total / completed_count if completed_count else 0.0
        ),
        "meanScenarioDecisionAgreement": (
            scenario_decision_agreement_total / completed_count if completed_count else 0.0
        ),
        "confidenceWeightedScenarioExactAgreement": (
            weighted_scenario_exact_total / scenario_weight_total
            if scenario_weight_total
            else 0.0
        ),
        "confidenceWeightedMeanScenarioDecisionAgreement": (
            weighted_scenario_decision_agreement_total / scenario_weight_total
            if scenario_weight_total
            else 0.0
        ),
        "sequenceEvaluatedScenarios": sequence_metrics["sequenceEvaluatedScenarios"],
        "sequencePointScore": sequence_metrics["sequencePointScore"],
        "sequencePerfectScoreRate": sequence_metrics["sequencePerfectScoreRate"],
        "byDecisionType": decision_summary,
    }


def evaluate_ground_truth_service(
    scenarios: Sequence[Mapping[str, Any]],
    *,
    service_url: str = "http://127.0.0.1:8791",
    controller_id: str | None = None,
    timeout_seconds: float = 30.0,
    client: Any | None = None,
) -> dict[str, Any]:
    base_url = service_url.rstrip("/")
    owns_client = client is None
    http_client = client or httpx.Client(timeout=timeout_seconds)
    predictions: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    model_names: list[str] = []
    model_catalog: list[Mapping[str, Any]] = []

    try:
        try:
            models_response = http_client.get(f"{base_url}/v1/models")
            models_response.raise_for_status()
            models_payload = models_response.json()
            if isinstance(models_payload, Mapping):
                raw_models = models_payload.get("models", [])
                if isinstance(raw_models, list):
                    model_catalog = [
                        model for model in raw_models if isinstance(model, Mapping)
                    ]
        except Exception:
            model_catalog = []

        for scenario in scenarios:
            scenario_id = str(scenario.get("id", ""))
            if not scenario_id:
                errors.append({"scenarioId": "", "error": "Scenario has no id."})
                continue
            try:
                payload = ground_truth_decision_request(
                    scenario,
                    controller_id=controller_id,
                )
                response = http_client.post(f"{base_url}/v1/decisions", json=payload)
                response.raise_for_status()
                result = response.json()
                action_id = str(result.get("actionId", ""))
                legal_actions = scenario.get("legalActions", [])
                if not isinstance(legal_actions, list):
                    raise ValueError("Scenario legalActions must be a list.")
                action_index = next(
                    (
                        index
                        for index, action in enumerate(legal_actions)
                        if isinstance(action, Mapping) and str(action.get("id", "")) == action_id
                    ),
                    None,
                )
                if action_index is None:
                    raise ValueError(
                        f"Inference returned action {action_id!r} outside the captured legal set."
                    )
                model_name = str(result.get("model", controller_id or ""))
                if model_name and model_name not in model_names:
                    model_names.append(model_name)
                predictions[scenario_id] = {
                    "actionIndex": action_index,
                    "actionId": action_id,
                    "actionProbabilities": result.get("actionProbabilities"),
                    "confidence": result.get("confidence"),
                    "policyEntropy": result.get("policyEntropy"),
                    "model": model_name,
                }
            except Exception as error:
                errors.append({"scenarioId": scenario_id, "error": str(error)})
    finally:
        if owns_client:
            http_client.close()

    model_name = model_names[0] if len(model_names) == 1 else ",".join(model_names)
    descriptor = next(
        (
            model
            for model in model_catalog
            if str(model.get("id", "")) == (controller_id or model_name)
        ),
        None,
    )
    training_step = int(descriptor.get("trainingStep", 0)) if descriptor else None
    metrics = ground_truth_agreement(scenarios, predictions)
    decision_count = len(scenarios)

    return {
        "schemaVersion": GROUND_TRUTH_EVALUATION_SCHEMA_VERSION,
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
        "serviceUrl": base_url,
        "controllerId": controller_id,
        "model": model_name or controller_id,
        "trainingStep": training_step,
        "scenarioCount": ground_truth_scenario_count(scenarios),
        "decisionCount": decision_count,
        "coverage": metrics["evaluated"] / decision_count if decision_count else 0.0,
        "metrics": metrics,
        "errors": errors,
        "predictions": predictions,
    }
