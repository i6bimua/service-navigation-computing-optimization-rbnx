"""Config parsing for the compute-optimization skill's Driver(CMD_INIT) payload.

Kept free of `robonix_api` so validation is testable without a Robonix
deployment installed. Field documentation lives in `config.spec`; the defaults
here are the single source of truth and must stay in sync with it.
"""
from __future__ import annotations

from typing import Any

VALID_MODES = ("mock", "websocket", "internnav")

DEFAULTS: dict[str, Any] = {
    # compute backend
    "mode": "mock",
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

    mode = str(merged["mode"]).strip().lower()
    if mode not in VALID_MODES:
        return merged, f"config.mode must be one of {VALID_MODES}, got {merged['mode']!r}"
    merged["mode"] = mode

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
