from __future__ import annotations

from dataclasses import dataclass

from oracle_ai.model_v3 import MagicTransformerActorCriticV3, ModelConfigV3


@dataclass(frozen=True)
class ModelConfigV4(ModelConfigV3):
    pass


class MagicTransformerActorCriticV4(MagicTransformerActorCriticV3):
    model_family = "structured-v4"
    observation_schema = "structured-observation/v4"

    def __init__(self, config: ModelConfigV4) -> None:
        super().__init__(config)
