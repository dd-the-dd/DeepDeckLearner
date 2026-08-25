from deepdeck_agent import RandomAgent


def build_random_agent(seed: int = 1) -> RandomAgent:
    """The baseline is deliberately small: sample one exact legal action ID."""
    return RandomAgent(seed=seed)

