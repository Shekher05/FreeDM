# Milestone 1 — council-revised tasks

Source: [`plan.md`](plan.md) · council review:
[`.claude/council-cache/local-council.md`](../.claude/council-cache/local-council.md)

Convention (`CONSTRAINTS.md`): one task = one commit; commit only when
`python -m pytest -q` **and** `ruff check .` are green. TDD for non-trivial
logic — failing test first.

Status coming in: `split_ranges` committed (`01e2cb1`); `derive_filename`
implemented in the working tree, uncommitted, 8 tests passing.

---

## Task 1: Harden `derive_filename`, then commit

**Description:** `CONSTRAINTS.md` already claims output filenames are
"sanitized," but the shipped `derive_filename` only strips directory
separators. Close the gaps the security lens flagged, then make the first commit
for this function.

**Acceptance criteria:**
- [ ] Strips/rejects on the primary Windows platform: `:` (NTFS alternate data
      streams), ASCII control characters (incl. `\x00`, `\n` that `unquote` can
      inject), and trailing dots / spaces.
- [ ] A result that is empty, `.`, `..`, or a Windows reserved device name
      (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, case-insensitive)
      falls back to `"download.bin"`.
- [ ] Existing behaviour unchanged: `Content-Disposition` `filename=` wins, then
      URL path basename (percent-decoded), then default.
- [ ] RFC 5987 `filename*=` is explicitly out of scope — one-line comment says so.

**Verification:**
- [ ] Tests pass: `python -m pytest tests/test_engine.py -q -k derive_filename`
- [ ] Lint: `ruff check .`
- [ ] New tests cover `:`,  `\x00`, `CON`, `..`, trailing-dot cases
- [ ] `git commit -m "feat: derive_filename with path-traversal and Windows-name hardening"`

**Dependencies:** None

**Files likely touched:** `myidm/engine.py`, `tests/test_engine.py`

**Estimated scope:** S (2 files)

---

## Task 2: Sidecar load/save — atomic write, validator-gated, bounds-checked

**Description:** Original plan Task 3, revised. The resume sidecar
(`<name>.myidm.json`) is the contract in `CONSTRAINTS.md`. Make its write atomic,
make the load refuse anything it cannot trust, and bounds-check the per-segment
counts before they are handed to seek logic.

**Acceptance criteria:**
- [ ] `_part_path` / `_meta_path` as in the original plan (`.part`,
      `.myidm.json` suffixes).
- [ ] `_save_progress` writes `<meta>.tmp` then `os.replace(tmp, meta)` — never a
      partial `meta`. No `fsync` (durability scope is process-restart, not power
      loss — documented in the module docstring).
- [ ] `_load_progress(meta, url, size, etag)` returns the saved list **only**
      when: file parses as JSON; `url` and `size` match; `etag` matches **and is
      non-empty**. Empty/absent `etag` → `None` (forces a clean restart).
- [ ] `_load_progress` also returns `None` if `progress` is not a list of the
      expected length, or any entry is not an `int` in `0..(segment length)`.
- [ ] Missing / corrupt / mismatched file → `None`, no exception.

**Verification:**
- [ ] Tests pass: `python -m pytest tests/test_engine.py -q -k "sidecar or progress"`
- [ ] Lint: `ruff check .`
- [ ] New tests: round-trip; rejected on url/size/etag mismatch; rejected on
      empty etag; rejected on out-of-range progress entry; a `*.tmp` left behind
      by a simulated crash does not affect the next load
- [ ] `git commit -m "feat: atomic, validator-gated resume sidecar"`

**Dependencies:** None (independent of Task 1)

**Files likely touched:** `myidm/engine.py`, `tests/test_engine.py`

**Estimated scope:** S–M (2 files)

---

## Checkpoint: Foundation (after Tasks 1–2)
- [ ] `python -m pytest -q` fully green
- [ ] `ruff check .` clean
- [ ] Manual: hand-write a corrupt `.myidm.json`, confirm `_load_progress` → `None`

---

## Task 3: Range-capable `make_server` fixture with failure-injection toggles

**Description:** Original plan Task 4, widened. The council's highest-rated single
edit: the fixture must be able to misbehave, or Tasks 5–7 test nothing that
matters.

**Acceptance criteria:**
- [ ] Base fixture as in the original plan: `make_server(data, support_range=True)`
      → server with `.url`, `.data`, `.served_bytes`; HEAD + GET; `206` +
      `Content-Range` for `Range` when enabled; `ETag: "test-etag"`.
- [ ] Toggle: `reject_head=True` → HEAD returns `405` (probe must still work via
      ranged GET).
- [ ] Toggle: `drop_after=<n>` → the server closes the connection after writing
      `n` body bytes for a given response (exercises the retry loop).
- [ ] Toggle: `range_returns_200=True` → a `Range` request gets `200` + the whole
      body (exercises `RangeNotSupported` / fallback).
- [ ] Toggle: `etag_changes_after=<n>` → the `ETag` changes once `n` requests
      have been served (exercises resume-invalidation).
- [ ] `no_etag=True` → responses carry no `ETag` header.
- [ ] Servers shut down at teardown; bind to port 0; `daemon_threads = True`.

**Verification:**
- [ ] `python -m pytest tests/ -q --collect-only` succeeds (existing tests still
      collect)
- [ ] A throwaway `test_fixture_toggles` proves each toggle, then is removed or
      kept minimal
- [ ] Lint: `ruff check .`
- [ ] `git commit -m "test: range-capable HTTP fixture with failure injection"`

**Dependencies:** None

**Files likely touched:** `tests/conftest.py`

**Estimated scope:** M (1 file, several behaviours)

---

## Checkpoint: Test infrastructure (after Task 3)
- [ ] Every toggle demonstrably works before any engine code relies on it

---

## Task 4: Probe (ranged GET) + shared `requests.Session`

**Description:** First slice of the engine. Replace the HEAD probe with a
`Range: bytes=0-0` GET and introduce the module-level `Session` all requests
share.

**Acceptance criteria:**
- [ ] Module-level `_session = requests.Session()` with an `HTTPAdapter`
      (`pool_maxsize` ≈ `MAX_SEGMENTS`). All engine HTTP goes through it.
- [ ] `_probe(url) -> ProbeResult` doing one `GET` with
      `headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"}`,
      `timeout=30`, `allow_redirects=True`.
- [ ] Returns: `size` (from `Content-Range` total, or `Content-Length` on a
      `200`), `accept_ranges` (`True` iff status `206`), `validator` (`ETag`
      then `Last-Modified` then `""`), `filename` (via `derive_filename`),
      `resolved_url` (`r.url`).
- [ ] Redirect to a non-`http(s)` scheme raises a clear error (per
      `CONSTRAINTS.md`).
- [ ] `constants`: `CHUNK = 65536`, `SEGMENT_RETRIES = 5`, `SAVE_INTERVAL = 1.0`,
      `MAX_SEGMENTS = 32`.

**Verification:**
- [ ] Tests pass: `python -m pytest tests/test_engine.py -q -k probe`
- [ ] New tests: probe against range server (accept=True, size correct);
      against `support_range=False` (accept=False); against `reject_head=True`
      (still works); validator falls back to `Last-Modified` when `no_etag=True`
- [ ] Lint: `ruff check .`
- [ ] `git commit -m "feat: ranged-GET probe over a shared requests session"`

**Dependencies:** Task 3

**Files likely touched:** `myidm/engine.py`, `tests/test_engine.py`

**Estimated scope:** S–M (2 files)

---

## Task 5: Segmented download + orchestration

**Description:** Original plan Task 5 + Task 6's guarantees, with the council's
correctness fixes baked in. Range-capable path only; the fallback and final
`download()` wiring land in Task 6.

**Acceptance criteria:**
- [ ] `_download_segment(resolved_url, part, start, end, progress, idx, cancel)`:
      `GET` with `Range` + `Accept-Encoding: identity`, `allow_redirects=False`,
      `timeout=30`; writes at `f.seek(pos)`; `progress[idx] += len(chunk)`;
      single writer per slot (unchanged invariant — comment says "single-writer,
      not atomicity").
- [ ] Retry: up to `SEGMENT_RETRIES`, `time.sleep(2 ** attempt + random.random())`,
      resume from current offset. A `200` response raises `RangeNotSupported`.
- [ ] `cancel` is a `threading.Event`; checked each chunk; set on any segment
      exception and on `KeyboardInterrupt` propagation.
- [ ] `_download_segmented(resolved_url, final, size, validator, segments, cb)`:
      `split_ranges`, load sidecar (Task 2), preallocate `part` with
      `f.truncate(size)` only on a fresh start, `ThreadPoolExecutor` with
      `max_workers=min(len(ranges), MAX_SEGMENTS)`, save sidecar every
      `SAVE_INTERVAL`, `_emit` progress with an engine-side rolling-window speed.
- [ ] **Before `os.replace(part, final)`**: assert `sum(progress) == size` and
      every `progress[i] == ranges[i] length`. On failure, raise (leave `.part`
      + sidecar for a retry).
- [ ] On success: `os.replace`, then `meta.unlink(missing_ok=True)`.
- [ ] Executor torn down with `cancel_futures=True`.

**Verification:**
- [ ] Tests pass: `python -m pytest tests/test_engine.py -q -k segmented`
- [ ] New tests: end-to-end 5 MB / 4 segments byte-equal, `.part`+sidecar gone;
      progress callback ends at `(size, size)`; `drop_after` mid-segment still
      completes via retry; a server under-reporting `Content-Length` trips the
      integrity assert
- [ ] Lint: `ruff check .`
- [ ] `git commit -m "feat: parallel segmented download with integrity gate and cancellation"`

**Dependencies:** Task 2, Task 4

**Files likely touched:** `myidm/engine.py`, `tests/test_engine.py`

**Estimated scope:** M (2 files)

---

## Task 6: Single-connection fallback + coherent `download()` wiring

**Description:** Original plan Task 7, but `_download_single` has **no resume
logic**, and `download()` is wired correctly in this same commit so the public
entry point is never knowingly broken.

**Acceptance criteria:**
- [ ] `_download_single(resolved_url, final, size, cb)`: one streamed `GET`
      (`Accept-Encoding: identity`, `timeout=30`), always writes `part` from
      zero (`"wb"`), no sidecar read, progress saved every `SAVE_INTERVAL` as
      `[done]`, `os.replace` on completion, `meta.unlink(missing_ok=True)`.
- [ ] `download(url, dest_dir=".", segments=8, progress_cb=None) -> Path`:
      `segments` clamped to `1..MAX_SEGMENTS`; `dest_dir` created; probe;
      `final = dest_dir / filename`.
- [ ] No range support or unknown size → `_download_single`.
- [ ] `RangeNotSupported` raised mid-segmented-run → delete `.part` + sidecar,
      retry once via `_download_single`.
- [ ] `RangeNotSupported` is defined once and used for both the predicted and
      the mid-flight case, but `download()` never re-raises it to its caller.

**Verification:**
- [ ] Tests pass: `python -m pytest -q`
- [ ] New tests: `support_range=False` downloads via fallback byte-equal;
      `range_returns_200=True` (mid-flight) cleans up and completes;
      `_download_single` called twice leaves a correct file (no half-resume)
- [ ] Lint: `ruff check .`
- [ ] `git commit -m "feat: single-connection fallback; coherent download() entry point"`

**Dependencies:** Task 5

**Files likely touched:** `myidm/engine.py`, `tests/test_engine.py`

**Estimated scope:** M (2 files)

---

## Task 7: Resume proof (segmented) + fix the flaky slack assertion

**Description:** Original plan Task 6, kept as its own task because it is the
proof of the headline feature. Also fixes the assertion the scalability lens
flagged as flaky.

**Acceptance criteria:**
- [ ] Test: pre-seed `part` + a valid sidecar with segments 0–1 done, 2–3
      untouched; `download()` produces a byte-equal file.
- [ ] Assertion on re-fetched bytes accounts for one full `CHUNK` (65536) of
      retry slack per in-flight segment, not a flat `+1024`.
- [ ] Test: `etag_changes_after=1` → the second run ignores the stale sidecar and
      restarts clean, producing a correct file (no corruption).
- [ ] Any engine change the first test forces is minimal and noted in the commit.

**Verification:**
- [ ] Tests pass: `python -m pytest -q -k "resume or stale"`
- [ ] Full suite: `python -m pytest -q`
- [ ] Lint: `ruff check .`
- [ ] `git commit -m "test: resume fetches only the remainder; stale validator restarts clean"`

**Dependencies:** Task 6

**Files likely touched:** `tests/test_engine.py`, possibly `myidm/engine.py`

**Estimated scope:** S (1–2 files)

---

## Checkpoint: Engine complete (after Tasks 4–7)
- [ ] Full suite green, including every failure-injection test
- [ ] End-to-end byte-equal; `.part` + sidecar removed on success
- [ ] Resume fetches only the remainder; mutated `ETag` forces a clean restart
- [ ] **Review with human before Phase 4**

---

## Task 8: `python -m myidm` CLI

**Description:** Original plan Task 8, with speed computed only in the engine and
credentials redacted from error output.

**Acceptance criteria:**
- [ ] `myidm/__main__.py`: `main(argv=None) -> int`. Args: `url` positional,
      `-n/--segments` (default 8), `-o/--output-dir` (default `.`).
- [ ] `\r`-updated progress line: bar, percent, speed, ETA — speed and ETA come
      from the `bytes_per_sec` the engine passes; **no second speed calculation
      / sample buffer in the CLI**.
- [ ] Final `Saved to <path>` line.
- [ ] Return codes: `0` success, `1` `KeyboardInterrupt` (prints resume hint),
      `2` any other exception.
- [ ] Exception path redacts credentials: a `user:pass@host` URL in the message
      is printed as `user:***@host` (or the netloc stripped).
- [ ] No engine logic in `__main__.py` (`CONSTRAINTS.md`).

**Verification:**
- [ ] Tests pass: `python -m pytest tests/test_cli.py -q`
- [ ] New tests: downloads a 1 MB file, `rc == 0`, `"Saved to"` in output;
      bad URL → `rc == 2`; a URL with embedded credentials does not appear
      verbatim in captured output
- [ ] Lint: `ruff check .`
- [ ] `git commit -m "feat: python -m myidm CLI with engine-side progress"`

**Dependencies:** Task 6 (needs a working `download()`)

**Files likely touched:** `myidm/__main__.py`, `tests/test_cli.py`

**Estimated scope:** M (2 files)

---

## Task 9: README, docs, and manual smoke test

**Description:** Original plan Task 9, expanded with the council's
"document, don't build" items.

**Acceptance criteria:**
- [ ] `README.md`: what it is, learning-project disclaimer, install, usage,
      how resume works, "resume survives Ctrl-C / process restart, not a hard
      power loss".
- [ ] `# ponytail:` comment on `split_ranges` (or `_download_segmented`) naming
      static equal segmentation as a known ceiling, with "upgrade path:
      work-stealing / dynamic re-segmentation".
- [ ] New section in `CONSTRAINTS.md` or a `docs/milestone-2-security.md`:
      SSRF / redirect-scheme validation, `dest_dir` symlink checks, shared-token
      auth for the localhost service, credential redaction, global concurrency
      ceiling — all listed as Milestone 2 obligations.
- [ ] `docs/superpowers/plans/2026-09-07-myidm-segmented-engine.md`: the two open
      questions (reboot vs restart; testing no-range servers) marked resolved,
      pointing at this plan.
- [ ] Manual smoke test run and its result (checksum stable across a clean run
      vs an interrupted+resumed run; a 1-segment vs 8-segment timing) recorded
      in the commit message.

**Verification:**
- [ ] `python -m pytest -q` and `ruff check .` still green
- [ ] `python -m myidm <large-file-url> -n 8`, Ctrl-C ~40%, re-run, compare hash
- [ ] One run against a host without range support completes
- [ ] `git commit -m "docs: README, ceiling note, milestone-2 security checklist, smoke test"`

**Dependencies:** Task 8

**Files likely touched:** `README.md`, `myidm/engine.py`, `CONSTRAINTS.md` or
`docs/milestone-2-security.md`, `docs/superpowers/plans/2026-09-07-myidm-segmented-engine.md`

**Estimated scope:** M (3–5 files, docs only)

---

## Checkpoint: Complete (after Task 9)
- [ ] All acceptance criteria met
- [ ] PRD success metrics either demonstrated or explicitly deferred with a note
- [ ] Branch ready for review / merge to `main`

---

## Parallelization notes

- Tasks 1 and 2 are independent — safe to do in parallel.
- Task 3 is independent of 1–2 — safe in parallel.
- Tasks 4 → 5 → 6 → 7 are a strict chain (shared `download()` / engine state).
- Task 8 needs Task 6; Task 9 needs Task 8.
