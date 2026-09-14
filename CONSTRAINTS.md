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
  dependency without the owner's explicit approval in the task. This still
  holds after Milestone 2 — the local service (`myidm/service.py`,
  `myidm/netcheck.py`, `myidm/paths.py`) is stdlib-only
  (`http.server`, `threading`, `concurrent.futures`, `secrets`, `hmac`,
  `ipaddress`, `socket`, `subprocess`, `signal`); `requests` is used by the
  engine and by `myidm/client.py` to talk to that service.
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
  `progress[idx]` slot has exactly one writer thread. `cancel` (Milestone 2)
  is a `threading.Event` that is only ever read inside a worker — it adds no
  new writer.
- **The resume sidecar is a contract.** `<name>.myidm.json` is exactly:
  `{"url": str, "size": int, "etag": str, "progress": [int, ...]}` where each
  int is bytes completed for that segment. A load is accepted only when
  `url`, `size`, and `etag` all match the current probe.
- The working file is `<name>.part`, preallocated to full size; it is renamed
  to `<name>` only on full success, and both sidecars are then deleted.
- No packaging — the tool runs as `python -m myidm` from the repo root. No
  `pyproject.toml`, no `setup.py`, no entry-point scripts. Milestone 2 adds
  sub-commands (`serve`/`_serve`/`stop`/`add`/`status`/`pause`/`resume`/
  `cancel`) to the same `python -m myidm` entry point, not packaging.

## Security standards

- Output filenames are reduced to a safe basename by `derive_filename`: it drops
  directory separators and any NTFS alternate-data-stream (`:`) suffix, strips
  control characters and trailing dots/spaces, and falls back to `download.bin`
  for an empty result, `.`/`..`, or a Windows reserved device name. A hostile
  `Content-Disposition` or URL therefore cannot write outside the chosen output
  directory. Do not bypass it. (Deeper containment — symlink/realpath checks on
  the output directory itself — is a Milestone 2 obligation, tracked in
  [`docs/milestone-2-security.md`](docs/milestone-2-security.md).)
- The local service (`myidm/service.py`) binds `127.0.0.1` only and requires a
  shared-secret Bearer token on every request, checked with
  `hmac.compare_digest` (constant-time — any web page in the browser can also
  reach a localhost port, so a naive `==` comparison would leak the token
  byte-by-byte via timing). Every URL a caller hands the service (`add`, and
  the CLI's bare one-shot path) is validated by `netcheck.assert_allowed_url`
  before a request is made on its behalf — rejects non-`http(s)` schemes and
  loopback/private/link-local/reserved/multicast IPs (including the
  `169.254.169.254` cloud-metadata address), resolving hostnames first.
- Do not add code that follows redirects to non-`http(s)` schemes.
- Credentials embedded in a download URL (`user:pass@host`) are never
  persisted: `myidm/redact.py`'s `strip_credentials` scrubs them from
  `queue.json` and every HTTP snapshot response before they are written or
  serialized; `redact()` scrubs them from error messages and CLI output.

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
