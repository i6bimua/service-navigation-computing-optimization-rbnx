# Changelog

All notable public changes are recorded here.

## 0.2.0 — 2026-07-25

### Added

- publishable RoboNix skill package `robonix.skill.compute_optimization`:
  root `package_manifest.yaml`, `config.spec`, `CAPABILITY.md`, and
  `scripts/{build,start,stop}.sh`;
- five capability contracts under `capabilities/` with their ROS 2 IDL —
  `robonix/skill/compute_optimization/driver`, `.../navigate`, `.../navigate/status`,
  `.../navigate/cancel`, and `robonix/skill/compute_optimization/telemetry`;
- Atlas-registered provider (`robonix_compute.rbnx.provider`) exposing the four
  MCP tools, with lazy activation: `CMD_INIT` validates config only, and
  checkpoints and the cloud link are acquired on the first call;
- discrete R2R-CE action to `robonix/primitive/chassis/move` mapping
  (`forward_m` / `rotate_deg`), so the policy binds to any chassis primitive
  offering the standard contract;
- observation input built from `robonix/primitive/camera/{rgb,depth,intrinsics}`
  plus `robonix/primitive/chassis/odom` or `robonix/service/map/pose`;
- `tests/harness/`: a synthetic body (`mock_robot`) plus a local deployment
  manifest, so `rbnx boot` and a full skill round-trip can be verified with no
  simulator, no checkpoints and no GPU. It exercises wiring only — its frames
  carry no semantic content and it is not a published package;
- per-run telemetry contract reporting synchronization, timeout, latent-reuse
  and latency counters.

### Changed

- the RoboNix integration boundary: the repository is now a RoboNix package
  rather than an external HTTP process. The HTTP lifecycle API is retained for
  non-RoboNix orchestrators, and both boundaries wrap the same `EdgeRuntime`;
- the README now states the two execution paths explicitly. The benchmark path
  is unchanged: the InternNav evaluator keeps ownership of the Habitat episode
  loop and the SR/SPL metrics, and this repository only supplies the compute
  runtime that plugs into it. The new skill path is separate and is what runs on
  a RoboNix robot;
- `scripts/release_audit.py` now requires the RoboNix package surface (and that
  the shell entry points stay executable) instead of forbidding it.

### Fixed

- per-run telemetry no longer absorbs a later run's steps: the recorder is
  cumulative across `EdgeRuntime.reset()`, so runs now carry a half-open slice.

## 0.1.0 — 2026-07-19

### Added

- adaptive dual-system compute runtime with cloud S2 and edge S1 execution;
- key-latent-aware asynchronous/synchronous switching;
- active/pending context buffering and adaptive timeout handling;
- InternVLA-N1 DualVLN and InternNav adapters;
- mock, WebSocket, HTTP Skill, and Habitat evaluation paths;
- structured R2R-CE result package and generated benchmark overview;
- strict model support matrix and bilingual documentation;
- GitHub Actions, documentation checks, release audit, and contribution policy.

### Validation

- R2R-CE benchmark metadata records the 1,839-episode DualVLN setup;
- Orin+A100 benchmark result records 2.22× latency speedup over Edge Only;
- CPU test suite covers runtime, protocol, adapters, Skill API, and metadata;
- GPU/InternNav/Habitat checks remain explicit local or self-hosted steps.

### Boundaries

- raw full-split logs, model weights, and licensed datasets are not
  included;
- the HTTP process is an external Skill boundary, not a RoboNix core Driver;
- only InternVLA-N1 DualVLN is listed as an end-to-end validated model.
