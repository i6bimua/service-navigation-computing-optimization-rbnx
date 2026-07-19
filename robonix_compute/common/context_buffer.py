from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any


@dataclass(frozen=True)
class LatentSlot:
    latent: Any
    latent_step_id: int
    request_id: str | None = None
    arrival_time: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BufferSnapshot:
    active: LatentSlot | None
    pending: LatentSlot | None
    current_step_id: int

    @property
    def misalignment_k(self) -> int | None:
        if self.active is None:
            return None
        return max(0, int(self.current_step_id) - int(self.active.latent_step_id))


class ContextBuffer:
    """Two-slot active/pending latent buffer with atomic swap semantics."""

    def __init__(self) -> None:
        self._active: LatentSlot | None = None
        self._pending: LatentSlot | None = None
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._active = None
            self._pending = None

    def seed(self, latent: Any, *, step_id: int = 0, request_id: str | None = None) -> LatentSlot:
        slot = LatentSlot(latent=latent, latent_step_id=int(step_id), request_id=request_id)
        with self._lock:
            self._active = slot
            self._pending = None
        return slot

    def write_pending(
        self,
        latent: Any,
        *,
        step_id: int,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LatentSlot:
        slot = LatentSlot(
            latent=latent,
            latent_step_id=int(step_id),
            request_id=request_id,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._pending = slot
        return slot

    def swap_pending_to_active(self) -> LatentSlot | None:
        with self._lock:
            if self._pending is None:
                return None
            self._active = self._pending
            self._pending = None
            return self._active

    def absorb(
        self,
        latent: Any,
        *,
        step_id: int,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LatentSlot:
        slot = self.write_pending(
            latent,
            step_id=step_id,
            request_id=request_id,
            metadata=metadata,
        )
        swapped = self.swap_pending_to_active()
        return swapped or slot

    def active(self) -> LatentSlot | None:
        with self._lock:
            return self._active

    def snapshot(self, *, current_step_id: int) -> BufferSnapshot:
        with self._lock:
            return BufferSnapshot(
                active=self._active,
                pending=self._pending,
                current_step_id=int(current_step_id),
            )

    def misalignment(self, *, current_step_id: int) -> int | None:
        return self.snapshot(current_step_id=current_step_id).misalignment_k
