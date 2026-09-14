# Personal Download Manager (myIDM)

> **Project renamed `myidm` -> `tdm` (TDM, Terminal Download Manager) on
> 2026-09-15; the rest of this doc refers to the old name.**

> **Base PRD.** This is the project's source-of-truth requirements document.
> When requirements change, revise this file — not the plans derived from it.

## Problem
Browser downloads run over a single connection, waste available bandwidth on
capable servers, and resume unreliably after an interruption. More to the point
for this project: the owner wants to understand *how* download managers achieve
segmented downloading, resume, and browser integration, and the only way to
learn that is to build one. Leaving it unsolved means the learning goal is not
met — the tool itself is replaceable by existing software.

## Evidence
- Self-evident for a personal learning project: the owner has stated the goal is
  to learn download-manager internals (concurrency, HTTP range requests, state
  persistence, browser extensions).
- Feature set validated by existing tools: Internet Download Manager, aria2, and
  Motrix all ship segmented downloading + resume + browser integration, so the
  capabilities are real and worth understanding.
- Not backed by external user research — none is needed at this scope.

## Users
- **Primary**: The project owner — an intermediate Python developer, on Windows,
  who triggers the need whenever they want a hands-on project to learn
  concurrency, HTTP internals, and Chrome extension development.
- **Not for**: General end users, distribution, or anyone who just needs a fast
  downloader (they should install Motrix or aria2).

## Hypothesis
We believe **a self-built segmented downloader with resume and a Chrome
right-click integration** will **teach the mechanics of parallel HTTP range
requests, download state persistence, and browser-to-local-app communication**
for **an intermediate Python developer**.
We'll know we're right when **all three pieces — engine, local service, Chrome
extension — work end-to-end on a real download, and the owner can explain how
each part works**.

## Success Metrics
| Metric | Target | How measured |
|---|---|---|
| Engine correctness | Downloaded file matches source checksum on 100% of test runs | Checksum comparison against a known local test file |
| Speedup from segmentation | Measurably faster than single-connection on a range-capable server | Timed download, same file, 1 segment vs N segments |
| Resume works | Interrupted download completes correctly on re-run, no re-downloading finished bytes | Interrupt mid-download, re-run, compare checksum + bytes transferred |
| Chrome path works | Right-click link in Chrome -> file downloads with no manual URL copying | Manual end-to-end test |
| Learning outcome | Owner can explain range requests, the resume sidecar, and native/HTTP bridge in their own words | Self-assessment |

## Scope
**MVP** — Piece 1 (the engine) only: a Python download engine plus a minimal
command-line entry point that takes one URL, downloads it using N parallel HTTP
range connections, shows live progress (percent, speed, ETA), writes progress to
disk continuously, and resumes correctly when re-run after an interruption.
Falls back to a single connection when the server does not support ranges.

**Out of scope**
- System tray UI, and any GUI before Milestone 4. Milestones 1–3 have no
  standalone GUI; Milestone 4 adds a single local dashboard window (owner
  decision, 2026-09-11). No system-tray integration at any milestone.
- Chrome native messaging — a localhost HTTP bridge is used instead; native messaging is a possible later re-implementation exercise, not a requirement.
- Download scheduler (time-of-day, calendar).
- Bandwidth throttling / rate limiting.
- Video and streaming-media grabbing ("download this video from the page").
- Auto-interception of all browser downloads.
- Non-Chrome browsers.
- Multi-source / mirror / torrent downloading.
- Packaging, installers, auto-update, or distribution to other users.

## Delivery Milestones
<!-- Business outcomes, not engineering tasks. /plan turns each into a plan. -->
<!-- Status: pending | in-progress | complete -->

| # | Milestone | Outcome | Status | Plan |
|---|---|---|---|---|
| 1 | Segmented engine + CLI | Owner can run one command with a URL and get a fast, resumable download with live progress | code-complete (pending merge to `main`) | council-revised task list in [`tasks/plan.md`](../../tasks/plan.md) |
| 2 | Local background service | The engine runs as a self-detaching background service that accepts URLs over a `127.0.0.1` HTTP endpoint (shared-secret token), keeps a queue that persists across restarts, reports status, and supports cancel / pause / resume | in-progress | [`docs/superpowers/specs/2026-09-11-myidm-local-service-design.md`](../../docs/superpowers/specs/2026-09-11-myidm-local-service-design.md); task list in [`tasks/plan-milestone-2.md`](../../tasks/plan-milestone-2.md) |
| 3 | Chrome extension | Right-clicking a link in Chrome sends it to the service; an extension popup shows progress of all downloads | pending | — |
| 4 | Desktop dashboard | A local dashboard window shows all downloads live and accepts a link by paste or drag; the service is packaged as a `.exe` that launches on interaction | pending | — |

## Open Questions

### Resolved (owner decisions, 2026-09-08)

- **Resume durability — process restart only.** Resume must survive a Ctrl-C /
  process restart, *not* a full machine reboot or power loss. Consequence: no
  `os.fsync`; the sidecar + `.part` write cadence in the Milestone 1 plan is
  sufficient.
- **Testing servers without range support.** Automated tests use the local
  `make_server` fixture's range toggles and never hit the network (per
  `CONSTRAINTS.md`). Manual verification uses a real download URL the owner
  supplies from prior IDM use.
- **Concurrency ceiling is content-dependent.** No fixed K. The sane cap depends
  on file size / content. Milestone 1 keeps a static `MAX_SEGMENTS = 32` per
  download as a footgun guard; Milestone 2 sets the K-concurrent policy as a
  size-aware heuristic.

### Resolved (owner decisions, 2026-09-11 — Milestone 2 brainstorm)

- **Localhost bridge transport:** HTTP on `127.0.0.1` via the standard library
  (`http.server`). Chrome native messaging is not used.
- **Localhost endpoint trust boundary:** shared-secret Bearer token on every
  request, generated by the service on start and written to a per-user state
  file; server binds `127.0.0.1` only. Plus SSRF / private-IP blocking on every
  URL before a request is made on a browser caller's behalf, `realpath`
  containment of the output path, and a free-space check before preallocation.
- **Concurrency policy:** 8 segments per file by default, hard ceiling 32; 3
  downloads run concurrently, the rest queue; a host that fails repeatedly in
  one download is demoted to 1–2 connections for the life of the service
  (not persisted).
- **Process model:** `myidm serve` self-detaches a console-less background
  process; `myidm stop` ends it. Cancel / pause / resume are added to the
  engine and exposed over the API.
- **Deferred out of Milestone 2:** work-stealing / dynamic re-segmentation
  becomes its own milestone; the desktop dashboard, clipboard capture, drag-and-
  drop, and `.exe` packaging become Milestone 4.

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Scope creep toward a full IDM clone (GUI, scheduler, video grab) stalls the learning goal | High | High | Out-of-scope list is explicit; each milestone ships and is usable on its own |
| Concurrency bugs (races writing the file or the progress sidecar) are hard to debug | Medium | Medium | Each segment writes a disjoint byte range at a fixed offset; sidecar written by a single monitor thread; one integration test with checksum assertion |
| Servers vary wildly in range support, redirects, auth, content-length accuracy | High | Medium | Probe first; single-connection fallback; test against a controllable local server |
| Localhost endpoint abused by malicious web pages | Medium | Medium | Shared secret token checked by the service; bind to 127.0.0.1 only |
| Chrome MV3 service worker lifecycle / permissions surprises delay milestone 3 | Medium | Low | Milestone 3 is last; engine and service deliver value without it |

---
*Status: BASE PRD — accepted as the project's source-of-truth requirements.
Milestone 1 is code-complete (pending merge); Milestone 2 is in progress; see the
plan links in the milestone table. Milestone 4 (desktop dashboard) was added
2026-09-11 by owner decision. Amend this document (in its own commit, with a
reason) when requirements change; do not let a plan drift from it.*
