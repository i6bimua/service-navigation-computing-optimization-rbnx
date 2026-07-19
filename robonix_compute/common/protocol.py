from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Literal


HELLO = "hello"
RESET = "reset"
EPISODE_RESET = "episode_reset"
OBSERVATION = "observation"
LATENT_REQUEST = "latent_request"
LATENT_RESPONSE = "latent_response"
ACTION_REQUEST = "action_request"
ACTION_RESPONSE = "action_response"
TELEMETRY_EVENT = "telemetry_event"
ACTION = "action"
ACK = "ack"
ERROR = "error"
CLOSE = "close"

MessageType = Literal[
    "hello",
    "reset",
    "episode_reset",
    "observation",
    "latent_request",
    "latent_response",
    "action_request",
    "action_response",
    "telemetry_event",
    "action",
    "ack",
    "error",
    "close",
]


def new_request_id() -> str:
    return str(uuid.uuid4())


def make_message(message_type: MessageType | str, **payload: Any) -> dict[str, Any]:
    request_id = payload.pop("request_id", None) or new_request_id()
    message = {
        "type": message_type,
        "request_id": request_id,
        "created_at": payload.pop("created_at", time.time()),
    }
    message.update(payload)
    return message


def make_reply(request: dict[str, Any], message_type: MessageType | str, **payload: Any) -> dict[str, Any]:
    return make_message(message_type, request_id=request.get("request_id", new_request_id()), **payload)


@dataclass(frozen=True)
class LatentRequest:
    request_id: str
    step_id: int
    observation: Any
    instruction: str | None = None
    sent_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, Any]:
        return make_message(
            LATENT_REQUEST,
            request_id=self.request_id,
            step_id=self.step_id,
            observation=self.observation,
            instruction=self.instruction,
            sent_at=self.sent_at,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class LatentResponse:
    request_id: str
    step_id: int
    latent: Any
    produced_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "LatentResponse":
        return cls(
            request_id=str(message["request_id"]),
            step_id=int(message["step_id"]),
            latent=message["latent"],
            produced_at=float(message.get("produced_at", time.time())),
            metadata=dict(message.get("metadata", {})),
        )

    def to_message(self) -> dict[str, Any]:
        return make_message(
            LATENT_RESPONSE,
            request_id=self.request_id,
            step_id=self.step_id,
            latent=self.latent,
            produced_at=self.produced_at,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class EpisodeReset:
    request_id: str = field(default_factory=new_request_id)
    episode_id: str | None = None
    instruction: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "EpisodeReset":
        return cls(
            request_id=str(message.get("request_id", new_request_id())),
            episode_id=None if message.get("episode_id") is None else str(message.get("episode_id")),
            instruction=message.get("instruction"),
            created_at=float(message.get("created_at", time.time())),
            metadata=dict(message.get("metadata", {})),
        )

    def to_message(self) -> dict[str, Any]:
        return make_message(
            EPISODE_RESET,
            request_id=self.request_id,
            created_at=self.created_at,
            episode_id=self.episode_id,
            instruction=self.instruction,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class ActionRequest:
    request_id: str
    step_id: int
    observation: Any
    instruction: str | None = None
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "ActionRequest":
        return cls(
            request_id=str(message["request_id"]),
            step_id=int(message["step_id"]),
            observation=message.get("observation"),
            instruction=message.get("instruction"),
            created_at=float(message.get("created_at", time.time())),
            metadata=dict(message.get("metadata", {})),
        )

    def to_message(self) -> dict[str, Any]:
        return make_message(
            ACTION_REQUEST,
            request_id=self.request_id,
            step_id=self.step_id,
            observation=self.observation,
            instruction=self.instruction,
            created_at=self.created_at,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class ActionResponse:
    request_id: str
    step_id: int
    action: Any
    created_at: float = field(default_factory=time.time)
    telemetry: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "ActionResponse":
        return cls(
            request_id=str(message["request_id"]),
            step_id=int(message["step_id"]),
            action=message.get("action"),
            created_at=float(message.get("created_at", time.time())),
            telemetry=dict(message.get("telemetry", {})),
            metadata=dict(message.get("metadata", {})),
        )

    def to_message(self) -> dict[str, Any]:
        return make_message(
            ACTION_RESPONSE,
            request_id=self.request_id,
            step_id=self.step_id,
            action=self.action,
            created_at=self.created_at,
            telemetry=self.telemetry,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class TelemetryEvent:
    request_id: str = field(default_factory=new_request_id)
    event: str = "step"
    created_at: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_message(cls, message: dict[str, Any]) -> "TelemetryEvent":
        return cls(
            request_id=str(message.get("request_id", new_request_id())),
            event=str(message.get("event", "step")),
            created_at=float(message.get("created_at", time.time())),
            payload=dict(message.get("payload", {})),
        )

    def to_message(self) -> dict[str, Any]:
        return make_message(
            TELEMETRY_EVENT,
            request_id=self.request_id,
            created_at=self.created_at,
            event=self.event,
            payload=self.payload,
        )
