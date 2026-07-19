# Benchmarks

## Main R2R-CE Result

The project benchmark covers the complete R2R-CE `val-unseen` split:

- 1,839 episodes
- InternVLA-N1 DualVLN with original weights
- NVIDIA A100 cloud GPU
- NVIDIA AGX Jetson Orin or Thor edge device in `MAX_N`
- Navigation metrics: NE, SR, SPL
- System metrics: average step latency and peak edge/cloud memory

Structured data and metadata are stored in
[`benchmarks/r2r_ce`](../benchmarks/r2r_ce/README.md).

## Orin + A100

| Method | NE ↓ | SR ↑ | SPL ↑ | Latency ↓ | Edge Memory | Cloud Memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edge Only | 4.11 | 62.9 | 57.6 | 497.8 ms | 16.63 GB | — |
| Cloud Only | 4.05 | 64.3 | 58.5 | 128.4 ms | — | 16.62 GB |
| Naive ECC | 4.76 | 56.7 | 45.1 | 202.0 ms | 0.60 GB | 16.03 GB |
| Step Sync | 4.13 | 65.2 | 58.1 | 1644.5 ms | 0.60 GB | 16.03 GB |
| Compute Skill | 4.18 | 62.8 | 57.8 | 224.4 ms | 0.60 GB | 16.03 GB |

## Thor + A100

| Method | NE ↓ | SR ↑ | SPL ↑ | Latency ↓ | Edge Memory | Cloud Memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edge Only | 4.09 | 63.1 | 57.8 | 265.1 ms | 16.63 GB | — |
| Cloud Only | 4.05 | 64.3 | 58.5 | 128.4 ms | — | 16.62 GB |
| Naive ECC | 4.72 | 56.7 | 45.4 | 164.6 ms | 0.60 GB | 16.03 GB |
| Step Sync | 4.15 | 64.1 | 57.2 | 1328.0 ms | 0.60 GB | 16.03 GB |
| Compute Skill | 4.14 | 63.1 | 58.1 | 166.3 ms | 0.60 GB | 16.03 GB |

Cloud Only is a compute upper bound and does not represent an edge-owned local
control loop. Step Sync recovers fresh-latent accuracy but incurs a large
blocking cost. Compute Skill targets the accuracy-latency trade-off.

## Analysis Evidence

The benchmark analysis also shows:

- cloud-edge placement increases mean latent misalignment from 1.2 to 3.8
  steps in the analyzed trajectories;
- the top 10% key steps account for 77.6% of the SR gain under step injection;
- visual similarity has a Pearson correlation of `r=0.76` with navigation
  error;
- a threshold setting that synchronizes 17% of steps recovers 73% of the
  observed gain.

These analysis values explain the event-driven synchronization design. They are
not additional rows in the main 1,839-episode comparison.

## Reproduce with the Public Runtime

Prepare the dependencies described in [Installation](installation.md), then:

```bash
robonix-compute-preflight \
  --internnav-root "$INTERNNAV_ROOT" \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --checkpoint-path "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-path "$ROBONIX_COMPUTE_S1_MODEL_DIR" \
  --cloud-gpu-id 1 \
  --edge-gpu-id 0 \
  --cloud-port 18765 \
  --strict

export ROBONIX_COMPUTE_HABITAT_EPISODES=1,2,3,4,5
export ROBONIX_COMPUTE_OUTPUT_DIR=outputs/habitat_eval_r2r_5eps
bash scripts/run_habitat_eval.sh
```

Remove the episode subset only when the complete split and required compute
time are available.

## Reporting Boundaries

- The committed main tables are the project benchmark summary.
- Raw full-split evaluation logs are not bundled.
- The repository contains runtime and evaluation entry points, not licensed
  Matterport3D or R2R data.
- Local two-GPU subset experiments are not used as headline results.
- Single-episode smoke runs are connectivity checks, not benchmark evidence.
- Delay injection is synthetic unless a specific network trace is named.

When publishing a new result, include model revision, split, episode count,
hardware, power mode, latency definition, network settings, and raw summary
artifacts.
