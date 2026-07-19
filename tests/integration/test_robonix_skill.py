from robonix_compute.robonix import RoboNixComputeSkill


def test_robonix_skill_mock_setup_reset_step_close():
    skill = RoboNixComputeSkill()
    try:
        setup = skill.setup({"mode": "mock", "require_initial_latent": False})
        reset = skill.reset(instruction="go to the target")
        first = skill.step({"rgb": [1.0, 0.0], "depth": [0.0]})
        second = skill.step({"rgb": [0.0, 1.0], "depth": [0.0]})
    finally:
        closed = skill.close()

    assert setup["status"] == "ready"
    assert reset["instruction"] == "go to the target"
    assert first["action"] == 0
    assert second["action"] == 1
    assert second["summary"]["sync_count"] >= 1
    assert setup["skill"]["name"] == "compute_optimization_adapter"
    assert skill.telemetry()["summary"]["step_count"] == 2
    assert closed["status"] == "closed"


def test_robonix_skill_can_setup_again_after_close():
    skill = RoboNixComputeSkill()
    skill.setup({"mode": "mock", "require_initial_latent": False})
    skill.close()
    setup = skill.setup({"mode": "mock", "require_initial_latent": False})
    reset = skill.reset(instruction="go")
    step = skill.step({"rgb": [1.0, 0.0], "depth": [0.0]})

    assert setup["status"] == "ready"
    assert reset["status"] == "reset"
    assert step["action"] == 0
