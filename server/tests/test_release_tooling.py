"""
Release tooling tests.

Version drift is caught by CI, but the tool doing the catching needs to work
— and its bump path rewrites seven files, which is exactly the kind of thing
that silently half-succeeds.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402


def test_every_version_string_agrees():
    """The check CI runs, as a test — so `make test` catches drift too."""
    versions = release.current_versions()

    unreadable = [label for label, value in versions.items() if value is None]
    assert not unreadable, f"could not read a version from: {unreadable}"

    distinct = set(versions.values())
    assert len(distinct) == 1, f"versions have drifted: {versions}"


def test_all_seven_sites_are_present():
    """A moved file would make the checker silently stop watching it."""
    for site in release.sites():
        assert site.path.exists(), f"{site.label}: {site.path} is missing"
        assert site.read() is not None, f"{site.label}: pattern no longer matches"
    assert len(release.sites()) == 7


def test_the_served_version_is_derived_not_a_literal():
    """
    The FastAPI app's version appears at /openapi.json and on the docs page.
    It was hardcoded to 0.2.0 and drifted a whole release before anyone
    noticed, so it now reads __version__ — and this keeps it that way.
    """
    main = (ROOT / "server" / "agentlens_server" / "main.py").read_text(encoding="utf-8")
    assert "version=__version__" in main, "the served version was hardcoded again"
    assert 'version="0.' not in main, "a version literal crept back into main.py"


def test_semver_parsing():
    assert release.parse_semver("0.3.0") == (0, 3, 0)
    assert release.parse_semver("1.10.2") == (1, 10, 2)

    for bad in ("0.3", "v0.3.0", "latest", "0.3.0.1", ""):
        with pytest.raises(ValueError):
            release.parse_semver(bad)


def test_semantic_bumps():
    assert release.next_version("0.3.4", "patch") == "0.3.5"
    assert release.next_version("0.3.4", "minor") == "0.4.0"
    assert release.next_version("0.3.4", "major") == "1.0.0"

    with pytest.raises(ValueError):
        release.next_version("0.3.4", "sideways")


def test_bump_rewrites_a_site_and_is_reversible(tmp_path):
    """Exercise the rewrite on a copy, never on the real tree."""
    sample = tmp_path / "pyproject.toml"
    sample.write_text('[project]\nname = "x"\nversion = "0.2.0"\n', encoding="utf-8")

    import re

    site = release.VersionSite(
        sample,
        "sample",
        re.compile(r'^version = "([^"]+)"', re.MULTILINE),
        lambda v: f'version = "{v}"',
    )

    assert site.read() == "0.2.0"
    assert site.write("0.9.1") is True
    assert site.read() == "0.9.1"
    # the rest of the file survives the rewrite
    assert 'name = "x"' in sample.read_text(encoding="utf-8")


def test_bump_reports_failure_on_an_unmatched_file(tmp_path):
    import re

    sample = tmp_path / "nothing.toml"
    sample.write_text("no version here\n", encoding="utf-8")
    site = release.VersionSite(
        sample,
        "sample",
        re.compile(r'^version = "([^"]+)"', re.MULTILINE),
        lambda v: f'version = "{v}"',
    )
    assert site.read() is None
    assert site.write("1.0.0") is False


def test_changelog_documents_the_current_version():
    """A release with no changelog entry is a release nobody can evaluate."""
    version = next(v for v in release.current_versions().values() if v)
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog, f"CHANGELOG.md has no section for the current version {version}"


def test_check_command_exits_zero_when_in_sync(capsys):
    assert release.main(["check"]) == 0
    assert "agree" in capsys.readouterr().out
