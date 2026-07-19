import gzip
import json
from pathlib import Path

from robonix_compute.benchmark_data import validate_benchmark_data


def _write_r2r_data(root: Path, *, include_navmesh: bool = True) -> None:
    episode_path = root / "vln_ce/raw_data/r2r/val_unseen/val_unseen.json.gz"
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

    scene_dir = root / "scene_data/mp3d_ce/mp3d/test-scan"
    scene_dir.mkdir(parents=True)
    (scene_dir / "test-scan.glb").write_bytes(b"glb")
    if include_navmesh:
        (scene_dir / "test-scan.navmesh").write_bytes(b"navmesh")


def test_validate_core_benchmark_data(tmp_path: Path):
    _write_r2r_data(tmp_path)

    report = validate_benchmark_data(tmp_path)

    assert report.ok
    assert report.datasets == ("r2r",)
    assert any(check.name == "R2R-CE val_unseen" for check in report.checks)
    assert any(check.name == "MP3D-CE scenes" for check in report.checks)


def test_validate_core_reports_missing_scene_asset(tmp_path: Path):
    _write_r2r_data(tmp_path, include_navmesh=False)

    report = validate_benchmark_data(tmp_path)

    assert not report.ok
    scene_check = next(check for check in report.checks if check.name == "MP3D-CE scenes")
    assert scene_check.status == "error"
    assert "test-scan" in scene_check.detail
