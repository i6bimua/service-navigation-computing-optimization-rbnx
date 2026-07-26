---
description: Vision-language navigation — follow a natural-language route description using a dual-system policy whose slow semantic reasoning runs in the cloud and whose fast action generation runs on the edge.
---

# robonix.skill.compute_optimization — compute-optimized navigation skill

Given a natural-language route description (e.g. `"walk down the hallway, turn
left at the painting and stop by the kitchen door"`), this skill closes the
navigation loop itself: it reads RGB-D and pose from the deployment's camera
and chassis primitives, asks a cloud System-2 model for a semantic latent when
its edge runtime decides one is needed, runs edge System-1 to get a discrete
action, and issues that action as a bounded `chassis/move` command.

It does **not** delegate to `service/navigation/navigate`. VLN grounds the
instruction in the live visual stream, so there is no metric goal pose to hand
to a planner — the instruction *is* the goal.

The differentiator over a plain VLN policy is the cloud-edge split: rather than
synchronizing with the cloud on every step (correct but blocking) or never
(fast but semantically stale), the edge runtime picks per step. On R2R-CE
`val-unseen` (1,839 episodes) this holds success rate level with an edge-only
baseline while cutting mean step latency 2.22x, at 0.60 GB of edge model
memory instead of 16.63 GB. See [README.md](README.md#benchmark-results) for
the full tables.

Those numbers come from the benchmark path, not from this skill: the InternNav
Habitat evaluator owns that episode loop and its metrics, and shares only the
compute runtime with this skill. This skill is the robot path — same runtime,
driving a real chassis through the standard contracts.

## Interface (4 MCP tools)

### `robonix/skill/compute_optimization/navigate`

Start a navigation run. Returns immediately — the run continues in the
background.

| param         | type   | default | meaning                                                                                  |
|---------------|--------|---------|------------------------------------------------------------------------------------------|
| `instruction` | string | —       | Natural-language route description. Empty string is rejected.                            |
| `timeout_s`   | float  | 0       | Wall-clock ceiling for the whole run. `0` = use `config.timeout_s` (300 s).               |
| `max_steps`   | uint32 | 0       | Control-step ceiling. `0` = use `config.max_steps` (500).                                 |

Returns `{accepted, run_id, message}`. `accepted=false` when a run is already
active, the skill has not finished activating, or the observation inputs have
not produced a frame yet — `message` says which.

### `robonix/skill/compute_optimization/navigate/status`

Poll a run. Empty `run_id` means the most recent run.

Returns `{known, state, steps_executed, actions_issued, elapsed_s,
mean_step_latency_ms, stop_predicted, detail}`.

`state` is one of:

| state       | meaning                                                                       |
|-------------|-------------------------------------------------------------------------------|
| `PENDING`   | Accepted, worker not yet running.                                             |
| `RUNNING`   | Loop is executing.                                                            |
| `SUCCEEDED` | The policy predicted STOP — it believes the instruction is complete.          |
| `FAILED`    | Step limit hit without STOP, policy/compute error, or a chassis command error. |
| `CANCELED`  | Cancelled via the cancel contract, or the skill was deactivated.               |
| `TIMEOUT`   | `timeout_s` elapsed.                                                          |

Note that `SUCCEEDED` means *the policy decided it arrived*, not that arrival
was independently verified. This skill carries no goal-checker; verifying
arrival is the caller's business.

### `robonix/skill/compute_optimization/navigate/cancel`

Abort the active run. Empty `run_id` cancels whatever is running. Idempotent —
cancelling a finished run returns `ok=true` with a `no-op` message.

Cancellation takes effect before the *next* `chassis/move`; the in-flight
bounded motion completes. Since each motion is a fixed 0.25 m or 15°, the
robot travels at most one increment past the cancel.

### `robonix/skill/compute_optimization/telemetry`

Read the compute-optimization measurements for a run. Empty `run_id` means the
most recent run.

Returns `{known, steps, sync_count, timeout_count, late_absorbed_count,
latent_reuse_count, mean_step_latency_ms, summary_json}`, where:

- `sync_count` — steps that requested a fresh cloud latent
- `timeout_count` — steps whose cloud response missed its adaptive deadline
- `late_absorbed_count` — late responses promoted from the pending slot
- `latent_reuse_count` — steps that reused the active latent
- `summary_json` — the whole per-run report including the per-step log

Use this to confirm the cloud-edge split is paying off on *this* deployment
rather than trusting the offline benchmark. A `sync_count` close to `steps`
means the policy is synchronizing nearly every step and the latency win is
gone; a large `timeout_count` means the cloud link is the bottleneck.

## Usage pattern

1. Call `navigate` **once** with an instruction. It returns a `run_id`
   immediately.
2. Poll `status` with that `run_id` until `state` is terminal.
3. `cancel` to abort early.
4. Optionally read `telemetry` afterwards.

Do **not** call `navigate` again while a run is active — the second call is
rejected. Do not run this skill alongside another skill or service that drives
the same chassis (`service/navigation/navigate`, `skill/explore`): both would
issue motion commands and fight for the chassis. Enable one motion-owning
stack at a time in the deploy manifest.

## Behaviour

```
camera rgb + depth + intrinsics          chassis odom
        │                                     │
        └──────────────┬──────────────────────┘
                       ▼
                 observation
                       │
        ┌──────────────┴───────────────┐
        │  edge runtime: sync needed?  │
        └──────┬────────────────┬──────┘
               │ yes            │ no
               ▼                │
      cloud S2 (semantic    reuse active
      latent, HTTP/WS)        latent
               │                │
               └───────┬────────┘
                       ▼
              edge S1 -> discrete action
                       │
                       ▼
        chassis/move (forward_m / rotate_deg)
```

1. **Observe** — latest RGB, depth, camera intrinsics and pose. Latest-wins:
   a VLN step must act on the freshest frame, so frames are never queued.
2. **Decide** — the key-latent switcher compares the current visual feature
   against the one the active latent was produced from. Similarity below the
   adaptive threshold (or `k_max` steps of reuse) triggers a cloud request.
3. **Synchronize (conditionally)** — the cloud request is issued with a
   predicted deadline. A response that arrives late is not discarded: it lands
   in a pending slot and is promoted at a safe step boundary.
4. **Act** — edge S1 produces R2R-CE discrete actions, mapped 1:1 onto bounded
   chassis motions:

   | action         | MoveCommand                     |
   |----------------|---------------------------------|
   | `MOVE_FORWARD` | `forward_m = +step_size_m`      |
   | `TURN_LEFT`    | `rotate_deg = +turn_angle_deg`  |
   | `TURN_RIGHT`   | `rotate_deg = -turn_angle_deg`  |
   | `STOP`         | no command; run ends SUCCEEDED  |

   `chassis/move` is burst-style and blocks for the motion's duration, which is
   what makes the observe → infer → move → observe loop well defined without
   this skill timing anything itself.
5. **Loop** — until STOP, `max_steps`, `timeout_s`, cancel, or an error.

An S1 inference may return several actions at once. By default the whole chunk
executes before re-observing; set `config.action_steps_to_execute = 1` for the
tightest closed loop at the cost of more inference round-trips.

## What this skill does NOT do

- No mapping, no global path planning, no obstacle avoidance. It issues
  bounded motion increments; whatever safety the chassis primitive enforces
  (watchdog, limits, e-stop) is the only guard.
- No goal verification — `SUCCEEDED` is the policy's own STOP prediction.
- No arrival recovery. If the policy gets lost it will keep acting until a
  limit is reached.
- No cloud S2 discovery through Atlas (see below).

## Dependencies

**Atlas-routed** — all resolved in `on_activate`; missing any of them keeps the
skill `Deferred` rather than failing:

| contract                              | transport | purpose                              |
|---------------------------------------|-----------|--------------------------------------|
| `robonix/primitive/camera/rgb`         | ROS 2     | observation                           |
| `robonix/primitive/camera/depth`       | ROS 2     | observation                           |
| `robonix/primitive/camera/intrinsics`  | ROS 2     | projection geometry                   |
| `robonix/primitive/chassis/odom`       | ROS 2     | pose (or `service/map/pose`, see config) |
| `robonix/primitive/chassis/move`       | gRPC      | action output                         |

Set `camera_provider_id` / `chassis_provider_id` when a deployment offers more
than one candidate for a contract.

**Not Atlas-routed** — the cloud System-2 process. It runs on a separate
GPU host outside this robot deployment, started with
`robonix-compute-cloud --mode <mock|internnav> --port <port>` from this same
repository, and is addressed by `cloud_host` / `cloud_port` config. It is
therefore not a Robonix package and does not appear in the capability graph —
the same boundary `skill-vla-openvla-rbnx` draws around its VLA server.

For `mode: internnav` the deployment host additionally needs InternNav
importable, a CUDA device, and the InternVLA-N1 checkpoints under `model_dir`
and `s1_model_dir`.

## Config

Full field documentation lives in [config.spec](config.spec). The values most
likely to need changing per deployment:

| key                       | default                 | meaning                                                                    |
|---------------------------|-------------------------|----------------------------------------------------------------------------|
| `mode`                    | `mock`                  | `mock` (no checkpoints, contract smoke test), `websocket`, `internnav`.     |
| `cloud_host` / `cloud_port` | `127.0.0.1` / `8765`  | Cloud S2 endpoint.                                                          |
| `step_size_m`             | `0.25`                  | Metres per `MOVE_FORWARD`. Must match the checkpoint's action semantics.     |
| `turn_angle_deg`          | `15.0`                  | Degrees per turn. Must match the checkpoint's action semantics.              |
| `max_steps` / `timeout_s` | `500` / `300.0`         | Per-run limits; a request may override both.                                |
| `action_steps_to_execute` | `0` (whole chunk)       | Set `1` for the tightest closed loop.                                       |
| `use_map_pose`            | `false`                 | `true` swaps chassis odom for SLAM-corrected `service/map/pose`.             |

`mode: mock` is the recommended first boot: it exercises every contract,
lifecycle transition and the whole action path on CPU with no checkpoints and
no cloud GPU. Its actions are meaningless by construction, so expect the run
to end quickly — that verifies wiring, not navigation quality.
