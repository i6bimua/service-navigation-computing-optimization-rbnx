# Supported Models

Support status is evidence-based. A model is “validated” only when this
repository contains its adapter, a smoke test, real run instructions, and
benchmark metadata.

## Validated

### InternVLA-N1 DualVLN

- Checkpoint: `InternRobotics/InternVLA-N1-DualVLN`
- Framework: InternNav
- Split: cloud S2 semantic latent generation and edge S1 action generation
- Observation: RGB, depth, pose/GPS, camera intrinsics, and instruction
- Evaluation: Habitat / VLN-CE, R2R-CE `val-unseen`
- Cloud hardware: NVIDIA A100
- Edge hardware: NVIDIA AGX Jetson Orin and Thor

The cloud payload includes the semantic trajectory latent and the observation
frame associated with it. The edge combines that context with the current
observation before S1 inference.

### Mock Backend

The mock S1/S2 backend validates the public runtime contract without heavyweight
dependencies:

```bash
bash scripts/run_mock_compute.sh --steps 5
```

It covers request/response serialization, context buffering, synchronization,
timeout behavior, late responses, reuse, and telemetry. It does not measure
navigation quality.

## Adapter API

The Python package exposes two boundaries:

```python
from robonix_compute.model_adapters import (
    InternNavS1Adapter,
    InternNavS2Adapter,
)
```

An S1 runner supplies either:

```python
action = runner.act(observation, latent)
```

or the InternNav-style contract:

```python
result = runner.step(
    images_dp=images_dp,
    depths_dp=depths_dp,
    latent=traj_latent,
    initial_latents=initial_latents,
)
```

An S2 runner supplies either:

```python
payload = runner.generate_latent(observation, instruction)
```

or an InternNav-style `step` method that returns a semantic latent and optional
memory frame.

The adapter contract makes another dual-system model integrable; it does not
make that model validated or benchmark-supported.

## Not Listed as Supported

OpenVLA, π0, π0.5, π0-FAST, StreamVLN, and other VLA/VLN families are not
currently listed as supported models. The repository contains no complete
public adapter-and-benchmark path for them.

## Adding a Model

A pull request that adds a supported model must include:

1. S1 and S2 adapter implementations;
2. serialization tests for the model payload;
3. a no-GPU or lightweight contract smoke test;
4. real installation, checkpoint, and launch instructions;
5. benchmark metadata with model revision, hardware, task split, and metrics;
6. an update to both language versions of the README.
