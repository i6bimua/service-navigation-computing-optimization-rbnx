import gzip
import json
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
    depth_checkpoint_path = tmp_path / "checkpoints/depth_anything_v2_metric_hypersim_vits.pth"
    output_dir = tmp_path / "outputs/run"
    episode_path = data_root / "vln_ce/raw_data/r2r/val_unseen/val_unseen.json.gz"
    episode_path.parent.mkdir(parents=True)
    episodes = [
        {
            "episode_id": episode_id,
            "scene_id": "data/scene_datasets/mp3d/test-scan/test-scan.glb",
        }
        for episode_id in range(1, 1840)
    ]
    with gzip.open(episode_path, "wt", encoding="utf-8") as handle:
        json.dump({"episodes": episodes}, handle)
    scene_dir = data_root / "scene_data/mp3d_ce/mp3d/test-scan"
    scene_dir.mkdir(parents=True)
    (scene_dir / "test-scan.glb").write_bytes(b"glb")
    (scene_dir / "test-scan.navmesh").write_bytes(b"navmesh")
    checkpoint_path.mkdir(parents=True)
    (checkpoint_path / "config.json").write_text("{}", encoding="utf-8")
    (checkpoint_path / "model.safetensors").write_bytes(b"weights")
    s1_model_path.mkdir(parents=True)
    (s1_model_path / "config.json").write_text("{}", encoding="utf-8")
    (s1_model_path / "model.safetensors").write_bytes(b"weights")
    depth_checkpoint_path.write_bytes(b"depth")
    output_dir.parent.mkdir()
    return {
        "mode": "habitat_eval",
        "internnav_root": internnav_root,
        "data_root": data_root,
        "checkpoint_path": checkpoint_path,
        "s1_model_path": s1_model_path,
        "depth_checkpoint_path": depth_checkpoint_path,
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
