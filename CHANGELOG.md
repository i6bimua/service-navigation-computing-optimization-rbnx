# Changelog

All notable public changes are recorded here.

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
