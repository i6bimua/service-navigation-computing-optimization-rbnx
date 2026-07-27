# SPDX-License-Identifier: MulanPSL-2.0
"""A navigate that cannot start must fail its RTDL node, through a real executor.

Asserting on the provider's own reply is not enough here, because the defect was
in what the executor does with that reply. `navigate`, `navigate/status` and
`navigate/cancel` form an async contract group, so the executor dispatches
`navigate`, reads a `run_id` out of the response and then polls `status` until it
reports a terminal state. A completed MCP round-trip counts as a successful
dispatch, so a refusal expressed as a *returned value* still starts the poll
loop — and that loop has no iteration cap and no deadline. Two observed
outcomes, both from this test's setup:

  * the executor polls with `{}` when the run id is empty, which fails this
    provider's argument schema, so the node did fail — but the error read
    `1 validation error for navigate_statusArg` and the real cause was gone.
  * given a non-empty run id the provider does not know, `status` answered
    `PENDING`, which is not terminal, so the plan ran for as long as it was
    allowed to: 22 polls in 45s with no terminal event.

So `navigate` raises on refusal and `status` reports an unknown run as `FAILED`.
This test drives a real atlas, executor and provider to check the first half;
`tests/unit/test_rbnx_provider_guards.py` covers both without a deployment.

Skipped unless a robonix source tree is set up (`rbnx` on PATH plus the codegen
output built by `rbnx build`). To run it:

    cd tests/harness/deployment
    rbnx build -f robonix_manifest.yaml
    R=$(git rev-parse --show-toplevel)
    PYTHONPATH="$(rbnx path robonix-api):$R:$R/rbnx-build/codegen/robonix_mcp_types:$R/rbnx-build/codegen/proto_gen" \\
        ~/miniconda3/envs/rbnx-ros/bin/python -m pytest \\
        tests/integration/test_executor_rejection.py -v

The deployment it boots uses `mode: internnav` with InternNav absent, so
`navigate` refuses on the compute-runtime check. That is a real refusal path and
needs no GPU.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

pytest.importorskip("grpc", reason="needs grpcio")
pytest.importorskip("pilot_pb2", reason="needs `rbnx build` codegen output on PYTHONPATH")

import grpc  # noqa: E402
import pilot_pb2  # noqa: E402
import robonix_contracts_pb2_grpc as contracts_grpc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOYMENT = REPO_ROOT / "tests" / "harness" / "deployment"
SOURCE_MANIFEST = DEPLOYMENT / "robonix_manifest.yaml"
TEST_MANIFEST = DEPLOYMENT / "robonix_manifest.executor-rejection.yaml"

RTDL_DO = 2
STATE_NAMES = {0: "PENDING", 1: "RUNNING", 2: "SUCCEEDED", 3: "FAILED", 4: "CANCELED", 5: "TIMEOUT"}
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELED", "TIMEOUT"}

NAVIGATE = "robonix/service/navigation/vln/navigate"
EXECUTE = "robonix/system/executor/execute"
SERVICE_INSTANCE = "navigation_vln"

BOOT_TIMEOUT_S = 180.0
# Comfortably longer than the 2s poll interval, so a plan that polls instead of
# failing is reported as a timeout here rather than passing by accident.
PLAN_TIMEOUT_S = 40.0

pytestmark = pytest.mark.skipif(
    shutil.which("rbnx") is None, reason="needs the rbnx CLI on PATH"
)


def _rbnx(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["rbnx", *args],
        cwd=DEPLOYMENT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def deployment():
    """Boot mock_robot plus this service, with a backend `navigate` will refuse."""
    manifest = SOURCE_MANIFEST.read_text(encoding="utf-8")
    stub = "      mode: mock\n      allow_stub_actions: true\n"
    assert stub in manifest, "harness manifest no longer has the expected backend block"
    TEST_MANIFEST.write_text(manifest.replace(stub, "      mode: internnav\n"), encoding="utf-8")

    build = _rbnx("build", "-f", TEST_MANIFEST.name, timeout=900.0)
    if build.returncode != 0:
        TEST_MANIFEST.unlink(missing_ok=True)
        pytest.skip(f"rbnx build failed, so this host cannot boot a deployment:\n{build.stderr[-2000:]}")

    log = DEPLOYMENT / "executor-rejection-boot.log"
    with log.open("w", encoding="utf-8") as sink:
        boot = subprocess.Popen(
            ["rbnx", "boot", "-f", TEST_MANIFEST.name],
            cwd=DEPLOYMENT,
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if "component(s) up" in log.read_text(encoding="utf-8", errors="replace"):
                break
            if boot.poll() is not None:
                pytest.fail(f"rbnx boot exited early:\n{log.read_text(errors='replace')[-2000:]}")
            time.sleep(2.0)
        else:
            pytest.fail(f"deployment did not come up in {BOOT_TIMEOUT_S:.0f}s")
        yield json.loads(_rbnx("inspect", timeout=120.0).stdout)
    finally:
        _rbnx("shutdown", "-f", TEST_MANIFEST.name, timeout=180.0)
        if boot.poll() is None:
            with contextlib.suppress(OSError):
                os.killpg(os.getpgid(boot.pid), signal.SIGTERM)
        TEST_MANIFEST.unlink(missing_ok=True)
        log.unlink(missing_ok=True)


def _endpoint(state: dict, provider_id: str, contract_id: str) -> str:
    for entry in state["providers"].get(provider_id, {}).get("endpoints", []):
        if entry.get("contract_id") == contract_id:
            return entry["endpoint"].replace("http://", "").replace("https://", "")
    raise AssertionError(f"{provider_id} does not publish {contract_id}")


def _one_call_plan(plan_id: str, op_id: str) -> pilot_pb2.Plan:
    return pilot_pb2.Plan(
        plan_id=plan_id,
        session_id="test-executor-rejection",
        round=1,
        root_index=0,
        nodes=[
            pilot_pb2.RtdlNode(
                node_kind=RTDL_DO,
                op_id=op_id,
                description="start a VLN run the service will refuse",
                call=pilot_pb2.CapabilityCall(
                    call_id=f"{plan_id}-call",
                    provider_id=SERVICE_INSTANCE,
                    contract_id=NAVIGATE,
                    args_json=json.dumps(
                        {"instruction": "walk to the kitchen", "timeout_s": 30.0, "max_steps": 5}
                    ),
                ),
            )
        ],
    )


def test_the_service_is_active_but_cannot_navigate(deployment):
    """The refusal has to come from the run, not from a half-built deployment:
    lazy loading means a missing backend still boots to ACTIVE."""
    provider = deployment["providers"][SERVICE_INSTANCE]
    assert provider["pushed_state"].endswith("ACTIVE"), provider["pushed_state"]
    assert any(e["contract_id"] == NAVIGATE for e in provider["endpoints"])


def test_a_refused_navigate_fails_its_rtdl_node(deployment):
    stub = contracts_grpc.RobonixSystemExecutorExecuteStub(
        grpc.insecure_channel(_endpoint(deployment, "executor", EXECUTE))
    )
    op_id = "op-navigate"
    observed: list[tuple[str, str]] = []
    completed = False

    try:
        for event in stub.Execute(
            _one_call_plan("test-executor-rejection", op_id), timeout=PLAN_TIMEOUT_S
        ):
            if event.HasField("node_state") and event.node_state.op_id == op_id:
                node = event.node_state
                observed.append(
                    (
                        STATE_NAMES.get(node.state, str(node.state)),
                        node.leaf_result.error or node.operator_detail,
                    )
                )
            if event.HasField("plan_complete"):
                completed = True
                assert event.plan_complete.any_failed is True
    except grpc.RpcError as exc:
        if exc.code() is grpc.StatusCode.DEADLINE_EXCEEDED:
            pytest.fail(
                f"the plan never reached a terminal state in {PLAN_TIMEOUT_S:.0f}s — a refused "
                "navigate is being polled instead of failing"
            )
        raise

    assert completed, "the executor never reported the plan as complete"
    assert observed, f"no node_state event for {op_id}"
    state, detail = observed[-1]
    assert state == "FAILED", f"navigate node ended {state}, events={observed}"
    # The message an operator reads off the failed node has to name the cause.
    assert "InternNav is required" in detail, detail
    assert not any(s == "PENDING" for s, _ in observed[1:]), observed
