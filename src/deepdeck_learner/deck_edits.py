"""Shared transaction and optimistic versions for local deck writers."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

from .replay_lock import replay_storage_lock

P = ParamSpec("P")
R = TypeVar("R")


class DeckRevisionConflict(ValueError):
    pass


def check_deck_version(current: dict[str, Any], expected: Any) -> None:
    if type(expected) is not int or expected < 1:
        raise ValueError(
            "Inspect this deck with get_deck first and send its version as expected_version."
        )
    if current.get("version") != expected:
        raise DeckRevisionConflict(
            "This local deck changed in another editor or the copilot. "
            "Your changes were not saved. Read the latest version before applying them."
        )


def deck_transaction(
    function: Callable[Concatenate[Path, P], R],
) -> Callable[Concatenate[Path, P], R]:
    @wraps(function)
    def transaction(root: Path, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with replay_storage_lock(root, namespace="decks"):
            return function(root, *args, **kwargs)

    return transaction
