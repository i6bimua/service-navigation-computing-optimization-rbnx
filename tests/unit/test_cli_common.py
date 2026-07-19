import json
from pathlib import Path

from robonix_compute.cli.common import load_json_mapping


def test_load_json_mapping_expands_environment(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ROBONIX_COMPUTE_CLOUD_HOST", "10.0.0.8")
    monkeypatch.setenv("ROBONIX_COMPUTE_CLOUD_PORT", "8765")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "cloud_host": "${ROBONIX_COMPUTE_CLOUD_HOST}",
                "cloud_port": "${ROBONIX_COMPUTE_CLOUD_PORT}",
                "nested": ["${ROBONIX_COMPUTE_CLOUD_HOST}"],
            }
        ),
        encoding="utf-8",
    )

    payload = load_json_mapping(str(config_path))

    assert payload["cloud_host"] == "10.0.0.8"
    assert payload["cloud_port"] == "8765"
    assert payload["nested"] == ["10.0.0.8"]
