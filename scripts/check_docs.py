#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\(([^)]+)\)")


def markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "README.zh-CN.md"]
    files.extend((ROOT / "docs").glob("*.md"))
    files.extend((ROOT / "benchmarks").rglob("*.md"))
    files.extend(
        ROOT / name
        for name in (
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "SECURITY.md",
            "CHANGELOG.md",
        )
    )
    return sorted(path for path in files if path.exists())


def local_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    split = urlsplit(target)
    if split.scheme or split.netloc:
        return None
    path = unquote(split.path)
    if not path:
        return None
    return (source.parent / path).resolve()


def validate_links() -> list[str]:
    failures: list[str] = []
    for source in markdown_files():
        text = source.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = local_target(source, raw_target)
            if target is not None and not target.exists():
                failures.append(
                    f"{source.relative_to(ROOT)}: missing local target {raw_target!r}"
                )
    return failures


def validate_svg() -> list[str]:
    failures: list[str] = []
    for path in sorted((ROOT / "docs" / "assets").glob("*.svg")):
        try:
            ElementTree.parse(path)
        except ElementTree.ParseError as exc:
            failures.append(f"{path.relative_to(ROOT)}: invalid SVG XML: {exc}")
    return failures


def main() -> None:
    failures = validate_links() + validate_svg()
    if failures:
        print("Documentation validation failed:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print(
        f"Documentation validation passed: "
        f"{len(markdown_files())} Markdown files, "
        f"{len(list((ROOT / 'docs' / 'assets').glob('*.svg')))} SVG files"
    )


if __name__ == "__main__":
    main()
