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
CATALOG_NAME = "robonix.service.navigation.vln"

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


def test_package_name_encodes_the_service_kind():
    # The catalog derives `kind` from the second dotted segment. This package
    # owns a navigation runtime and drives the chassis itself rather than
    # sequencing other services, which is the service boundary — see
    # CHANGELOG 0.3.0.
    assert CATALOG_NAME.split(".")[0] == "robonix"
    assert CATALOG_NAME.split(".")[1] == "service"


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
    # The catalog filters on tags, so the kind has to be one of them.
    assert "service" in tags
    assert "skill" not in tags, "a service must not advertise itself as a skill"
    # The mechanism belongs here rather than in the package name — this is one of
    # the places the catalog review explicitly kept it.
    assert "navigation-computing-optimization" in tags


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
        assert entry["name"].startswith("robonix/service/navigation/vln/"), entry["name"]


def test_the_mandatory_driver_contract_is_declared(manifest):
    # rbnx boot sends Driver(CMD_INIT) against this; without it the on_init
    # handler never fires and every MCP call fails.
    names = {entry["name"] for entry in manifest["capabilities"]}
    assert "robonix/service/navigation/vln/driver" in names


def test_the_long_task_triple_is_complete(manifest):
    # The executor polls status until a terminal state and needs cancel to abort.
    names = {entry["name"] for entry in manifest["capabilities"]}
    assert {
        "robonix/service/navigation/vln/navigate",
        "robonix/service/navigation/vln/navigate/status",
        "robonix/service/navigation/vln/navigate/cancel",
    } <= names


def test_contract_ids_inside_the_toml_match_the_manifest(manifest):
    for entry in manifest["capabilities"]:
        text = (ROOT / entry["path"]).read_text(encoding="utf-8")
        match = re.search(r'^\s*id\s*=\s*"([^"]+)"', text, flags=re.M)
        assert match, f"no contract id in {entry['path']}"
        assert match.group(1) == entry["name"], (
            f"{entry['path']} declares {match.group(1)!r}, manifest says {entry['name']!r}"
        )


def test_every_contract_declares_kind_service_and_rpc_mode(manifest):
    for entry in manifest["capabilities"]:
        text = (ROOT / entry["path"]).read_text(encoding="utf-8")
        assert re.search(r'^\s*kind\s*=\s*"service"', text, flags=re.M), entry["path"]
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


def test_every_declared_contract_is_actually_registered_in_code(manifest):
    """A contract in the manifest but not in the provider is worse than absent.

    rbnx declares each manifest capability on atlas at startup, so a consumer
    resolves it, connects, and only then finds nothing serving it. This checks
    the two lists agree: `driver` is handled by the lifecycle decorators, and
    every other capability needs its own `@service.mcp("<id>")`.

    Replaces an earlier check on a `[semantics] user_invocable` flag. That flag
    appears nowhere in the robonix source tree — pilot lists every provider that
    ships a non-empty CAPABILITY.md and marks the ones whose kind is `skill`; it
    never reads `user_invocable`. The check therefore asserted a rule the
    framework does not have.
    """
    provider = (ROOT / "robonix_compute" / "rbnx" / "provider.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'@service\.mcp\(\s*"([^"]+)"', provider))

    declared = {entry["name"] for entry in manifest["capabilities"]}
    driver = {name for name in declared if name.endswith("/driver")}
    assert len(driver) == 1, f"expected exactly one lifecycle contract, got {driver}"

    assert registered == declared - driver, (
        f"manifest and provider disagree — declared but unregistered: "
        f"{sorted(declared - driver - registered)}; registered but undeclared: "
        f"{sorted(registered - declared)}"
    )
    # The lifecycle contract is served by the Driver handler, never as a tool.
    assert not (registered & driver)


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
