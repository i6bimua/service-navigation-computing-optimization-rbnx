# Runtime config accepted by the compute-optimization skill.
#
# This file documents the mapping passed as this package's `config:` value in
# a deployment `robonix_manifest.yaml`, delivered to on_init through
# Driver(CMD_INIT, config_json). It is documentation only — the provider does
# not load this file. An empty `config: {}` uses every default below, which
# yields the CPU-only `mock` backend (useful for verifying the contract
# surface without checkpoints or a cloud GPU).
#
# Defaults mirror configs/defaults/robonix_compute.yaml; see README.md for
# how each switching parameter affects the latency/accuracy trade-off.

config:
  # ── Compute backend ────────────────────────────────────────────────────
  # string enum, default: mock. Selects how S1 and S2 are realised.
  #   mock      — in-process stub S1/S2, no checkpoints, no network. The
  #               contract surface is fully exercised but actions are
  #               meaningless. Use this to smoke-test a deployment.
  #   websocket — real cloud S2 over the WebSocket transport, stub edge S1.
  #               Isolates transport behaviour from model behaviour.
  #   internnav — real InternVLA-N1 dual-system. Requires checkpoints under
  #               model_dir / s1_model_dir, a CUDA device, and InternNav
  #               importable. Anything missing makes CMD_INIT fail.
  mode: mock

  # ── Cloud System-2 endpoint ────────────────────────────────────────────
  # Edge and cloud are one system, not two packages: the cloud process is this
  # same runtime started with
  # `robonix-compute-cloud --mode <mock|internnav> --port <port>`, running on a
  # GPU host outside the robot deployment. The scheduling that spans the two
  # (when a fresh latent is worth paying for, how long to wait, what to do with
  # a late reply) lives on the edge, in this package.
  # string, default: 127.0.0.1. Ignored when mode=mock.
  cloud_host: 127.0.0.1
  # integer TCP port, default: 8765. Ignored when mode=mock.
  cloud_port: 8765

  # ── Checkpoints (mode=internnav only) ──────────────────────────────────
  # string path, default: checkpoints/InternVLA-N1. Cloud S2 weights.
  model_dir: checkpoints/InternVLA-N1
  # string path, default: checkpoints/InternVLA-N1-S1. Edge S1 weights.
  s1_model_dir: checkpoints/InternVLA-N1-S1
  # string, default: cuda:0. Torch device for edge S1 inference.
  device: cuda:0

  # ── Action mapping: policy action index -> chassis/move ────────────────
  # The policy emits R2R-CE discrete actions
  # (0=STOP, 1=MOVE_FORWARD, 2=TURN_LEFT, 3=TURN_RIGHT). Each non-STOP
  # action becomes one bounded robonix/primitive/chassis/move command:
  #   MOVE_FORWARD -> MoveCommand.forward_m  = +step_size_m
  #   TURN_LEFT    -> MoveCommand.rotate_deg = +turn_angle_deg  (CCW)
  #   TURN_RIGHT   -> MoveCommand.rotate_deg = -turn_angle_deg
  # These two values must match the training-time action semantics of the
  # checkpoint, otherwise the agent under- or over-shoots every step.
  # float metres, default: 0.25. Must be > 0.
  step_size_m: 0.25
  # float degrees, default: 15.0. Must be > 0.
  turn_angle_deg: 15.0
  # integer, default: 0. How many actions of one S1 inference chunk to
  # execute before re-observing. 0 = execute the whole chunk (fewest cloud
  # round-trips); 1 = tightest closed loop at the cost of more inference.
  action_steps_to_execute: 0
  # float seconds, default: 5.0. Per-command deadline for one chassis/move
  # RPC. A command that does not ack in time fails the run.
  move_timeout_s: 5.0

  # ── Run limits (per-request overrides win when non-zero) ───────────────
  # float seconds, default: 300.0. Wall-clock ceiling for one navigate run.
  timeout_s: 300.0
  # integer, default: 500. Control-step ceiling for one navigate run.
  max_steps: 500
  # float seconds, default: 10.0. How long on_activate waits for the first
  # RGB/depth frame before reporting the observation source unavailable.
  observation_timeout_s: 10.0

  # ── Dependency disambiguation ──────────────────────────────────────────
  # The skill resolves its inputs by contract id and expects exactly one
  # provider per contract. Set these when a deployment has more than one
  # candidate (two cameras, a real chassis plus a simulator) — an empty value
  # means "there is only one, find it". A wrong or absent value keeps
  # CMD_ACTIVATE in Deferred rather than binding the wrong device.
  # string provider instance name, default: "" (unique match required).
  camera_provider_id: ""
  # string provider instance name, default: "". Provides chassis/odom and
  # chassis/move.
  chassis_provider_id: ""
  # string provider instance name, default: "". Only consulted when
  # use_map_pose is true; provides service/map/pose.
  map_provider_id: ""

  # ── Pose source ────────────────────────────────────────────────────────
  # bool, default: false. When false the skill uses raw odom-frame pose from
  # robonix/primitive/chassis/odom. When true it prefers the SLAM-corrected
  # map-frame pose from robonix/service/map/pose, which is drift-free but
  # requires a mapping service in the deployment. VLN is relative-motion
  # driven, so odom is sufficient for short episodes.
  use_map_pose: false

  # ── Edge runtime: adaptive cloud-edge synchronization ──────────────────
  # bool, default: false. Reads as "the context buffer arrives pre-seeded".
  # Only the offline evaluation harness seeds it; a Robonix deployment does
  # not, so leaving this false lets the first step perform an initial
  # synchronization. Setting it true on this path makes step 0 raise
  # "No active latent available". Change it only if you also seed the buffer.
  require_initial_latent: false
  # float seconds, default: 0.4. Deadline for the very first cloud request,
  # before any RTT samples exist to predict from.
  initial_timeout_s: 0.4
  # float seconds, default: 0.05. Floor for the predicted timeout.
  min_timeout_s: 0.05
  # float seconds, default: 10.0. Ceiling for the predicted timeout.
  max_timeout_s: 10.0
  # integer, default: 32. RTT samples kept for timeout prediction.
  timeout_window_size: 32

  # ── Key-latent switching thresholds ────────────────────────────────────
  # Visual-similarity thresholds deciding when a step is "key" enough to
  # justify a fresh cloud latent. Lower tau => synchronize more often =>
  # fresher context, higher latency. See README "What the Skill Optimizes".
  # float in (0,1), default: 0.50. Lower bound on the adaptive threshold.
  tau_lower: 0.50
  # float in (0,1), default: 0.80. Upper bound on the adaptive threshold.
  tau_upper: 0.80
  # float in (0,1), default: 0.80. Starting threshold for a fresh episode.
  tau_initial: 0.80
  # float, default: 0.015. Per-step threshold adjustment magnitude.
  delta: 0.015
  # integer, default: 8. Max steps a latent may be reused before a
  # synchronization is forced regardless of similarity.
  k_max: 8
  # float, default: 0.05. Learning rate for the threshold controller.
  eta: 0.05
