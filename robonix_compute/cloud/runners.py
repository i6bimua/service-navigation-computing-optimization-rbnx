from __future__ import annotations

from typing import Any, Protocol


class S2Runner(Protocol):
    def reset(self) -> None:
        ...

    def generate_latent(self, observation: Any, instruction: str | None = None) -> Any:
        ...


class CallableS2Runner:
    def __init__(self, generate_fn, reset_fn=None):
        self._generate_fn = generate_fn
        self._reset_fn = reset_fn

    def reset(self) -> None:
        if self._reset_fn is not None:
            self._reset_fn()

    def generate_latent(self, observation: Any, instruction: str | None = None) -> Any:
        return self._generate_fn(observation, instruction)
