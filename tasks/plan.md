# Implementation Plan: Milestone 1 engine — council-revised task list

## Overview

This plan revises the remaining work in the original 2026-09-07 implementation
plan (fully superseded by this document; removed 2026-09-11 as redundant)
using a local-council review of that plan (review transcript also removed
2026-09-11 - its conclusions are folded into this document).

Tasks 1–2 of the original plan (`split_ranges`, `derive_filename`) are already
implemented. `split_ranges` is committed; `derive_filename` is in the working
tree, 8 tests green, uncommitted. This plan covers everything from there to the
end of Milestone 1, folding in the council's cheap correctness fixes and
structural simplifications, and explicitly deferring the rest.

Scope of a "task" here matches `CONSTRAINTS.md`: one task = one commit, committed
only when `python -m pytest -q` and `ruff check .` are both green.

## Revision 2 — local council, 2026-09-09

A second local council (review transcript removed 2026-09-11 as redundant -
its conclusions are folded into the deltas below) reviewed this plan. Verdict
from all three lenses: **proceed, with changes** —
no one wanted to stop or rethink. Deltas, folded into the tasks below:

**Add (correctness / security):**
- `If-Range: <validator>` on every segment GET *and* the single-connection
  resume GET when the validator is non-empty. Guards the mid-run
  resource-swap TOCTOU: a changed file returns `200`, which is already the
  `RangeNotSupported` → clean-restart path.
- Strip URL userinfo (`user:pass@`) before it is written to / matched against
  the sidecar. Credentials must never be persisted next to the download.
- The pre-promotion integrity check is `if not (...): raise`, never `assert`
  (`python -O` deletes asserts).
- If a download response carries `Content-Encoding`, raise — `Accept-Encoding:
  identity` is a request, not a guarantee, and decoded bytes desync offsets.
- Task 5 acceptance: the executor is fully joined before `os.replace(part,
  final)`, and the replace is wrapped in a short retry (Windows: AV / preview
  handles on `.part` cause transient `PermissionError`).

**Cut / simplify (over-built for a one-person tool):**
- Drop the cooperative-cancellation `threading.Event`. Replace with capped
  backoff `time.sleep(min(2 ** attempt, 8) + random.random())` and
  `executor.shutdown(cancel_futures=True)`. Worst-case Ctrl-C wait ~10s, no
  new state. (Add the Event back as its own task only if that wait annoys in
  real use.)
- Task 3 fixture: **two** toggles only — `drop_after=N` and `no_etag`.
  `reject_head` is dead (ranged-GET probe, no HEAD); `range_returns_200` is
  already covered by `support_range=False`; `etag_changes_after` is covered by
  a hand-written stale sidecar in the resume test.
- Engine speed is two stored floats — `(done - last_done) / (now - last_time)`
  per callback — not a rolling ring buffer. Delete `_ProgressLine._samples`
  in the CLI; it recomputes and discards a second number.
- Defer the mounted `HTTPAdapter(pool_maxsize=...)`. Plain
  `requests.Session()` now; add the adapter with a `# ponytail:` note only if
  32 segments produce connection-pool-full warnings.
- Fold Task 7 (resume proof) into Task 5/6 — it is one test plus a one-line
  constant fix, not a commit of its own. Target ~6–7 commits, not 9.
- Pull one real-network download forward to immediately after Task 6, not
  only Task 9.

**Write down explicitly (Task 9):**
- The automated suite deliberately does not cover the "faster than
  single-connection" success metric — 8 segments against a localhost fixture
  are *slower*. The 1-vs-N timing in the manual smoke test is **required**,
  not optional, and the smoke test includes one HTTPS redirect chain and one
  gzip endpoint.
- M2 security checklist gains: engine writes follow symlinks at both `final`
  and `.part` — realpath containment is required before the service is
  web-drivable; expiring presigned-redirect URLs are a known limitation;
  `f.truncate(size)` preallocation has no free-space check.

**Task 1 (contested — resolved):** keep the Windows-specific filename
hardening (`:` ADS, control chars, trailing dot/space, `.`/`..`, `CON`/`NUL`/
`COM#`/`LPT#`) — the owner's primary platform is Windows and it is ~10 lines
written once. Skip RFC 5987 `filename*=`. Ship the CONSTRAINTS.md wording fix
in the same commit.

---

## Architecture Decisions (deltas from the original plan)

- **Probe with a ranged `GET` (`Range: bytes=0-0`), not `HEAD`.** One request
  returns the real `206` + `Content-Range` (total size), the validator, and
  proves range support. Avoids the "HEAD said X, GET said Y" class of bug.
- **The probe's redirect-resolved URL (`r.url`) is canonical.** Every segment
  request uses it with `allow_redirects=False`. Prevents divergent bytes from a
  per-request-signed or round-robin CDN.
- **Resume requires a strong validator.** If the probe returns no `ETag` and no
  `Last-Modified`, the sidecar is not trusted — the download starts clean. An
  empty-string `etag` must never satisfy the match.
- **The sidecar write is atomic:** write `<name>.myidm.json.tmp`, then
  `os.replace`. A crash mid-write can no longer strand the whole download.
- **`download()` is coherent at every commit.** The original plan's Task 5 shipped
  a deliberate `raise RangeNotSupported` stand-in that Task 7 replaced, leaving
  the public entry point knowingly wrong across two commits on a shared branch.
  The segmented path and the single-connection fallback now land together
  (Task 6), so `download()` is always correct.
- **`_download_single` has no resume logic.** A server that ignored `Range` on
  the first request ignores it on re-run too, so single-connection resume can
  never succeed. It always starts from zero; the `.part` file is overwritten.
- **One `SAVE_INTERVAL` constant** for both the segmented and single paths.
- **Speed is computed once, in the engine.** `progress_cb(done, total,
  bytes_per_sec)` is the mandated signature (`CONSTRAINTS.md`); the engine does
  the rolling-window average and the CLI only formats it. No second speed
  calculation in `__main__.py`.
- **Per-download segment cap.** `download()` clamps `segments` to `MAX_SEGMENTS`
  (32). This is a footgun guard, *not* the out-of-scope `-k` multi-download cap.
- **`Accept-Encoding: identity` on every download GET.** A `Content-Encoding:
  gzip` response would desync byte offsets and corrupt the file.
- **Integrity gate before promotion.** `_download_segmented` asserts
  `sum(progress) == size` (and every segment full) before `os.replace(part,
  final)`. Servers lie about `Content-Length`; the honest test fixture will not
  catch it, so the assert must.
- **Cooperative cancellation.** A shared `threading.Event` is checked in the
  chunk loop; the executor is torn down with `cancel_futures=True`. Keeps the
  "Ctrl-C, then re-run to resume" flow from hanging on retry backoff.
- **The test fixture can misbehave on purpose.** `make_server` grows toggles for
  connection drop after N bytes, answering a `Range` with `200`, rejecting
  `HEAD`, and mutating the `ETag` on the second request. Tasks 6–8 depend on
  these.

## Task List

(The original per-task acceptance-criteria doc, `todo.md`, was removed
2026-09-11 as redundant now that M1 is code-complete; see `SESSION_GUIDE.md`
for what was implemented.)

### Phase 1: Foundation (pure functions + resume state)
- [ ] Task 1: Harden `derive_filename`, then commit
- [ ] Task 2: Sidecar load/save — atomic write, validator-gated, bounds-checked

### Checkpoint: Foundation
- [ ] `python -m pytest -q` green, `ruff check .` clean
- [ ] Corrupt/absent/mismatched sidecar all return `None`; a truncated temp file never replaces a good sidecar

### Phase 2: Test infrastructure
- [ ] Task 3: Range-capable `make_server` fixture with failure-injection toggles

### Checkpoint: Test infrastructure
- [ ] `python -m pytest -q --collect-only` succeeds
- [ ] A throwaway test proves each toggle (drop-after-N, range→200, reject-HEAD, mutate-ETag) behaves

### Phase 3: Download engine
- [ ] Task 4: Probe (ranged GET) + shared `requests.Session`
- [ ] Task 5: Segmented download + orchestration (cap, identity encoding, cancel Event, retry jitter, integrity assert)
- [ ] Task 6: Single-connection fallback + coherent `download()` wiring
- [ ] Task 7: Resume proof (segmented) + fix the flaky slack assertion

### Checkpoint: Engine complete
- [ ] Full suite green including failure-injection tests
- [ ] End-to-end download byte-equal to source; `.part` and sidecar deleted on success
- [ ] Resume fetches only the remainder; a mutated `ETag` on re-run forces a clean restart, not corruption
- [ ] Review with human before proceeding

### Phase 4: CLI
- [ ] Task 8: `python -m myidm` CLI — engine-side speed, credential redaction in errors

### Phase 5: Docs + smoke test
- [ ] Task 9: README, `# ponytail:` ceiling note, Milestone 2 security checklist, PRD open-question resolutions, manual smoke test

### Checkpoint: Complete
- [ ] All acceptance criteria met
- [x] Original 2026-09-07 plan's two open questions (reboot-vs-restart, testing
  no-range servers) resolved — see `SESSION_GUIDE.md` Task 9 notes
- [ ] Ready for review / merge to `main`

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `ThreadingHTTPServer` fixture is flaky on Windows CI (port reuse, slow teardown) | Medium | Bind to port 0, `daemon_threads=True`, explicit `shutdown()` in fixture teardown; keep blobs small (≤5 MB) |
| Cooperative-cancel `Event` adds threading complexity for marginal benefit on a personal tool | Low | Single `Event`, checked only in the chunk loop; if it turns hairy, fall back to capped retries + `cancel_futures=True` alone and note it |
| `requests` streaming + preallocated `r+b` seek pattern misbehaves under concurrent writes | Medium | Task 5 integration test uses 4 segments on a 5 MB blob and asserts byte-equality; each segment owns a disjoint range + its own handle (unchanged invariant) |
| Merging original Tasks 5+6+7 makes one task too big | Medium | Split into Task 4 (probe), Task 5 (segmented), Task 6 (fallback + wiring); each is ≤2 files |
| Speedup success metric (`N` segments faster than 1) is never measured before the optional manual smoke test | Low | Task 9 smoke test records a 1-vs-8 timing in the commit message; acceptable for a learning project |

## Open Questions

- **Resume durability:** confirmed as **process-restart / Ctrl-C only** — no
  `fsync`, so a hard power-loss re-fetches under a second of data. Task 2 and
  Task 9 document this; the PRD open question is resolved by decision, not code.
- **Should `derive_filename` also handle RFC 5987 `filename*=`?** Deferred — not
  load-bearing for the learning goal. Noted in Task 1.
- **Milestone 2 trust boundary** (any web page can call a localhost service):
  out of scope here. Task 9 writes the checklist; nothing is built.

## Deferred — documented, not built in Milestone 1

Recorded here and in Task 9's checklist so a future session knows they were a
conscious choice, not an oversight:

- `os.fsync` on segment writes and the sidecar (hard-power-loss durability)
- Work-stealing / dynamic re-segmentation (slowest-connection convergence)
- SSRF / redirect-scheme validation, `dest_dir` symlink checks, reserved-name
  and ADS filename handling beyond the Task 1 basics
- `--checksum` / digest verification flag
- Minimum-throughput / stall detection beyond the socket `timeout=30`
- Global concurrency ceiling across K downloads (belongs in Milestone 2)
