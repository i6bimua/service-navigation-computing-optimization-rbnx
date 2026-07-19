from __future__ import annotations

from typing import Any, Protocol

import numpy as np


class S1Runner(Protocol):
    def reset(self) -> None:
        ...

    def extract_visual_feature(self, observation: Any) -> Any:
        ...

    def act(self, observation: Any, latent: Any) -> Any:
        ...


class CallableS1Runner:
    """Small adapter for tests or downstream projects with callable S1 hooks."""

    def __init__(self, act_fn, feature_fn=None, reset_fn=None):
        self._act_fn = act_fn
        self._feature_fn = feature_fn or self._default_feature
        self._reset_fn = reset_fn

    def reset(self) -> None:
        if self._reset_fn is not None:
            self._reset_fn()

    def extract_visual_feature(self, observation: Any) -> Any:
        return self._feature_fn(observation)

    def act(self, observation: Any, latent: Any) -> Any:
        return self._act_fn(observation, latent)

    @staticmethod
    def _default_feature(observation: Any) -> np.ndarray:
        if isinstance(observation, dict):
            for key in ("rgb", "image", "observation"):
                if key in observation:
                    return np.asarray(observation[key], dtype=np.float32).reshape(-1)
        return np.asarray(observation, dtype=np.float32).reshape(-1)
