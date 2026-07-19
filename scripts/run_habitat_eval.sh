#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${INTERNNAV_ROOT:-}" ]]; then
  echo "INTERNNAV_ROOT must point to the InternNav checkout." >&2
  exit 2
fi
if [[ -z "${ROBONIX_COMPUTE_DATA_ROOT:-}" ]]; then
  echo "ROBONIX_COMPUTE_DATA_ROOT must point to the Habitat VLN data root." >&2
  exit 2
fi
if [[ -z "${ROBONIX_COMPUTE_MODEL_DIR:-}" ]]; then
  echo "ROBONIX_COMPUTE_MODEL_DIR must point to the full InternVLA-N1 checkpoint." >&2
  exit 2
fi
if [[ -z "${ROBONIX_COMPUTE_S1_MODEL_DIR:-}" ]]; then
  echo "ROBONIX_COMPUTE_S1_MODEL_DIR must point to the S1-only checkpoint." >&2
  exit 2
fi

RUN_TAG="${ROBONIX_COMPUTE_RUN_TAG:-habitat_eval_$(date +%Y%m%d_%H%M%S)}"
OUTPUT_DIR="${ROBONIX_COMPUTE_OUTPUT_DIR:-outputs/${RUN_TAG}}"
EPISODES="${ROBONIX_COMPUTE_HABITAT_EPISODES:-1}"
CLOUD_GPU_ID="${ROBONIX_COMPUTE_CLOUD_GPU_ID:-1}"
EDGE_GPU_ID="${ROBONIX_COMPUTE_EDGE_GPU_ID:-0}"
PORT="${ROBONIX_COMPUTE_CLOUD_PORT:-18765}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

"${PYTHON_BIN}" -m robonix_compute.cli.habitat_eval \
  --internnav-root "${INTERNNAV_ROOT}" \
  --data-root "${ROBONIX_COMPUTE_DATA_ROOT}" \
  --checkpoint-path "${ROBONIX_COMPUTE_MODEL_DIR}" \
  --s1-model-path "${ROBONIX_COMPUTE_S1_MODEL_DIR}" \
  --episodes "${EPISODES}" \
  --cloud-gpu-id "${CLOUD_GPU_ID}" \
  --edge-gpu-id "${EDGE_GPU_ID}" \
  --port "${PORT}" \
  --output-dir "${OUTPUT_DIR}" \
  --strict

"${PYTHON_BIN}" scripts/summarize_habitat_eval.py "${OUTPUT_DIR}" --format table
