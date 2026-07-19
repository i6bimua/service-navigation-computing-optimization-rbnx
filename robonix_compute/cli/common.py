from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser()


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


def load_json_mapping(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with Path(path).expanduser().open(encoding="utf-8") as handle:
        payload = _expand_env(json.load(handle))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expanduser(os.path.expandvars(value))
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def default_model_settings(*, model_dir: Path, device: str) -> dict[str, Any]:
    return {
        "policy_name": "InternVLAN1_Policy",
        "model_path": str(model_dir),
        "device": device,
        "mode": "dual_system",
        "state_encoder": None,
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
