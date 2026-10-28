from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_HEADINGS = (
    "## About Me",
    "## GitHub Snapshot",
    "## Technical Arsenal",
    "## Featured Projects",
    "## Recently on GitHub",
    "## Experience & Recognition",
    "## GitHub Engineering Activity",
    "## 🐍 My Contribution Journey",
    "## Developer Philosophy",
)


def validate_content(content: str, repository_root: Path) -> list[str]:
    errors: list[str] = []
    if len(content.strip()) < 2_000:
        errors.append("README is unexpectedly short")
    if "{{" in content or "{%" in content or "{#" in content:
        errors.append("README contains unresolved Jinja syntax")
    for heading in REQUIRED_HEADINGS:
        if heading not in content:
            errors.append(f"Missing required heading: {heading}")

    for reference in re.findall(r'(?:src|href)="(\./[^"#?]+)"', content):
        target = repository_root / reference[2:]
        if not target.is_file():
            errors.append(f"Missing local asset: {reference}")

    for url in re.findall(r'https?://[^\s"<>]+', content):
        clean = url.replace("&amp;", "&").rstrip(")].,;'")
        parsed = urlparse(clean)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"Invalid URL: {clean}")
        if parsed.username or parsed.password:
            errors.append(f"URL must not contain credentials: {clean}")

    for svg in (repository_root / "assets").rglob("*.svg"):
        try:
            ET.parse(svg)
        except ET.ParseError as error:
            errors.append(f"Invalid SVG {svg.relative_to(repository_root)}: {error}")

    return errors


def validate_file(path: Path, repository_root: Path) -> list[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [f"Could not read {path}: {error}"]
    return validate_content(content, repository_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate the generated profile README.")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = validate_file(args.readme, args.root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"Validated {args.readme} successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
