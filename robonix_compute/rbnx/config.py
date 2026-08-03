"""Config parsing for the navigation computing optimization service's Driver(CMD_INIT) payload.

Kept free of `robonix_api` so validation is testable without a Robonix
deployment installed. Field documentation lives in `config.spec`; the defaults
here are the single source of truth and must stay in sync with it.
"""
from __future__ import annotations

from typing import Any

# `mock` and `websocket` both run `_mock_s1`, whose action is the argmax of the
# latent and ignores the instruction entirely. They exercise the transport and
# the contract surface; they do not navigate. Only `internnav` runs a trained
# policy, so it is the only backend whose actions may reach a chassis.
STUB_ACTION_MODES = ("mock", "websocket")
PRODUCTION_MODES = ("internnav",)
VALID_MODES = STUB_ACTION_MODES + PRODUCTION_MODES

DEFAULTS: dict[str, Any] = {
    # Compute backend. Deliberately absent from the defaults: a deployment that
    # does not name a backend must not silently get a stub one, because a stub
    # backend reports runs as SUCCEEDED without having navigated.
    "allow_stub_actions": False,
    "cloud_host": "127.0.0.1",
    "cloud_port": 8765,
    "model_dir": "checkpoints/InternVLA-N1",
    "s1_model_dir": "checkpoints/InternVLA-N1-S1",
    "device": "cuda:0",
    # action mapping
    "step_size_m": 0.25,
    "turn_angle_deg": 15.0,
    "action_steps_to_execute": 0,
    "move_timeout_s": 5.0,
    # run limits
    "timeout_s": 300.0,
    "max_steps": 500,
    "observation_timeout_s": 10.0,
    # dependency wiring
    "use_map_pose": False,
    "camera_provider_id": "",
    "chassis_provider_id": "",
    "map_provider_id": "",
    # Must stay False on the online path. EdgeRuntime.step() reads this as
    # "the context buffer is already seeded"; a Robonix deployment never seeds
    # it (only the offline eval harness does), so True makes the first step
    # raise instead of performing an initial synchronization.
    "require_initial_latent": False,
}

# Every value must be strictly positive; a zero step size or timeout would
# either freeze the robot or spin the loop.
_POSITIVE_FLOATS = (
    "step_size_m",
    "turn_angle_deg",
    "move_timeout_s",
    "timeout_s",
    "observation_timeout_s",
)
_POSITIVE_INTS = ("max_steps", "cloud_port")


def parse_config(cfg: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    """Merge `cfg` over DEFAULTS and validate.

    Returns `(config, error)`. `error` is None when the config is usable;
    otherwise it is a message suitable for `Err()` naming the offending field,
    and `config` holds the partially-coerced merge (for logging only).

    Unknown keys are preserved rather than rejected: the same mapping is handed
    to `RoboNixComputeSkill.setup()`, which reads its own switcher and timeout
    knobs (`tau_*`, `delta`, `k_max`, `eta`, `require_initial_latent`, ...)
    straight out of it.
    """
    merged = dict(DEFAULTS)
    merged.update(cfg or {})

    raw_mode = merged.get("mode")
    mode = str(raw_mode).strip().lower() if raw_mode is not None else ""
    if not mode:
        return merged, (
            "config.mode is required and has no default. Use mode: internnav for a "
            f"deployment that navigates, or one of {list(STUB_ACTION_MODES)} together "
            "with allow_stub_actions: true for a test deployment — a stub backend "
            "never drives chassis/move."
        )
    if mode not in VALID_MODES:
        return merged, f"config.mode must be one of {VALID_MODES}, got {raw_mode!r}"
    merged["mode"] = mode

    merged["allow_stub_actions"] = bool(merged.get("allow_stub_actions", False))
    if mode in STUB_ACTION_MODES and not merged["allow_stub_actions"]:
        return merged, (
            f"config.mode={mode} runs a stub edge policy: the action is read off the "
            "latent and the instruction is ignored, so the run cannot navigate and a "
            "STOP prediction does not mean arrival. Set allow_stub_actions: true to "
            "run it anyway as a test deployment, in which case chassis/move is never "
            "called, or use mode: internnav to navigate for real."
        )

    for key in _POSITIVE_FLOATS:
        try:
            value = float(merged[key])
        except (TypeError, ValueError):
            return merged, f"config.{key} must be a number, got {merged[key]!r}"
        if not value > 0:
            return merged, f"config.{key} must be > 0, got {value}"
        merged[key] = value

    for key in _POSITIVE_INTS:
        try:
            value = int(merged[key])
        except (TypeError, ValueError):
            return merged, f"config.{key} must be an integer, got {merged[key]!r}"
        if not value > 0:
            return merged, f"config.{key} must be > 0, got {value}"
        merged[key] = value

    try:
        chunk = int(merged["action_steps_to_execute"])
    except (TypeError, ValueError):
        return merged, (
            f"config.action_steps_to_execute must be an integer, "
            f"got {merged['action_steps_to_execute']!r}"
        )
    if chunk < 0:
        return merged, f"config.action_steps_to_execute must be >= 0 (0 = whole chunk), got {chunk}"
    merged["action_steps_to_execute"] = chunk

    merged["use_map_pose"] = bool(merged["use_map_pose"])
    for key in ("camera_provider_id", "chassis_provider_id", "map_provider_id", "cloud_host"):
        merged[key] = str(merged[key]).strip()
    if not merged["cloud_host"]:
        return merged, "config.cloud_host must not be empty"

    return merged, None
