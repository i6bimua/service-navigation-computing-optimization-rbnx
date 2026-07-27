<!-- SPDX-License-Identifier: MulanPSL-2.0 -->
# Wiring harness — not published packages

Two RoboNix packages that exist only to verify this repository's skill end to
end. Neither is listed in `syswonder/robonix-package-catalog`, and neither should
be: the catalog reads the manifest at a repository *root*, so these nested ones
are invisible to it, and their package names carry a `.testing.` segment to make
the intent explicit.

| Directory | Package name | What it is |
|---|---|---|
| [mock_robot/](mock_robot/) | `robonix.primitive.testing.mock_robot` | A synthetic body: publishes RGB-D + odometry, accepts `chassis/move` |
| [deployment/](deployment/) | `robonix.robot.testing.navigation_vln_harness` | A local `robonix_manifest.yaml` wiring the body to the service |

## What this verifies, and what it does not

**Verifies** — the RoboNix path end to end, with no simulator, no checkpoints and
no GPU:

- both packages declare their contracts and Atlas resolves them
- the skill decodes real `sensor_msgs/Image` (rgb8 + 32FC1) and `nav_msgs/Odometry`
- a `chassis/move` gRPC round-trip actually reaches the body and comes back
- skills stay `INACTIVE` after boot and activate on the first MCP call
- `navigate` → `status` polling → terminal state, and `cancel`
- `telemetry` reports per-run counters

**Does not verify** — anything about navigation. `mock_robot`'s frames are
heading-dependent gradients with no semantic content, and in `mode: mock` the
policy itself is a stub. Benchmark numbers (R2R-CE SR/SPL, step latency) come
from the InternNav Habitat harness, which owns its own episode loop and metrics.
See the repository README's "Two execution paths" table.

## Requirements

Only ROS 2 — specifically an interpreter that can `import rclpy` plus the
`sensor_msgs` / `nav_msgs` / `geometry_msgs` / `std_msgs` message packages.

Without root, RoboStack provides these as conda packages:

```bash
conda create -n rbnx-ros -c robostack-staging -c conda-forge python=3.11 \
    ros-humble-rclpy ros-humble-sensor-msgs ros-humble-nav-msgs \
    ros-humble-geometry-msgs ros-humble-std-msgs ros-humble-rmw-fastrtps-cpp
```

Then point the body at it:

```bash
export ROS_PYTHON="$HOME/miniconda3/envs/rbnx-ros/bin/python"
```

`mock_robot/scripts/start.sh` uses `ROS_PYTHON` when set and otherwise sources
`/opt/ros/$ROS_DISTRO/setup.bash`.

## Running it

```bash
# 1. Cloud S2 — CPU only, no weights.
robonix-compute-cloud --mode mock --port 8765 &

# 2. Deployment
cd deployment
rbnx build -f robonix_manifest.yaml
rbnx boot  -f robonix_manifest.yaml

# 3. Inspect (another terminal)
rbnx caps -v      # mock_robot and navigation_vln both ACTIVE
rbnx tools        # the four robonix/service/navigation/vln/* tools
rbnx describe --provider navigation_vln

# 4. Drive it
rbnx chat         # "walk down the hallway and stop at the kitchen door"

# 5. Clean up (also use this if boot died on an error path)
rbnx shutdown -f robonix_manifest.yaml
```

`navigation_vln` reaching `ACTIVE` during boot is correct: services are
activated by `rbnx boot` itself, unlike skills, whose just-in-time activation is
gated on a `robonix/skill` namespace. ACTIVE here means the camera and chassis
contracts are bound — the compute runtime still loads on the first `navigate`.

Expect the run to end quickly. In `mode: mock` the stub policy emits `STOP`
almost immediately, which the service correctly reports as `SUCCEEDED` — that
confirms the loop and the terminal-state path, not that anywhere was reached.

## Tests

`mock_robot/tests/` covers the motion mapping, dead reckoning and ROS message
construction; it runs as part of the repository suite (see
`mock_robot/conftest.py`) and skips its provider half when `robonix_api` or the
codegen output is absent.

The cross-contract round trip — the service's `MoveCommand` back into a discrete
action, including turn sign — lives in the service's own suite at
`tests/unit/test_rbnx_contract_closure.py`, because that is the assertion the
skill must not regress.
