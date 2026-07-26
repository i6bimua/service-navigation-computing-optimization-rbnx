#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Build phase for the mock_robot wiring fixture.
#
# Codegen only: the chassis/move handler is typed against the generated
# `chassis_pb2` / `std_msgs_pb2` classes. No `--mcp` — this fixture declares no
# MCP tools.
#
# The dependency check WARNs and never fails: `rbnx build` runs with whatever
# python is on PATH, which is not necessarily the interpreter start.sh launches.
#
# RBNX_BUILD_CLEAN=1 wipes rbnx-build/ and regenerates from scratch.
set -euo pipefail

PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"

CLEAN="${RBNX_BUILD_CLEAN:-}"
if [[ "$CLEAN" == "1" ]]; then
    echo "[mock_robot/build] clean: removing rbnx-build/"
    rm -rf rbnx-build
fi
mkdir -p rbnx-build

if ! command -v rbnx >/dev/null 2>&1; then
    echo "[mock_robot/build] ERR: rbnx not on PATH." >&2
    echo "[mock_robot/build]   install robonix-cli and run 'rbnx setup <robonix-source-root>' once." >&2
    exit 1
fi

if [[ "$CLEAN" == "1" ]]; then
    rbnx codegen -p "$PKG" --clean
else
    rbnx codegen -p "$PKG"
fi

RUNTIME_PY="${ROS_PYTHON:-python3}"
if command -v "$RUNTIME_PY" >/dev/null 2>&1 || [[ -x "$RUNTIME_PY" ]]; then
    echo "[mock_robot/build] runtime interpreter: $RUNTIME_PY"
    "$RUNTIME_PY" - <<'PY' || true
import importlib.util as u

absent = [m for m in ("numpy", "rclpy", "sensor_msgs", "nav_msgs") if u.find_spec(m) is None]
if absent:
    print(f"[mock_robot/build] WARN: missing: {', '.join(absent)}")
PY
else
    echo "[mock_robot/build] WARN: ROS_PYTHON=$RUNTIME_PY is not executable; skipping dependency check"
fi

touch "$PKG/rbnx-build/.rbnx-built"
echo "[mock_robot/build] done."
