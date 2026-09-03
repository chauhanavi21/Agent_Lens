# Releases

Every package is versioned together — they share a wire format, and letting
them drift would mean maintaining a compatibility matrix. `scripts/release.py`
keeps the seven version strings in sync and CI fails a pull request if they
disagree.

```bash
python scripts/release.py check      # do all seven agree?
python scripts/release.py bump 0.4.0 # set them together
python scripts/release.py plan 0.4.0 # the release checklist
```

Publishing a GitHub release triggers PyPI (trusted publishing) and npm
(with provenance). See [CHANGELOG.md](https://github.com/chauhanavi21/Agent_Lens/blob/main/CHANGELOG.md).

# Development

```bash
make install     # SDKs, server, UI
make test        # every suite: python, server, typescript, interop
make lint        # ruff check + format check
make up          # docker compose: postgres + server + UI
```

CI runs the Python SDK across 3.9–3.13 (plus macOS and Windows), the server
across 3.10–3.13 and against real Postgres, the TypeScript SDK on Node
18/20/22, the UI test suite and build on Node 20/22, a cross-language
wire-compatibility check, lint, and both Docker images.

Test counts: **51** Python SDK, **82** server, **16** TypeScript SDK, and
**56** UI — the last of which run entirely in demo mode, so they double as a
check that the offline experience works with no server at all. Releases publish to PyPI via trusted publishing and to
npm with provenance — no long-lived tokens in either.
