#!/usr/bin/env bash
# Record Habitat / R2R-CE episodes under two (or three) strategies for a
# side-by-side demo. Videos land under each lane's InternNav output tree;
# compose them with scripts/demo/compose_side_by_side.py.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

EPISODES_FILE="${ROOT}/benchmarks/r2r_ce/demo_episodes.yaml"
STRATEGIES="naive_ecc,ours"
RTT_DELAY_MS="${ROBONIX_COMPUTE_RTT_DELAY_MS:-200}"
OUTPUT_DIR="${ROOT}/outputs/demo_comparison"
DRY_RUN=0
EPISODE_FILTER=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

usage() {
  cat <<'EOF'
Usage: bash scripts/demo/run_comparison.sh [options]

Options:
  --episodes-file PATH   YAML list (default: benchmarks/r2r_ce/demo_episodes.yaml)
  --episodes IDS         Comma-separated subset, e.g. 206,1,2
  --strategies LIST      naive_ecc,ours[,edge_only]  (default: naive_ecc,ours)
  --rtt-delay-ms N       Extra RTT injected on every lane (default: 200)
  --output-dir PATH      Root for lane outputs (default: outputs/demo_comparison)
  --dry-run              Print the commands only
  -h, --help             Show this help

Required environment:
  INTERNNAV_ROOT
  ROBONIX_COMPUTE_DATA_ROOT
  ROBONIX_COMPUTE_MODEL_DIR
  ROBONIX_COMPUTE_S1_MODEL_DIR

Optional:
  PYTHON_BIN             Interpreter with Habitat + InternNav + imageio
  ROBONIX_COMPUTE_CLOUD_GPU_ID / ROBONIX_COMPUTE_EDGE_GPU_ID
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --episodes-file) EPISODES_FILE="$2"; shift 2 ;;
    --episodes) EPISODE_FILTER="$2"; shift 2 ;;
    --strategies) STRATEGIES="$2"; shift 2 ;;
    --rtt-delay-ms) RTT_DELAY_MS="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for var in INTERNNAV_ROOT ROBONIX_COMPUTE_DATA_ROOT ROBONIX_COMPUTE_MODEL_DIR ROBONIX_COMPUTE_S1_MODEL_DIR; do
  if [[ -z "${!var:-}" ]]; then
    echo "error: ${var} must be set" >&2
    exit 2
  fi
done

# InternNav's measure_edge_cloud_s2_runtime.py used to hard-code ANALYSIS_SAVE_VIDEO=0,
# which silently drops every demo mp4. Refuse to start until the checkout honours the env.
MEASURE_PY="${INTERNNAV_ROOT}/scripts/eval/measure_edge_cloud_s2_runtime.py"
if [[ ! -f "${MEASURE_PY}" ]]; then
  echo "error: missing ${MEASURE_PY}" >&2
  exit 2
fi
if grep -qE 'ANALYSIS_SAVE_VIDEO"\]\s*=\s*"0"' "${MEASURE_PY}" \
  || grep -qE "ANALYSIS_SAVE_VIDEO'\]\s*=\s*'0'" "${MEASURE_PY}"; then
  echo "error: ${MEASURE_PY} hard-codes ANALYSIS_SAVE_VIDEO=0;" >&2
  echo "       change that assignment to os.environ.get(\"ANALYSIS_SAVE_VIDEO\", \"0\")" >&2
  echo "       or demos will finish with no mp4. See benchmarks/r2r_ce/DEMO_FILMING.md." >&2
  exit 2
fi

EPISODE_IDS="$(
  EPISODES_FILE="${EPISODES_FILE}" EPISODE_FILTER="${EPISODE_FILTER}" "${PYTHON_BIN}" - <<'PY'
import os, re
from pathlib import Path
path = Path(os.environ["EPISODES_FILE"])
text = path.read_text(encoding="utf-8")
ids = []
try:
    import yaml  # type: ignore
    doc = yaml.safe_load(text) or {}
    ids = [str(ep["id"]) for ep in (doc.get("episodes") or [])]
except Exception:
    ids = re.findall(r"^\s*-\s*id:\s*(\d+)\s*$", text, flags=re.M)
filt = os.environ.get("EPISODE_FILTER", "").strip()
if filt:
    want = {x.strip() for x in filt.split(",") if x.strip()}
    ids = [i for i in ids if i in want]
if not ids:
    raise SystemExit(f"no episode ids resolved from {path}")
print(",".join(ids))
PY
)"

mkdir -p "${OUTPUT_DIR}"
echo "[demo] episodes=${EPISODE_IDS}"
echo "[demo] strategies=${STRATEGIES}  rtt_delay_ms=${RTT_DELAY_MS}"
echo "[demo] output=${OUTPUT_DIR}"

make_naive_manifest() {
  local episode_ids="$1"
  local out="$2"
  (
    cd "${INTERNNAV_ROOT}"
    "${PYTHON_BIN}" scripts/eval/make_fixed_period_sync_manifest.py \
      --period 513 \
      --max-s2-index 512 \
      --episode-ids "${episode_ids}" \
      --output "${out}"
  )
}

lane_label() {
  case "$1" in
    naive_ecc) echo "A · Naive ECC" ;;
    ours|acevln) echo "B · Ours (VLN service)" ;;
    edge_only) echo "C · Edge Only" ;;
    *) echo "$1" ;;
  esac
}

run_lane() {
  local strategy="$1"
  local episode_ids="$2"
  local lane_dir="${OUTPUT_DIR}/${strategy}"
  mkdir -p "${lane_dir}"

  export ANALYSIS_SAVE_VIDEO=1
  export ANALYSIS_VIS_DEBUG=0

  case "${strategy}" in
    naive_ecc)
      local manifest="${lane_dir}/naive_ecc_sync_manifest.json"
      if [[ "${DRY_RUN}" != "1" ]]; then
        make_naive_manifest "${episode_ids}" "${manifest}"
      else
        echo "[demo] would write naive manifest -> ${manifest}"
      fi
      export INTERNNAV_EDGE_CLOUD_STRATEGY=naive_ecc
      export INTERNNAV_EDGE_CLOUD_SYNC_SELECTION_PATH="${manifest}"
      ;;
    ours|acevln)
      unset INTERNNAV_EDGE_CLOUD_STRATEGY || true
      unset INTERNNAV_EDGE_CLOUD_SYNC_SELECTION_PATH || true
      ;;
    edge_only)
      echo "[demo] edge_only is optional: record with InternNav habitat_dual_system_cfg.py" >&2
      echo "       and place the mp4 under ${lane_dir}/ before composing." >&2
      "${PYTHON_BIN}" - <<PY
import json
from pathlib import Path
Path(${lane_dir@Q}).mkdir(parents=True, exist_ok=True)
(Path(${lane_dir@Q}) / "lane_meta.json").write_text(json.dumps({
  "strategy": "edge_only",
  "episodes": ${episode_ids@Q},
  "rtt_delay_ms": ${RTT_DELAY_MS},
  "label": "C · Edge Only",
  "skipped": True,
}, indent=2) + "\n")
PY
      return 0
      ;;
    *)
      echo "error: unknown strategy '${strategy}'" >&2
      return 2
      ;;
  esac

  local label
  label="$(lane_label "${strategy}")"
  "${PYTHON_BIN}" - <<PY
import json
from pathlib import Path
meta = {
  "strategy": ${strategy@Q},
  "episodes": ${episode_ids@Q},
  "rtt_delay_ms": ${RTT_DELAY_MS},
  "label": ${label@Q},
}
Path(${lane_dir@Q}).mkdir(parents=True, exist_ok=True)
(Path(${lane_dir@Q}) / "lane_meta.json").write_text(json.dumps(meta, indent=2) + "\n")
print("[demo] wrote", Path(${lane_dir@Q}) / "lane_meta.json")
PY

  # Service package must be importable even when PYTHON_BIN is a Habitat env.
  export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

  local cmd=(
    "${PYTHON_BIN}" -m robonix_compute.cli.habitat_eval
    --internnav-root "${INTERNNAV_ROOT}"
    --data-root "${ROBONIX_COMPUTE_DATA_ROOT}"
    --checkpoint-path "${ROBONIX_COMPUTE_MODEL_DIR}"
    --s1-model-path "${ROBONIX_COMPUTE_S1_MODEL_DIR}"
    --episodes "${episode_ids}"
    --cloud-gpu-id "${ROBONIX_COMPUTE_CLOUD_GPU_ID:-1}"
    --edge-gpu-id "${ROBONIX_COMPUTE_EDGE_GPU_ID:-0}"
    --port "${ROBONIX_COMPUTE_CLOUD_PORT:-18765}"
    --edge-cloud-rtt-delay-ms "${RTT_DELAY_MS}"
    --output-dir "${lane_dir}"
    --strict
  )

  echo "[demo] lane=${strategy}"
  printf '  '
  printf '%q ' "${cmd[@]}"
  printf '\n'

  if [[ "${DRY_RUN}" == "1" ]]; then
    return 0
  fi
  "${cmd[@]}"
}

IFS=',' read -r -a STRATEGY_ARR <<< "${STRATEGIES}"
for strategy in "${STRATEGY_ARR[@]}"; do
  strategy="$(echo "${strategy}" | tr -d '[:space:]')"
  [[ -z "${strategy}" ]] && continue
  run_lane "${strategy}" "${EPISODE_IDS}"
done

OUTPUT_DIR="${OUTPUT_DIR}" EPISODE_IDS="${EPISODE_IDS}" RTT_DELAY_MS="${RTT_DELAY_MS}" \
"${PYTHON_BIN}" - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ["OUTPUT_DIR"])
lanes = []
for p in sorted(root.glob("*/lane_meta.json")):
    lanes.append(json.loads(p.read_text(encoding="utf-8")))
doc = {
    "episodes": os.environ["EPISODE_IDS"],
    "rtt_delay_ms": float(os.environ["RTT_DELAY_MS"]),
    "lanes": lanes,
}
(root / "index.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
print(f"[demo] wrote {root / 'index.json'}")
PY

echo "[demo] done. Compose with:"
echo "  ${PYTHON_BIN} scripts/demo/compose_side_by_side.py --run-dir ${OUTPUT_DIR} --out docs/assets/demo/habitat_comparison.mp4"
