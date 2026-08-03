from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]


def test_supported_model_matrix_is_evidence_based() -> None:
    payload = json.loads((ROOT / "configs" / "supported_models.json").read_text(encoding="utf-8"))
    models = payload["models"]
    validated = [model for model in models if model["status"] == "validated"]

    assert [model["id"] for model in validated] == ["internvla-n1-dualvln"]
    assert validated[0]["checkpoint"] == "InternRobotics/InternVLA-N1-DualVLN"
    assert "OpenVLA" in payload["unsupported_examples"]


def test_benchmark_headline_values_match_structured_results() -> None:
    path = ROOT / "benchmarks" / "r2r_ce" / "results" / "main_results.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))

    def get(method: str) -> dict[str, str]:
        return next(
            row
            for row in rows
            if row["platform"] == "Orin+A100" and row["method"] == method
        )

    edge = get("Edge Only")
    naive = get("Naive ECC")
    ours = get("Navigation Computing")

    assert round(float(edge["avg_step_latency_ms"]) / float(ours["avg_step_latency_ms"]), 2) == 2.22
    assert round(float(ours["sr_pct"]) - float(naive["sr_pct"]), 1) == 6.1
    assert round(float(ours["spl_pct"]) - float(naive["spl_pct"]), 1) == 12.7
    assert len(rows) == 10


def test_all_svg_assets_are_valid_xml() -> None:
    svg_paths = sorted((ROOT / "docs" / "assets").glob("*.svg"))
    assert svg_paths
    for path in svg_paths:
        ElementTree.parse(path)


def test_docs_directory_contains_assets_only() -> None:
    assert not list((ROOT / "docs").glob("*.md"))
    assert (ROOT / "docs" / "assets" / "compute_optimization_architecture.png").is_file()


def test_contributor_and_citation_metadata_match() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")

    profile = "https://github.com/zhengzihaoPKU"
    assert profile in readme
    assert profile in readme_zh
    assert "— Leader." in readme
    assert "— Maintainer." in readme
    assert "author  = {Cao, Hangyu and Zheng, Zihao}" in readme
    assert "family-names: Cao" in citation
    assert "given-names: Hangyu" in citation
    assert "family-names: Zheng" in citation
    assert "given-names: Zihao" in citation
