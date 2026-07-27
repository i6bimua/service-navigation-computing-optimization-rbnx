"""Driver(CMD_INIT) config validation.

Kept separate from the provider module so it runs without robonix_api or the
codegen output installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from robonix_compute.rbnx.config import (
    DEFAULTS,
    PRODUCTION_MODES,
    STUB_ACTION_MODES,
    VALID_MODES,
    parse_config,
)

SPEC_PATH = Path(__file__).resolve().parents[2] / "config.spec"

# Field-level tests need a config that is otherwise acceptable, because a
# missing backend is rejected before any other field is looked at.
BASE = {"mode": "internnav"}


def _with(**overrides):
    return {**BASE, **overrides}


@pytest.mark.parametrize("empty", [{}, None])
def test_a_config_that_names_no_backend_is_rejected(empty):
    """`config: {}` used to yield the mock backend, which meant a deployment
    that said nothing got a stub policy: it reports runs as SUCCEEDED without
    navigating, and its actions came from the latent rather than the
    instruction. Naming a backend is now mandatory."""
    _, error = parse_config(empty)
    assert error is not None
    assert "config.mode is required" in error
    assert "internnav" in error and "allow_stub_actions" in error


@pytest.mark.parametrize("mode", PRODUCTION_MODES)
def test_a_production_backend_needs_no_further_opt_in(mode):
    config, error = parse_config({"mode": mode})
    assert error is None
    assert config["mode"] == mode
    assert config["allow_stub_actions"] is False
    assert config["step_size_m"] == pytest.approx(0.25)
    assert config["turn_angle_deg"] == pytest.approx(15.0)


@pytest.mark.parametrize("mode", STUB_ACTION_MODES)
def test_a_stub_backend_is_rejected_until_the_deployment_says_so(mode):
    """mock and websocket both run the stub S1, so a run cannot navigate and a
    STOP prediction does not mean arrival. Opting in is what separates a test
    deployment from a production one that forgot to configure itself."""
    _, error = parse_config({"mode": mode})
    assert error is not None
    assert f"config.mode={mode}" in error and "allow_stub_actions" in error

    config, error = parse_config({"mode": mode, "allow_stub_actions": True})
    assert error is None
    assert config["mode"] == mode and config["allow_stub_actions"] is True


@pytest.mark.parametrize("mode", VALID_MODES)
def test_every_documented_mode_is_accepted(mode):
    config, error = parse_config({"mode": mode, "allow_stub_actions": True})
    assert error is None
    assert config["mode"] == mode


def test_mode_is_case_insensitive_and_trimmed():
    config, error = parse_config({"mode": "  InternNav "})
    assert error is None
    assert config["mode"] == "internnav"


def test_unknown_mode_is_rejected():
    _, error = parse_config({"mode": "telepathy"})
    assert error is not None and "config.mode" in error


@pytest.mark.parametrize(
    "key",
    ["step_size_m", "turn_angle_deg", "move_timeout_s", "timeout_s", "observation_timeout_s"],
)
@pytest.mark.parametrize("bad", [0, -1.0])
def test_non_positive_floats_are_rejected(key, bad):
    _, error = parse_config(_with(**{key: bad}))
    assert error is not None and f"config.{key}" in error and "> 0" in error


@pytest.mark.parametrize("key", ["step_size_m", "timeout_s"])
def test_non_numeric_floats_are_rejected(key):
    _, error = parse_config(_with(**{key: "fast"}))
    assert error is not None and "must be a number" in error


@pytest.mark.parametrize("key", ["max_steps", "cloud_port"])
def test_non_positive_ints_are_rejected(key):
    _, error = parse_config(_with(**{key: 0}))
    assert error is not None and f"config.{key}" in error


def test_numeric_strings_are_coerced():
    # YAML/JSON round-trips can turn numbers into strings; accept them.
    config, error = parse_config(_with(max_steps="42", step_size_m="0.5"))
    assert error is None
    assert config["max_steps"] == 42
    assert config["step_size_m"] == pytest.approx(0.5)


def test_action_chunk_zero_means_whole_chunk():
    config, error = parse_config(_with(action_steps_to_execute=0))
    assert error is None and config["action_steps_to_execute"] == 0


def test_negative_action_chunk_is_rejected():
    _, error = parse_config(_with(action_steps_to_execute=-1))
    assert error is not None and ">= 0" in error


def test_empty_cloud_host_is_rejected():
    _, error = parse_config(_with(cloud_host="   "))
    assert error is not None and "cloud_host" in error


def test_provider_ids_are_trimmed():
    config, error = parse_config(_with(camera_provider_id="  head_cam  "))
    assert error is None and config["camera_provider_id"] == "head_cam"


def test_use_map_pose_is_coerced_to_bool():
    assert parse_config(_with(use_map_pose=1))[0]["use_map_pose"] is True
    assert parse_config(_with(use_map_pose=None))[0]["use_map_pose"] is False


def test_unknown_keys_are_preserved():
    # The same mapping is handed to RoboNixComputeSkill.setup(), which reads its
    # own switcher/timeout knobs out of it.
    config, error = parse_config(_with(tau_lower=0.4, k_max=4))
    assert error is None
    assert config["tau_lower"] == 0.4 and config["k_max"] == 4


def test_config_spec_documents_every_default():
    """config.spec is the published contract for these fields; keep it honest."""
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    documented = set((spec.get("config") or {}).keys())
    undocumented = sorted(set(DEFAULTS) - documented)
    assert not undocumented, f"config.spec is missing: {undocumented}"


def test_config_spec_defaults_match_the_code():
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))["config"]
    mismatched = {
        key: (value, spec[key])
        for key, value in DEFAULTS.items()
        if key in spec and spec[key] != value and str(spec[key]) != str(value)
    }
    assert not mismatched, f"config.spec disagrees with DEFAULTS: {mismatched}"
