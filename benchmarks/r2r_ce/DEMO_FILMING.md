# Habitat demo filming guide

This is the filming script for a **side-by-side** Habitat demo of
`robonix.service.navigation.computing_optimization`. The public README links here; the automation
lives under `scripts/demo/` and `benchmarks/r2r_ce/demo_episodes.yaml`.

## Goal

Viewers should see, without reading the Orin tables:

1. **We finish** — Ours reaches `SUCCEEDED` while Naive ECC fails or times out
   under the same injected network delay.
2. **We are faster** — when both succeed, Ours has a shorter wall clock and a
   lower on-screen step latency.

All HUD numbers must come from **that run's** telemetry. The Orin+A100 CSV in
the README remains the project-wide summary and must be labeled as such.

## Fairness rules

| Keep identical | May change |
| --- | --- |
| Episode id, scene, instruction text | Strategy (`naive_ecc` / `ours` / optional `edge_only`) |
| Camera / top-down layout from InternNav | Injected RTT (`--rtt-delay-ms`, default 200) |
| Checkpoint pair (full N1 + S1-only) | Output directory |

Do **not** compare different instructions, different maps, or a mock backend.

## Lanes

| Id | Meaning |
| --- | --- |
| `naive_ecc` | Fixed-period manifest with period ≫ episode length: almost never refreshes S2 after the first latent. |
| `ours` | Online switcher used by this service (no pinned fixed-period manifest). |
| `edge_only` | Optional. Full dual-system on the edge via InternNav `habitat_dual_system_cfg.py` — record separately and drop the mp4 into `outputs/demo_comparison/edge_only/`. |

## Episode list

Start from `benchmarks/r2r_ce/demo_episodes.yaml` (8 episodes, including the
existing public clip **206**). Prefer:

- ≥4 episodes where both succeed and Ours is clearly faster;
- ≥2 episodes where Ours succeeds and Naive ECC fails;
- at least one long multi-turn route (e.g. 100, 206).

Mark roles in the YAML (`hero` / `short` / `mid` / `long`) as you shoot so the
edit can pick a short reel without re-watching everything.

## Record

```bash
export INTERNNAV_ROOT=/path/to/InternNav
export ROBONIX_COMPUTE_DATA_ROOT=/path/to/data   # vln_ce/, scene_data/
export ROBONIX_COMPUTE_MODEL_DIR=/path/to/InternVLA-N1
export ROBONIX_COMPUTE_S1_MODEL_DIR=/path/to/InternVLA-N1-S1
export PYTHON_BIN=/path/to/conda/envs/habitat/bin/python

# Smoke one episode first (recommended).
bash scripts/demo/run_comparison.sh \
  --episodes 206 \
  --strategies naive_ecc,ours \
  --rtt-delay-ms 200 \
  --output-dir outputs/demo_comparison

# Full curated list.
bash scripts/demo/run_comparison.sh \
  --episodes-file benchmarks/r2r_ce/demo_episodes.yaml \
  --strategies naive_ecc,ours \
  --rtt-delay-ms 200 \
  --output-dir outputs/demo_comparison
```

`ANALYSIS_SAVE_VIDEO=1` is set by the script. InternNav's
`measure_edge_cloud_s2_runtime.py` must **not** overwrite that to `"0"` (older
checkouts hard-coded it). If videos are missing after a run, change that line
to `os.environ.get("ANALYSIS_SAVE_VIDEO", "0")` and re-record. Mp4 files land
under each lane's `eval/vis_*/<scene>/` tree (the analysis evaluator also saves
failures).

## Compose

Telemetry-faithful side-by-side (one episode):

```bash
$PYTHON_BIN scripts/demo/compose_side_by_side.py \
  --run-dir outputs/demo_comparison \
  --episode 206 \
  --out outputs/demo_comparison/habitat_comparison.mp4
```

Public result reels keep only two representative Habitat comparisons:

```bash
# We succeed · they hang
python3 scripts/demo/make_demo_reels.py --mode fail \
  --left  outputs/demo_comparison_v2/naive_ecc/eval/vis_0/Z6MFQCViBuw/0206.mp4 \
  --right outputs/demo_comparison_v2/ours/eval/vis_0/Z6MFQCViBuw/0206.mp4 \
  --out docs/assets/demo/habitat_comparison_fail.mp4

# Both succeed · we finish first
python3 scripts/demo/make_demo_reels.py --mode speed \
  --left  outputs/demo_comparison_v2/edge_only/eval/vis_0/Z6MFQCViBuw/0206.mp4 \
  --right outputs/demo_comparison_v2/ours/eval/vis_0/Z6MFQCViBuw/0206.mp4 \
  --out docs/assets/demo/habitat_comparison_speed.mp4
```

HUD fields burned into each half:

- lane label (`A · Naive ECC` / `B · Ours (VLN service)`)
- step count · mean step latency · elapsed seconds
- terminal badge: green `SUCCEEDED` / red `TIMEOUT`|`FAIL`

Editing rules:

- For paper-faithful cuts: keep decision and motion at **1×**; only idle tails may be 2×.
- For public result clips, keep Ours at normal speed. Any added baseline delay
  must appear only at planning boundaries, not during motion, and the on-screen
  label must distinguish measured benchmark speedup from a presentation target.
- Freeze the final frame ~2 s with the badge visible.
- Do not add a static opening card; begin with the live run.

## What “good” looks like for the public reel

Ship three assets under `docs/assets/demo/`:

1. `habitat_comparison_fail.mp4` (+ `.gif` preview) — baseline hangs, ours succeeds.
2. `habitat_comparison_speed.mp4` (+ `.gif`) — Edge Only and Ours both succeed,
   Ours finishes first.
3. `robonix_tui_demo.mp4` (+ `.jpg` poster) — complete `rbnx chat` startup,
   instruction, tool call, polling, and terminal state.

## Workstation notes

A known-good layout on a dual-GPU machine (one GPU per lane role):

```bash
export INTERNNAV_ROOT="${WORKSPACE}/InternNav"
export ROBONIX_COMPUTE_DATA_ROOT="${VLN_ASSETS}/InternNav/data"
export ROBONIX_COMPUTE_MODEL_DIR="${VLN_ASSETS}/InternNav/checkpoints/InternVLA-N1"
export ROBONIX_COMPUTE_S1_MODEL_DIR="${VLN_ASSETS}/InternNav/checkpoints/InternVLA-N1-s1-only"
export PYTHON_BIN="${CONDA_PREFIX}/bin/python"   # env with Habitat + InternNav + imageio
export ROBONIX_COMPUTE_CLOUD_GPU_ID=1
export ROBONIX_COMPUTE_EDGE_GPU_ID=0
```

Expect tens of minutes per episode per lane with real InternVLA-N1 weights.
