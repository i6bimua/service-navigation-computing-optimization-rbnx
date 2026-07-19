from __future__ import annotations

from dataclasses import asdict, dataclass
import gzip
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    relative_path: str
    expected_episodes: int
    label: str


DATASET_SPECS = {
    "r2r": DatasetSpec(
        name="r2r",
        relative_path="vln_ce/raw_data/r2r/val_unseen/val_unseen.json.gz",
        expected_episodes=1839,
        label="R2R-CE val_unseen",
    ),
    "r2r_short": DatasetSpec(
        name="r2r_short",
        relative_path="vln_ce/raw_data/r2r/val_unseen_short/val_unseen_short.json.gz",
        expected_episodes=609,
        label="R2R-CE val_unseen short",
    ),
    "r2r_medium": DatasetSpec(
        name="r2r_medium",
        relative_path="vln_ce/raw_data/r2r/val_unseen_medium/val_unseen_medium.json.gz",
        expected_episodes=615,
        label="R2R-CE val_unseen medium",
    ),
    "r2r_long": DatasetSpec(
        name="r2r_long",
        relative_path="vln_ce/raw_data/r2r/val_unseen_long/val_unseen_long.json.gz",
        expected_episodes=609,
        label="R2R-CE val_unseen long",
    ),
    "rxr": DatasetSpec(
        name="rxr",
        relative_path="vln_ce/raw_data/rxr/val_unseen/val_unseen_guide_en_compat.json.gz",
        expected_episodes=3669,
        label="RxR-CE English val_unseen compatibility split",
    ),
    "reverie_nav_proxy": DatasetSpec(
        name="reverie_nav_proxy",
        relative_path="vln_ce/raw_data/reverie_nav_proxy/val_unseen/val_unseen.json.gz",
        expected_episodes=3433,
        label="REVERIE navigation-only proxy val_unseen",
    ),
}

PROFILES = {
    "core": ("r2r",),
    "extended": tuple(DATASET_SPECS),
}


@dataclass(frozen=True)
class DataCheck:
    name: str
    status: str
    detail: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class DataValidationReport:
    data_root: str
    profile: str
    datasets: tuple[str, ...]
    checks: list[DataCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_root": self.data_root,
            "profile": self.profile,
            "datasets": list(self.datasets),
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
        }


def _scene_name(scene_id: Any) -> str:
    return Path(str(scene_id)).stem


def _load_episodes(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    episodes = payload.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("top-level 'episodes' must be a list")
    return episodes


def _selected_datasets(profile: str, datasets: Iterable[str] | None) -> tuple[str, ...]:
    if datasets is None:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile: {profile}")
        return PROFILES[profile]
    selected = tuple(dict.fromkeys(str(name) for name in datasets))
    unknown = [name for name in selected if name not in DATASET_SPECS]
    if unknown:
        raise ValueError(f"unknown datasets: {unknown}")
    return selected


def validate_benchmark_data(
    data_root: Path,
    *,
    profile: str = "core",
    datasets: Iterable[str] | None = None,
) -> DataValidationReport:
    root = data_root.expanduser().resolve()
    selected = _selected_datasets(profile, datasets)
    checks: list[DataCheck] = []
    referenced_scenes: set[str] = set()

    if not root.is_dir():
        checks.append(DataCheck("data root", "error", f"missing directory: {root}"))
        return DataValidationReport(str(root), profile, selected, checks)
    checks.append(DataCheck("data root", "ok", str(root)))

    for name in selected:
        spec = DATASET_SPECS[name]
        path = root / spec.relative_path
        if not path.is_file():
            checks.append(DataCheck(spec.label, "error", f"missing file: {path}"))
            continue
        try:
            episodes = _load_episodes(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            checks.append(DataCheck(spec.label, "error", f"cannot parse {path}: {exc}"))
            continue
        actual = len(episodes)
        if actual != spec.expected_episodes:
            checks.append(
                DataCheck(
                    spec.label,
                    "error",
                    f"episodes={actual}; expected={spec.expected_episodes}; path={path}",
                )
            )
        else:
            checks.append(DataCheck(spec.label, "ok", f"episodes={actual}; path={path}"))
        for episode in episodes:
            scene_id = episode.get("scene_id")
            if scene_id:
                referenced_scenes.add(_scene_name(scene_id))

    scene_root = root / "scene_data/mp3d_ce/mp3d"
    if not scene_root.is_dir():
        checks.append(DataCheck("MP3D-CE scenes", "error", f"missing directory: {scene_root}"))
    else:
        missing_glb = [
            scene
            for scene in sorted(referenced_scenes)
            if not (scene_root / scene / f"{scene}.glb").is_file()
        ]
        missing_navmesh = [
            scene
            for scene in sorted(referenced_scenes)
            if not (scene_root / scene / f"{scene}.navmesh").is_file()
        ]
        if missing_glb or missing_navmesh:
            checks.append(
                DataCheck(
                    "MP3D-CE scenes",
                    "error",
                    f"referenced={len(referenced_scenes)}; "
                    f"missing_glb={missing_glb}; missing_navmesh={missing_navmesh}",
                )
            )
        else:
            checks.append(
                DataCheck(
                    "MP3D-CE scenes",
                    "ok",
                    f"{len(referenced_scenes)} referenced scenes have GLB and navmesh assets",
                )
            )

    if "rxr" in selected:
        rxr_gt = root / "vln_ce/raw_data/rxr/val_unseen/val_unseen_guide_gt.json.gz"
        if rxr_gt.is_file():
            checks.append(DataCheck("RxR nDTW ground truth", "ok", str(rxr_gt)))
        else:
            checks.append(DataCheck("RxR nDTW ground truth", "error", f"missing file: {rxr_gt}"))

    return DataValidationReport(str(root), profile, selected, checks)


def format_data_validation_table(report: DataValidationReport) -> str:
    rows = [(check.status.upper(), check.name, check.detail) for check in report.checks]
    status_width = max(len("STATUS"), *(len(row[0]) for row in rows))
    name_width = max(len("CHECK"), *(len(row[1]) for row in rows))
    lines = [f"{'STATUS':<{status_width}}  {'CHECK':<{name_width}}  DETAIL"]
    for status, name, detail in rows:
        lines.append(f"{status:<{status_width}}  {name:<{name_width}}  {detail}")
    lines.append(f"OK: {str(report.ok).lower()}")
    return "\n".join(lines)
