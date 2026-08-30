from __future__ import annotations

import random

from oracle_ai.agents.mixed_model_rotation import RotationModel, refill_rotation_bag


def test_each_randomized_rotation_bag_contains_both_frozen_models_once() -> None:
    models = (
        RotationModel("v12", "v12-step-411247"),
        RotationModel("v12.1", "v12.1-step-418148"),
    )
    bag = refill_rotation_bag(models, random.Random(7))
    assert len(bag) == 2
    assert set(bag) == set(models)
