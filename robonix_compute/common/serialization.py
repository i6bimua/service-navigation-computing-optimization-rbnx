from __future__ import annotations

from typing import Any

import msgpack
import numpy as np

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None


def _default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return {
            "__ndarray__": True,
            "dtype": str(value.dtype),
            "shape": value.shape,
            "data": value.tobytes(),
        }
    if isinstance(value, np.generic):
        return value.item()
    if torch is not None and isinstance(value, torch.Tensor):
        return _default(value.detach().to("cpu", dtype=torch.float32).numpy())
    raise TypeError(f"Unsupported msgpack type: {type(value)!r}")


def _object_hook(value: dict[str, Any]) -> Any:
    if value.get("__ndarray__"):
        array = np.frombuffer(value["data"], dtype=np.dtype(value["dtype"]))
        return array.reshape(value["shape"])
    return value


def packb(message: dict[str, Any]) -> bytes:
    return msgpack.packb(message, default=_default, use_bin_type=True)


def unpackb(raw_message: bytes) -> dict[str, Any]:
    return msgpack.unpackb(raw_message, raw=False, object_hook=_object_hook)
