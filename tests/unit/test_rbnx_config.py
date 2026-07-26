"""Driver(CMD_INIT) config validation.

Kept separate from the provider module so it runs without robonix_api or the
codegen output installed.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from robonix_compute.rbnx.config import DEFAULTS, VALID_MODES, parse_config

SPEC_PATH = Path(__file__).resolve().parents[2] / "config.spec"


def test_empty_config_is_valid_and_yields_the_mock_backend():
    config, error = parse_config({})
    assert error is None
    # A deployment entry of `config: {}` must boot without checkpoints or a GPU.
    assert config["mode"] == "mock"
    assert config["step_size_m"] == pytest.approx(0.25)
    assert config["turn_angle_deg"] == pytest.approx(15.0)


def test_none_config_is_valid():
    _, error = parse_config(None)
    assert error is None


@pytest.mark.parametrize("mode", VALID_MODES)
def test_every_documented_mode_is_accepted(mode):
    config, error = parse_config({"mode": mode})
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
    _, error = parse_config({key: bad})
    assert error is not None and f"config.{key}" in error and "> 0" in error


@pytest.mark.parametrize("key", ["step_size_m", "timeout_s"])
def test_non_numeric_floats_are_rejected(key):
    _, error = parse_config({key: "fast"})
    assert error is not None and "must be a number" in error


@pytest.mark.parametrize("key", ["max_steps", "cloud_port"])
def test_non_positive_ints_are_rejected(key):
    _, error = parse_config({key: 0})
    assert error is not None and f"config.{key}" in error


def test_numeric_strings_are_coerced():
    # YAML/JSON round-trips can turn numbers into strings; accept them.
    config, error = parse_config({"max_steps": "42", "step_size_m": "0.5"})
    assert error is None
    assert config["max_steps"] == 42
    assert config["step_size_m"] == pytest.approx(0.5)


def test_action_chunk_zero_means_whole_chunk():
    config, error = parse_config({"action_steps_to_execute": 0})
    assert error is None and config["action_steps_to_execute"] == 0


def test_negative_action_chunk_is_rejected():
    _, error = parse_config({"action_steps_to_execute": -1})
    assert error is not None and ">= 0" in error


def test_empty_cloud_host_is_rejected():
    _, error = parse_config({"cloud_host": "   "})
    assert error is not None and "cloud_host" in error


def test_provider_ids_are_trimmed():
    config, error = parse_config({"camera_provider_id": "  head_cam  "})
    assert error is None and config["camera_provider_id"] == "head_cam"


def test_use_map_pose_is_coerced_to_bool():
    assert parse_config({"use_map_pose": 1})[0]["use_map_pose"] is True
    assert parse_config({"use_map_pose": None})[0]["use_map_pose"] is False


def test_unknown_keys_are_preserved():
    # The same mapping is handed to RoboNixComputeSkill.setup(), which reads its
    # own switcher/timeout knobs out of it.
    config, error = parse_config({"tau_lower": 0.4, "k_max": 4})
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
