# PROJECT.md

## What this is

**myIDM** — a personal, command-line download accelerator written in Python. A
stripped-down Internet Download Manager for one user (the project owner).

## Why it exists

This is a **build-to-learn** project. Free tools already solve the download
problem (`aria2`, Motrix, IDM); the point is not to have the tool but to
understand how it works by building it. The learning targets:

- **Concurrency** — parallel HTTP connections with threads.
- **HTTP internals** — range requests (`Range` / `206 Partial Content`),
  conditional validators (`ETag`), redirects, content metadata.
- **Download-state persistence** — resuming an interrupted transfer without
  re-fetching completed bytes.
- **Browser integration** — a Chrome extension talking to a local app.

## Who it is for

- **Primary user:** the project owner — an intermediate Python developer on
  Windows.
- **Not for:** general users, distribution, or anyone who just needs a fast
  downloader.

## How it works (one paragraph)

A normal browser download uses one connection. myIDM opens several connections
to the same file at once, each requesting a different byte range with an HTTP
`Range` header, and writes each range to its correct offset in a preallocated
file. Per-segment progress is written to a small JSON sidecar continuously, so
an interrupted download resumes on re-run. Servers that do not support ranges
fall back to a single streamed connection.

## Milestones

| # | Milestone | Outcome |
|---|-----------|---------|
| 1 | Segmented engine + CLI | `python -m myidm <url>` gives a fast, resumable download with live progress |
| 2 | Local background service | Engine runs in the background, accepts URLs over a `127.0.0.1` endpoint, keeps a persistent queue, reports status |
| 3 | Chrome extension | Right-click a link in Chrome to send it to the service; an extension popup shows progress |

Each milestone ships independently and is usable on its own.

## Definition of success

All three pieces work end-to-end on a real download, **and** the owner can
explain in their own words how range requests, the resume sidecar, and the
browser-to-app bridge work.

## Related documents

- [`.claude/prds/personal-download-manager.prd.md`](.claude/prds/personal-download-manager.prd.md) — the base PRD (source-of-truth requirements).
- [`tasks/plan.md`](tasks/plan.md) — milestone 1 implementation plan (council-revised, Revision 2).
- [`CONSTRAINTS.md`](CONSTRAINTS.md) — architectural invariants and rules.
- [`CLAUDE.md`](CLAUDE.md) — commands and workflow for agents.
