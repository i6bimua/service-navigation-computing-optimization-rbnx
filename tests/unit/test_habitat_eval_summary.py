import json
import subprocess
import sys
from pathlib import Path


def test_summarize_habitat_eval_outputs_metrics(tmp_path: Path):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "result.json").write_text(
        json.dumps(
            {
                "sucs_all": 0.5,
                "spls_all": 0.4,
                "oss_all": 0.75,
                "nes_all": 2.5,
                "length": 2,
            }
        ),
        encoding="utf-8",
    )
    (eval_dir / "progress.json").write_text(
        "\n".join(
            [
                json.dumps({"episode_id": 1, "success": 1.0, "spl": 0.8, "os": 1.0, "ne": 1.0, "steps": 10}),
                json.dumps({"episode_id": 2, "success": 0.0, "spl": 0.0, "os": 0.5, "ne": 4.0, "steps": 20}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "edge_cloud_s2_summary.json").write_text(
        json.dumps(
            {
                "control_step_count": 3,
                "edge_cloud_rtt_delay_ms": 100.0,
                "mean_s2_total_wall_time_ms": 12.5,
                "mean_s1_round_trip_wall_time_ms": 4.0,
                "cloud_peak_memory_gb_1024": 20.0,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "edge_cloud_s2_control_steps.csv").write_text(
        "\n".join(
            [
                "episode_id,step_id,s2_total_wall_time_s,s1_round_trip_wall_time_s,sync_total_latency_s",
                "1,1,0.010,0.004,0.014",
                "1,2,0.020,0.008,0.028",
                "1,3,0.030,0.012,0.042",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "scripts/summarize_habitat_eval.py", str(tmp_path), "--format", "json"],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["episode_count"] == 2
    assert payload["metrics"]["sr"] == 0.5
    assert payload["metrics"]["spl"] == 0.4
    assert payload["metrics"]["ne"] == 2.5
    assert payload["mean_steps"] == 15
    assert payload["runtime"]["mean_s2_total_wall_time_ms"] == 12.5
    assert payload["runtime"]["p95_s2_total_wall_time_ms"] == 30.0
    assert payload["runtime"]["p95_s1_round_trip_wall_time_ms"] == 12.0
    assert payload["runtime"]["edge_cloud_rtt_delay_ms"] == 100.0


def test_summarize_habitat_eval_handles_missing_runtime_files(tmp_path: Path):
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    (eval_dir / "progress.json").write_text(
        json.dumps({"episode_id": 1, "success": 1.0, "spl": 0.5, "os": 1.0, "ne": 2.0, "steps": 7}) + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, "scripts/summarize_habitat_eval.py", str(tmp_path), "--format", "json"],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["episode_count"] == 1
    assert payload["metrics"]["sr"] == 1.0
    assert payload["runtime"]["p95_s2_total_wall_time_ms"] is None
