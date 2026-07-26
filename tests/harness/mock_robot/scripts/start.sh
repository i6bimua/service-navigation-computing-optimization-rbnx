#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Start phase for the mock_robot wiring fixture.
#
# Needs an interpreter with rclpy plus numpy. Resolution order matches the
# skill's start.sh so one deployment-level ROS_PYTHON covers both:
#
#   1. $ROS_PYTHON — explicit interpreter. Use this when ROS 2 lives in a conda
#      environment rather than /opt/ros (e.g. a RoboStack install).
#   2. /opt/ros/$ROS_DISTRO/setup.bash — a system ROS 2 install, then `python3`.
#   3. plain `python3`.
set -eo pipefail

PKG_ROOT="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$PKG_ROOT"

PYTHON="${ROS_PYTHON:-}"
if [[ -z "$PYTHON" ]]; then
    ROS_DISTRO="${ROS_DISTRO:-humble}"
    if [[ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]]; then
        # shellcheck disable=SC1090
        set +u; source "/opt/ros/${ROS_DISTRO}/setup.bash"; set -u
    fi
    PYTHON=python3
fi

CODEGEN_PROTO="$PKG_ROOT/rbnx-build/codegen/proto_gen"
if [[ ! -d "$CODEGEN_PROTO" ]]; then
    echo "[mock_robot/start] ERR: codegen output missing — run scripts/build.sh first" >&2
    exit 2
fi
export PYTHONPATH="$CODEGEN_PROTO:$PKG_ROOT:${PYTHONPATH:-}"
if ROBONIX_API="$(rbnx path robonix-api 2>/dev/null)"; then
    export PYTHONPATH="$ROBONIX_API:$PYTHONPATH"
else
    echo "[mock_robot/start] WARN: 'rbnx path robonix-api' failed; relying on an installed robonix-api" >&2
fi

# Fail with a precise message rather than dying on an ImportError mid-boot.
if ! "$PYTHON" -c "import robonix_api" 2>/dev/null; then
    echo "[mock_robot/start] ERR: $PYTHON cannot import robonix_api." >&2
    echo "[mock_robot/start]   pip install grpcio protobuf pyyaml 'mcp>=1.0' 'fastmcp>=3' into that interpreter." >&2
    exit 2
fi
if ! "$PYTHON" -c "import rclpy" 2>/dev/null; then
    echo "[mock_robot/start] ERR: $PYTHON cannot import rclpy." >&2
    echo "[mock_robot/start]   Source a ROS 2 overlay, or set ROS_PYTHON to a ROS-capable interpreter." >&2
    exit 2
fi

exec "$PYTHON" -u -m mock_robot.main
