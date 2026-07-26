#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Start phase for robonix.skill.compute_optimization.
#
# Needs an interpreter that can import BOTH rclpy (to consume the camera and
# chassis topic contracts) and this repository's `robonix_compute`. Resolution
# order:
#
#   1. $ROS_PYTHON — an explicit interpreter. Set this when ROS 2 lives in a
#      conda environment rather than /opt/ros, e.g. a RoboStack install:
#          conda create -n rbnx-ros -c robostack-staging -c conda-forge \
#              python=3.11 ros-humble-rclpy ros-humble-sensor-msgs \
#              ros-humble-nav-msgs ros-humble-geometry-msgs ros-humble-std-msgs
#      A deployment can pass it down through the manifest's `env:` block.
#   2. /opt/ros/$ROS_DISTRO/setup.bash — a system ROS 2 install, then `python3`.
#   3. plain `python3`.
#
# Note the PKG entry on PYTHONPATH: the compute runtime (`robonix_compute`) lives
# in this same repository, so the skill needs no git submodule and no cross-repo
# pip dependency to import it.
set -eo pipefail

PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG"

# ── Interpreter ────────────────────────────────────────────────────────────
PYTHON="${ROS_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    ROS_DISTRO="${ROS_DISTRO:-humble}"
    if [[ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]]; then
        # shellcheck disable=SC1090
        set +u; source "/opt/ros/${ROS_DISTRO}/setup.bash"; set -u
    fi
    PYTHON=python3
fi

# ── Codegen output ─────────────────────────────────────────────────────────
CODEGEN_PROTO="$PKG/rbnx-build/codegen/proto_gen"
CODEGEN_MCP="$PKG/rbnx-build/codegen/robonix_mcp_types"
if [[ ! -d "$CODEGEN_PROTO" || ! -d "$CODEGEN_MCP" ]]; then
    echo "[compute_optimization/start] ERR: codegen output missing — run scripts/build.sh first" >&2
    exit 2
fi

export PYTHONPATH="$CODEGEN_PROTO:$CODEGEN_MCP:$PKG:${PYTHONPATH:-}"
if ROBONIX_API="$(rbnx path robonix-api 2>/dev/null)"; then
    export PYTHONPATH="$ROBONIX_API:$PYTHONPATH"
else
    echo "[compute_optimization/start] WARN: 'rbnx path robonix-api' failed; relying on an installed robonix-api" >&2
fi

# ── Preflight ──────────────────────────────────────────────────────────────
# Fail here with a precise message rather than letting the provider die on an
# ImportError several seconds into boot.
if ! "$PYTHON" -c "import robonix_api" 2>/dev/null; then
    echo "[compute_optimization/start] ERR: $PYTHON cannot import robonix_api." >&2
    echo "[compute_optimization/start]   pip install grpcio protobuf pyyaml 'mcp>=1.0' 'fastmcp>=3' into that interpreter." >&2
    exit 2
fi
if ! "$PYTHON" -c "import rclpy" 2>/dev/null; then
    echo "[compute_optimization/start] ERR: $PYTHON cannot import rclpy." >&2
    echo "[compute_optimization/start]   The skill consumes camera/chassis contracts over ROS 2." >&2
    echo "[compute_optimization/start]   Source a ROS 2 overlay, or set ROS_PYTHON to a ROS-capable interpreter." >&2
    exit 2
fi

exec "$PYTHON" -u -m robonix_compute.rbnx.provider
