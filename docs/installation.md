# Installation

## Requirements

- Linux
- Python 3.9 or newer; Python 3.10 is recommended
- A CUDA-capable cloud GPU for InternVLA-N1 S2 inference
- NVIDIA AGX Jetson Orin or Thor for the validated edge deployment
- InternNav, Habitat, Habitat-Sim, and licensed R2R-CE assets for evaluation

The repository uses Conda and pip. It does not require Docker.

## Install the Package

```bash
git clone https://github.com/i6bimua/RoboNix-Compute-Optimization-Skill.git
cd RoboNix-Compute-Optimization-Skill

conda create -n robonix-compute python=3.10 -y
conda activate robonix-compute
python -m pip install -U pip setuptools wheel packaging
```

Install the PyTorch build appropriate for each machine before installing the
project. On Orin/Thor, use the NVIDIA Jetson/Thor wheel that matches the device
software stack.

```bash
# Example for a compatible CUDA server only.
python -m pip install torch torchvision \
  --index-url https://download.pytorch.org/whl/cu128

python -m pip install -e ".[dev,websocket,download]"
```

## Install InternNav

The real model and Habitat paths call InternNav as an external dependency:

```bash
export INTERNNAV_ROOT=<path-to-InternNav>
cd "$INTERNNAV_ROOT"
python -m pip install -e .

export PYTHONPATH="$INTERNNAV_ROOT:$INTERNNAV_ROOT/third_party/diffusion-policy:${PYTHONPATH:-}"
```

The external module name `internnav.edgecloud` is preserved because it is part
of the InternNav API. It is not the project brand.

## Download Checkpoints

```bash
cd <path-to-RoboNix-Compute-Optimization-Skill>
robonix-compute-download --output checkpoints
```

The default download sources are:

- `InternRobotics/InternVLA-N1-DualVLN`
- Depth Anything V2 Metric Hypersim Small

Expected layout:

```text
checkpoints/
├── InternVLA-N1/
│   ├── config.json
│   ├── model.safetensors.index.json
│   └── model-*.safetensors
└── depth_anything_v2_metric_hypersim_vits.pth
```

Export the S1-only checkpoint for the edge:

```bash
robonix-compute-export-s1 \
  --source checkpoints/InternVLA-N1 \
  --output checkpoints/InternVLA-N1-S1
```

Set absolute runtime paths:

```bash
export ROBONIX_COMPUTE_MODEL_DIR="$(pwd)/checkpoints/InternVLA-N1"
export ROBONIX_COMPUTE_S1_MODEL_DIR="$(pwd)/checkpoints/InternVLA-N1-S1"
export ROBONIX_COMPUTE_DEPTH_CKPT="$(pwd)/checkpoints/depth_anything_v2_metric_hypersim_vits.pth"
```

## Prepare Habitat Data

Obtain Matterport3D and R2R-CE from their official sources and keep their
original licenses.

```bash
export ROBONIX_COMPUTE_DATA_ROOT=<path-to-vln-data>
export INTERNNAV_HABITAT_DATA_ROOT="$ROBONIX_COMPUTE_DATA_ROOT"
```

Expected layout:

```text
${ROBONIX_COMPUTE_DATA_ROOT}/
├── scene_data/
│   └── mp3d_ce/mp3d/
└── vln_ce/
    └── raw_data/r2r/val_unseen/val_unseen.json.gz
```

## Verify

```bash
python -m pytest -q

robonix-compute-preflight \
  --internnav-root "$INTERNNAV_ROOT" \
  --data-root "$ROBONIX_COMPUTE_DATA_ROOT" \
  --checkpoint-path "$ROBONIX_COMPUTE_MODEL_DIR" \
  --s1-model-path "$ROBONIX_COMPUTE_S1_MODEL_DIR" \
  --strict
```

See [Troubleshooting](troubleshooting.md) when the strict preflight reports a
missing dependency or incompatible path.
