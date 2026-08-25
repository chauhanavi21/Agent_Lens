## What this changes

<!-- One or two sentences. Link an issue if there is one. -->

## Why

<!-- The reasoning matters more than the diff. If this is a design decision
     with a tradeoff, say what you gave up. -->

## Checklist

- [ ] Tests cover the behaviour, including what *shouldn't* happen
- [ ] `ruff check . && ruff format --check .` passes
- [ ] If a wire-format field changed, both SDKs changed and `scripts/interop_check.py` passes
- [ ] If this is structural, ARCHITECTURE.md is updated
