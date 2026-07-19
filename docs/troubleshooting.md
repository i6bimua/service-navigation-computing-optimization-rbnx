# Troubleshooting

## `python` Is Not Available

Use the active Conda environment and call `python3` if the system does not
provide a `python` alias:

```bash
conda activate robonix-compute
python3 -m pip install -e ".[dev,websocket,download]"
```

## `websockets` Is Missing

```bash
python -m pip install -e ".[websocket]"
```

The in-process mock works without WebSocket support; the two-process path does
not.

## InternNav Cannot Be Imported

```bash
export INTERNNAV_ROOT=<path-to-InternNav>
export PYTHONPATH="$INTERNNAV_ROOT:$INTERNNAV_ROOT/third_party/diffusion-policy:${PYTHONPATH:-}"
python -c "import internnav; print(internnav.__file__)"
```

Some upstream helpers are imported from InternNav source paths and are not
vendored by this repository.

## S1 Export Fails

Confirm that the source is the full InternVLA-N1 DualVLN checkpoint and that
InternNav exposes `export_internvla_s1_only_checkpoint`:

```bash
python -c "from internnav.edgecloud import export_internvla_s1_only_checkpoint; print('ok')"
```

## Strict Preflight Reports Missing Data

Check:

```text
${ROBONIX_COMPUTE_DATA_ROOT}/scene_data/mp3d_ce/mp3d/
${ROBONIX_COMPUTE_DATA_ROOT}/vln_ce/raw_data/r2r/val_unseen/val_unseen.json.gz
```

Use absolute paths. The repository cannot download licensed Matterport3D
content.

## CUDA Out of Memory

- use the S1-only exported checkpoint on Orin/Thor;
- ensure only one model worker is running per selected GPU;
- verify `ROBONIX_COMPUTE_CLOUD_GPU_ID` and
  `ROBONIX_COMPUTE_EDGE_GPU_ID`;
- stop stale evaluation processes before retrying;
- use the PyTorch build supplied for the edge platform.

## The First Step Has No Latent

Set the runtime to require an initial cloud latent when the model cannot act
without semantic context. In non-blocking mode, confirm that the configured
fallback is safe for the target controller.

## Timeout or Reuse Counts Are Unexpected

Inspect the telemetry output for:

- requested and completed synchronization events;
- predicted timeout;
- latent age and generation step;
- reuse reason;
- late response count;
- injected send/reply delay.

Do not interpret synthetic RTT injection as real network validation.

## `edge_cloud_s2_*` Files Still Use the Old Phrase

Those filenames come from the external InternNav evaluation script and are
preserved for compatibility. Project-owned package names, CLIs, environment
variables, and output directories use `robonix-compute` /
`ROBONIX_COMPUTE_*`.

## SVG Does Not Render

Validate the XML:

```bash
python - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

for path in Path("docs/assets").glob("*.svg"):
    ElementTree.parse(path)
    print("valid:", path)
PY
```

All SVG source must be UTF-8 XML.
