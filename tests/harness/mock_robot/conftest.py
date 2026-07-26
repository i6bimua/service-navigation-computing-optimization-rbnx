# SPDX-License-Identifier: MulanPSL-2.0
"""Make the fixture importable when collected from the repository root.

`mock_robot` is a self-contained Robonix package nested under tests/, so its own
root has to be on sys.path for `import mock_robot` to resolve. Doing it here
rather than in the test module means the harness runs both as part of the
repository suite and standalone from this directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

# The provider module additionally needs `rbnx codegen` output (chassis_pb2,
# std_msgs_pb2). Add it when present; the provider tests importorskip otherwise,
# so a bare checkout still collects and passes.
_CODEGEN = PACKAGE_ROOT / "rbnx-build" / "codegen" / "proto_gen"
if _CODEGEN.is_dir() and str(_CODEGEN) not in sys.path:
    sys.path.insert(0, str(_CODEGEN))
