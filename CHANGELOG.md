# Changelog

All notable public changes are recorded here.

## 0.3.1 — 2026-07-27

Documentation and reference-deployment only; no contract or runtime change.

### Changed

- this service is the only entry point a deployment needs for natural-language
  navigation, and the docs now say so. `CAPABILITY.md`, both READMEs and the
  `package_manifest.yaml` header previously told LLM callers to prefer a thin
  `robonix.skill.navigation.vln` wrapper deployed alongside; that advice was
  wrong on two counts. Pilot discovers the MCP contracts a service exposes
  directly — provider kind changes the capability-document guidance, not
  visibility — and `navigate`, `navigate/status` and `navigate/cancel` form an
  async contract group whose polling lifecycle the executor already owns, so a
  caller never had to sequence start → poll → cancel itself. Deploying a
  forwarding wrapper next to the service therefore published two equivalent
  copies of the same four operations instead of a service/skill boundary.
  Established in review on
  [syswonder/robonix-package-catalog#9](https://github.com/syswonder/robonix-package-catalog/pull/9);
  the catalog carries this service alone.
- `tests/harness/deployment/robonix_manifest.yaml` drops its `skill:` section
  and deploys the service by itself, which is the shape a real deployment
  should copy.
- corrected a claim in the 0.3.0 notes below: it said `rbnx` and the executor
  both gate just-in-time activation on a `robonix/skill` namespace. Only the
  executor keys on the namespace; `rbnx boot` keys on which manifest section an
  instance is declared in. The conclusion — a service is activated during boot,
  so `on_activate` must not load the model — is unaffected.

## 0.3.0 — 2026-07-27

Re-published as a **service**. Every contract id changed, so this release is not
drop-in for a 0.2.0 deployment manifest.

### Changed — BREAKING

- package renamed `robonix.skill.compute_optimization` -> `robonix.service.navigation.vln`,
  and the provider is now `Service(id="navigation_vln", …)` under
  `robonix/service/navigation/vln`. Contract ids move with it:

  | 0.2.0 | 0.3.0 |
  | --- | --- |
  | `robonix/skill/compute_optimization/driver` | `robonix/service/navigation/vln/driver` |
  | `robonix/skill/compute_optimization/navigate` | `robonix/service/navigation/vln/navigate` |
  | `robonix/skill/compute_optimization/navigate/status` | `robonix/service/navigation/vln/navigate/status` |
  | `robonix/skill/compute_optimization/navigate/cancel` | `robonix/service/navigation/vln/navigate/cancel` |
  | `robonix/skill/compute_optimization/telemetry` | `robonix/service/navigation/vln/telemetry` |

  Requested in review on the package-catalog submission
  ([syswonder/robonix-package-catalog#9](https://github.com/syswonder/robonix-package-catalog/pull/9)):
  `compute_optimization` names an implementation technique rather than the
  capability a user deploys, and baking it into every public contract id makes
  the mechanism permanent. The taxonomy also matched: a skill in this catalog
  sequences other services — `skill-explore-rbnx` consumes
  `robonix/service/navigation/*` and never touches a chassis — whereas this
  package owns a long-running runtime and drives `chassis/move` directly. That
  is the service boundary, and it makes this the instruction-following sibling
  of `robonix.service.navigation` (Nav2), whose goal is a metric `PoseStamped`.

  compute-optimization, dual-system and cloud-edge remain where they describe the
  mechanism: tags, telemetry field names, the `robonix_compute` module tree, the
  `robonix-compute-*` commands and this documentation.

- deployment manifests must move the instance from the `skill:` section to
  `service:`, and rename it to `navigation_vln` to match `Service(id=…)`.
- `capabilities/lib/compute_optimization/srv/` -> `capabilities/lib/navigation_vln/srv/`,
  so codegen now emits `navigation_vln_mcp` instead of `compute_optimization_mcp`.
- `on_activate` no longer builds the compute runtime. A service is activated
  during `rbnx boot` rather than on its first call, so loading the
  checkpoints there would have made GPU memory and a reachable cloud host
  boot-time requirements of every deployment that merely lists this package, and
  a cloud outage would fail the boot rather than the call. `on_activate` now
  binds the camera / pose / chassis contracts only; the runtime is acquired by
  `_ensure_compute_ready()` on the first `navigate`, which reports a setup
  failure as `accepted=false` with a diagnosis. `status`, `cancel` and
  `telemetry` deliberately do not trigger it — the executor polls status
  immediately after a rejected navigate.
- `on_deactivate` now also releases the wiring. Its early-return guard tested
  `active` alone, which after boot (wired, not yet active) would have skipped
  destroying the subscriptions and closing the chassis channel.
- pip distribution renamed `robonix-compute-optimization-skill` ->
  `robonix-compute-optimization`; the import package `robonix_compute` is
  unchanged.
- repository renamed `skill-compute-optimization-rbnx` ->
  `service-navigation-vln-rbnx`. Every self-referencing URL moved with it: the
  CI badge, the clone commands, `pyproject.toml`'s project URLs, `CITATION.cff`
  and the BibTeX entry. GitHub redirects the old path, so existing `url:` entries
  keep resolving, but the catalog entry names the new one.
- documentation no longer calls this package a skill: the README title, the
  `CITATION.cff` / BibTeX title and the `robonix_compute` docstrings drop the
  `-Skill` suffix from the project name, and "What the Skill Optimizes" is now
  "What the Runtime Optimizes". The benchmark method label `Compute Skill` is
  deliberately unchanged — it is published data, appearing in
  `benchmarks/r2r_ce/results/*.csv`, `metadata.yaml`, `render_results.py` and the
  generated figures, and renaming it would desynchronize the tables from the
  artifacts they cite.

### Removed

- `[semantics] user_invocable` from the contract TOMLs. The string appears
  nowhere in the robonix source tree: pilot lists every provider shipping a
  non-empty `CAPABILITY.md` and tags those whose kind is `skill`; it never reads
  the flag. The test that asserted it has been replaced by one that checks every
  declared capability is actually registered by a `@service.mcp` handler — a
  contract declared on atlas but unserved fails only after a consumer connects.

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
