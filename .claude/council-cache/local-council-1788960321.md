# Local Council — Should we proceed with the Milestone 1 plan?

**Question:** Should this project proceed with the Milestone 1 implementation plan
as written in `tasks/plan.md` / `tasks/todo.md`, or change course? On disagreement,
find the best plan and execute it.

**Local council** — these perspectives all come from Claude playing different
roles, not from different AI vendors. Treat agreement as a shared starting point
to pressure-test, not as independent confirmation.

(Cross-vendor council unavailable: council scripts require `jq`, not installed.)

Roles convened: Devil's Advocate, Simplicity Champion, Security Auditor.

Reviewed state: branch `milestone-1-engine`; `split_ranges` committed;
`derive_filename` + 8 tests in the working tree, uncommitted. `tasks/plan.md`
is itself a revision of the original plan produced from an earlier local council
(`.claude/council-cache/local-council.md`).

---

## 🗳️ Devil's Advocate

### Position
Proceed, but with **cuts, not additions** — verdict (b). The revised plan is correct
where the first was broken, but it over-absorbed four review lenses and is now
heavier than a solo Windows learning toy justifies. It also carries an internal
contradiction (cooperative cancel vs. blocking backoff) that means a headline
decision doesn't deliver what it claims.

### Key points
- **Task 1 is gold-plating a threat that doesn't exist in M1.** The engine's only
  driver is the owner at a shell. The one case with real bite is a server
  `Content-Disposition` of `../../x` or `.`/`..` — a one-line guard. NTFS `:` ADS,
  `CON`/`NUL`/`COM1`, control-char scrubbing belong in the M2 checklist Task 9
  already writes. Trim Task 1 to: empty/`.`/`..` → `download.bin` + existing
  separator strip; amend CONSTRAINTS.md's aspirational "sanitized" to match.
- **The cooperative-cancel `threading.Event` doesn't do what the plan says.** It's
  "checked each chunk," but retry backoff is `time.sleep(2**attempt + random())`
  up to ~16s — a thread asleep in backoff is not in the chunk loop. Ctrl-C during
  backoff still hangs. Either use `cancel.wait(backoff)` or drop the Event and
  rely on `shutdown(cancel_futures=True)` + short capped retries. The plan's own
  risk table rates the Event "Low" benefit and pre-writes its retreat — a tell it
  shouldn't be a hard requirement.
- **No test in the plan can validate the project's stated success metric.** 8
  segments against the localhost fixture will be *slower* than 1. The entire
  hermetic suite proves correctness and proves nothing about why segmentation is
  worth building. Make the 1-vs-N timing a **required** Task 9 step and write down
  that the automated suite deliberately doesn't cover the speed claim.
- **Windows `os.replace` + open handles is hand-waved.** It raises `PermissionError`
  if any handle to `.part` is open; AV scan-on-close holds it transiently. The
  design happens to join the executor first, but it's not an acceptance criterion
  anywhere and it's the most likely Windows-specific failure. Make it explicit in
  Task 5; wrap the replace in a brief retry.
- **Task 7 barely exists** (a test + a one-line constant change). Fold it into
  Task 5/6. ~6 commits is honest, not 9.

### Risks & blind spots
- Expiring redirect URLs (S3/CDN presigned, short TTL) — download outlives the
  token, all retries 403, integrity assert fires with no useful message. List it
  as a known limitation.
- The Task 3 fixture is the most complex code in the milestone and it's scaffolding;
  `drop_after` is fiddly on Windows with `BaseHTTPRequestHandler`. Ship
  `drop_after` + `no_etag` first; treat the other toggles as time-permitting.
- `f.truncate(size)` preallocation has no free-space / sanity check.
- Checkpoint language lets M1 ship having never demonstrated the capability it's
  named for.

### Confidence
`medium` — the cancel/backoff contradiction and the untested speedup metric are
verifiable in the plan text; "Task 1 over-scoped" is a threat-model judgment call.

---

## 🗳️ Simplicity Champion

### Position
Proceed, but with **named cuts** — option (b). The skeleton is right. But the prior
council's robustness push added two pieces of *machinery* that don't earn their
keep for a one-person Windows learning tool: the cooperative-cancellation
`threading.Event` and the five-toggle failure-injection fixture. Take the one-line
integrity guards, drop the machinery.

### Key points
- **Keep every one-liner** — atomic sidecar, `sum(progress) == size` before rename,
  `Accept-Encoding: identity`, `min(segments, 32)`, resolved-URL-from-probe,
  refuse-resume-without-validator, redirect scheme check. 1–2 lines each, each
  stops silent corruption or total progress loss, honest fixture never catches
  them. Not over-engineering.
- **Cut the cancellation `Event`.** One-line fix instead: cap the backoff
  `time.sleep(min(2**attempt, 8) + random())` + `shutdown(cancel_futures=True)`.
  Worst-case Ctrl-C wait drops from ~30s to under 10s with zero new state.
- **Shrink Task 3's fixture from five toggles to two.** The ranged-GET probe makes
  `reject_head` dead on arrival. `range_returns_200` is already covered by
  `support_range=False`. `etag_changes_after` is covered by a hand-written stale
  sidecar (Task 7 already does this). Keep `drop_after=N` (exercises the retry loop,
  the code most likely wrong) and `no_etag`. Two toggles, not a framework.
- **Don't build a rolling-window speed buffer in the engine.** Minimal "once" is
  two stored floats: `(done - last_done) / (now - last_time)`. Delete
  `_ProgressLine._samples` in the CLI (the dead second calc).
- **Defer the custom `HTTPAdapter(pool_maxsize=...)`.** `_session = requests.Session()`
  now; add the mounted adapter only if you see pool-full spam at 32 segments,
  with a `# ponytail:` note.
- **Do one real-network download after Task 6, not only in Task 9.** Reordering, not
  new code. Highest-signal test in the plan.

### Risks & blind spots
- Cutting the cancel Event assumes Ctrl-C landing in a retry sleep is rare — on a
  genuinely flaky connection it isn't. Capped backoff is the mitigation; add the
  Event later if a ~10s wait proves annoying.
- Trimming toggles leaves the mid-flight `206 → 200` path untested. Acceptable:
  rare server bug, fallback still covered via `support_range=False`.
- The framing risk is per-task gold-plating — each task can quietly grow a disk
  check, a stall detector, an fsync "while I'm here." Keep the "Deferred" list
  disciplined.
- Windows: `os.replace` handle contention; `ThreadingHTTPServer` port reuse
  flaking the suite on repeated runs. Add known-issue lines.

### Confidence
`medium` — the three cuts are clear low-risk wins; the honest uncertainty is
whether capped backoff is a good-enough substitute for cooperative cancellation
in real Ctrl-C use.

---

## 🗳️ Security Auditor

### Position
Proceed **with specific named changes** — verdict (b). The revised plan closes the
threats that actually corrupt files or write outside the output dir in M1
(hostile `Content-Disposition`, lexical traversal, `file://` redirect, gzip offset
desync, stale-validator resume). Deferring SSRF, symlink checks, and `--checksum`
to M2 is *correct* — M1 has no privilege boundary. But two real silent-corruption /
credential-at-rest gaps survive the revision, one line each to fix.

### Key points
- **Mid-download resource change is an unguarded TOCTOU.** Validator is checked at
  probe and on resume, but not *during* a live run: server swaps the file between
  segment 1 and segment 3, `sum(progress) == size` still passes, integrity gate
  promotes a Frankenstein file, no `--checksum` in M1 to catch it. Fix: send
  `If-Range: <validator>` on every segment GET (and the single-connection resume
  GET) when the validator is non-empty. A changed resource then returns `200`,
  which the plan already treats as `RangeNotSupported` → clean restart. The prior
  council's security lens asked for this; the revision dropped it.
- **Credentials leak to disk via the sidecar.** `http://user:pass@host/file` is
  written verbatim into `<name>.myidm.json` (Task 2), which lingers whenever a run
  fails — exactly when resume matters. Task 8 redacts creds from error *strings*
  but not persisted state. Fix: strip userinfo from the netloc before storing /
  matching the URL; keep credentials in memory for the request only.
- **The integrity gate must not be an `assert`** — `python -O` deletes it. Make it
  `if not (...): raise`. Same for any assert guarding promotion.
- **`Accept-Encoding: identity` needs a response-side check.** A server can gzip
  anyway; `iter_content` returns decoded bytes, offsets go wrong. One line: if the
  download response carries `Content-Encoding`, raise.
- **Filename hardening (Task 1) and the CONSTRAINTS.md correction are right and
  necessary.** CONSTRAINTS currently overclaims. The lexical defense (strip
  separators + `:`, reject `.`/`..`/device names, then `out_dir / name`) genuinely
  cannot escape `dest_dir` — sufficient for M1. Keep the code+doc coupling in one
  commit.

### Risks & blind spots
- `os.replace` silently overwrites an existing file and follows a pre-planted
  symlink at both `final` and `.part`. Low severity for a personal CLI, documented
  as M2 — acceptable. Note it in the M2 checklist explicitly: *engine writes follow
  symlinks; add O_NOFOLLOW / realpath containment when the service can be driven by
  a web page.* The engine is the shared component — the check must eventually live
  there, not the service layer.
- Hostile `Content-Length` → `f.truncate(size)` disk exhaustion. NTFS fails loudly
  with ENOSPC and cleans up. Annoying, fails safe. `shutil.disk_usage` check is
  nice-to-have, not a blocker.
- Every failure-injection test uses a cooperative local fixture — the `If-Range`,
  redirect-scheme, and gzip guards will be asserted, never observed. Task 9 manual
  smoke test should include one HTTPS redirect chain and one known-gzip endpoint.
- `derive_filename`'s `Content-Disposition` parse is naive (splits on `;`, ignores
  RFC 5987). No shell is invoked — not a security issue. Flagging so a reviewer
  doesn't "fix" it thinking it is one.

### Confidence
`medium` — code is unwritten so implementation may quietly add some of these, but
the two load-bearing gaps (`If-Range` TOCTOU, sidecar credential persistence) are
absent from the revised plan by omission, not by a documented decision.

---

## Synthesis — angles, not consensus

### Where all three converged (a shared prior to stress-test, not corroboration)
- **Proceed. Do not rethink.** No member wanted to stop or restart; no member
  wanted the plan as written. All three: verdict (b), proceed with changes. The
  engine skeleton, single dependency, dropped single-connection resume, and
  M2-deferred threat work are all endorsed.
- **Keep the cheap integrity guards** — atomic sidecar, integrity check before
  rename, `Accept-Encoding: identity`, `MAX_SEGMENTS` clamp, resolved-URL-from-probe,
  refuse-resume-without-validator, redirect-scheme check, `requests.Session`.
- **Kill the cooperative-cancellation `threading.Event`.** Devil: it's internally
  contradictory. Simplicity: it's unjustified machinery. Both land on the same
  one-line replacement — cap the backoff + `cancel_futures=True`.
- **The failure-injection fixture is over-built.** Both Devil and Simplicity want
  it cut to `drop_after` + `no_etag`.
- **The speedup measurement is buried and must be pulled forward and made
  mandatory.** All three. It is the actual lesson of a build-to-learn project.

  *What they might all be missing for the same reason:* every member is Claude and
  every member reached for "a one-line guard is always worth it" and "capped
  backoff is good enough." Seven one-liners threaded through a concurrent download
  loop are still a debugging surface, and none of them proved capped backoff
  actually covers the flaky-connection Ctrl-C case — Simplicity flagged it as the
  one real uncertainty and then recommended it anyway.

### Genuine tensions
- **Task 1 filename hardening — Devil vs. Security.** Devil: NTFS `:`, device
  names, control chars defend against a driver that doesn't exist in M1 — trim to
  the traversal one-liner, defer the rest. Security: on Windows those cases are
  real and the lexical defense is *what makes* `out_dir / name` safe — keep it,
  it's sufficient for M1. **The user's platform breaks the tie toward Security:**
  `:` (ADS) and `CON`/`NUL` are genuinely exploitable-into-weird-states on Windows
  and the fix is ~10 lines written once. But Devil's real point stands — timebox
  it, skip RFC 5987, don't let Task 1 balloon.
- **Is capped backoff a real substitute for cooperative cancel?** Only the owner
  can judge from real Ctrl-C use. Recommendation: take the cut now, add the Event
  back as its own task if a ~10s wait annoys you in practice.

### Blind spots (raised by one member; the others' plans walk into them)
- **Sidecar persists `user:pass@host` credentials to disk** (Security only). Real,
  one-line fix, absent from the plan.
- **Mid-flight resource swap → Frankenstein file passes the size gate** (Security
  only). `If-Range` on every segment closes it and reuses the existing 200→fallback
  path.
- **Expiring presigned redirect URLs** (Devil only) — document as a known limit.
- **`os.replace` on Windows with open / AV-held handles** (all three touched it) —
  make it an explicit Task 5 acceptance criterion with a short retry, not an
  implicit property.
- **The hermetic suite structurally cannot test the speed claim** (Devil sharpest)
  — say so in writing.

### Suggested direction

Proceed with `tasks/plan.md`, revised:

**KEEP as planned:** atomic sidecar; integrity guard (as `if/raise`, never
`assert`); `Accept-Encoding: identity` **plus** a response-side `Content-Encoding`
→ raise check; `MAX_SEGMENTS=32` clamp; resolved-URL-from-probe; refuse resume
without a strong validator; redirect-scheme check; module-level `requests.Session`.

**ADD (Security):**
- `If-Range: <validator>` on every segment GET and the single-connection resume GET.
- Strip userinfo from the URL before writing / matching it in the sidecar.
- Task 9 smoke test must hit one HTTPS redirect chain and one gzip endpoint.
- M2 checklist line: engine writes follow symlinks — needs realpath containment
  before the service is web-drivable.

**CUT / SIMPLIFY (Devil + Simplicity):**
- Drop the cancellation `threading.Event`. Use `time.sleep(min(2**attempt, 8) +
  random())` + `executor.shutdown(cancel_futures=True)`.
- Task 3 fixture: two toggles only — `drop_after=N`, `no_etag`.
- Engine speed = two floats, no ring buffer. Delete `_ProgressLine._samples`.
- Defer the mounted `HTTPAdapter(pool_maxsize)` — plain `Session()` now.
- Fold Task 7 into Task 5/6 → ~6–7 commits, not 9.
- Pull one real-network download forward to right after Task 6.

**CONTESTED — resolved for this user:** keep Task 1's Windows-specific hardening
(`:`, control chars, trailing dot/space, `.`/`..`, `CON`/`NUL`/`COM#`/`LPT#`),
skip RFC 5987, ship the CONSTRAINTS.md wording fix in the same commit. Timebox it.

**WRITE DOWN explicitly:** the automated suite deliberately does not cover the
"faster than single-connection" success metric; the 1-vs-N timing in Task 9 is
required, not optional.
