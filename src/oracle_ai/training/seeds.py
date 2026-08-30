from __future__ import annotations

from collections.abc import Iterable

_MASK_64 = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15


def _splitmix64(value: int) -> int:
    value = (value ^ (value >> 30)) * 0xBF58476D1CE4E5B9 & _MASK_64
    value = (value ^ (value >> 27)) * 0x94D049BB133111EB & _MASK_64
    return value ^ (value >> 31)


class UniqueSeedStream:
    """Deterministic stream without repeats before the 64-bit counter wraps."""

    def __init__(self, seed: int, excluded: Iterable[int] = ()) -> None:
        self._state = seed & _MASK_64
        self._excluded = {value & _MASK_64 for value in excluded}
        self._issued: set[int] = set()

    def next(self) -> int:
        while True:
            self._state = (self._state + _GOLDEN_GAMMA) & _MASK_64
            candidate = _splitmix64(self._state)
            if candidate not in self._excluded and candidate not in self._issued:
                self._issued.add(candidate)
                return candidate

    def take(self, count: int) -> list[int]:
        if count < 0:
            raise ValueError("seed count cannot be negative")
        return [self.next() for _ in range(count)]
