<div align="center">

# RoboNix Compute Optimization Skill

**An open-source compute optimization tool for RoboNix dual-system vision-language navigation**

[简体中文](README.zh-CN.md) ·
[🎬 Demo Video](#demo-video) ·
[🏆 Benchmark Results](#benchmark-results) ·
[Running Images](#running-images) ·
[Quick Start](#quick-start)

[![CI](https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill/actions/workflows/ci.yml/badge.svg)](https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill/actions/workflows/ci.yml)
<br>
[![Project metrics](docs/assets/result_badges.svg)](#benchmark-results)

</div>

<a id="demo-video"></a>
## 🎬 Demo Video

<div align="center">
  <a href="docs/assets/demo/habitat_demo.mp4">
    <img width="100%" src="docs/assets/demo/habitat_demo.gif" alt="Habitat runtime demo">
  </a>
</div>

RoboNix Compute Optimization Skill provides RoboNix with an external, measured
compute optimization module for dual-system VLN. Slow semantic reasoning runs
on a cloud GPU, while latency-sensitive action generation stays on the edge.
The tool combines asynchronous execution, key-latent synchronization,
active/pending context buffering, adaptive timeout handling, and per-step
telemetry.

**Project boundary.** This repository provides the compute runtime, model
adapters, evaluation entry points, and an external HTTP Skill process. It does
not modify RoboNix core and does not claim an in-tree RoboNix Driver, capability
manifest, or Atlas registration.

<a id="table-of-contents"></a>
## 📚 Table of Contents

- [Demo Video](#demo-video)
- [News](#news)
- [Results](#results)
- [What the Skill Optimizes](#what-the-skill-optimizes)
- [Architecture](#architecture)
- [Running Images](#running-images)
- [RoboNix Integration Boundary](#robonix-integration-boundary)
- [Validated Scope](#validated-scope)
- [Supported Models and Platforms](#supported-models-and-platforms)
- [Quick Start](#quick-start)
- [Requirements](#requirements)
- [Checkpoint Sources](#checkpoint-sources)
- [Step 1: Installation](#step-1-installation)
- [Step 2: Checkpoint Preparation](#step-2-checkpoint-preparation)
- [Step 3: Dataset Preparation](#dataset-preparation)
- [Step 4: Strict Preflight](#step-4-strict-preflight)
- [Step 5: Cloud and Edge Runtime](#step-5-cloud-and-edge-runtime)
- [Step 6: HTTP Skill API](#step-6-http-skill-api)
- [Benchmark Reproduction](#benchmark-reproduction)
- [Benchmark Results](#benchmark-results)
- [Additional Dataset Readiness](#additional-dataset-readiness)
- [Service Ports and Environment Variables](#service-ports-and-environment-variables)
- [Repository Layout](#repository-layout)
- [Roadmap](#roadmap)
- [Troubleshooting](#troubleshooting)
- [Validation and Contribution](#validation-and-contribution)
- [Contributors](#contributors)
- [Citation](#citation)
- [License](#license)

<a id="news"></a>
## 📰 News

- **2026-07-19 — v0.1.0:** Released the public runtime, InternVLA-N1 DualVLN
  adapter, HTTP Skill boundary, structured R2R-CE result package, licensed-data
  gate, strict model/environment preflight, and bilingual reproduction guide.

<a id="results"></a>
## ⚡ Results

| System-level result | Current value | Structured source |
| --- | --- | --- |
| Main benchmark | R2R-CE `val-unseen`, **1,839 episodes** | `benchmarks/r2r_ce/metadata.yaml` |
| Orin+A100 step latency | **2.22× faster** than Edge Only, `497.8 → 224.4 ms` | `benchmarks/r2r_ce/results/main_results.csv` |
| Quality recovery over Naive ECC | **+6.1 SR**, **+12.7 SPL** | `benchmarks/r2r_ce/results/main_results.csv` |
| Edge model memory | **0.60 GB**, versus **16.63 GB** for Edge Only | `benchmarks/r2r_ce/results/main_results.csv` |
| Runtime control state | **< 8 KB** for pending latent and counters | `benchmarks/r2r_ce/results/runtime_overhead.csv` |

<div align="center">
  <img width="100%" src="docs/assets/benchmark_overview.svg" alt="R2R-CE benchmark overview">
</div>

Compute Skill optimizes the latency–accuracy trade-off rather than a single
metric. On Orin+A100, SR remains on par with Edge Only (`62.8` versus `62.9`)
while average step latency falls by 2.22×. Step Sync reaches a higher SR but
blocks for `1644.5 ms` per step. The complete Orin and Thor tables are included
in [Benchmark Results](#benchmark-results).

### Start by goal

| Goal | Entry point | Required resources |
| --- | --- | --- |
| Verify the runtime contract | `bash scripts/run_mock_compute.sh --steps 5` | CPU only; about one minute after installation |
| Check real-model readiness | `robonix-compute-preflight ... --strict` | InternNav, Habitat, checkpoints, data, and free GPUs |
| Reproduce a navigation run | `bash scripts/run_habitat_eval.sh` | Prepared R2R-CE/MP3D-CE environment |
| Integrate an orchestrator | `robonix-compute-skill --port 8090 ...` | External client calling the HTTP lifecycle API |

<a id="what-the-skill-optimizes"></a>
## 🧩 What the Skill Optimizes

| Mechanism | Runtime effect |
| --- | --- |
| **Asynchronous execution** | S1 keeps the edge control loop moving while cloud S2 refreshes semantic context. |
| **Key-latent synchronization** | High-value steps can request a fresh latent instead of synchronizing every step. |
| **Active/pending context buffer** | Late responses enter a pending slot and are promoted only at a controlled boundary. |
| **Adaptive missing handling** | Timeout prediction and latent reuse bound network stalls without discarding delayed responses. |
| **Measured operation** | Per-step telemetry records S1/S2 latency, synchronization, timeout, reuse, payload size, and navigation metrics. |

Naive asynchronous execution is fast but may repeatedly consume stale semantic
context. Full step synchronization keeps context fresh but blocks the control
loop. Compute Skill switches between these behaviors according to runtime
state.

<a id="architecture"></a>
## 🧠 Architecture

<div align="center">
  <img width="100%" src="docs/assets/compute_optimization_architecture.png" alt="Compute optimization runtime architecture">
</div>

1. **Fast local loop:** edge S1 consumes the latest observation and active
   context to produce the next action without waiting for cloud inference.
2. **On-demand semantic refresh:** adaptive sync requests cloud S2 only when a
   fresh latent is needed; responses enter the pending context before safe
   promotion.
3. **Measured execution:** telemetry records latency, synchronization, timeout,
   context reuse, payload size, and navigation metrics.

Model-specific fields such as trajectory latents, pixel goals, observation
memory, and optional diffusion latents remain behind adapters.

<a id="running-images"></a>
## 🖼️ Running Images

<div align="center">
  <img width="100%" src="docs/assets/demo/habitat_running_images.png" alt="Habitat running images and navigation path">
</div>

<a id="robonix-integration-boundary"></a>
## 🔌 RoboNix Integration Boundary

| Delivered here | Boundary |
| --- | --- |
| Cloud S2 and edge S1 processes | Run independently from RoboNix core |
| HTTP lifecycle API | `/health`, `/setup`, `/reset`, `/step`, `/telemetry`, `/close` |
| WebSocket cloud–edge transport | Example transport, not production authentication or encryption |
| InternNav adapters | Upstream model APIs do not enter RoboNix core |
| Runtime telemetry | Exposed through files and the HTTP telemetry endpoint |

<a id="validated-scope"></a>
## 🧪 Validated Scope

The following table distinguishes an executable path from data-only readiness
and from a published result summary.

**Status vocabulary:** ✅ executed means the path was run; data check means
licensed assets were parsed and validated but no model result is claimed.

| Item | Status | What was actually verified |
| --- | --- | --- |
| CPU mock runtime | ✅ Executed | Five steps, context reuse, switching, timeout logic, and telemetry output |
| Unit and integration tests | ✅ Executed | **35 passed**, 2 GPU/InternNav tests skipped outside the model environment |
| R2R-CE core data | ✅ Executed | 1,839 episodes; all 11 referenced scenes have `.glb` and `.navmesh` |
| Extended data profile | ✅ Data check | R2R short/medium/long, RxR English, and REVERIE navigation proxy parse successfully |
| Habitat scene loading | ✅ Executed | Habitat-Sim 0.2.4 loaded an MP3D-CE scene and navmesh |
| Real-model strict preflight | ✅ Executed | InternNav, Habitat, four model shards, S1 weights, depth checkpoint, data, ports, and two GPU IDs |
| R2R one-episode dual-GPU run | ✅ Connectivity smoke | Completed navigation and produced result, progress, runtime, and telemetry artifacts; not benchmark evidence |
| R2R-CE 1,839-episode tables | ✅ Project benchmark summary | Compact CSV and metadata are versioned; raw full-split logs are not bundled |
| R2R difficulty / RxR / REVERIE proxy results | Data preparation only | No Compute Skill full-split result is claimed |
| Original REVERIE task | Not supported | Different MatterSim/object-grounding stack; not presented as REVERIE-CE support |

Hosted CI covers the Python 3.9–3.11 test matrix, CPU quick start, CLI entry
points, documentation links, SVG XML, deterministic result rendering, package
builds, and the release audit. GPU/Habitat checks remain explicit local or
self-hosted operations.

<a id="supported-models-and-platforms"></a>
## 🧪 Supported Models and Platforms

| Component | Status | Validated scope |
| --- | --- | --- |
| InternVLA-N1 DualVLN | ✅ End-to-end | Cloud S2 + edge S1 |
| Mock S1/S2 backend | ✅ CI | Protocol, synchronization, timeout, reuse, and telemetry |
| NVIDIA A100 | ✅ Validated | Cloud S2 and local benchmark smoke |
| NVIDIA AGX Jetson Orin | ✅ Project benchmark | Edge S1 in `MAX_N` |
| NVIDIA AGX Jetson Thor | ✅ Project benchmark | Edge S1 in `MAX_N` |
| Habitat / VLN-CE / R2R-CE | ✅ Integrated | Local subset runner and structured full-split summary |
| Generic dual-system runner | 🔧 Adapter API | Requires model-specific adapters and benchmark evidence |

OpenVLA, π0, π0.5, π0-FAST, StreamVLN, and other model families are not
presented as supported. A new model requires S1/S2 adapters, serialization
tests, a lightweight contract test, real launch instructions, and benchmark
metadata.

<a id="quick-start"></a>
## 🚀 Quick Start

This complete CPU path requires no model weights, simulator data, or GPU:

```bash
git clone https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill.git
cd RoboNix-Compute-Optimization-Skill

conda create -n robonix-compute python=3.10 -y
conda activate robonix-compute
python3 -m pip install -U pip
python3 -m pip install -e ".[dev,websocket]"

bash scripts/run_mock_compute.sh --steps 5
```

Expected output:

```text
{
  "actions": [0, 0, 1, 1, 1],
  "telemetry": "outputs/mock_compute/telemetry.json"
}
```

This verifies the public runtime contract. It does **not** measure navigation
quality or reproduce the headline benchmark.

<details>
<summary><b>Run the mock cloud and edge as separate processes</b></summary>

```bash
# Terminal 1
robonix-compute-cloud --mode mock --host 0.0.0.0 --port 8765

# Terminal 2
robonix-compute-edge \
  --mode mock \
  --cloud-host 127.0.0.1 \
  --cloud-port 8765 \
  --steps 5
```

</details>

<a id="requirements"></a>
## ⚙️ Requirements

| Component | Requirement |
| --- | --- |
| Operating system | Linux |
| Python | 3.9–3.11; Python 3.10 recommended |
| Installation | Conda + pip; Docker is not required |
| Cloud | CUDA-capable GPU; A100 is the benchmarked platform |
| Edge | Orin/Thor for the reported split deployment; a second CUDA GPU can run local smoke |
| Model framework | InternNav |
| Simulator | Habitat-Lab / Habitat-Sim |
| Core benchmark | Matterport3D scene assets + R2R-CE annotations |

The validated local simulator environment uses:

| Package | Version |
| --- | --- |
| Habitat-Lab / Habitat-Baselines | 0.2.4 |
| Habitat-Sim | 0.2.4, headless Bullet build |
| InternNav | 0.3.x |
| Transformers | 4.51.0 |
| Diffusers | 0.32.2 |

PyTorch and FlashAttention must match the CUDA and device software stack.
Install the appropriate NVIDIA wheel on Orin/Thor instead of copying a server
wheel.

<a id="checkpoint-sources"></a>
## 📦 Checkpoint and Dataset Sources

| Asset | Official source | Expected placement |
| --- | --- | --- |
| InternVLA-N1 DualVLN | [Hugging Face](https://huggingface.co/InternRobotics/InternVLA-N1-DualVLN) | `checkpoints/InternVLA-N1/` |
| InternNav | [GitHub](https://github.com/InternRobotics/InternNav) | `$INTERNNAV_ROOT` |
| Depth Anything V2 Metric Hypersim Small | [Hugging Face](https://huggingface.co/depth-anything/Depth-Anything-V2-Metric-Hypersim-Small) | `checkpoints/depth_anything_v2_metric_hypersim_vits.pth` |
| Matterport3D | [Official access page](https://niessner.github.io/Matterport/) | `$ROBONIX_COMPUTE_DATA_ROOT/scene_data/mp3d_ce/mp3d/` |
| R2R-CE / VLN-CE | [Official VLN-CE repository](https://github.com/jacobkrantz/VLN-CE) | `$ROBONIX_COMPUTE_DATA_ROOT/vln_ce/raw_data/r2r/` |
| RxR annotations | [Official RxR repository](https://github.com/google-research-datasets/rxr) | Optional extended profile |

This repository does not redistribute model weights, Matterport3D assets,
R2R/RxR annotations, or populated raw evaluation logs. Obtain each external
asset under its original license.

<a id="step-1-installation"></a>
## 🧰 Step 1: Installation

### 1.1 Create the environment

```bash
git clone https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill.git
cd RoboNix-Compute-Optimization-Skill
export ROBONIX_COMPUTE_ROOT="$(pwd)"

conda create -n robonix-compute python=3.10 -y
conda activate robonix-compute
python3 -m pip install -U pip setuptools wheel packaging
```

### 1.2 Install PyTorch and this package

Install a PyTorch build compatible with the machine. The following is only a
CUDA-server example:

```bash
python3 -m pip install torch torchvision \
  --index-url https://download.pytorch.org/whl/cu128

python3 -m pip install -e ".[dev,websocket,download]"
```

### 1.3 Install InternNav and Habitat

```bash
git clone https://github.com/InternRobotics/InternNav.git "$HOME/InternNav"
export INTERNNAV_ROOT="$HOME/InternNav"

python3 -m pip install -e "$INTERNNAV_ROOT[habitat,internvla_n1]"
export PYTHONPATH="$INTERNNAV_ROOT:$INTERNNAV_ROOT/third_party/diffusion-policy:${PYTHONPATH:-}"
```

Install Habitat-Lab and Habitat-Sim with versions compatible with InternNav.
The validated combination is 0.2.4:

```bash
conda install -y habitat-sim=0.2.4 headless withbullet \
  -c conda-forge -c aihabitat
python3 -m pip install habitat-lab==0.2.4 habitat-baselines==0.2.4
```

Verify imports before downloading large assets:

```bash
python3 -c "import habitat, habitat_sim, internnav; print('imports OK', habitat.__version__)"
```

If the exact Habitat packages are unavailable for the target Python/CUDA
combination, create the environment using the matching InternNav/Habitat
release instead of mixing versions.

<a id="step-2-checkpoint-preparation"></a>
## 📥 Step 2: Checkpoint Preparation

### 2.1 Download the full model and depth checkpoint

```bash
cd "$ROBONIX_COMPUTE_ROOT"
robonix-compute-download --output checkpoints
```

Expected layout:

```text
checkpoints/
├── InternVLA-N1/
│   ├── config.json
│   ├── model.safetensors.index.json
│   └── model-00001-of-00004.safetensors ... model-00004-of-00004.safetensors
└── depth_anything_v2_metric_hypersim_vits.pth
```

### 2.2 Export the S1-only edge checkpoint

```bash
robonix-compute-export-s1 \
  --source checkpoints/InternVLA-N1 \
  --output checkpoints/InternVLA-N1-S1
```

Expected S1 layout:

```text
checkpoints/InternVLA-N1-S1/
├── config.json
├── internvla_n1_s1_only.json
└── model.safetensors
```

### 2.3 Set absolute paths

```bash
export ROBONIX_COMPUTE_MODEL_DIR="$ROBONIX_COMPUTE_ROOT/checkpoints/InternVLA-N1"
export ROBONIX_COMPUTE_S1_MODEL_DIR="$ROBONIX_COMPUTE_ROOT/checkpoints/InternVLA-N1-S1"
export ROBONIX_COMPUTE_DEPTH_CKPT="$ROBONIX_COMPUTE_ROOT/checkpoints/depth_anything_v2_metric_hypersim_vits.pth"
```

Do not point `ROBONIX_COMPUTE_S1_MODEL_DIR` at the full checkpoint merely to
make a path check pass. The strict preflight requires an actual
`model.safetensors` S1 export.

<a id="dataset-preparation"></a>
## 🗃️ Step 3: Dataset Preparation

### 3.1 Obtain licensed assets

1. Request Matterport3D access from the official Matterport3D page and accept
   its terms.
2. Obtain R2R-CE / VLN-CE annotations from the official VLN-CE source.
3. Convert or arrange the files into the layout below.
4. Add RxR and derived splits only when using the optional extended data
   profile.

### 3.2 Required core layout

```text
${ROBONIX_COMPUTE_DATA_ROOT}/
├── scene_data/
│   └── mp3d_ce/
│       └── mp3d/
│           └── <scan-id>/
│               ├── <scan-id>.glb
│               └── <scan-id>.navmesh
└── vln_ce/
    └── raw_data/
        └── r2r/
            └── val_unseen/
                └── val_unseen.json.gz
```

Set the root:

```bash
export ROBONIX_COMPUTE_DATA_ROOT=/absolute/path/to/vln_benchmark
export INTERNNAV_HABITAT_DATA_ROOT="$ROBONIX_COMPUTE_DATA_ROOT"
```

### 3.3 Validate the core R2R-CE data

```bash
robonix-compute-data-check \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --profile core \
  --strict
```

Expected checks:

```text
OK  R2R-CE val_unseen  episodes=1839
OK  MP3D-CE scenes     11 referenced scenes have GLB and navmesh assets
OK: true
```

The checker validates annotation parsing, exact episode count, referenced
scene IDs, GLB files, and navmeshes. It does not validate model inference.

### 3.4 Optional extended layout

```text
${ROBONIX_COMPUTE_DATA_ROOT}/vln_ce/raw_data/
├── r2r/
│   ├── val_unseen_short/val_unseen_short.json.gz
│   ├── val_unseen_medium/val_unseen_medium.json.gz
│   └── val_unseen_long/val_unseen_long.json.gz
├── rxr/
│   └── val_unseen/
│       ├── val_unseen_guide_en_compat.json.gz
│       └── val_unseen_guide_gt.json.gz
└── reverie_nav_proxy/
    └── val_unseen/val_unseen.json.gz
```

```bash
robonix-compute-data-check \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --profile extended \
  --strict
```

The extended check expects:

| Data split | Episodes | Readiness meaning |
| --- | ---: | --- |
| R2R-CE `val_unseen` | 1,839 | Integrated core benchmark |
| R2R-CE short | 609 | Derived data split validated |
| R2R-CE medium | 615 | Derived data split validated |
| R2R-CE long | 609 | Derived data split validated |
| RxR-CE English compatibility split | 3,669 | Data and nDTW ground truth validated |
| REVERIE navigation-only proxy | 3,433 | Navigation proxy data validated |

Passing the extended data check does not imply that Compute Skill has a
full-split result on those optional datasets.

<a id="step-4-strict-preflight"></a>
## 🔍 Step 4: Strict Preflight

Run the preflight in the same environment that will launch Habitat:

```bash
robonix-compute-preflight \
  --internnav-root "$INTERNNAV_ROOT" \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --checkpoint-path "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-path "$ROBONIX_COMPUTE_S1_MODEL_DIR" \
  --depth-checkpoint-path "$ROBONIX_COMPUTE_DEPTH_CKPT" \
  --cloud-gpu-id 1 \
  --edge-gpu-id 0 \
  --cloud-port 18765 \
  --require-gpu \
  --strict
```

The command fails on:

- missing or malformed R2R-CE annotations;
- missing referenced MP3D GLB/navmesh assets;
- incomplete full-model shard sets;
- missing S1 `model.safetensors`;
- missing depth checkpoint;
- unavailable InternNav/Habitat imports;
- missing InternNav evaluation scripts/configs;
- unavailable port or invalid GPU IDs.

Preflight validates readiness, not available GPU memory and not navigation
quality. Check GPU occupancy separately before loading the model:

```bash
nvidia-smi
```

<a id="step-5-cloud-and-edge-runtime"></a>
## ☁️ Step 5: Cloud and Edge Runtime

### 5.1 Start cloud S2

```bash
robonix-compute-cloud \
  --mode internnav \
  --host 0.0.0.0 \
  --port 8765 \
  --model-dir "$ROBONIX_COMPUTE_MODEL_DIR" \
  --device cuda:0
```

### 5.2 Start edge S1

Copy `checkpoints/InternVLA-N1-S1` to the edge host, install the same package,
and run:

```bash
robonix-compute-edge \
  --mode internnav \
  --cloud-host <cloud-ip> \
  --cloud-port 8765 \
  --model-dir "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-dir "$ROBONIX_COMPUTE_S1_MODEL_DIR" \
  --device cuda:0 \
  --stdin-jsonl
```

The edge process reads one JSON observation per line:

```json
{
  "step_id": 0,
  "instruction": "go to the kitchen",
  "observation": {
    "rgb": [[[0, 0, 0]]],
    "depth": [[0.0]],
    "pose": [0.0, 0.0, 0.0]
  }
}
```

It returns the action and per-step telemetry as JSON. Restrict the WebSocket
port to trusted hosts. The example transport does not provide production
authentication, encryption, or robot-side action safety.

<a id="step-6-http-skill-api"></a>
## 🌐 Step 6: HTTP Skill API

### Mock-backed HTTP service

```bash
robonix-compute-skill \
  --host 0.0.0.0 \
  --port 8090 \
  --config-json examples/robonix_compute_config.json
```

### InternNav-backed HTTP service

```bash
robonix-compute-skill \
  --host 0.0.0.0 \
  --port 8090 \
  --config-json examples/robonix_compute_internnav_config.json
```

### Health and step request

```bash
curl http://127.0.0.1:8090/health

curl -X POST http://127.0.0.1:8090/step \
  -H 'Content-Type: application/json' \
  -d '{"observation":{"rgb":[1.0,0.0],"depth":[0.0]}}'
```

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Process and runtime health |
| `POST` | `/setup` | Initialize runtime configuration |
| `POST` | `/reset` | Start a new instruction/episode |
| `POST` | `/step` | Submit an observation and receive an action |
| `GET` | `/telemetry` | Read runtime measurements |
| `POST` | `/close` | Release runtime resources |

This HTTP process is the delivered Skill boundary. RoboNix-native registration
and orchestration remain external.

<a id="benchmark-reproduction"></a>
## 📊 Benchmark Reproduction

### Benchmark definition

| Field | Main evaluation |
| --- | --- |
| Task | R2R-CE continuous navigation |
| Split | `val_unseen` |
| Episodes | 1,839 |
| Backbone | InternVLA-N1 DualVLN, original weights |
| Cloud | NVIDIA A100 |
| Reported edge devices | NVIDIA AGX Jetson Orin and Thor, `MAX_N` |
| Navigation metrics | NE, SR, SPL |
| System metrics | Average step latency, peak edge memory, peak cloud memory |
| Compared strategies | Edge Only, Cloud Only, Naive ECC, Step Sync, Compute Skill |

### 1. Local one-episode connectivity run

Use separate local GPUs for cloud S2 and edge S1:

```bash
export ROBONIX_COMPUTE_HABITAT_EPISODES=1
export ROBONIX_COMPUTE_OUTPUT_DIR=outputs/habitat_eval_r2r_ep1
export ROBONIX_COMPUTE_CLOUD_GPU_ID=1
export ROBONIX_COMPUTE_EDGE_GPU_ID=0
export ROBONIX_COMPUTE_CLOUD_PORT=18765

bash scripts/run_habitat_eval.sh
```

This path has been verified to complete one R2R-CE episode and emit navigation
and runtime artifacts. A single episode is a connectivity check, not benchmark
evidence.

### 2. Five-episode subset

```bash
export ROBONIX_COMPUTE_HABITAT_EPISODES=1,2,3,4,5
export ROBONIX_COMPUTE_OUTPUT_DIR=outputs/habitat_eval_r2r_5eps

bash scripts/run_habitat_eval.sh
```

### 3. Complete local split

```bash
export ROBONIX_COMPUTE_HABITAT_EPISODES=all
export ROBONIX_COMPUTE_OUTPUT_DIR=outputs/habitat_eval_r2r_full

bash scripts/run_habitat_eval.sh
```

`all` is expanded from the prepared `val_unseen.json.gz`; the validated split
contains episode IDs 1–1839. This local two-GPU path does not reproduce the
Orin/Thor hardware condition by itself. Compare results with the committed
headline table only when the model revision, hardware, power mode, network
settings, and latency definition match.

### 4. Synthetic network delay

```bash
export ROBONIX_COMPUTE_RTT_DELAY_MS=200
bash scripts/run_habitat_eval.sh
```

The injected delay is a controlled synthetic input. It is not evidence of a
real Wi-Fi, 5G, or cellular trace.

### 5. Output artifacts

```text
outputs/habitat_eval_r2r_5eps/
├── cloud_eval_stdout.log
├── edge_stdout.log
├── cloud_gpu_mem_trace.csv
├── edge_cloud_s2_summary.json
├── edge_cloud_s2_summary_table.csv
├── edge_cloud_s2_control_steps.csv
├── edge_cloud_s2_episode_summary.csv
└── eval/
    ├── progress.json
    ├── result.json
    └── analysis_logs/
```

Summarize an existing run:

```bash
python3 scripts/summarize_habitat_eval.py \
  outputs/habitat_eval_r2r_5eps \
  --format table
```

Regenerate the README result figure from structured CSV:

```bash
python3 benchmarks/r2r_ce/render_results.py
```

<a id="benchmark-results"></a>
## 🏆 Benchmark Results

### Orin + A100

| Strategy | NE ↓ | SR ↑ | SPL ↑ | Avg. step latency ↓ | Edge memory | Cloud memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edge Only | 4.11 | 62.9 | 57.6 | 497.8 ms | 16.63 GB | — |
| Cloud Only | 4.05 | 64.3 | 58.5 | 128.4 ms | — | 16.62 GB |
| Naive ECC | 4.76 | 56.7 | 45.1 | 202.0 ms | 0.60 GB | 16.03 GB |
| Step Sync | 4.13 | 65.2 | 58.1 | 1644.5 ms | 0.60 GB | 16.03 GB |
| **Compute Skill** | **4.18** | **62.8** | **57.8** | **224.4 ms** | **0.60 GB** | **16.03 GB** |

### Thor + A100

| Strategy | NE ↓ | SR ↑ | SPL ↑ | Avg. step latency ↓ | Edge memory | Cloud memory |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Edge Only | 4.09 | 63.1 | 57.8 | 265.1 ms | 16.63 GB | — |
| Cloud Only | 4.05 | 64.3 | 58.5 | 128.4 ms | — | 16.62 GB |
| Naive ECC | 4.72 | 56.7 | 45.4 | 164.6 ms | 0.60 GB | 16.03 GB |
| Step Sync | 4.15 | 64.1 | 57.2 | 1328.0 ms | 0.60 GB | 16.03 GB |
| **Compute Skill** | **4.14** | **63.1** | **58.1** | **166.3 ms** | **0.60 GB** | **16.03 GB** |

### Orin+A100 ablation

| Variant | NE ↓ | SR ↑ | SPL ↑ | Avg. step latency ↓ |
| --- | ---: | ---: | ---: | ---: |
| Naive ECC | 4.76 | 56.7 | 45.1 | 202.0 ms |
| + Switching | 4.06 | 63.2 | 58.0 | 236.0 ms |
| + Missing Handling | 4.18 | 62.8 | 57.8 | 224.4 ms |

### Runtime bookkeeping overhead

| Component | Upper bound / cost |
| --- | --- |
| Extra pending latent slot | `< 7 KB` edge memory |
| Control states and counters | `< 1 KB` edge memory |
| Switching and orchestration logic | Negligible relative to model inference |

Cloud Only is a compute upper bound and does not represent an edge-owned local
control loop. Step Sync is a freshness upper bound with substantial blocking.
Compute Skill targets the accuracy–latency trade-off.

### Reporting boundaries

- The committed CSV files are compact project benchmark summaries.
- Raw 1,839-episode logs are not distributed in this repository.
- A smoke or subset run must not be reported as the headline benchmark.
- A local A100+A100 run must not be labeled as Orin or Thor.
- Synthetic delay must not be labeled as a measured network trace.
- `< 8 KB` describes runtime control state, not total system overhead.
- New results must report model revision, split, episode count, hardware,
  power mode, latency definition, network settings, and raw summary artifacts.

<a id="additional-dataset-readiness"></a>
## 🧭 Additional Dataset Readiness

The optional extended profile is useful for future generalization studies, but
the readiness levels are intentionally narrow:

| Dataset | Data validation | Public Compute Skill result |
| --- | --- | --- |
| R2R-CE short / medium / long | Episode counts, ranges, and scene assets checked | Not claimed |
| RxR-CE English compatibility split | 3,669 episodes and nDTW ground truth checked | Not claimed |
| REVERIE navigation-only proxy | 3,433 navigation episodes checked | Not claimed |
| Original REVERIE | Not part of this runtime path | Not claimed |

The REVERIE proxy does not include object grounding, RGS, or RGSPL and must not
be called full REVERIE or REVERIE-CE. Original REVERIE requires a separate
MatterSim/object-grounding stack and is deliberately excluded here.

<a id="service-ports-and-environment-variables"></a>
## 🔧 Service Ports and Environment Variables

### Ports

| Service | Default | Purpose |
| --- | ---: | --- |
| Cloud S2 WebSocket | `8765` | Semantic latent requests |
| HTTP Skill | `8090` | Lifecycle and step API |
| Local Habitat evaluation example | `18765` | Isolated cloud S2 port |

### Main environment variables

| Variable | Purpose |
| --- | --- |
| `INTERNNAV_ROOT` | InternNav checkout |
| `ROBONIX_COMPUTE_MODEL_DIR` | Full DualVLN checkpoint |
| `ROBONIX_COMPUTE_S1_MODEL_DIR` | Exported S1-only checkpoint |
| `ROBONIX_COMPUTE_DEPTH_CKPT` | Depth Anything checkpoint |
| `ROBONIX_COMPUTE_DATA_ROOT` | MP3D-CE and VLN annotation root |
| `ROBONIX_COMPUTE_CLOUD_GPU_ID` | Cloud GPU index |
| `ROBONIX_COMPUTE_EDGE_GPU_ID` | Local edge-smoke GPU index |
| `ROBONIX_COMPUTE_CLOUD_PORT` | Evaluation WebSocket port |
| `ROBONIX_COMPUTE_HABITAT_EPISODES` | Comma-separated IDs or `all` |
| `ROBONIX_COMPUTE_OUTPUT_DIR` | Evaluation output directory |
| `ROBONIX_COMPUTE_RTT_DELAY_MS` | Synthetic round-trip delay |
| `PYTHON_BIN` | Python executable used by `run_habitat_eval.sh`; default `python3` |

<a id="repository-layout"></a>
## 🗂️ Repository Layout

```text
RoboNix-Compute-Optimization-Skill/
├── .github/workflows/ci.yml       # Python matrix, CLI, docs, audit and build
├── robonix_compute/
│   ├── benchmark_data.py          # Licensed dataset integrity checks
│   ├── cloud/                     # S2 runner and WebSocket service
│   ├── edge/                      # S1 runtime, switcher and timeout handling
│   ├── common/                    # Messages, buffer, serialization and telemetry
│   ├── eval/                      # Habitat adapter and strategy definitions
│   ├── robonix/                   # External HTTP Skill boundary
│   └── cli/                       # robonix-compute-* commands
├── benchmarks/r2r_ce/
│   ├── results/                   # Main, ablation and overhead CSV files
│   ├── metadata.yaml              # Benchmark conditions and reporting boundary
│   └── render_results.py          # Deterministic result-figure generation
├── configs/                       # Defaults, deployment presets and support matrix
├── docs/assets/                   # README figures
├── examples/                      # Mock and InternNav JSON configurations
├── scripts/                       # Launch, summary, docs and release checks
└── tests/                         # Unit and integration coverage
```

`robonix_compute/` is the canonical import-compatible Tool implementation.
Top-level directories expose its deployment, benchmark, configuration,
validation, and RoboNix-facing service workflows.

<a id="roadmap"></a>
## 🗺️ Roadmap

- [x] Publish an independently runnable source-only package and CPU mock path.
- [x] Validate the InternVLA-N1 DualVLN cloud-S2/edge-S1 runtime.
- [x] Add strict R2R-CE data, checkpoint, environment, and result-provenance checks.
- [ ] Publish sanitized full-split raw evaluation artifacts.
- [ ] Provide a versioned RoboNix service adapter beyond the external HTTP boundary.
- [ ] Add authenticated and encrypted production transport examples.
- [ ] Validate additional model families and datasets before listing them as supported.

<a id="troubleshooting"></a>
## 🩺 Troubleshooting

### `python: command not found`

All repository commands use `python3`. For the Habitat wrapper:

```bash
export PYTHON_BIN=python3
```

### `module not importable: habitat` or `internnav`

Activate the same environment used to install InternNav and Habitat, then:

```bash
export PYTHONPATH="$INTERNNAV_ROOT:$INTERNNAV_ROOT/third_party/diffusion-policy:${PYTHONPATH:-}"
python3 -c "import habitat, habitat_sim, internnav; print('imports OK')"
```

### Incomplete full checkpoint

The strict preflight reads `model.safetensors.index.json` and verifies every
referenced shard. Re-run:

```bash
robonix-compute-download --output checkpoints
```

### Missing S1 weights

An S1 directory containing only `config.json` is not valid:

```bash
robonix-compute-export-s1 \
  --source "$ROBONIX_COMPUTE_MODEL_DIR" \
  --output "$ROBONIX_COMPUTE_S1_MODEL_DIR"
```

### Dataset check fails

Confirm the absolute root and run the core checker. The repository cannot
download licensed Matterport3D assets:

```bash
robonix-compute-data-check \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --profile core \
  --strict
```

### Port is occupied

```bash
export ROBONIX_COMPUTE_CLOUD_PORT=18766
```

Use the same port for the cloud and edge processes.

### CUDA out of memory

Check `nvidia-smi` before model loading. The strict preflight validates GPU IDs,
not free memory. Stop only processes you own, or select unused GPUs.

### Timeout under injected delay

Increase the timeout budget only after recording the delay configuration. Keep
synthetic-delay results separate from measured-network results.

<a id="validation-and-contribution"></a>
## ✅ Validation and Contribution

```bash
python3 -m pytest -q
python3 scripts/check_docs.py
python3 scripts/release_audit.py
python3 benchmarks/r2r_ce/render_results.py
python3 -m build
```

User-visible changes require tests and matching English/Chinese README updates.
A new model requires adapters and benchmark evidence. A new dataset entry must
state whether it is data-validated, runner-integrated, smoke-tested, or
full-split benchmarked.

Security issues should be reported privately according to `SECURITY.md`.
Contribution rules are in `CONTRIBUTING.md`; release history is in
`CHANGELOG.md`.

<a id="contributors"></a>
## 🤝 Contributors

- **Zihao Zheng** ([@zhengzihaoPKU](https://github.com/zhengzihaoPKU)) — Leader.
- **Hangyu Cao** ([@i6bimua](https://github.com/i6bimua)) — Maintainer.

<a id="citation"></a>
## 📝 Citation

If this Tool supports your work, please consider giving the repository a star
and citing it:

```bibtex
@software{robonix_compute_optimization_skill_2026,
  author  = {Cao, Hangyu and Zheng, Zihao},
  title   = {RoboNix Compute Optimization Skill},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill}
}
```

InternNav, InternVLA-N1 DualVLN, Habitat, VLN-CE, R2R/RxR, and Matterport3D
retain their own citation and attribution requirements.

<a id="license"></a>
## 📄 License

This project is licensed under the
[Mulan Permissive Software License, Version 2](LICENSE). Third-party models,
simulators, datasets, and libraries retain their original licenses and terms;
see `THIRD_PARTY_LICENSES.md`.
