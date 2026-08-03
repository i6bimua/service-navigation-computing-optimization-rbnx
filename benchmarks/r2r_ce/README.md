# R2R-CE Benchmark

This directory contains the lightweight, reviewable benchmark record used by
the project README. It does not contain model weights, datasets, or raw
full-split logs.

## Scope

- Benchmark: R2R-CE `val-unseen`
- Episodes: 1,839
- Backbone: InternVLA-N1 DualVLN with original weights
- Cloud: NVIDIA A100
- Edge: NVIDIA AGX Jetson Orin or Thor in `MAX_N`
- Strategies: Edge Only, Cloud Only, Naive ECC, Step Sync, and Navigation Computing

The CSV files are the structured source for the project benchmark summary. See
`metadata.yaml` for the complete evaluation conditions.

## Results

- `results/main_results.csv`: main accuracy, latency, and memory comparison
- `results/ablation_orin_a100.csv`: switching and missing-handling ablation
- `results/runtime_overhead.csv`: runtime state and buffer overhead

Regenerate the README result figure after changing a result file:

```bash
python benchmarks/r2r_ce/render_results.py
```

## Reproduction Entry

After preparing InternNav, InternVLA-N1, Habitat, and R2R-CE, run:

```bash
robonix-compute-preflight --strict \
  --internnav-root "$INTERNNAV_ROOT" \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --checkpoint-path "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-path "$ROBONIX_COMPUTE_S1_MODEL_DIR"

bash scripts/run_habitat_eval.sh
```

The public repository provides the runtime and evaluation entry points. Exact
reproduction requires the licensed Matterport3D/R2R assets and the documented
cloud/edge hardware.
