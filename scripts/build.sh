#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Build phase for robonix.service.navigation.computing_optimization.
#
# Pure Python skill — no colcon, no vendored ROS packages, no docker image.
# The only mandatory step is codegen: `@skill.mcp` handlers are typed against the
# Request/Response dataclasses generated from
# capabilities/lib/navigation_computing_optimization/srv/*.srv, so `--mcp` is required rather
# than optional. Output lands in rbnx-build/codegen/{proto_gen,robonix_mcp_types},
# which robonix-api puts on sys.path automatically at import time.
#
# Dependency checks below WARN and never fail the build. `rbnx build` runs with
# whatever python is on PATH, which is not necessarily the interpreter start.sh
# will launch (a ROS 2 environment usually differs from the build shell's), so a
# hard failure here would block a deployment that is in fact fine. Same policy as
# the reference packages.
#
# RBNX_BUILD_CLEAN=1 wipes rbnx-build/ and regenerates from scratch.
set -euo pipefail

PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"

CLEAN="${RBNX_BUILD_CLEAN:-}"
if [[ "$CLEAN" == "1" ]]; then
    echo "[navigation_computing_optimization/build] clean: removing rbnx-build/"
    rm -rf rbnx-build
fi
mkdir -p rbnx-build

# ── 1. Codegen (required) ──────────────────────────────────────────────────
if ! command -v rbnx >/dev/null 2>&1; then
    echo "[navigation_computing_optimization/build] ERR: rbnx not on PATH." >&2
    echo "[navigation_computing_optimization/build]   install robonix-cli and run 'rbnx setup <robonix-source-root>' once." >&2
    exit 1
fi

FLAGS=(--mcp)
[[ "$CLEAN" == "1" ]] && FLAGS+=(--clean)
echo "[navigation_computing_optimization/build] rbnx codegen ${FLAGS[*]}"
rbnx codegen -p "$PKG" "${FLAGS[@]}"

# ── 2. Runtime dependency check (advisory) ─────────────────────────────────
# Checked against the interpreter start.sh will actually use, so the warning
# reflects the runtime rather than the build shell. See scripts/start.sh for how
# ROS_PYTHON is resolved.
RUNTIME_PY="${ROS_PYTHON:-python3}"
if command -v "$RUNTIME_PY" >/dev/null 2>&1 || [[ -x "$RUNTIME_PY" ]]; then
    echo "[navigation_computing_optimization/build] runtime interpreter: $RUNTIME_PY"
    "$RUNTIME_PY" - <<'PY' || true
import importlib.util as u

# rclpy is needed to consume the camera and chassis topic contracts; websockets
# only for mode=websocket|internnav.
groups = {
    "required": ("numpy", "msgpack", "grpc"),
    "required for ROS 2 inputs": ("rclpy", "sensor_msgs", "nav_msgs"),
    "required for mode=websocket|internnav": ("websockets",),
}
for label, mods in groups.items():
    absent = [m for m in mods if u.find_spec(m) is None]
    if absent:
        print(f"[navigation_computing_optimization/build] WARN: missing ({label}): {', '.join(absent)}")
PY
else
    echo "[navigation_computing_optimization/build] WARN: ROS_PYTHON=$RUNTIME_PY is not executable; skipping dependency check"
fi

touch "$PKG/rbnx-build/.rbnx-built"
echo "[navigation_computing_optimization/build] done."
