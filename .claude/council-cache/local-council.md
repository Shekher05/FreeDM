# Local Council — Milestone 1 plan review

**Question:** Review my Milestone 1 implementation plan for myIDM and tell me what changes I should make before executing it further.

**Local council** — these perspectives all come from Claude playing different
roles, not from different AI vendors. Treat agreement as a shared starting point
to pressure-test, not as independent confirmation.

(Cross-vendor council was unavailable: the council scripts require `jq`, which is not installed.)

Roles convened: Devil's Advocate, Simplicity Champion, Security Auditor, Scalability Architect.

---

## 🗳️ Devil's Advocate

### Position
The plan is a clean build of the happy path, but it treats the three genuinely hard parts of a download manager — crash-consistent resume state, validating that a resumed resource is unchanged, and cooperative cancellation — as afterthoughts or "open questions." Tasks 5–7 need redesign, not tweaks, before you execute further.

### Key points
- The resume sidecar write is not atomic, so the headline feature is unsound. `_save_progress` does `meta.write_text(json.dumps(...))` every ~0.5s; a Ctrl-C mid-write leaves truncated JSON, `_load_progress` returns `None`, and the *entire* download restarts from zero. Segment bytes are written with plain `f.write`, never flushed/fsynced — a `kill -9` can lose buffered data while the sidecar claims those bytes are done, producing a file that passes the size check and fails the checksum. Fix: `meta.tmp` + `os.replace`; `f.flush()` (and ideally `os.fsync`) before each sidecar save. Without fsync you don't even survive a process kill, so "reboot vs restart" isn't the real line.
- Resume has no real integrity gate when ETag is absent. `data.get("etag") == ""` happily "matches," so a resume against a file that changed server-side yields corrupt output with nothing to catch it — violating the PRD metric "matches source checksum on 100% of runs." Fall back to `Last-Modified`, or refuse to resume when no strong validator exists.
- The HEAD probe is the wrong primitive and the resolved URL is never captured. Real servers reject HEAD (405) or omit `Content-Length`/`Accept-Ranges` on HEAD. Use `GET` with `Range: bytes=0-0`, read `206` + `Content-Range`. `_probe` follows redirects but segments re-request the *original* `url` — use `r.url` as canonical. Send `Accept-Encoding: identity` on range GETs or a gzipping server desyncs your byte offsets.
- Cooperative cancellation is missing entirely. No `threading.Event` checked in the chunk loop. On Ctrl-C, `with ThreadPoolExecutor` blocks until every worker finishes — each possibly mid-`time.sleep(2**attempt)`, up to 31s. Add a shared cancel Event, check it each chunk, pass `cancel_futures=True`.
- Zero test coverage for the code most likely to be wrong. Every test uses a perfect, instant, reliable server. Nothing exercises the retry loop, the mid-flight 200→fallback, a corrupt sidecar, or a changed ETag. Make the Task 4 fixture able to inject failures — highest-value edit in the whole plan.

### Risks & blind spots
- The "atomicity" comment is factually wrong. `progress[idx] += len(chunk)` is load-add-store, not atomic assignment. What makes it safe is single-writer-per-slot. Name the real design or a reviewer fixes the wrong thing.
- Preallocation has no disk-space check; `f.truncate(size)` on NTFS isn't free for multi-GB.
- `-n` is unbounded. `-n 500` = 500 threads/sockets/handles. A `min(n, 64)` clamp is a footgun guard, not the out-of-scope `-k` feature.
- Task 5 ships a public `download()` that raises internal `RangeNotSupported` to the caller for a *predicted* condition, and reuses the same exception for the *exceptional* mid-flight 200. Consider merging Tasks 5 and 7.
- The plan spends 8 hermetic tasks on a local server and one unenforced manual paragraph on real HTTP — for a project whose purpose is learning how downloaders cope with real servers.
- Engine computes `avg_bytes_per_sec` but the CLI recomputes speed and discards it — two speed calcs, one dead. `derive_filename` ignores RFC 5987 `filename*=`.

### Confidence
`medium` — persistence, cancellation, and test-coverage gaps are concrete; some severity calls depend on servers that can't be tested from here.

---

## 🗳️ Simplicity Champion

### Position
The plan is already lean in its bones — one engine file, one dependency, no packaging — and that's the right instinct. But it carries three pieces of complexity that don't earn their keep: single-connection *resume*, the separate HEAD probe, and a 9-task ceremony with half-wired control flow spanning commits. Cut those before executing.

### Key points
- Delete single-connection resume (Task 7's `_download_single` body). A server that ignored `Range` on request 1 will ignore it on the re-run too — the plan even resets `done = 0` on a 200. The `done = min(part.stat().st_size, sum(prev))`, the `wb`/`r+b` juggling, the two `done = 0` resets, the `_load_progress` call are all guarding a case that can't succeed. Make `_download_single` always start from zero with no sidecar.
- Collapse the probe into the first real GET, or at minimum reuse the redirected URL. `requests.head(url, allow_redirects=True)` then discards the response, then every segment does `requests.get(url, ...)` against the *original* URL — re-running redirect resolution N+1 times. If the redirect target is a per-request signed CDN URL, segments fetch inconsistent bytes and the file is silently corrupt. Cheapest fix: capture `r.url`.
- One save interval, not two. `SAVE_INTERVAL = 1.0` for single, `~0.5s` for segmented, is a distinction with no reason.
- Merge the tiny tasks; 9 → ~6. Tasks 2 (`derive_filename`) and 3 (sidecar helpers) are both small pure functions — one commit. Task 6 writes *no new code* — fold it into Task 5. Task 5's deliberate `raise RangeNotSupported` stand-in means `download()`'s control flow is knowingly wrong for two commits.
- Pick one place to compute speed. Do the rolling average in the engine and let the CLI just format the number — delete `_ProgressLine._samples`. (`_fmt_size` also has a dead `return` after the loop.)

### Risks & blind spots
- The "build-to-learn" framing cuts against my lens. If the owner *wants* to implement transport-agnostic resume as a learning exercise, "it's practically useless" isn't a veto — it's a note. Same for the HEAD probe as a distinct pedagogical step.
- Simplicity shouldn't skip two one-line safety checks cheaper than the bugs they prevent: `assert sum(progress) == size` before `os.replace` (servers lie about `Content-Length`; honest fixture won't catch it), and refusing resume when `etag` is empty.
- Open questions close cheaply. Sidecar + `.part` already survive reboot and `os.replace` is atomic — just don't add `fsync` and document the worst case. Task 4's fixture already has `support_range=False`; the no-range testing question is answered.
- Thread-pool shutdown on failure blocks ~31s with 5 retries at `2**attempt`. If you touch this code, `shutdown(cancel_futures=True)` or capped retries.

### Confidence
`medium` — the structural cuts are clear wins, but the learning-project framing legitimately justifies some redundancy.

---

## 🗳️ Security Auditor

### Position
The plan is functionally reasonable but its threat model silently assumes a trusted server and a trusted network. Several concrete steps ship integrity and DoS gaps that are cheap to close now and expensive to retrofit once Milestone 2 exposes this engine to any web page.

### Key points
- Task 5 `_probe` / `_download_segment` — redirect + range TOCTOU. `_probe` never revalidates the final scheme (`requests` will follow to `localhost` / `169.254.169.254` / RFC1918). Segments then `requests.get` the *original* `url` (redirects followed again, per thread) with a bare `Range` and no `If-Range: <etag>`. Probe once, cap redirects, assert final scheme ∈ {http,https}, pass the *resolved* URL + `If-Range` to every segment with `allow_redirects=False`, treat 200 as `RangeNotSupported`.
- Task 5 — attacker-controlled `Content-Length` and encoding. `f.truncate(size)` trusts the server header verbatim → hostile server returns a huge `Content-Length` = local disk-exhaustion DoS. No `Accept-Encoding: identity`, so a gzip response makes `progress[idx] += len(chunk)` and `f.seek(pos)` offsets wrong (silent corruption) and a decompression bomb possible. Add a size sanity ceiling / free-space check, force identity encoding.
- Task 5/8 — unbounded segment count. `download()` only does `segments = max(1, segments)`. `-n 10000000` against a 5 MB file = millions of threads/ranges. A per-download segment cap (32–64) is *not* the out-of-scope `-k` cap.
- No integrity gate anywhere. Nothing validates `Content-Range` against what each segment requested, nothing checks `sum(progress) == size` (and each segment complete) before `os.replace`, no optional `--checksum`. On plain `http://` an on-path attacker substitutes content and the tool prints "Saved to …".
- Task 3/6 — resume trusts an unauthenticated, non-durable sidecar. With no ETag, `_load_progress` accepts a resume on `url` + `size` alone. It never checks `progress` entries are non-negative ints ≤ segment length. The segmented path blindly trusts `progress[]`, unlike the single-connection path which clamps. `_save_progress` is non-atomic `write_text` with no fsync.

### Risks & blind spots
- `derive_filename` is already shipped and CONSTRAINTS calls filenames "sanitized," but only path separators are handled. On Windows it does not strip `:` (NTFS ADS), reserved device names (`CON`, `NUL`, `COM1`), trailing dots/spaces, or control chars `unquote` can inject (`%00`, `%0a`). It can return `..` or `.` from a URL path.
- Lexical-only traversal defense. `os.replace(part, final)` silently overwrites an existing file and follows a pre-existing symlink at `dest_dir/name` (and at `.part`).
- Milestone 2 is the real exposure and the engine is the shared component. SSRF filtering, filename hardening, size caps cost a few lines now; bolting them onto a service layer later means the engine is still the weak spot.
- Info leak: `except Exception: print(f"\nError: {exc}")` echoes credentials embedded in a URL (`http://user:pass@host/...`).
- Test matrix is all happy-path. Widen "how to test against servers without range support" to "how to test against *misbehaving* servers" — grow the `make_server` fixture before Tasks 5–7 rely on it.

### Confidence
`medium` — code is unwritten so implementation may quietly fix some of this, but the plan's explicit function bodies show the gaps concretely, and two (filename sanitization, the resume contract) are already frozen into CONSTRAINTS.

---

## 🗳️ Scalability Architect

### Position
The plan is sound for a single-file CLI, but it hard-codes three decisions that will fight you at 10x file size and again at Milestone 2's K-concurrent queue: static equal-size segmentation, a per-download unbounded `ThreadPoolExecutor`, and a non-atomic whole-file sidecar rewrite. Fix the seams now while they're one-line changes.

### Key points
- Static equal segmentation converges to the slowest connection. `split_ranges` cuts N fixed contiguous ranges up front and `_download_segmented` waits for all. One lossy connection strands the download at 99%. Serious accelerators use work-stealing. For M1: (a) name it as a known ceiling with `# ponytail:` and an upgrade path, and (b) shape `_download_segment` to pull its `(start,end)` from a shared work list rather than closing over loop variables, so the requeue hook has somewhere to attach later.
- Answer the PRD's concurrency-ceiling question now with one parameter. Add `max_workers: int | None` (and a `MAX_SEGMENTS` clamp) to `download()` in Task 5. Then M2 injects a shared bounded pool / semaphore instead of rewriting `_download_segmented`.
- No shared `requests.Session`, no connection pooling, no retry jitter. Every call uses module-level `requests.get`, so each retry pays a fresh TCP+TLS handshake. `time.sleep(2 ** attempt)` with no jitter means all N segments retry in lockstep (thundering herd). Add a module `Session` with an `HTTPAdapter(pool_maxsize=...)` and `sleep(2**attempt + random())`.
- Probe with a ranged GET, not HEAD. A `GET` with `Range: bytes=0-0` gets the real 206/`Content-Range`, the real validator, and proves range support in one request.
- Reboot survival + truncation defense are both cheap. `_save_progress` `write_text` is not atomic; a crash mid-write loses *all* progress on a multi-hour download. Write `meta.tmp` + `os.replace`. `_download_segmented` renames `.part` to final without checking `sum(progress) == size` — one assert closes that.

### Risks & blind spots
- The plan treats Milestone 1 as isolated, but Milestone 2's queue is a scaling change to *this* code. The threading model, executor ownership, and session/pool all get locked in here.
- No minimum-throughput / stall detection. `timeout=30` catches a dead socket but not a server dribbling 100 B/s for an hour.
- `f.truncate(size)` commits full file size to disk up front; a failed 50 GB download leaves a 50 GB `.part`.
- Sidecar with `etag=""` when absent: two different files of the same size from the same URL "resume" into each other.
- The resume test asserts `served_bytes <= remainder + 1024`, but a single mid-stream retry re-fetches up to a full 65536-byte chunk — the test is quietly flaky under the very retry path that matters.

### Confidence
`medium` — correctness and session/pool points are solid and low-cost; work-stealing and global-cap points partly land in Milestone 2.

---

## Synthesis — angles, not consensus

### Shared starting points (a common prior to stress-test, not corroboration)

All four members, independently, flagged the same cluster. Because they share a model, treat this as "the obvious stuff a second Claude also sees" — necessary to fix, but ask what they're *all* missing for the same reason:

1. **Non-atomic sidecar write** (`meta.write_text` → truncated JSON on interrupt → total progress loss). Fix: `meta.tmp` + `os.replace`. Unanimous.
2. **Redirect handling is broken**: `_probe` follows redirects, discards the response, then each segment independently re-resolves the *original* URL. Against a signed/round-robin CDN this silently corrupts the file. Fix: capture `r.url`, pass the resolved URL to segments. Unanimous.
3. **Unbounded segment count** (`-n` only does `max(1, n)`). A `MAX_SEGMENTS` clamp (~32–64) is explicitly *not* the out-of-scope `-k` feature. 3 of 4.
4. **No integrity gate before `os.replace`**: nothing asserts `sum(progress) == size`. Servers lie about `Content-Length` and the honest test fixture will never catch it. 3 of 4.
5. **Weak resume validator**: `etag == ""` matches, so a changed/different same-size file resumes into corruption. Fix: refuse resume without a strong validator, or use `Last-Modified`. 3 of 4.
6. **Probe with ranged `GET` (`Range: bytes=0-0`) instead of `HEAD`** — one request, real 206, real validator, proves range support. 3 of 4.
7. **The test fixture is too honest.** Every test hits a perfect server. The retry loop, mid-flight fallback, corrupt sidecar, and changed-ETag paths are untested. Growing `make_server` (Task 4) to inject failures is called the single highest-value edit. 3 of 4.

**What they might all be missing for the same reason:** every member reasoned from "make the code robust." None questioned whether *N-connection segmentation itself* is worth it for the stated learning goal, or whether the milestone is scoped too big to finish. None looked hard at Windows-specific behavior beyond filename chars (file locking during `os.replace` while a handle is open, antivirus scan-on-close latency, `ThreadingHTTPServer` port reuse flakiness in CI). And none sanity-checked the plan's own value of committing 9 times for a personal toy.

### Genuine tensions

- **Add robustness vs. cut scope.** Security + Devil's Advocate + Scalability want *more* in Task 5 (SSRF checks, `If-Range`, session pooling, cancel Events, disk-space guards). Simplicity wants *less* (delete single-connection resume, merge tasks 2+3 and 5+6, one save interval). For a build-to-learn personal tool, Simplicity's cuts are safe to take now; the robustness additions should be triaged — take the cheap integrity ones (atomic write, `sum==size` assert, resolved URL, segment clamp, `Accept-Encoding: identity`), defer the threat-model ones (SSRF, symlink, ADS filenames) to a documented Milestone 2 checklist since the CLI trusts the user-supplied URL anyway.
- **`fsync` or not.** Devil's Advocate + Security say add it (survive `kill -9`). Simplicity says don't — document that a hard power-loss re-fetches <1s of data. Resolution hinges on the PRD open question. For a learning tool: **don't fsync**, write it down. If you later want the exercise, add it as its own task.
- **Work-stealing segmentation.** Scalability wants the *seam* for it now (pull ranges from a shared list, don't close over loop vars). Simplicity would call that premature. Cheap compromise: the `# ponytail:` ceiling comment + upgrade note, skip the structural change until a real multi-GB download annoys you.

### Blind spots one member caught that the others' approaches walk into

- **Devil's Advocate alone:** cooperative cancellation is missing — `ThreadPoolExecutor.__exit__` blocks on every in-flight retry backoff (up to ~31s) after Ctrl-C. The "press Ctrl-C, re-run to resume" UX in the PRD is undermined.
- **Security alone:** `derive_filename` (already shipped, already blessed by CONSTRAINTS as "sanitized") misses Windows `:` (NTFS ADS), device names (`CON`/`NUL`/`COM1`), trailing dots, and `%00`/`%0a` from `unquote`. Also `os.replace` silently overwrites and follows a pre-planted symlink.
- **Security alone:** the CLI catch-all `print(f"\nError: {exc}")` leaks `user:pass@host` credentials from the URL.
- **Scalability alone:** the resume test's `served_bytes <= remainder + 1024` assertion is flaky — one mid-stream retry re-fetches a full 65536-byte chunk.
- **Scalability alone:** no stall detection — `timeout=30` doesn't catch a server dribbling bytes for an hour.
- **Devil's Advocate + Simplicity:** the dead second speed calculation (engine computes `avg_bytes_per_sec`, CLI ignores it and rolls its own).

**Not covered by anyone:** whether 8-way parallelism actually beats 1-way on the connections you'll test against (the PRD makes this a success metric but no task measures it before Task 9's optional manual smoke test); CI reliability of `ThreadingHTTPServer` on Windows; and whether `requests` streaming + `iter_content` interacts badly with the preallocated-file `r+b` seek pattern under load.

### Suggested direction

Before resuming execution, revise the plan with a small, high-leverage set of edits — most are one or two lines:

**Do now (cheap, correctness, all/most members):**
1. Atomic sidecar write: `meta.tmp` + `os.replace` in `_save_progress`. (Task 3)
2. Capture and use `r.url` from the probe as the canonical URL for all segments; `allow_redirects=False` on segment GETs. (Task 5)
3. Switch `_probe` from `HEAD` to `GET` with `Range: bytes=0-0`. (Task 5)
4. `MAX_SEGMENTS` clamp (~32) in `download()`. (Task 5)
5. `assert sum(progress) == size` (and every segment full) before `os.replace`. (Task 5)
6. Refuse resume when no strong validator (`etag` empty and no `Last-Modified`) — start clean instead. (Tasks 3/6)
7. Send `Accept-Encoding: identity` on all download GETs. (Task 5)
8. Add a shared `threading.Event` cancel flag checked in the chunk loop; `shutdown(cancel_futures=True)`. (Task 5, helps the Ctrl-C UX)
9. Grow the Task 4 `make_server` fixture with toggles: drop connection after N bytes, answer `Range` with `200`, mutate `ETag` on second request. Then add tests in Tasks 5–7 that use them.
10. Fix the `derive_filename` gaps CONSTRAINTS already claims are handled: strip `:`, control chars, trailing dots/spaces; reject `.`/`..`/device names. (small follow-up commit on Task 2)

**Simplify (Simplicity Champion, safe for a learning tool):**
11. Make `_download_single` always start from zero — delete its resume/sidecar logic.
12. Merge Task 6 into Task 5 (it writes no code) and merge Tasks 5+7 so `download()` is never knowingly half-wired across commits. Optionally merge Task 2+3.
13. One `SAVE_INTERVAL` constant for both paths.
14. Compute speed once — in the engine; CLI just formats.

**Document, don't build (defer):**
15. `# ponytail:` note on `split_ranges` naming static segmentation as a known ceiling with a work-stealing upgrade path.
16. A "Milestone 2 security checklist" section: SSRF/scheme validation, symlink checks on `dest_dir`, shared-token auth, credential redaction in errors, global concurrency cap.
17. Resolve the two PRD open questions in the plan text: "resume survives process restart / Ctrl-C, not guaranteed on hard power loss (no fsync)"; "misbehaving-server testing via the extended fixture."

Where the real uncertainty remains: whether items 8 (cancel Event) and 15 (work-stealing seam) are worth the added code *now* vs. after you've felt the pain — that's a judgment call the council can't make for you, and it depends on how much you want Milestone 1 to be a finishable checkpoint vs. a faithful mini-IDM.

---
Local council saved to `.claude/council-cache/local-council-1788841116.md`
For cross-vendor perspectives, install `jq` and re-run `/claude-council:ask`, or run `/claude-council:status` to see what's available.
