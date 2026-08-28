#!/usr/bin/env python3
"""
Version management across the monorepo.

The version lives in seven files — two pyproject.toml, two package.json, two
Python `__version__`, and a TypeScript constant. That's seven chances to
drift, and the publish workflows reject a tag that doesn't match the
packaged version, so drift is discovered at the worst possible moment: after
you've cut a release.

    python scripts/release.py check          # are they all in sync?
    python scripts/release.py bump 0.3.0     # set all seven
    python scripts/release.py bump minor     # or bump semantically
    python scripts/release.py plan 0.3.0     # what to run, in order

`check` is wired into CI, so drift fails a pull request rather than a
release.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


@dataclass
class VersionSite:
    """One place a version string lives, and how to read or rewrite it."""

    path: Path
    label: str
    pattern: re.Pattern[str]
    template: Callable[[str], str]

    def read(self) -> Optional[str]:
        if not self.path.exists():
            return None
        match = self.pattern.search(self.path.read_text(encoding="utf-8"))
        return match.group(1) if match else None

    def write(self, version: str) -> bool:
        if not self.path.exists():
            return False
        text = self.path.read_text(encoding="utf-8")
        updated, count = self.pattern.subn(lambda _m: self.template(version), text, count=1)
        if count == 0:
            return False
        self.path.write_text(updated, encoding="utf-8")
        return True


def sites() -> list[VersionSite]:
    return [
        VersionSite(
            ROOT / "sdk" / "pyproject.toml",
            "Python SDK (pyproject)",
            re.compile(r'^version = "([^"]+)"', re.MULTILINE),
            lambda v: f'version = "{v}"',
        ),
        VersionSite(
            ROOT / "sdk" / "agentlens" / "__init__.py",
            "Python SDK (__version__)",
            re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE),
            lambda v: f'__version__ = "{v}"',
        ),
        VersionSite(
            ROOT / "server" / "pyproject.toml",
            "Server (pyproject)",
            re.compile(r'^version = "([^"]+)"', re.MULTILINE),
            lambda v: f'version = "{v}"',
        ),
        VersionSite(
            ROOT / "server" / "agentlens_server" / "__init__.py",
            "Server (__version__)",
            re.compile(r'^__version__ = "([^"]+)"', re.MULTILINE),
            lambda v: f'__version__ = "{v}"',
        ),
        VersionSite(
            ROOT / "sdk-ts" / "package.json",
            "TypeScript SDK (package.json)",
            re.compile(r'"version": "([^"]+)"'),
            lambda v: f'"version": "{v}"',
        ),
        VersionSite(
            ROOT / "sdk-ts" / "src" / "index.ts",
            "TypeScript SDK (VERSION)",
            re.compile(r"export const VERSION = '([^']+)';"),
            lambda v: f"export const VERSION = '{v}';",
        ),
        VersionSite(
            ROOT / "ui" / "package.json",
            "UI (package.json)",
            re.compile(r'"version": "([^"]+)"'),
            lambda v: f'"version": "{v}"',
        ),
    ]


def current_versions() -> dict[str, Optional[str]]:
    return {site.label: site.read() for site in sites()}


def parse_semver(version: str) -> tuple[int, int, int]:
    match = SEMVER.match(version)
    if not match:
        raise ValueError(f"'{version}' is not a semantic version. Use MAJOR.MINOR.PATCH, e.g. 0.3.0.")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def next_version(current: str, part: str) -> str:
    major, minor, patch = parse_semver(current)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown bump '{part}'. Use major, minor, patch, or an explicit version.")


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


def cmd_check(args: argparse.Namespace) -> int:
    versions = current_versions()
    missing = [label for label, value in versions.items() if value is None]
    found = {label: value for label, value in versions.items() if value is not None}
    distinct = sorted(set(found.values()))

    width = max(len(label) for label in versions)
    for label, value in versions.items():
        marker = " " if value and len(distinct) == 1 else "!"
        print(f"  {marker} {label.ljust(width)}  {value or '<not found>'}")

    if missing:
        print(f"\nCould not read a version from: {', '.join(missing)}")
        print("The file moved, or the pattern in scripts/release.py needs updating.")
        return 1

    if len(distinct) != 1:
        print(f"\nVersions have drifted: {', '.join(distinct)}")
        print("Fix with: python scripts/release.py bump <version>")
        return 1

    print(f"\nAll {len(versions)} version strings agree: {distinct[0]}")
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    versions = current_versions()
    known = [v for v in versions.values() if v]
    if not known:
        print("Could not read any current version.", file=sys.stderr)
        return 1

    target = args.version
    if target in ("major", "minor", "patch"):
        target = next_version(sorted(known)[-1], target)
    else:
        try:
            parse_semver(target)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1

    print(f"Setting every version to {target}\n")
    changed = 0
    for site in sites():
        before = site.read()
        if site.write(target):
            changed += 1
            note = "unchanged" if before == target else f"{before} → {target}"
            print(f"  {site.label}: {note}")
        else:
            print(f"  {site.label}: FAILED to write", file=sys.stderr)
            return 1

    print(f"\n{changed} files updated.")
    print("\nNext:")
    print(f"  1. Add a {target} section to CHANGELOG.md")
    print("  2. make test")
    print(f'  3. git commit -am "I release {target}"')
    print(f"  4. git tag v{target} && git push origin main --tags")
    print(f"  5. Create the GitHub release for v{target} — that's what")
    print("     triggers the PyPI and npm publish workflows")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Print the release checklist, with the state of each item where knowable."""
    target = args.version
    try:
        parse_semver(target)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"Release checklist for v{target}\n")

    versions = current_versions()
    in_sync = len({v for v in versions.values() if v}) == 1
    at_target = all(v == target for v in versions.values() if v)
    print(f"  [{'x' if in_sync else ' '}] version strings agree")
    print(
        f"  [{'x' if at_target else ' '}] versions set to {target}"
        + ("" if at_target else "   → python scripts/release.py bump " + target)
    )

    changelog = ROOT / "CHANGELOG.md"
    has_entry = changelog.exists() and f"## [{target}]" in changelog.read_text(encoding="utf-8")
    print(f"  [{'x' if has_entry else ' '}] CHANGELOG.md has a {target} section")

    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
        ).stdout.strip()
        print(f"  [{'x' if not dirty else ' '}] working tree is clean")
    except FileNotFoundError:
        print("  [ ] working tree is clean  (git not available here)")

    print("  [ ] make test passes")
    print("  [ ] tag pushed: git tag v" + target + " && git push origin main --tags")
    print("  [ ] GitHub release published (triggers PyPI + npm)")
    print("\nThe publish workflows re-check that the tag matches the packaged")
    print("version, so a mismatch fails the release rather than shipping wrong.")
    return 0 if (in_sync and at_target and has_entry) else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Manage AgentLens versions and releases.")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Verify every version string agrees.")
    check.set_defaults(func=cmd_check)

    bump = sub.add_parser("bump", help="Set every version string.")
    bump.add_argument("version", help="An explicit version, or major/minor/patch")
    bump.set_defaults(func=cmd_bump)

    plan = sub.add_parser("plan", help="Show the release checklist.")
    plan.add_argument("version")
    plan.set_defaults(func=cmd_plan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
