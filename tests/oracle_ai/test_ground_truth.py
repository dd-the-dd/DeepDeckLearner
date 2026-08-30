import json

import pytest

from oracle_ai.ground_truth import (
    evaluate_ground_truth_service,
    ground_truth_agreement,
    ground_truth_decision_request,
    ground_truth_scenario_count,
    load_ground_truth_scenarios,
)


def scenario(
    identifier: str,
    action_index: int,
    confidence: int,
    decision_type: str = "priority",
    sequence_id: str | None = None,
):
    legal_actions = [
        {"id": f"{identifier}-pass", "kind": "passPriority"},
        {"id": f"{identifier}-cast", "kind": "cast"},
    ]
    scenario_metadata = {"resolvedDecisionType": decision_type}
    if sequence_id is not None:
        scenario_metadata["scenarioSequenceId"] = sequence_id
    return {
        "schemaVersion": 1,
        "id": identifier,
        "confidence": confidence,
        "scenario": scenario_metadata,
        "humanChoice": {"actionIndex": action_index},
        "legalActions": legal_actions,
        "initialSession": {
            "state": {"status": "inProgress", "turnNumber": 3},
            "decision": {
                "id": f"decision-{identifier}",
                "kind": "priority",
                "playerId": "player-1",
                "options": legal_actions,
            },
        },
    }


def scored_sequence_record(
    identifier: str,
    *,
    action_index: int,
    decision_type: str,
    line_id: str,
    line_points: int,
    sequence_id: str = "root-sequence",
    sequence_index: int = 0,
    final_signature: str = "board-a",
):
    record = scenario(
        identifier,
        action_index,
        10,
        decision_type,
        sequence_id,
    )
    record["scenario"] = {
        **record["scenario"],
        "lineFinalStateSignature": final_signature,
        "lineId": line_id,
        "lineIndex": 0,
        "linePoints": line_points,
        "sequenceIndex": sequence_index,
    }
    record["terminalBoardSignature"] = final_signature
    return record


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeAiClient:
    def __init__(self):
        self.requests = []

    def get(self, url):
        self.requests.append(("GET", url, None))
        return FakeResponse(
            {
                "models": [
                    {"id": "ia-in-training", "trainingStep": 4200},
                ]
            }
        )

    def post(self, url, json):
        self.requests.append(("POST", url, json))
        selected = json["decision"]["options"][1]
        return FakeResponse(
            {
                "actionId": selected["id"],
                "actionProbabilities": [0.2, 0.8],
                "confidence": 0.8,
                "model": "ia-in-training",
                "policyEntropy": 0.5,
            }
        )


def test_load_ground_truth_scenarios_filters_confidence(tmp_path):
    path = tmp_path / "ground-truth.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "scenarios": [
                    scenario("strong", 1, 9),
                    scenario("weak", 0, 3),
                    {**scenario("old", 0, 10), "schemaVersion": 0},
                ],
            }
        ),
        encoding="utf-8",
    )

    records = load_ground_truth_scenarios(path, minimum_confidence=5)

    assert [record["id"] for record in records] == ["strong"]


def test_load_ground_truth_scenarios_adds_selected_deterministic_win_roots(tmp_path):
    human_path = tmp_path / "ground-truth.json"
    human_path.write_text(
        json.dumps({"schemaVersion": 1, "scenarios": [scenario("human", 1, 10)]}),
        encoding="utf-8",
    )
    options = [
        {"id": "pass", "kind": "passPriority", "label": "Pass"},
        {"id": "win", "kind": "castSpell", "label": "Cast the finisher"},
    ]
    deterministic_path = tmp_path / "deterministic.json"
    deterministic_path.write_text(
        json.dumps(
            {
                "scenarios": [
                    {
                        "id": "omnath-fast-win",
                        "schemaVersion": "mtg-deterministic-omnath-mana-scenario/v1",
                        "initialSession": {
                            "state": {"status": "inProgress", "turnNumber": 5},
                            "decision": {
                                "id": "priority:5:0:0",
                                "kind": "priority",
                                "playerId": "omnath",
                                "options": options,
                            },
                        },
                        "proof": {
                            "actionTrace": [{"actionId": "win"}],
                            "firstActionIndex": 1,
                        },
                    },
                    {
                        "id": "not-selected",
                        "schemaVersion": "mtg-deterministic-punching-bag-scenario/v1",
                        "initialSession": {
                            "state": {"status": "inProgress", "turnNumber": 1},
                            "decision": {
                                "id": "priority:1:0:0",
                                "kind": "priority",
                                "playerId": "learner",
                                "options": options,
                            },
                        },
                        "proof": {
                            "winningLine": [{
                                "choice": "win",
                                "choiceKind": "action",
                            }],
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    records = load_ground_truth_scenarios(
        human_path,
        deterministic_paths=[deterministic_path],
        deterministic_scenario_ids=["omnath-fast-win"],
        deterministic_decision_type="fastDeterministicWin",
    )

    assert ground_truth_scenario_count(records) == 2
    deterministic = records[1]
    assert deterministic["humanChoice"]["actionIndex"] == 1
    assert deterministic["humanChoice"]["actionId"] == "win"
    assert deterministic["scenario"]["resolvedDecisionType"] == "fastDeterministicWin"
    assert deterministic["scenario"]["scenarioSequenceId"] == "omnath-fast-win"


def test_ground_truth_decision_request_replays_exact_snapshot():
    record = scenario("snapshot", 1, 10)

    payload = ground_truth_decision_request(
        record,
        controller_id="ia-gt-2",
    )

    assert payload["schemaVersion"] == "ai-decision/v1"
    assert payload["requestId"] == "decision-snapshot"
    assert payload["playerId"] == "player-1"
    assert payload["decision"]["options"] == record["legalActions"]
    assert payload["state"] == record["initialSession"]["state"]
    assert payload["controllerId"] == "ia-gt-2"
    assert payload["deterministic"] is True


def test_ground_truth_agreement_reports_exact_probability_and_confidence_scores():
    scenarios = [
        scenario("high", 1, 10, "mainAction"),
        scenario("low", 0, 2, "mainAction"),
    ]

    summary = ground_truth_agreement(
        scenarios,
        {
            "high": {"actionIndex": 1, "actionProbabilities": [0.1, 0.9]},
            "low": {"actionIndex": 1, "actionProbabilities": [0.4, 0.6]},
        },
    )

    assert summary["evaluated"] == 2
    assert summary["exactAgreement"] == pytest.approx(0.5)
    assert summary["confidenceWeightedAgreement"] == pytest.approx(10 / 12)
    assert summary["meanHumanActionProbability"] == pytest.approx(0.65)
    assert summary["confidenceWeightedHumanActionProbability"] == pytest.approx(9.8 / 12)
    assert summary["byDecisionType"]["mainAction"]["exactAgreement"] == pytest.approx(0.5)


def test_ground_truth_agreement_weights_compound_scenarios_equally():
    scenarios = [
        scenario("attack-1", 1, 10, "attackers", "attack-scenario"),
        scenario("attack-2", 1, 10, "attackers", "attack-scenario"),
        scenario("attack-3", 1, 10, "attackers", "attack-scenario"),
        scenario("priority-1", 1, 10, "priority", "priority-scenario"),
    ]
    predictions = {
        "attack-1": 1,
        "attack-2": 0,
        "attack-3": 0,
        "priority-1": 1,
    }

    summary = ground_truth_agreement(scenarios, predictions)

    assert summary["exactAgreement"] == pytest.approx(0.5)
    assert summary["evaluatedScenarios"] == 2
    assert summary["scenarioCoverage"] == 1.0
    assert summary["scenarioExactAgreement"] == pytest.approx(0.5)
    assert summary["meanScenarioDecisionAgreement"] == pytest.approx(2 / 3)
    assert summary["confidenceWeightedScenarioExactAgreement"] == pytest.approx(0.5)
    assert summary["confidenceWeightedMeanScenarioDecisionAgreement"] == pytest.approx(2 / 3)


def test_ground_truth_agreement_scores_sequence_points_against_best_line():
    scenarios = [
        scored_sequence_record(
            "best-1",
            action_index=1,
            decision_type="mainAction",
            line_id="best",
            line_points=60,
            sequence_index=0,
            final_signature="board-best",
        ),
        scored_sequence_record(
            "best-2",
            action_index=1,
            decision_type="attackers",
            line_id="best",
            line_points=60,
            sequence_index=1,
            final_signature="board-best",
        ),
        scored_sequence_record(
            "okay-1",
            action_index=0,
            decision_type="mainAction",
            line_id="okay",
            line_points=40,
            sequence_index=0,
            final_signature="board-okay",
        ),
    ]

    summary = ground_truth_agreement(
        scenarios,
        {
            "best-1": 0,
            "best-2": 0,
            "okay-1": 0,
        },
    )

    assert summary["sequenceEvaluatedScenarios"] == 1
    assert summary["sequencePointScore"] == pytest.approx(40 / 60)
    assert summary["sequencePerfectScoreRate"] == 0
    assert summary["byDecisionType"]["mainAction"]["sequencePointScore"] == pytest.approx(40 / 60)
    assert summary["byDecisionType"]["attackers"]["sequencePointScore"] == pytest.approx(40 / 60)


def test_ground_truth_agreement_scores_equivalent_final_board_signatures():
    scenarios = [
        scored_sequence_record(
            "line-a",
            action_index=1,
            decision_type="priority",
            line_id="line-a",
            line_points=60,
            final_signature="same-board",
        ),
        scored_sequence_record(
            "line-b",
            action_index=0,
            decision_type="priority",
            line_id="line-b",
            line_points=40,
            final_signature="same-board",
        ),
    ]

    summary = ground_truth_agreement(
        scenarios,
        {
            "line-a": 0,
            "line-b": 0,
        },
    )

    assert summary["sequencePointScore"] == 1.0
    assert summary["sequencePerfectScoreRate"] == 1.0


def test_ground_truth_scenario_metrics_require_complete_predictions():
    scenarios = [
        scenario("attack-1", 1, 8, "attackers", "attack-scenario"),
        scenario("attack-2", 1, 8, "attackers", "attack-scenario"),
    ]

    summary = ground_truth_agreement(scenarios, {"attack-1": 1})

    assert summary["evaluated"] == 1
    assert summary["evaluatedScenarios"] == 0
    assert summary["scenarioCoverage"] == 0.0


def test_ground_truth_agreement_ignores_missing_predictions():
    scenarios = [scenario("labeled", 1, 8), scenario("missing", 0, 8)]

    summary = ground_truth_agreement(scenarios, {"labeled": 1})

    assert summary["evaluated"] == 1
    assert summary["exactAgreement"] == 1.0


def test_evaluate_ground_truth_service_uses_model_action_and_training_step():
    records = [scenario("one", 1, 9), scenario("two", 1, 7)]
    client = FakeAiClient()

    report = evaluate_ground_truth_service(
        records,
        service_url="http://127.0.0.1:8791/",
        client=client,
    )

    assert report["model"] == "ia-in-training"
    assert report["trainingStep"] == 4200
    assert report["coverage"] == 1.0
    assert report["metrics"]["exactAgreement"] == 1.0
    assert report["metrics"]["meanHumanActionProbability"] == pytest.approx(0.8)
    assert report["errors"] == []
    assert client.requests[0][0] == "GET"
    assert all(request[1].startswith("http://127.0.0.1:8791/") for request in client.requests)
