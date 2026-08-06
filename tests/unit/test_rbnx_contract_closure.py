# SPDX-License-Identifier: MulanPSL-2.0
"""Does the skill's action output actually close with a chassis primitive?

The skill turns a policy action index into a `MoveCommand`; a chassis primitive
turns a `MoveCommand` back into a discrete motion. Each side has its own unit
tests, but a mismatch between them — different increment, inverted turn sign, a
rejected magnitude — only shows up when both halves are checked together.

A sign inversion is the worst bug class here: nothing raises, the robot simply
turns the wrong way and the policy's belief about where it is drifts from the
truth. So the round trip is asserted directly rather than inferred.

The counterpart is `tests/harness/mock_robot`, the synthetic body used for local
`rbnx boot` verification. It is not a published package; it stands in for any
real chassis primitive implementing the same contract.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_ROOT = REPO_ROOT / "tests" / "harness"
MOCK_ROBOT_ROOT = HARNESS_ROOT / "mock_robot"
DEPLOYMENT_ROOT = HARNESS_ROOT / "deployment"
MANIFEST_PATH = DEPLOYMENT_ROOT / "robonix_manifest.yaml"

from robonix_compute.rbnx import action_bridge  # noqa: E402


@pytest.fixture(scope="module")
def body():
    """The fixture body's mapping module. Pure Python: no rclpy, no robonix_api."""
    if str(MOCK_ROBOT_ROOT) not in sys.path:
        sys.path.insert(0, str(MOCK_ROBOT_ROOT))
    return pytest.importorskip("mock_robot.backend")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


def _instance(manifest: dict, section: str, name: str) -> dict:
    for entry in manifest.get(section) or []:
        if entry.get("name") == name:
            return entry
    raise AssertionError(f"{section} instance {name!r} not found in the manifest")


@pytest.fixture(scope="module")
def increments(manifest) -> dict:
    config = _instance(manifest, "service", "navigation_computing_optimization")["config"]
    return {
        "step_size_m": float(config["step_size_m"]),
        "turn_angle_deg": float(config["turn_angle_deg"]),
    }


# -- the round trip ---------------------------------------------------------
def test_forward_action_survives_the_round_trip(body, increments):
    motion = action_bridge.action_to_motion(action_bridge.ACTION_MOVE_FORWARD, **increments)
    assert body.motion_to_action(motion.forward_m, motion.rotate_deg, **increments) == body.MOVE_FORWARD


@pytest.mark.parametrize(
    "action_name,expected_attr",
    [("ACTION_TURN_LEFT", "TURN_LEFT"), ("ACTION_TURN_RIGHT", "TURN_RIGHT")],
)
def test_turn_direction_is_preserved_end_to_end(body, increments, action_name, expected_attr):
    """A sign inversion here would silently steer the robot the wrong way."""
    motion = action_bridge.action_to_motion(getattr(action_bridge, action_name), **increments)
    action = body.motion_to_action(motion.forward_m, motion.rotate_deg, **increments)
    assert action == getattr(body, expected_attr)


def test_stop_never_reaches_the_chassis(body, increments):
    """STOP ends the episode; it must not become a zero-magnitude MoveCommand.

    A zero MoveCommand would fall through to a real driver's velocity mode and
    publish an all-zero twist for its default duration — harmless on a fixture,
    but on real hardware it is a command where none was intended.
    """
    assert action_bridge.action_to_motion(action_bridge.ACTION_STOP, **increments) is None
    # And a chassis would refuse an all-zero command anyway.
    with pytest.raises(body.UnsupportedMotionError):
        body.motion_to_action(0.0, 0.0, **increments)


def test_every_moving_action_round_trips(body, increments):
    for index in (
        action_bridge.ACTION_MOVE_FORWARD,
        action_bridge.ACTION_TURN_LEFT,
        action_bridge.ACTION_TURN_RIGHT,
    ):
        motion = action_bridge.action_to_motion(index, **increments)
        assert motion is not None
        assert body.motion_to_action(motion.forward_m, motion.rotate_deg, **increments) in body.ACTIONS


def test_mismatched_increments_would_be_caught_at_runtime(body):
    """Documents the failure mode the manifest invariant prevents."""
    # Skill configured for 0.5 m, body for 0.25 m.
    motion = action_bridge.action_to_motion(
        action_bridge.ACTION_MOVE_FORWARD, step_size_m=0.5, turn_angle_deg=15.0
    )
    with pytest.raises(body.UnsupportedMotionError, match="step size"):
        body.motion_to_action(motion.forward_m, motion.rotate_deg, step_size_m=0.25, turn_angle_deg=15.0)


def test_action_indices_match_the_r2rce_space():
    """Both sides must agree on the discrete action encoding."""
    assert action_bridge.ACTION_STOP == 0
    assert action_bridge.ACTION_MOVE_FORWARD == 1
    assert action_bridge.ACTION_TURN_LEFT == 2
    assert action_bridge.ACTION_TURN_RIGHT == 3


# -- observation closure ----------------------------------------------------
def test_body_depth_encoding_is_one_the_skill_decodes():
    """The body publishes 32FC1; the skill must accept that encoding in metres."""
    from robonix_compute.rbnx import observation

    assert "32FC1" in observation._DEPTH_ENCODINGS
    assert observation._DEPTH_ENCODINGS["32FC1"][1] == 1.0  # already metres


def test_body_rgb_encoding_is_one_the_skill_decodes():
    from robonix_compute.rbnx import observation

    assert "rgb8" in observation._COLOR_ENCODINGS


def test_skill_requires_exactly_what_the_body_publishes():
    """Every contract the skill resolves must be declared by the fixture body."""
    body_manifest = yaml.safe_load(
        (MOCK_ROBOT_ROOT / "package_manifest.yaml").read_text(encoding="utf-8")
    )
    declared = {entry["name"] for entry in body_manifest["capabilities"]}
    required = {
        "robonix/primitive/camera/rgb",
        "robonix/primitive/camera/depth",
        "robonix/primitive/camera/intrinsics",
        "robonix/primitive/chassis/odom",
        "robonix/primitive/chassis/move",
    }
    missing = sorted(required - declared)
    assert not missing, f"the fixture body does not declare: {missing}"


# -- deployment manifest self-consistency -----------------------------------
def test_motion_increments_agree_between_the_two_instances(manifest):
    """The manifest invariant: mismatched increments make every command fail."""
    primitive = _instance(manifest, "primitive", "mock_robot")["config"]
    service = _instance(manifest, "service", "navigation_computing_optimization")["config"]
    assert float(primitive["step_size_m"]) == float(service["step_size_m"])
    assert float(primitive["turn_angle_deg"]) == float(service["turn_angle_deg"])


def test_service_binds_the_body_explicitly(manifest):
    # Without explicit provider ids the service needs a unique match per
    # contract, which breaks the moment a second camera or chassis joins the
    # deployment.
    service = _instance(manifest, "service", "navigation_computing_optimization")["config"]
    assert service["camera_provider_id"] == "mock_robot"
    assert service["chassis_provider_id"] == "mock_robot"


def test_map_pose_is_off_because_no_mapping_service_is_deployed(manifest):
    # use_map_pose would resolve robonix/service/map/pose, which nothing here
    # provides, leaving the provider permanently Deferred.
    assert _instance(manifest, "service", "navigation_computing_optimization")["config"]["use_map_pose"] is False
    # navigation_computing_optimization is itself a service instance now, so "the service section is
    # empty" is no longer the right check — what matters is that nothing here
    # provides map/pose. Anything other than our own instance might.
    others = [e["name"] for e in (manifest.get("service") or []) if e["name"] != "navigation_computing_optimization"]
    assert not others, f"a second service could provide map/pose: {others}"


def test_cloud_process_is_not_a_deployment_package(manifest):
    """Cloud S2 lives outside the robot; it must not appear as an instance."""
    names = {
        entry.get("name")
        for section in ("primitive", "service", "skill")
        for entry in (manifest.get(section) or [])
    }
    assert not {"cloud", "cloud_s2", "compute_cloud"} & names
    service = _instance(manifest, "service", "navigation_computing_optimization")["config"]
    assert service["cloud_host"] and service["cloud_port"]


def test_manifest_paths_resolve(manifest):
    for section, name in (("primitive", "mock_robot"), ("service", "navigation_computing_optimization")):
        entry = _instance(manifest, section, name)
        target = (DEPLOYMENT_ROOT / entry["path"]).resolve()
        assert (target / "package_manifest.yaml").is_file(), f"{name}: {target} has no manifest"


def test_soma_and_urdf_referenced_by_the_manifest_exist(manifest):
    soma_path = DEPLOYMENT_ROOT / manifest["system"]["soma"]["robot_yaml"]
    assert soma_path.is_file()
    soma = yaml.safe_load(soma_path.read_text(encoding="utf-8"))
    urdf = (DEPLOYMENT_ROOT / soma["urdf"]["path"]).resolve()
    assert urdf.is_file()
    # Soma resolves the root link against the URDF; a stale name breaks boot.
    assert f'link name="{soma["urdf"]["root_link"]}"' in urdf.read_text(encoding="utf-8")


def test_fixture_is_not_advertised_as_publishable(manifest):
    """The harness must never look like a package someone should install."""
    body_manifest = yaml.safe_load(
        (MOCK_ROBOT_ROOT / "package_manifest.yaml").read_text(encoding="utf-8")
    )
    assert body_manifest["package"]["name"].startswith("robonix.primitive.testing.")
    assert manifest["catalog"]["name"].startswith("robonix.robot.testing.")
