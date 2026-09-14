# myIDM

A tiny personal download accelerator, built to learn. For a real tool use
[Motrix](https://motrix.app), [aria2](https://aria2.github.io/), or a browser
extension — this one exists to understand how segmented downloading and resume
work, not to compete with them.

## What it does

Downloads one file over several parallel HTTP connections, each fetching a
different byte range, then writes them into one preallocated `.part` file at the
right offsets. Per-segment progress is flushed to a `<name>.myidm.json` sidecar
about once a second, so an interrupted download **resumes on re-run** instead of
starting over. If the server does not support range requests (or does not report
a size), it falls back to a single streamed connection.

The assembled file is promoted to its final name only after an integrity check
(`sum(bytes per segment) == size`, every segment full). A server that lies about
its size fails loudly and leaves the `.part` for a retry — it never produces a
silently corrupt file.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements.txt
```

Python 3.11+ is required.

## Usage

```bash
python -m myidm https://example.com/big.iso              # 8 segments (default)
python -m myidm https://example.com/big.iso -n 16        # 16 parallel connections
python -m myidm https://example.com/big.iso -o downloads # write into ./downloads
```

A `\r`-updated line shows a bar, percent, speed, and ETA (speed comes straight
from the engine). On success it prints `Saved to <path>`.

Exit codes: `0` success, `1` you pressed Ctrl-C (re-run the same command to
resume), `2` any other error. Credentials in a `user:pass@host` URL are redacted
from error output.

## How resume works

- While downloading, each segment's byte count is written to
  `<name>.myidm.json` next to the output.
- On a re-run, that sidecar is trusted **only if** the URL, the total size, and
  a non-empty strong validator (`ETag`, else `Last-Modified`) all still match.
  If the file on the server changed, the sidecar is ignored and the download
  restarts clean — no corruption.
- Finished segments are not re-fetched; only the remaining bytes are pulled.
- On success both `<name>.part` and `<name>.myidm.json` are deleted.

**Durability scope:** resume survives Ctrl-C and a process restart. It does
**not** survive a hard power loss — the sidecar write is atomic (temp file +
`os.replace`) but not `fsync`-ed, so a crash can cost the last sub-second of
progress. That is a deliberate choice for a personal tool.

## Tests

```bash
pip install pytest ruff
python -m pytest -q          # no network — a local HTTP fixture stands in
ruff check .
```

## Running as a background service (Milestone 2)

For downloads that outlive a single `python -m myidm <url>` invocation — pause,
resume, cancel, or several queued at once — run myIDM as a small local service
instead:

```bash
python -m myidm serve                              # starts a detached background process
python -m myidm add https://example.com/big.iso     # queues a download, prints its id
python -m myidm add https://example.com/big.iso -o downloads -n 16
python -m myidm status                              # table: id, state, percent, filename
python -m myidm status <id> --json                  # one download, raw JSON
python -m myidm pause <id>
python -m myidm resume <id>
python -m myidm cancel <id>
python -m myidm stop                                # graceful shutdown
```

`serve` is idempotent — running it again while a service is already up just
prints "already running on <url>" instead of starting a second one. Up to 3
downloads run concurrently; the rest queue in FIFO order. A host that starts
failing gets demoted to 2 connections and retried once before it's marked
`error`. The queue and each download's progress survive `stop` / a crash /
Ctrl-C: a `queued`/`running`/`paused` entry on disk is picked back up and
resumed (via the same segment-sidecar resume that powers the one-shot CLI) the
next time `serve` runs.

**Where state lives:** `%LOCALAPPDATA%\myidm` on Windows, else
`$XDG_STATE_HOME/myidm` (or `~/.local/state/myidm`) — `queue.json` (the
download list; URLs there always have any embedded credentials stripped),
`service.port`, `service.token`, `service.pid`, and `service.log` (the
detached process's stdout/stderr).

**Trust boundary:** the service binds `127.0.0.1` only and requires the Bearer
token from `service.token` on every request — but *any* local process, and any
page open in your browser, can reach a `127.0.0.1` port and read that token
file. Do not run this on a machine you share with people you don't trust, and
do not expose the port past loopback (no `--host`, on purpose).

**Presigned-URL limitation:** if a queued download's URL is a presigned link
that expires before its turn comes up (or before pause/resume gets back to
it), it will fail with whatever error the server returns for an expired
signature — the service does not re-sign or refresh URLs. Re-queue it with a
fresh link via `add`.

## Known limitations

- Static equal segmentation: the slowest connection sets the finish time. A
  work-stealing / dynamic re-segmentation scheme is its own future milestone.
- The service queues downloads FIFO and demotes a failing host to 2
  connections once, but has no bandwidth throttling and no per-host priority.
- No GUI, clipboard capture, or drag-and-drop — those, plus a packaged `.exe`,
  are Milestone 4, deliberately deferred.
- See [`docs/milestone-2-security.md`](docs/milestone-2-security.md) for the
  full security write-up (SSRF/redirect validation, presigned-URL expiry,
  output-directory containment, the service's trust boundary).

## Manual smoke test (2026-09-10)

Run against `https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe`
(26,216,840 bytes, range-capable):

| Scenario | Result |
|---|---|
| 8 segments, clean | 4.3 s, sha256 `5ee42c4e…f5fdde` |
| 1 segment, clean | 3.9 s, same sha256 |
| interrupt at ~40%, re-run | resumes from sidecar, same sha256 |

On a fast CDN the 8-segment run is not faster than 1 segment — parallel segments
help on a per-connection-throttled server, not a link that is already saturated.

- Redirect chain (`github.com` → `codeload.github.com`, no range support):
  falls back to a single connection, 11 MB tarball downloaded intact.
- gzip endpoint (`Content-Encoding: gzip`): refused with
  `RuntimeError: unexpected Content-Encoding` — decoded bytes would desync
  offsets, so the engine will not save them.
