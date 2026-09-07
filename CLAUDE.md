# CLAUDE.md

Guidance for Claude Code (and other agents) working in this repository.

## What this project is

myIDM — a personal, build-to-learn segmented download manager in Python. See
[`PROJECT.md`](PROJECT.md) for the domain overview and
[`.claude/prds/personal-download-manager.prd.md`](.claude/prds/personal-download-manager.prd.md)
for requirements.

## Source-of-truth documents

- [`PROJECT.md`](PROJECT.md) — what and why.
- [`CONSTRAINTS.md`](CONSTRAINTS.md) — architectural invariants, forbidden
  dependencies, security standards, Git conventions. **Read it before making
  changes. Never weaken a constraint to make a change pass.**
- [`docs/superpowers/plans/2026-09-07-myidm-segmented-engine.md`](docs/superpowers/plans/2026-09-07-myidm-segmented-engine.md)
  — the current implementation plan (milestone 1).

## Environment

- Python 3.11+ (required — code uses `X | None` syntax and `unlink(missing_ok=)`).
- Windows is the primary dev platform; keep everything cross-platform.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
pip install pytest ruff
```

## Commands

| Task | Command |
|------|---------|
| Build | none — pure Python, no build step |
| Run | `python -m myidm <url> [-n SEGMENTS] [-o OUTPUT_DIR]` |
| Test | `python -m pytest -q` |
| Single test | `python -m pytest tests/test_engine.py::test_name -q` |
| Lint | `ruff check .` |
| Format | `ruff format .` |

There is no dev server. Tests must not hit the network — they use the local
HTTP server fixture in `tests/conftest.py`.

## Workflow

- TDD for non-trivial logic: failing test first, then implementation (see the
  plan — each task is structured this way).
- One plan task per commit; commit only when `pytest` and `ruff check` pass.
- Conventional Commit messages (`feat:`, `fix:`, `test:`, `docs:`).
- Commit trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

## Layout

```
myidm/
  __init__.py      version only
  engine.py        pure library: probe, split, segmented download, resume, fallback
  __main__.py      argparse CLI + progress rendering (no engine logic here)
tests/
  conftest.py      make_server fixture (local range-capable HTTP server)
  test_engine.py   engine unit + integration tests
  test_cli.py      CLI end-to-end test
```

Keep engine logic out of `__main__.py` and UI concerns out of `engine.py` (see
`CONSTRAINTS.md`).
