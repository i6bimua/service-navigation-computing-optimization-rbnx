"""Guards the RoboNix package surface against publishing hazards.

The package-catalog CI (`syswonder/robonix-package-catalog`,
`scripts/build_catalog.py`) does not clone this repository: it reads
`package_manifest.yaml` from the default branch root over the GitHub API and
fails the PR on any metadata problem. Every rule it enforces is asserted here so
the failure shows up locally instead of in someone else's pull request.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "package_manifest.yaml"
CAPABILITIES_DIR = ROOT / "capabilities"


def _pyproject_field(field: str) -> str:
    """Read a top-level `[project]` string field.

    Hand-rolled rather than via tomllib: the CI matrix still covers Python 3.9
    and 3.10, where tomllib does not exist, and pulling in tomli just for two
    lookups is not worth a dependency.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    match = re.search(rf'^{re.escape(field)}\s*=\s*"([^"]+)"', project, flags=re.M)
    assert match, f"{field} not found in [project]"
    return match.group(1)

# The catalog entry this package will be published under. `package.name` must
# match it byte for byte.
CATALOG_NAME = "robonix.skill.compute_optimization"

# Copied from robonix-package-catalog/scripts/build_catalog.py.
MAINTAINER_RE = re.compile(r"^[^<>\n]+ <[^<>\s@]+@[^<>\s@]+\.[^<>\s@]+>$")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def package(manifest) -> dict:
    assert isinstance(manifest.get("package"), dict), "manifest missing `package` mapping"
    return manifest["package"]


def test_manifest_lives_at_the_repository_root():
    # The catalog fetches `package_manifest.yaml` from the root of the default
    # branch; anywhere else is invisible to it.
    assert MANIFEST_PATH.is_file()


def test_manifest_version_field():
    assert yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))["manifestVersion"] == 1


def test_package_name_matches_the_catalog_entry(package):
    assert package["name"] == CATALOG_NAME


def test_package_name_encodes_the_skill_kind():
    # The catalog derives `kind` from the second dotted segment.
    assert CATALOG_NAME.split(".")[0] == "robonix"
    assert CATALOG_NAME.split(".")[1] == "skill"


def test_version_is_a_string_not_a_float(package):
    """`version: 0.2` would parse as a float and the catalog CI rejects it."""
    version = package["version"]
    assert isinstance(version, str), f"version parsed as {type(version).__name__}"
    assert version.strip()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"expected semantic version, got {version!r}"


def test_version_agrees_across_every_metadata_file(package):
    citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
    assert package["version"] == _pyproject_field("version")
    assert package["version"] == str(citation["version"])
    assert f"## {package['version']} —" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def test_description_and_license_are_present(package):
    assert isinstance(package["description"], str) and package["description"].strip()
    # Must be a non-empty SPDX identifier.
    assert package["license"] == "MulanPSL-2.0"


def test_license_matches_pyproject(package):
    assert package["license"] == _pyproject_field("license")


def test_tags_is_a_non_empty_list_of_strings(package):
    tags = package["tags"]
    assert isinstance(tags, list) and tags
    assert all(isinstance(tag, str) and tag.strip() for tag in tags)
    assert "skill" in tags


def test_maintainers_match_the_catalog_regex(package):
    maintainers = package["maintainers"]
    assert isinstance(maintainers, list) and maintainers
    for entry in maintainers:
        assert MAINTAINER_RE.match(entry), f"not 'Name <email@domain>': {entry!r}"


def test_lifecycle_scripts_are_declared(manifest):
    assert manifest["build"] == "bash scripts/build.sh"
    assert manifest["start"] == "bash scripts/start.sh"
    assert manifest["stop"] == "bash scripts/stop.sh"


def test_every_capability_entry_has_a_name_and_a_resolvable_path(manifest):
    capabilities = manifest["capabilities"]
    assert isinstance(capabilities, list) and capabilities
    for entry in capabilities:
        assert isinstance(entry, dict), f"capability entry must be a mapping: {entry!r}"
        assert isinstance(entry.get("name"), str) and entry["name"].strip()
        # These are package-local contracts, so each needs a path that exists.
        path = entry.get("path")
        assert isinstance(path, str) and (ROOT / path).is_file(), f"missing contract file: {path!r}"


def test_capability_names_are_under_the_declared_namespace(manifest):
    for entry in manifest["capabilities"]:
        assert entry["name"].startswith("robonix/skill/compute_optimization/"), entry["name"]


def test_the_mandatory_driver_contract_is_declared(manifest):
    # rbnx boot sends Driver(CMD_INIT) against this; without it the on_init
    # handler never fires and every MCP call fails.
    names = {entry["name"] for entry in manifest["capabilities"]}
    assert "robonix/skill/compute_optimization/driver" in names


def test_the_long_task_triple_is_complete(manifest):
    # The executor polls status until a terminal state and needs cancel to abort.
    names = {entry["name"] for entry in manifest["capabilities"]}
    assert {
        "robonix/skill/compute_optimization/navigate",
        "robonix/skill/compute_optimization/navigate/status",
        "robonix/skill/compute_optimization/navigate/cancel",
    } <= names


def test_contract_ids_inside_the_toml_match_the_manifest(manifest):
    for entry in manifest["capabilities"]:
        text = (ROOT / entry["path"]).read_text(encoding="utf-8")
        match = re.search(r'^\s*id\s*=\s*"([^"]+)"', text, flags=re.M)
        assert match, f"no contract id in {entry['path']}"
        assert match.group(1) == entry["name"], (
            f"{entry['path']} declares {match.group(1)!r}, manifest says {entry['name']!r}"
        )


def test_every_contract_declares_kind_skill_and_rpc_mode(manifest):
    for entry in manifest["capabilities"]:
        text = (ROOT / entry["path"]).read_text(encoding="utf-8")
        assert re.search(r'^\s*kind\s*=\s*"skill"', text, flags=re.M), entry["path"]
        assert re.search(r'^\s*type\s*=\s*"rpc"', text, flags=re.M), entry["path"]


def test_referenced_idl_files_exist(manifest):
    """Package-local `idl` paths must resolve under capabilities/lib/."""
    for entry in manifest["capabilities"]:
        text = (ROOT / entry["path"]).read_text(encoding="utf-8")
        idl = re.search(r'^\s*idl\s*=\s*"([^"]+)"', text, flags=re.M)
        assert idl, f"no idl in {entry['path']}"
        reference = idl.group(1)
        if reference.startswith("lifecycle/"):
            continue  # global contract, supplied by the robonix source tree
        assert (CAPABILITIES_DIR / "lib" / reference).is_file(), f"missing IDL: {reference}"


def test_user_invocable_contracts_are_marked(manifest):
    """Pilot only offers `user_invocable` contracts to the LLM."""
    invocable = set()
    for entry in manifest["capabilities"]:
        text = (ROOT / entry["path"]).read_text(encoding="utf-8")
        if re.search(r"^\s*user_invocable\s*=\s*true", text, flags=re.M):
            invocable.add(entry["name"])
    assert "robonix/skill/compute_optimization/navigate" in invocable
    # The lifecycle contract must NOT be user-invocable.
    assert "robonix/skill/compute_optimization/driver" not in invocable


def test_every_srv_declares_a_request_response_split():
    srv_files = sorted((CAPABILITIES_DIR / "lib").rglob("*.srv"))
    assert srv_files
    for path in srv_files:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines.count("---") == 1, f"{path.name} needs exactly one '---' separator"


def test_capability_manual_has_frontmatter_description():
    # The catalog and Pilot both read the frontmatter description.
    text = (ROOT / "CAPABILITY.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = yaml.safe_load(text.split("---")[1])
    assert isinstance(frontmatter.get("description"), str)
    assert frontmatter["description"].strip()


def test_readme_exists_for_the_catalog_page():
    # build_catalog.py fetches README.md and renders it as the package page.
    assert (ROOT / "README.md").is_file()


def test_generated_build_output_is_git_ignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "rbnx-build/" in ignored
