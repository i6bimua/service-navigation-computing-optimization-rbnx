from __future__ import annotations

from dataclasses import asdict, dataclass
import importlib.util
from pathlib import Path
import shutil
import socket
import subprocess
from typing import Any, Iterable


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "warn"}


@dataclass(frozen=True)
class PreflightReport:
    mode: str
    checks: list[PreflightCheck]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ok": self.ok,
            "checks": [asdict(item) for item in self.checks],
        }


def _check_path(name: str, path: Path, *, kind: str, required: bool = True) -> PreflightCheck:
    path = path.expanduser()
    exists = path.is_dir() if kind == "dir" else path.is_file()
    if exists:
        return PreflightCheck(name, "ok", str(path))
    status = "error" if required else "warn"
    return PreflightCheck(name, status, f"missing {kind}: {path}")


def _check_import(module_name: str, *, required: bool = True) -> PreflightCheck:
    if importlib.util.find_spec(module_name) is not None:
        return PreflightCheck(f"python import: {module_name}", "ok", module_name)
    status = "error" if required else "warn"
    return PreflightCheck(f"python import: {module_name}", status, f"module not importable: {module_name}")


def _check_port(host: str, port: int) -> PreflightCheck:
    bind_host = "0.0.0.0" if host in {"", "*"} else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, int(port)))
    except OSError as exc:
        return PreflightCheck("cloud port", "error", f"{host}:{port} unavailable: {exc}")
    return PreflightCheck("cloud port", "ok", f"{host}:{port} available")


def _gpu_count() -> int | None:
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return len([line for line in completed.stdout.splitlines() if line.strip()])


def _check_gpu_ids(gpu_ids: Iterable[int], *, required: bool) -> PreflightCheck:
    count = _gpu_count()
    requested = sorted({int(item) for item in gpu_ids})
    if count is None:
        status = "error" if required else "warn"
        return PreflightCheck("gpu ids", status, f"nvidia-smi unavailable; requested={requested}")
    invalid = [item for item in requested if item < 0 or item >= count]
    if invalid:
        return PreflightCheck("gpu ids", "error", f"available=0..{count - 1}; invalid={invalid}")
    return PreflightCheck("gpu ids", "ok", f"available={count}; requested={requested}")


def run_preflight(
    *,
    mode: str,
    internnav_root: Path,
    data_root: Path,
    checkpoint_path: Path,
    s1_model_path: Path,
    output_dir: Path,
    analysis_config: str,
    edge_config: str,
    cloud_bind_host: str,
    cloud_port: int,
    cloud_gpu_id: int,
    edge_gpu_id: int,
    require_gpu: bool = False,
) -> PreflightReport:
    internnav_root = internnav_root.expanduser().resolve()
    data_root = data_root.expanduser()
    checkpoint_path = checkpoint_path.expanduser()
    s1_model_path = s1_model_path.expanduser()
    output_dir = output_dir.expanduser()

    checks: list[PreflightCheck] = []
    checks.append(_check_path("InternNav root", internnav_root, kind="dir"))
    checks.append(_check_path("Habitat data root", data_root, kind="dir"))
    checks.append(_check_path("full model checkpoint", checkpoint_path, kind="dir"))
    checks.append(_check_path("S1-only checkpoint", s1_model_path, kind="dir"))
    checks.append(_check_path("Habitat measurement script", internnav_root / "scripts/eval/measure_edge_cloud_s2_runtime.py", kind="file"))
    checks.append(_check_path("analysis config", internnav_root / analysis_config, kind="file"))
    checks.append(_check_path("edge config", internnav_root / edge_config, kind="file"))
    checks.append(_check_path("output parent", output_dir.parent, kind="dir", required=False))
    checks.append(_check_import("habitat"))
    checks.append(_check_import("internnav"))
    checks.append(_check_port(cloud_bind_host, cloud_port))
    checks.append(_check_gpu_ids([cloud_gpu_id, edge_gpu_id], required=require_gpu))
    return PreflightReport(mode=mode, checks=checks)


def format_preflight_table(report: PreflightReport) -> str:
    rows = [(item.status.upper(), item.name, item.detail) for item in report.checks]
    status_width = max(len("STATUS"), *(len(row[0]) for row in rows))
    name_width = max(len("CHECK"), *(len(row[1]) for row in rows))
    lines = [f"{'STATUS':<{status_width}}  {'CHECK':<{name_width}}  DETAIL"]
    for status, name, detail in rows:
        lines.append(f"{status:<{status_width}}  {name:<{name_width}}  {detail}")
    lines.append(f"OK: {str(report.ok).lower()}")
    return "\n".join(lines)
