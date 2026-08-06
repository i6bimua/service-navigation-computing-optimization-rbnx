#!/usr/bin/env bash
# SPDX-License-Identifier: MulanPSL-2.0
#
# Stop phase for robonix.service.navigation.computing_optimization.
#
# The Driver shutdown handler already releases everything this package owns
# (cancels the active run, joins the worker, closes the cloud transport, stops
# the rclpy thread). This script only exists to reap a provider that outlived
# its Driver — e.g. `rbnx boot` died on an error path before CMD_SHUTDOWN
# landed, leaving the process holding the cloud socket.
#
# Must be reentrant: running it twice, or with nothing running, is a no-op
# that still exits 0.
set -euo pipefail

PKG="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PATTERN="robonix_compute.rbnx.provider"

pids="$(pgrep -f -- "$PATTERN" 2>/dev/null || true)"
if [[ -z "$pids" ]]; then
    echo "[navigation_computing_optimization/stop] no provider process found — nothing to do"
    exit 0
fi

echo "[navigation_computing_optimization/stop] SIGTERM to: $pids"
# shellcheck disable=SC2086
kill -TERM $pids 2>/dev/null || true

# Give the process the same 2s budget the deactivate handler uses to join its
# worker thread before escalating.
for _ in 1 2 3 4; do
    sleep 0.5
    pgrep -f -- "$PATTERN" >/dev/null 2>&1 || {
        echo "[navigation_computing_optimization/stop] provider exited cleanly"
        exit 0
    }
done

survivors="$(pgrep -f -- "$PATTERN" 2>/dev/null || true)"
if [[ -n "$survivors" ]]; then
    echo "[navigation_computing_optimization/stop] still alive after 2s; SIGKILL to: $survivors"
    # shellcheck disable=SC2086
    kill -KILL $survivors 2>/dev/null || true
fi
exit 0
