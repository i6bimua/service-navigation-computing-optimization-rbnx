from pathlib import Path

from robonix_compute import preflight


def _make_valid_tree(tmp_path: Path) -> dict:
    internnav_root = tmp_path / "InternNav"
    (internnav_root / "scripts/eval/configs").mkdir(parents=True)
    (internnav_root / "scripts/eval/measure_edge_cloud_s2_runtime.py").write_text("", encoding="utf-8")
    (internnav_root / "scripts/eval/configs/analysis_cfg.py").write_text("", encoding="utf-8")
    (internnav_root / "scripts/eval/configs/h1_internvla_n1_async_cfg.py").write_text("", encoding="utf-8")
    data_root = tmp_path / "data"
    checkpoint_path = tmp_path / "checkpoints/InternVLA-N1"
    s1_model_path = tmp_path / "checkpoints/InternVLA-N1-S1"
    output_dir = tmp_path / "outputs/run"
    data_root.mkdir()
    checkpoint_path.mkdir(parents=True)
    s1_model_path.mkdir(parents=True)
    output_dir.parent.mkdir()
    return {
        "mode": "habitat_eval",
        "internnav_root": internnav_root,
        "data_root": data_root,
        "checkpoint_path": checkpoint_path,
        "s1_model_path": s1_model_path,
        "output_dir": output_dir,
        "analysis_config": "scripts/eval/configs/analysis_cfg.py",
        "edge_config": "scripts/eval/configs/h1_internvla_n1_async_cfg.py",
        "cloud_bind_host": "127.0.0.1",
        "cloud_port": 0,
        "cloud_gpu_id": 0,
        "edge_gpu_id": 0,
    }


def test_preflight_passes_with_valid_tree(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(preflight, "_gpu_count", lambda: 1)

    report = preflight.run_preflight(**_make_valid_tree(tmp_path))

    assert report.ok
    assert all(item.status == "ok" for item in report.checks)
    assert "OK: true" in preflight.format_preflight_table(report)


def test_preflight_reports_missing_required_paths(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(preflight.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(preflight, "_gpu_count", lambda: None)
    config = _make_valid_tree(tmp_path)
    config["checkpoint_path"] = tmp_path / "missing-checkpoint"

    report = preflight.run_preflight(**config)

    assert not report.ok
    assert any(item.name == "full model checkpoint" and item.status == "error" for item in report.checks)
    assert any(item.name == "python import: habitat" and item.status == "error" for item in report.checks)
