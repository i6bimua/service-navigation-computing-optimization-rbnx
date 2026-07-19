#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


DEFAULT_DENY_PATTERNS = [
    re.compile(r"/home/"),
    re.compile(r"/data/[^\s`]+"),
    re.compile(r"iflab", re.IGNORECASE),
    re.compile(r"HF_TOKEN\s*="),
    re.compile(r"API_KEY\s*="),
    re.compile(r"OPENAI_API_KEY\s*="),
    re.compile(r"WANDB_API_KEY\s*="),
    re.compile(r"PASSWORD\s*="),
    re.compile(r"SECRET\s*="),
    re.compile(r"BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY"),
    re.compile(r"hf_[A-Za-z0-9]{20,}"),
    re.compile(r"RoboNix-CloudEdge-Skill-Toolkit"),
    re.compile(r"robonix_cloudedge"),
    re.compile(r"robonix-cloudedge"),
    re.compile(r"ROBONIX_CE_"),
    re.compile("A" + "ce" + "VLN", re.IGNORECASE),
]

BINARY_SUFFIXES = {
    ".safetensors",
    ".pth",
    ".pt",
    ".ckpt",
    ".onnx",
    ".npy",
    ".npz",
    ".pkl",
    ".h5",
    ".hdf5",
    ".bag",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".mp4",
    ".zip",
    ".tar",
    ".gz",
}
ALLOWED_BINARY_PATHS = {
    "docs/assets/demo/habitat_demo.mp4",
}

SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "dist", "build", ".venv", "outputs", "logs", "data", "checkpoints"}
SKIP_FILES = {"scripts/release_audit.py"}
DENY_PATH_PARTS = {"clash-for-linux", "latex"}
REQUIRED_PATHS = {
    "README.md",
    "README.zh-CN.md",
    "LICENSE",
    "CITATION.cff",
    "CHANGELOG.md",
    "configs/supported_models.json",
    "benchmarks/r2r_ce/metadata.yaml",
    "docs/assets/benchmark_overview.svg",
    "docs/assets/compute_optimization_architecture.png",
    "docs/assets/demo/habitat_demo.gif",
    "docs/assets/demo/habitat_demo.mp4",
    "docs/assets/demo/habitat_running_images.png",
}
FORBIDDEN_PATHS = {
    "setup.py",
    "robonix_cloudedge",
    "package_manifest.yaml",
    "capabilities",
}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit RoboNix-Compute-Optimization-Skill release tree for common publishing hazards.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-file-mb", type=float, default=20.0)
    args = parser.parse_args()

    root = args.root.resolve()
    failures: list[str] = []
    max_bytes = int(args.max_file_mb * 1024 * 1024)

    for relative in sorted(REQUIRED_PATHS):
        if not (root / relative).exists():
            failures.append(f"missing required release path: {relative}")
    for relative in sorted(FORBIDDEN_PATHS):
        if (root / relative).exists():
            failures.append(f"forbidden legacy release path: {relative}")
    for path in sorted((root / "docs").glob("*.md")):
        failures.append(
            f"documentation must remain in the root README files: {path.relative_to(root)}"
        )

    for path in iter_files(root):
        rel = path.relative_to(root)
        if rel.as_posix() in SKIP_FILES:
            continue
        if any(part in DENY_PATH_PARTS for part in rel.parts):
            failures.append(f"blocked path component: {rel}")
        if path.suffix in BINARY_SUFFIXES and rel.as_posix() not in ALLOWED_BINARY_PATHS:
            failures.append(f"blocked artifact suffix: {rel}")
        if path.stat().st_size > max_bytes:
            failures.append(f"file too large: {rel} ({path.stat().st_size} bytes)")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in DEFAULT_DENY_PATTERNS:
            if pattern.search(text):
                failures.append(f"denied pattern {pattern.pattern!r}: {rel}")

    if failures:
        print("Release audit failed:")
        for item in failures:
            print(f"  - {item}")
        raise SystemExit(1)
    print(f"Release audit passed: {root}")


if __name__ == "__main__":
    main()
