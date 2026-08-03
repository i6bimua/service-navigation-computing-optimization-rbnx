#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any


RESULT_KEYS = {
    "sr": ("sucs_all", "success"),
    "spl": ("spls_all", "spl"),
    "os": ("oss_all", "os"),
    "ne": ("nes_all", "ne"),
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise ValueError(f"expected JSON object at {path}:{line_number}")
            rows.append(payload)
    return rows


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _mean_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    if not values:
        return None
    return mean(values)


def _p95_float(rows: list[dict[str, Any]], key: str) -> float | None:
    values = sorted(float(row[key]) for row in rows if row.get(key) not in {None, ""})
    if not values:
        return None
    index = max(0, math.ceil(0.95 * len(values)) - 1)
    return values[index]


def _runtime_or_csv_p95(runtime: dict[str, Any], runtime_key: str, rows: list[dict[str, Any]], csv_key: str) -> float | None:
    if runtime.get(runtime_key) is not None:
        return float(runtime[runtime_key])
    value = _p95_float(rows, csv_key)
    if value is None:
        return None
    return value * 1000.0


def _sum_int(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(row.get(key) or 0) for row in rows)


def _sum_first_available(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> int:
    total = 0
    for row in rows:
        for key in keys:
            if row.get(key) is not None:
                total += int(row.get(key) or 0)
                break
    return total


def _pick_metric(result: dict[str, Any], rows: list[dict[str, Any]], result_key: str, row_key: str) -> float | None:
    if result.get(result_key) is not None:
        return float(result[result_key])
    return _mean_float(rows, row_key)


def load_eval_summary(run_root: Path) -> dict[str, Any]:
    run_root = run_root.expanduser().resolve()
    eval_dir = run_root / "eval"
    result_path = eval_dir / "result.json"
    progress_path = eval_dir / "progress.json"
    runtime_path = run_root / "edge_cloud_s2_summary.json"
    control_steps_path = run_root / "edge_cloud_s2_control_steps.csv"
    result = _read_json(result_path)
    progress_rows = _read_jsonl(progress_path)
    runtime = _read_json(runtime_path)
    control_step_rows = _read_csv(control_steps_path)

    metrics = {
        name: _pick_metric(result, progress_rows, result_key, row_key)
        for name, (result_key, row_key) in RESULT_KEYS.items()
    }
    episode_count = int(result.get("length") or len(progress_rows))
    summary: dict[str, Any] = {
        "run_root": str(run_root),
        "episode_count": episode_count,
        "metrics": metrics,
        "mean_steps": _mean_float(progress_rows, "steps"),
        "total_steps": _sum_int(progress_rows, "steps"),
        "total_s2_calls": _sum_int(progress_rows, "s2_call_count"),
        "total_s2_decisions": _sum_int(progress_rows, "s2_decision_count"),
        "timeout_count": int(runtime.get("timeout_count") or _sum_first_available(progress_rows, ("timeout_count", "edge_cloud_timeout_count"))),
        "latent_reuse_count": int(
            runtime.get("latent_reuse_count") or _sum_first_available(progress_rows, ("latent_reuse_count", "reuse_count"))
        ),
        "late_absorbed_count": int(
            runtime.get("late_absorbed_count") or _sum_first_available(progress_rows, ("late_absorbed_count",))
        ),
        "runtime": {
            "control_step_count": runtime.get("control_step_count"),
            "edge_cloud_rtt_delay_ms": runtime.get("edge_cloud_rtt_delay_ms"),
            "edge_cloud_send_delay_ms": runtime.get("edge_cloud_send_delay_ms"),
            "edge_cloud_reply_delay_ms": runtime.get("edge_cloud_reply_delay_ms"),
            "mean_s2_total_wall_time_ms": runtime.get("mean_s2_total_wall_time_ms"),
            "median_s2_total_wall_time_ms": runtime.get("median_s2_total_wall_time_ms"),
            "p95_s2_total_wall_time_ms": _runtime_or_csv_p95(
                runtime, "p95_s2_total_wall_time_ms", control_step_rows, "s2_total_wall_time_s"
            ),
            "mean_s1_round_trip_wall_time_ms": runtime.get("mean_s1_round_trip_wall_time_ms"),
            "median_s1_round_trip_wall_time_ms": runtime.get("median_s1_round_trip_wall_time_ms"),
            "p95_s1_round_trip_wall_time_ms": _runtime_or_csv_p95(
                runtime, "p95_s1_round_trip_wall_time_ms", control_step_rows, "s1_round_trip_wall_time_s"
            ),
            "mean_sync_total_latency_ms": runtime.get("mean_sync_total_latency_ms"),
            "p95_sync_total_latency_ms": _runtime_or_csv_p95(
                runtime, "p95_sync_total_latency_ms", control_step_rows, "sync_total_latency_s"
            ),
            "cloud_peak_memory_gb_1024": runtime.get("cloud_peak_memory_gb_1024"),
            "cloud_mean_memory_gb_1024": runtime.get("cloud_mean_memory_gb_1024"),
            "latent_payload_kb_fp32": runtime.get("latent_payload_kb_fp32"),
            "latent_payload_kb_bf16": runtime.get("latent_payload_kb_bf16"),
        },
        "artifacts": {
            "result_json": str(result_path) if result_path.is_file() else None,
            "progress_json": str(progress_path) if progress_path.is_file() else None,
            "runtime_summary_json": str(runtime_path) if runtime_path.is_file() else None,
            "control_steps_csv": str(control_steps_path) if control_steps_path.is_file() else None,
            "analysis_logs": str(eval_dir / "analysis_logs") if (eval_dir / "analysis_logs").is_dir() else None,
            "cloud_stdout": str(run_root / "cloud_eval_stdout.log") if (run_root / "cloud_eval_stdout.log").is_file() else None,
            "edge_stdout": str(run_root / "edge_stdout.log") if (run_root / "edge_stdout.log").is_file() else None,
        },
    }
    return summary


def _format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def print_table(summary: dict[str, Any]) -> None:
    rows = [
        ("episodes", summary["episode_count"]),
        ("SR", summary["metrics"]["sr"]),
        ("SPL", summary["metrics"]["spl"]),
        ("OS", summary["metrics"]["os"]),
        ("NE", summary["metrics"]["ne"]),
        ("mean steps", summary["mean_steps"]),
        ("total steps", summary["total_steps"]),
        ("total S2 calls", summary["total_s2_calls"]),
        ("total S2 decisions", summary["total_s2_decisions"]),
        ("timeout count", summary["timeout_count"]),
        ("latent reuse count", summary["latent_reuse_count"]),
        ("late absorbed count", summary["late_absorbed_count"]),
        ("mean S2 total latency ms", summary["runtime"]["mean_s2_total_wall_time_ms"]),
        ("median S2 total latency ms", summary["runtime"]["median_s2_total_wall_time_ms"]),
        ("p95 S2 total latency ms", summary["runtime"]["p95_s2_total_wall_time_ms"]),
        ("mean S1 round-trip ms", summary["runtime"]["mean_s1_round_trip_wall_time_ms"]),
        ("p95 S1 round-trip ms", summary["runtime"]["p95_s1_round_trip_wall_time_ms"]),
        ("RTT delay ms", summary["runtime"]["edge_cloud_rtt_delay_ms"]),
        ("cloud peak memory GiB", summary["runtime"]["cloud_peak_memory_gb_1024"]),
    ]
    width = max(len(name) for name, _ in rows)
    for name, value in rows:
        print(f"{name:<{width}}  {_format_value(value)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a real Habitat navigation computing optimization evaluation run.")
    parser.add_argument("run_root", type=Path, help="Run output directory containing eval/result.json.")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    args = parser.parse_args()
    summary = load_eval_summary(args.run_root)
    if args.format == "json":
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_table(summary)


if __name__ == "__main__":
    main()
