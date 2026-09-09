# CONSTRAINTS.md

Non-negotiable rules for this project. Agents and contributors must not weaken
these to make a change pass. If a constraint genuinely needs to change, change
it here first, in its own commit, with a reason.

## Floor (always)

- Tests pass: `python -m pytest -q` is green before any commit.
- Lint clean: `ruff check .` reports no errors.
- No secrets, credentials, or personal data committed.
- Every network call passes an explicit `timeout=` (currently 30s). No
  unbounded requests.

## Dependencies

- **Exactly one runtime dependency: `requests`.** Do not add another runtime
  dependency without the owner's explicit approval in the task.
- Dev-only tools allowed: `pytest`, `ruff`. Nothing else without approval.
- Standard library first. Reach for a new package only when stdlib genuinely
  cannot do the job in a few lines.

## Architecture invariants

- **`myidm/engine.py` is a pure library.** No `print`, no `sys.exit`, no
  `argparse`, no reading of environment/CLI. All user-interface concerns live
  in `myidm/__main__.py`. The engine communicates progress only through the
  `progress_cb(done: int, total: int, bytes_per_sec: float)` callback.
- **Concurrency model is fixed for milestone 1:** threads +
  `concurrent.futures`, not `asyncio`. Each download segment owns a disjoint
  byte range and its own file handle; segment writes take no lock. Each
  `progress[idx]` slot has exactly one writer thread.
- **The resume sidecar is a contract.** `<name>.myidm.json` is exactly:
  `{"url": str, "size": int, "etag": str, "progress": [int, ...]}` where each
  int is bytes completed for that segment. A load is accepted only when
  `url`, `size`, and `etag` all match the current probe.
- The working file is `<name>.part`, preallocated to full size; it is renamed
  to `<name>` only on full success, and both sidecars are then deleted.
- No packaging in milestone 1 — the tool runs as `python -m myidm` from the
  repo root. No `pyproject.toml`, no `setup.py`, no entry-point scripts yet.

## Security standards

- Output filenames are reduced to a safe basename by `derive_filename`: it drops
  directory separators and any NTFS alternate-data-stream (`:`) suffix, strips
  control characters and trailing dots/spaces, and falls back to `download.bin`
  for an empty result, `.`/`..`, or a Windows reserved device name. A hostile
  `Content-Disposition` or URL therefore cannot write outside the chosen output
  directory. Do not bypass it. (Deeper containment — symlink/realpath checks on
  the output directory itself — is a Milestone 2 obligation, tracked in the
  milestone-2 security notes.)
- (Milestone 2, when it lands) the local service binds to `127.0.0.1` only and
  requires a shared secret token on every request — any web page in the
  browser can also reach a localhost port.
- Do not add code that follows redirects to non-`http(s)` schemes.

## Git conventions

- Conventional Commit prefixes: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`,
  `chore:`.
- One plan task per commit (see the milestone plan). Commit only when the
  task's tests pass.
- Never `--no-verify`, never skip signing, never force-push a shared branch.
- Commit message trailer, per session configuration:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`

## Testing

- TDD for non-trivial logic: write the failing test, watch it fail, implement,
  watch it pass. Trivial one-liners do not need a test.
- Tests must not hit the network. Use the local `make_server` fixture in
  `tests/conftest.py`.

## Measured-only (no target yet — record real values once code exists)

| Metric | Today's value | Note |
|---|---|---|
| Test count | TBD — measure at end of milestone 1 | |
| Changed-line coverage | TBD — measure at end of milestone 1 | Hold at whatever the engine lands at |
| Slowest test | TBD | End-to-end tests spin up a local server; keep under a few seconds |

## Exceptions

| Exception | Reason | Owner | Expires |
|---|---|---|---|
| _(none)_ | | | |
