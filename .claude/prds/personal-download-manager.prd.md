# Personal Download Manager (myIDM)

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
- Graphical desktop application / system tray UI — the Chrome extension popup is the only UI, and only in a later milestone; no standalone GUI ever.
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
| 1 | Segmented engine + CLI | Owner can run one command with a URL and get a fast, resumable download with live progress | pending | — |
| 2 | Local background service | The engine runs as a background service that accepts URLs over a localhost endpoint, keeps a queue, persists it across restarts, and reports status | pending | — |
| 3 | Chrome extension | Right-clicking a link in Chrome sends it to the service; an extension popup shows progress of all downloads | pending | — |

## Open Questions
- [ ] Does resume need to survive a full machine reboot, or only a process restart / Ctrl-C? (Affects how aggressively progress is flushed to disk.)
- [ ] Localhost HTTP bridge vs Chrome native messaging — confirmed as HTTP for now; revisit at milestone 2 if the security model feels wrong.
- [ ] How to test behavior against servers that do *not* support range requests — stand up a local test server that can toggle range support?
- [ ] What is the trust-boundary mitigation for the localhost endpoint (any web page can also call it)? Shared token between extension and service is the current assumption; confirm at milestone 2.
- [ ] Concurrency ceiling: with K concurrent downloads x N segments each, what caps are sane on a personal machine?

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Scope creep toward a full IDM clone (GUI, scheduler, video grab) stalls the learning goal | High | High | Out-of-scope list is explicit; each milestone ships and is usable on its own |
| Concurrency bugs (races writing the file or the progress sidecar) are hard to debug | Medium | Medium | Each segment writes a disjoint byte range at a fixed offset; sidecar written by a single monitor thread; one integration test with checksum assertion |
| Servers vary wildly in range support, redirects, auth, content-length accuracy | High | Medium | Probe first; single-connection fallback; test against a controllable local server |
| Localhost endpoint abused by malicious web pages | Medium | Medium | Shared secret token checked by the service; bind to 127.0.0.1 only |
| Chrome MV3 service worker lifecycle / permissions surprises delay milestone 3 | Medium | Low | Milestone 3 is last; engine and service deliver value without it |

---
*Status: DRAFT — requirements only. Implementation planning pending via /plan.*
