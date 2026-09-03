#!/usr/bin/env python3
"""
Check the docs for links that point at nothing.

`mkdocs --strict` catches broken links *between* docs pages. It can't catch
a README link to a docs page that doesn't exist, or a page that fell out of
the nav and became unreachable — both of which ship silently, because nobody
files a bug about documentation.

    python scripts/check_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def docs_url_targets() -> set[str]:
    """Every page the site will publish, as the URL path mkdocs generates."""
    targets = set()
    for path in DOCS.rglob("*.md"):
        rel = path.relative_to(DOCS).with_suffix("")
        slug = "" if rel.name == "index" else str(rel).replace("\\", "/")
        targets.add(slug)
    return targets


def main() -> int:
    problems: list[str] = []
    pages = docs_url_targets()

    # 1. README links into the docs site must resolve to a real page
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for label, target in LINK.findall(readme):
        if "chauhanavi21.github.io" not in target:
            continue
        # a bare site root has nothing after the domain to resolve
        if "github.io/Agent_Lens/" not in target:
            continue
        slug = target.split("github.io/Agent_Lens/", 1)[1].strip("/")
        if slug and slug not in pages:
            problems.append(f"README links to a docs page that doesn't exist: {slug!r} ({label})")

    # 2. every docs page must be reachable from the nav
    nav = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
    for page in sorted(pages):
        source = "index.md" if page == "" else f"{page}.md"
        if source not in nav:
            problems.append(f"{source} exists but is not in the mkdocs nav — it would be unreachable")

    # 3. relative links between docs pages
    for path in DOCS.rglob("*.md"):
        for label, target in LINK.findall(path.read_text(encoding="utf-8")):
            if target.startswith(("http", "#", "mailto:")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                rel = path.relative_to(ROOT)
                problems.append(f"{rel}: link to {target!r} ({label}) doesn't resolve")

    if problems:
        print(f"{len(problems)} documentation problem(s):")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"docs ok: {len(pages)} pages, all reachable, all links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
