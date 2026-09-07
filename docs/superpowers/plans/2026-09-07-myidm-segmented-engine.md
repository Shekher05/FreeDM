# myIDM Segmented Engine + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python CLI that downloads one URL over N parallel HTTP range connections, shows live progress, and resumes cleanly after an interruption.

**Architecture:** A pure library module (`myidm/engine.py`) does the work: probe the URL with HEAD, split the byte range into N contiguous segments, download them concurrently with a `ThreadPoolExecutor` (one blocking `requests` stream per segment writing to its own offset in a preallocated `.part` file), and persist per-segment byte counts to a JSON sidecar every ~0.5s so a re-run resumes. A thin `myidm/__main__.py` wraps it with `argparse` and a terminal progress line. Servers without range support fall back to a single streamed connection.

**Tech Stack:** Python 3.11+, `requests` (only runtime dependency), `pytest` + `ruff` (dev). Tests use stdlib `http.server` for a local range-capable server — no network.

**Spec:** `.claude/prds/personal-download-manager.prd.md`

## Global Constraints

- Python 3.11+ (uses `X | None` type syntax, `Path.unlink(missing_ok=True)`).
- Exactly one runtime dependency: `requests`. Do not add others.
- Runtime is `python -m myidm` from the repo root — no packaging / `pyproject.toml` in this milestone.
- Engine writes two sidecar files next to the output: `<name>.part` (raw bytes, preallocated to full size) and `<name>.myidm.json` (`{"url": str, "size": int, "etag": str, "progress": [int, ...]}`, one int per segment = bytes completed). Both are deleted on success.
- Concurrency safety rule: each segment owns a disjoint byte range and its own file handle; `progress[idx]` is written by exactly one thread. No locks. `# ponytail: relies on CPython list-item assignment atomicity`.
- Segment retry policy: 5 attempts, `time.sleep(2 ** attempt)` backoff, resume from current offset each attempt.
- Chunk size: 65536 bytes. Sidecar save interval: 1.0s (single path) / every wait-tick ~0.5s (segmented path).
- Out of scope: multi-URL queue, `-k` concurrency cap, throttling, scheduler, GUI, Chrome bits.

---

## File Structure

| File | Responsibility |
|---|---|
| `myidm/__init__.py` | Package marker + `__version__`. |
| `myidm/engine.py` | Everything: probe, `split_ranges`, `derive_filename`, sidecar load/save, segment download, segmented orchestration, single-connection fallback, public `download()`. |
| `myidm/__main__.py` | `argparse` CLI, rolling-speed progress renderer, `KeyboardInterrupt` handling. |
| `tests/__init__.py` | Package marker (empty). |
| `tests/conftest.py` | `make_server` fixture: a `ThreadingHTTPServer` serving a fixed byte blob, honouring `Range` (toggleable), counting bytes served. |
| `tests/test_engine.py` | Unit tests for pure functions + end-to-end / resume / fallback tests. |
| `tests/test_cli.py` | One end-to-end test of `myidm.__main__.main()`. |
| `requirements.txt` | `requests>=2.31` |
| `.gitignore` | Python + `*.part`, `*.myidm.json`, `.venv/` |
| `README.md` | What it is, install, usage, how resume works. |

---

## Task 1: Project skeleton + `split_ranges`

**Files:**
- Create: `myidm/__init__.py`
- Create: `myidm/engine.py`
- Create: `tests/__init__.py`
- Create: `tests/test_engine.py`
- Create: `requirements.txt`
- Create: `.gitignore`

**Interfaces:**
- Consumes: nothing.
- Produces: `myidm.engine.split_ranges(size: int, n: int) -> list[tuple[int, int]]` — contiguous inclusive `(start, end)` pairs covering `[0, size)`, at most `n` of them, last absorbs the remainder. Raises `ValueError` if `size <= 0`.

- [ ] **Step 1: Initialize the repo and dependency files**

Run:
```bash
cd "C:/Users/05she/OneDrive/Desktop/IDM tryAL"
git init
```

Create `requirements.txt`:
```
requests>=2.31
```

Create `.gitignore`:
```
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.part
*.myidm.json
```

Create `myidm/__init__.py`:
```python
"""myIDM - a small segmented HTTP download engine with resume."""

__version__ = "0.1.0"
```

Create empty `tests/__init__.py` (0 bytes).

- [ ] **Step 2: Write the failing test**

Create `tests/test_engine.py`:
```python
import pytest

from myidm.engine import split_ranges


def test_split_ranges_even():
    assert split_ranges(100, 4) == [(0, 24), (25, 49), (50, 74), (75, 99)]


def test_split_ranges_remainder_on_last():
    r = split_ranges(103, 4)
    assert r[0] == (0, 24)
    assert r[-1][1] == 102
    for a, b in zip(r, r[1:]):
        assert b[0] == a[1] + 1


def test_split_ranges_more_segments_than_bytes():
    assert split_ranges(3, 8) == [(0, 0), (1, 1), (2, 2)]


def test_split_ranges_rejects_zero():
    with pytest.raises(ValueError):
        split_ranges(0, 4)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -q`
Expected: FAIL — `ImportError` / `cannot import name 'split_ranges'` (module has no such symbol yet).

- [ ] **Step 4: Write minimal implementation**

Create `myidm/engine.py`:
```python
"""Segmented HTTP download engine with resume support."""
from __future__ import annotations


def split_ranges(size: int, n: int) -> list[tuple[int, int]]:
    """Split [0, size) into at most n contiguous, inclusive (start, end) ranges."""
    if size <= 0:
        raise ValueError("size must be positive")
    n = max(1, min(n, size))
    step = size // n
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(n):
        end = size - 1 if i == n - 1 else start + step - 1
        ranges.append((start, end))
        start = end + 1
    return ranges
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -q`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add .gitignore requirements.txt myidm/ tests/
git commit -m "feat: project skeleton and split_ranges"
```

---

## Task 2: `derive_filename`

**Files:**
- Modify: `myidm/engine.py` (add imports + `derive_filename`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `myidm.engine.derive_filename(url: str, resp) -> str` — `resp` is any object with a `.headers` mapping (`requests.Response` in production). Order of preference: `Content-Disposition` `filename=`, then the URL path basename (percent-decoded), then `"download.bin"`. Always strips directory separators (no path traversal).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:
```python
from myidm.engine import derive_filename


class _FakeResp:
    def __init__(self, headers):
        self.headers = headers


def test_derive_filename_from_url():
    assert derive_filename("http://h/a/b/file.zip", _FakeResp({})) == "file.zip"


def test_derive_filename_from_content_disposition():
    resp = _FakeResp({"Content-Disposition": 'attachment; filename="real.tar.gz"'})
    assert derive_filename("http://h/", resp) == "real.tar.gz"


def test_derive_filename_fallback():
    assert derive_filename("http://h/", _FakeResp({})) == "download.bin"


def test_derive_filename_strips_traversal():
    assert derive_filename("http://h/..%2f..%2fetc%2fpasswd", _FakeResp({})) == "passwd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -q -k derive_filename`
Expected: FAIL — `cannot import name 'derive_filename'`.

- [ ] **Step 3: Write minimal implementation**

At the top of `myidm/engine.py`, below `from __future__ import annotations`:
```python
from urllib.parse import unquote, urlsplit
```

Add the function:
```python
def derive_filename(url: str, resp) -> str:
    """Pick an output filename from Content-Disposition, then the URL, then a default."""
    cd = resp.headers.get("Content-Disposition", "")
    name = ""
    if "filename=" in cd:
        name = cd.split("filename=", 1)[1].split(";")[0].strip().strip('"')
    if not name:
        name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return name or "download.bin"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add myidm/engine.py tests/test_engine.py
git commit -m "feat: derive_filename with path-traversal stripping"
```

---

## Task 3: Sidecar state load / save

**Files:**
- Modify: `myidm/engine.py` (add `json`, `pathlib` imports + 4 helpers)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `_part_path(final: Path) -> Path` → `final` + `".part"`
  - `_meta_path(final: Path) -> Path` → `final` + `".myidm.json"`
  - `_load_progress(meta: Path, url: str, size: int, etag: str) -> list[int] | None` — returns the saved per-segment list only when `url`/`size`/`etag` all match; otherwise `None` (including missing / corrupt file).
  - `_save_progress(meta: Path, url: str, size: int, etag: str, progress: list[int]) -> None` — writes `{"url", "size", "etag", "progress"}` as JSON.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:
```python
from pathlib import Path

from myidm.engine import _load_progress, _meta_path, _part_path, _save_progress


def test_sidecar_paths():
    final = Path("/tmp/movie.mkv")
    assert _part_path(final).name == "movie.mkv.part"
    assert _meta_path(final).name == "movie.mkv.myidm.json"


def test_progress_round_trip(tmp_path):
    meta = tmp_path / "f.myidm.json"
    _save_progress(meta, "http://h/f", 100, '"e1"', [10, 20, 0])
    assert _load_progress(meta, "http://h/f", 100, '"e1"') == [10, 20, 0]


def test_progress_rejected_on_mismatch(tmp_path):
    meta = tmp_path / "f.myidm.json"
    _save_progress(meta, "http://h/f", 100, '"e1"', [10, 20, 0])
    assert _load_progress(meta, "http://h/f", 999, '"e1"') is None
    assert _load_progress(meta, "http://h/OTHER", 100, '"e1"') is None


def test_progress_missing_file(tmp_path):
    assert _load_progress(tmp_path / "nope.json", "u", 1, "e") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -q -k "sidecar or progress"`
Expected: FAIL — `cannot import name '_load_progress'`.

- [ ] **Step 3: Write minimal implementation**

Add imports to `myidm/engine.py`:
```python
import json
from pathlib import Path
```

Add the helpers:
```python
def _part_path(final: Path) -> Path:
    return final.with_name(final.name + ".part")


def _meta_path(final: Path) -> Path:
    return final.with_name(final.name + ".myidm.json")


def _load_progress(meta: Path, url: str, size: int, etag: str) -> list[int] | None:
    """Return saved per-segment byte counts if the sidecar matches this download."""
    try:
        data = json.loads(meta.read_text())
    except (OSError, ValueError):
        return None
    if data.get("url") == url and data.get("size") == size and data.get("etag") == etag:
        return list(data["progress"])
    return None


def _save_progress(meta: Path, url: str, size: int, etag: str, progress: list[int]) -> None:
    meta.write_text(json.dumps({"url": url, "size": size, "etag": etag, "progress": progress}))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -q`
Expected: PASS (12 passed).

- [ ] **Step 5: Commit**

```bash
git add myidm/engine.py tests/test_engine.py
git commit -m "feat: resume sidecar load/save helpers"
```

---

## Task 4: Local range-capable test server fixture

**Files:**
- Create: `tests/conftest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `pytest` fixture `make_server` — a factory `make_server(data: bytes, support_range: bool = True) -> server` where `server` has `.url` (`str`), `.data` (`bytes`), `.support_range` (`bool`), `.served_bytes` (`int`, running total of body bytes written to clients). Servers are shut down automatically at teardown. Responds to HEAD (`Content-Length`, `ETag: "test-etag"`, `Accept-Ranges: bytes` when enabled) and GET (206 + `Content-Range` for `Range` requests when enabled, otherwise 200 with the whole body).

- [ ] **Step 1: Write the fixture**

Create `tests/conftest.py`:
```python
import http.server
import threading

import pytest


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence test output
        pass

    def _shared_headers(self):
        self.send_header("ETag", '"test-etag"')
        if self.server.support_range:
            self.send_header("Accept-Ranges", "bytes")

    def do_HEAD(self):
        data = self.server.data
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self._shared_headers()
        self.end_headers()

    def do_GET(self):
        data = self.server.data
        rng = self.headers.get("Range")
        if rng and self.server.support_range:
            spec = rng.split("=", 1)[1]
            start_s, _, end_s = spec.partition("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(data) - 1
            body = data[start : end + 1]
            self.server.served_bytes += len(body)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
            self._shared_headers()
            self.end_headers()
            self.wfile.write(body)
            return
        self.server.served_bytes += len(data)
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self._shared_headers()
        self.end_headers()
        self.wfile.write(data)


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, data: bytes, support_range: bool):
        super().__init__(("127.0.0.1", 0), _RangeHandler)
        self.data = data
        self.support_range = support_range
        self.served_bytes = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/file.bin"


@pytest.fixture
def make_server():
    servers = []

    def _make(data: bytes, support_range: bool = True) -> _Server:
        server = _Server(data, support_range)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.shutdown()
```

- [ ] **Step 2: Sanity-check the fixture loads**

Run: `python -m pytest tests/ -q --collect-only`
Expected: collection succeeds, no errors (existing 12 tests listed).

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: local range-capable HTTP server fixture"
```

---

## Task 5: Segment download + segmented orchestration (fresh download)

**Files:**
- Modify: `myidm/engine.py` (add `os`, `time`, `concurrent.futures`, `requests` imports; constants; `RangeNotSupported`; `_probe`, `_download_segment`, `_emit`, `_download_segmented`, `download`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `split_ranges`, `derive_filename`, `_part_path`, `_meta_path`, `_load_progress`, `_save_progress`.
- Produces:
  - `RangeNotSupported(Exception)`
  - `_probe(url: str) -> tuple[int, bool, str, str]` → `(size, accept_ranges, etag, filename)`
  - `_download_segment(url, part: Path, start: int, end: int, progress: list[int], idx: int) -> None`
  - `_download_segmented(url, final: Path, size: int, etag: str, segments: int, cb) -> Path`
  - `download(url: str, dest_dir: str = ".", segments: int = 8, progress_cb=None) -> Path` — public entry point. `progress_cb(done_bytes: int, total_bytes: int, avg_bytes_per_sec: float)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:
```python
import os

from myidm.engine import download


def test_download_end_to_end(make_server, tmp_path):
    blob = os.urandom(5 * 1024 * 1024)
    server = make_server(blob)
    path = download(server.url, tmp_path, segments=4)
    assert path == tmp_path / "file.bin"
    assert path.read_bytes() == blob
    assert not (tmp_path / "file.bin.part").exists()
    assert not (tmp_path / "file.bin.myidm.json").exists()


def test_download_reports_progress(make_server, tmp_path):
    blob = os.urandom(2 * 1024 * 1024)
    server = make_server(blob)
    seen = []
    download(server.url, tmp_path, segments=4, progress_cb=lambda d, t, s: seen.append((d, t)))
    assert seen and seen[-1][0] == seen[-1][1] == len(blob)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -q -k download`
Expected: FAIL — `cannot import name 'download'`.

- [ ] **Step 3: Write minimal implementation**

Add imports + constants near the top of `myidm/engine.py`:
```python
import os
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

import requests

CHUNK = 65536
SEGMENT_RETRIES = 5
SAVE_INTERVAL = 1.0
```

Add the class and functions:
```python
class RangeNotSupported(Exception):
    """Raised when a server ignores a Range request and returns the whole body."""


def _probe(url: str) -> tuple[int, bool, str, str]:
    r = requests.head(url, allow_redirects=True, timeout=30)
    r.raise_for_status()
    size = int(r.headers.get("Content-Length", 0))
    accept = r.headers.get("Accept-Ranges", "").lower() == "bytes"
    etag = r.headers.get("ETag", "")
    return size, accept, etag, derive_filename(url, r)


def _emit(cb, done: int, total: int, started: float) -> None:
    if cb:
        elapsed = max(time.monotonic() - started, 0.001)
        cb(done, total, done / elapsed)


def _download_segment(url, part: Path, start: int, end: int, progress: list[int], idx: int) -> None:
    """Fetch bytes [start + progress[idx] .. end] into `part` at the right offset."""
    for attempt in range(SEGMENT_RETRIES):
        pos = start + progress[idx]
        if pos > end:
            return
        try:
            with requests.get(
                url, headers={"Range": f"bytes={pos}-{end}"}, stream=True, timeout=30
            ) as r:
                if r.status_code == 200:
                    raise RangeNotSupported(url)
                r.raise_for_status()
                with open(part, "r+b") as f:
                    f.seek(pos)
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
                        progress[idx] += len(chunk)
            return
        except RangeNotSupported:
            raise
        except requests.RequestException:
            if attempt == SEGMENT_RETRIES - 1:
                raise
            time.sleep(2 ** attempt)


def _download_segmented(url, final: Path, size: int, etag: str, segments: int, cb) -> Path:
    part, meta = _part_path(final), _meta_path(final)
    ranges = split_ranges(size, segments)
    progress = _load_progress(meta, url, size, etag)
    if progress is None or len(progress) != len(ranges):
        progress = [0] * len(ranges)
        with open(part, "wb") as f:
            f.truncate(size)
    _save_progress(meta, url, size, etag, progress)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(ranges)) as ex:
        pending = {
            ex.submit(_download_segment, url, part, s, e, progress, i)
            for i, (s, e) in enumerate(ranges)
            if progress[i] < e - s + 1
        }
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_EXCEPTION)
            _save_progress(meta, url, size, etag, progress)
            _emit(cb, sum(progress), size, started)
            for fut in done:
                exc = fut.exception()
                if exc:
                    raise exc

    _save_progress(meta, url, size, etag, progress)
    _emit(cb, size, size, started)
    os.replace(part, final)
    meta.unlink(missing_ok=True)
    return final


def download(url: str, dest_dir: str = ".", segments: int = 8, progress_cb=None) -> Path:
    """Download `url` into `dest_dir` using up to `segments` parallel connections."""
    segments = max(1, segments)
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    size, accept, etag, name = _probe(url)
    final = out_dir / name
    if not accept or size <= 0:
        raise RangeNotSupported(url)  # replaced with single-connection path in Task 7
    return _download_segmented(url, final, size, etag, segments, progress_cb)
```

Note: the `not accept or size <= 0` branch raising `RangeNotSupported` is a deliberate temporary stand-in so this task stays small; Task 7 replaces it with the real fallback. Task 5 tests only exercise range-capable servers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_engine.py -q`
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add myidm/engine.py tests/test_engine.py
git commit -m "feat: parallel segmented download for range-capable servers"
```

---

## Task 6: Resume

**Files:**
- Test: `tests/test_engine.py` (new test only — `_download_segmented` already reads the sidecar; this task proves it and fixes anything the test exposes)

**Interfaces:**
- Consumes: `download`, `split_ranges`.
- Produces: no new symbols. Guarantee: given a valid sidecar + partial `.part`, `download()` fetches only the missing bytes and produces a correct file.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:
```python
import json

from myidm.engine import split_ranges


def test_resume_only_fetches_remainder(make_server, tmp_path):
    blob = os.urandom(4 * 1024 * 1024)
    server = make_server(blob)
    size = len(blob)
    ranges = split_ranges(size, 4)

    # Simulate an interrupted run: segments 0 and 1 fully done, 2 and 3 untouched.
    part = tmp_path / "file.bin.part"
    with open(part, "wb") as f:
        f.truncate(size)
        for s, e in ranges[:2]:
            f.seek(s)
            f.write(blob[s : e + 1])
    done_counts = [
        ranges[0][1] - ranges[0][0] + 1,
        ranges[1][1] - ranges[1][0] + 1,
        0,
        0,
    ]
    (tmp_path / "file.bin.myidm.json").write_text(
        json.dumps({"url": server.url, "size": size, "etag": '"test-etag"', "progress": done_counts})
    )

    path = download(server.url, tmp_path, segments=4)

    assert path.read_bytes() == blob
    already_done = sum(done_counts)
    assert server.served_bytes <= (size - already_done) + 1024  # only the remainder (+slack)
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_engine.py -q -k resume`
Expected: PASS immediately (the sidecar-reading path from Task 5 already handles this). If it FAILS, the likely cause is the `len(progress) != len(ranges)` guard or an off-by-one in the "segment already complete" check `progress[i] < e - s + 1` — fix that line in `_download_segmented` and re-run.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (15 passed).

- [ ] **Step 4: Commit**

```bash
git add tests/test_engine.py myidm/engine.py
git commit -m "test: prove resume fetches only the missing bytes"
```

---

## Task 7: Single-connection fallback

**Files:**
- Modify: `myidm/engine.py` (add `_download_single`; wire it into `download`)
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `_part_path`, `_meta_path`, `_load_progress`, `_save_progress`, `_emit`, `RangeNotSupported`.
- Produces: `_download_single(url, final: Path, size: int, etag: str, cb) -> Path`. `download()` now: no range support or unknown size → `_download_single`; a `RangeNotSupported` raised mid-segmented-run → clean up `.part`/sidecar, retry once via `_download_single`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`:
```python
def test_fallback_when_server_ignores_range(make_server, tmp_path):
    blob = os.urandom(512 * 1024)
    server = make_server(blob, support_range=False)
    path = download(server.url, tmp_path, segments=4)
    assert path.read_bytes() == blob
    assert not (tmp_path / "file.bin.part").exists()
    assert not (tmp_path / "file.bin.myidm.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_engine.py -q -k fallback`
Expected: FAIL — `download()` currently raises `RangeNotSupported` for `support_range=False`.

- [ ] **Step 3: Write the implementation**

Add to `myidm/engine.py`:
```python
def _download_single(url, final: Path, size: int, etag: str, cb) -> Path:
    part, meta = _part_path(final), _meta_path(final)
    done = 0
    prev = _load_progress(meta, url, size, etag)
    if prev is not None and part.exists():
        done = min(part.stat().st_size, sum(prev))
    headers = {"Range": f"bytes={done}-"} if done else {}
    started = time.monotonic()
    last_save = 0.0
    with requests.get(url, headers=headers, stream=True, timeout=30) as r:
        if done and r.status_code == 200:
            done = 0  # server ignored our resume request; start over
        r.raise_for_status()
        mode = "r+b" if done and part.exists() else "wb"
        with open(part, mode) as f:
            if mode == "r+b":
                f.seek(done)
            else:
                done = 0
            for chunk in r.iter_content(CHUNK):
                f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last_save > SAVE_INTERVAL:
                    _save_progress(meta, url, size, etag, [done])
                    last_save = now
                if size:
                    _emit(cb, done, size, started)
    _save_progress(meta, url, size, etag, [done])
    os.replace(part, final)
    meta.unlink(missing_ok=True)
    return final
```

Replace the body of `download()` after `final = out_dir / name` with:
```python
    if not accept or size <= 0:
        return _download_single(url, final, size, etag, progress_cb)
    try:
        return _download_segmented(url, final, size, etag, segments, progress_cb)
    except RangeNotSupported:
        _part_path(final).unlink(missing_ok=True)
        _meta_path(final).unlink(missing_ok=True)
        # ponytail: threads mid-206 may have written data we now discard; rare, only
        # when a server answers 206 and 200 inconsistently across connections.
        return _download_single(url, final, size, etag, progress_cb)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q`
Expected: PASS (16 passed).

- [ ] **Step 5: Commit**

```bash
git add myidm/engine.py tests/test_engine.py
git commit -m "feat: single-connection fallback for servers without range support"
```

---

## Task 8: CLI

**Files:**
- Create: `myidm/__main__.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: `myidm.engine.download`.
- Produces: `myidm.__main__.main(argv: list[str] | None = None) -> int`. Args: `url` (positional), `-n/--segments` (int, default 8), `-o/--output-dir` (default `.`). Returns `0` success, `1` on `KeyboardInterrupt`, `2` on any other exception. Prints a `\r`-updated progress line to stdout and a final `Saved to <path>`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:
```python
import os

from myidm.__main__ import main


def test_cli_downloads_file(make_server, tmp_path, capsys):
    blob = os.urandom(1024 * 1024)
    server = make_server(blob)
    rc = main([server.url, "-n", "4", "-o", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "file.bin").read_bytes() == blob
    assert "Saved to" in capsys.readouterr().out


def test_cli_bad_url_returns_2(tmp_path):
    rc = main(["http://127.0.0.1:1/nope.bin", "-o", str(tmp_path)])
    assert rc == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'myidm.__main__'`.

- [ ] **Step 3: Write the implementation**

Create `myidm/__main__.py`:
```python
"""Command-line entry point: python -m myidm <url>"""
from __future__ import annotations

import argparse
import sys
import time

from .engine import download


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _fmt_eta(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class _ProgressLine:
    """Renders a single updating terminal line with rolling-window speed."""

    def __init__(self) -> None:
        self._samples: list[tuple[float, int]] = []

    def __call__(self, done: int, total: int, avg_speed: float) -> None:
        now = time.monotonic()
        self._samples.append((now, done))
        self._samples = self._samples[-20:]
        if len(self._samples) >= 2:
            dt = self._samples[-1][0] - self._samples[0][0]
            db = self._samples[-1][1] - self._samples[0][1]
            speed = db / dt if dt > 0 else avg_speed
        else:
            speed = avg_speed
        pct = (done / total * 100) if total else 0.0
        filled = int(20 * done / total) if total else 0
        bar = "#" * filled + " " * (20 - filled)
        eta = (total - done) / speed if speed > 0 else 0
        sys.stdout.write(
            f"\r[{bar}] {pct:5.1f}%  {_fmt_size(speed)}/s  ETA {_fmt_eta(eta)}   "
        )
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="myidm", description="Segmented download with resume")
    parser.add_argument("url")
    parser.add_argument("-n", "--segments", type=int, default=8)
    parser.add_argument("-o", "--output-dir", default=".")
    args = parser.parse_args(argv)

    try:
        path = download(args.url, args.output_dir, args.segments, _ProgressLine())
        print(f"\nSaved to {path}")
        return 0
    except KeyboardInterrupt:
        print("\nPaused - re-run the same command to resume.")
        return 1
    except Exception as exc:  # noqa: BLE001 - top-level CLI guard
        print(f"\nError: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest -q`
Expected: PASS (18 passed).

- [ ] **Step 5: Lint**

Run: `ruff check .`
Expected: no errors. Fix any it reports (import order, unused names).

- [ ] **Step 6: Commit**

```bash
git add myidm/__main__.py tests/test_cli.py
git commit -m "feat: python -m myidm CLI with progress line"
```

---

## Task 9: README + manual smoke test

**Files:**
- Create: `README.md`

**Interfaces:** none.

- [ ] **Step 1: Write `README.md`**

````markdown
# myIDM

A tiny personal download accelerator. Learning project — for a real tool use
[Motrix](https://motrix.app) or `aria2`.

## What it does

Downloads one file over several parallel HTTP connections, each fetching a
different byte range, then stitches them together. Saves per-segment progress
to a `<name>.myidm.json` sidecar so an interrupted download resumes on re-run.
Falls back to a single connection when the server does not support ranges.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

## Usage

```bash
python -m myidm https://example.com/big.iso            # 8 segments (default)
python -m myidm https://example.com/big.iso -n 16      # 16 segments
python -m myidm https://example.com/big.iso -o downloads
```

Press Ctrl-C to stop; run the same command again to resume.

## Tests

```bash
pip install pytest ruff
python -m pytest -q
ruff check .
```
````

- [ ] **Step 2: Manual smoke test (record the result in the commit message)**

```bash
python -m myidm https://speed.hetzner.de/100MB.bin -n 8
# Ctrl-C around 40%, then re-run the same command
python -m myidm https://speed.hetzner.de/100MB.bin -n 8
certutil -hashfile 100MB.bin SHA256
```
Expected: second run starts near 40%, finishes, file hash is stable across a
clean run. Also try one small file on a host without range support and confirm
it still downloads.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README and smoke-test notes"
```

---

## Self-Review

**1. Spec coverage:**
- PRD MVP "one URL, N parallel range connections" → Tasks 1, 5.
- "live progress (%, speed, ETA)" → Task 8 `_ProgressLine`; engine `progress_cb` Tasks 5, 7.
- "writes progress to disk continuously" → Task 3 + save calls in Tasks 5, 7.
- "resumes correctly when re-run" → Task 6 (segmented), Task 7 (single).
- "falls back to a single connection when the server does not support ranges" → Task 7.
- Success metric "downloaded file matches source checksum" → Tasks 5, 6, 7 assert byte-equality.
- Success metric "resume … no re-downloading finished bytes" → Task 6 asserts `served_bytes <= remainder`.
- Out-of-scope items (queue, service, extension, GUI) → not in any task. Correct.

**2. Placeholder scan:** The only forward reference is Task 5's deliberate temporary `raise RangeNotSupported` stand-in, explicitly called out and replaced in Task 7. No TBD/TODO; every code step has real code.

**3. Type consistency:** `download(url, dest_dir, segments, progress_cb)` identical in Tasks 5, 7, 8. `progress` is `list[int]` throughout (Tasks 3, 5, 6). `_load_progress` / `_save_progress` / `_part_path` / `_meta_path` signatures match across Tasks 3, 5, 7. `progress_cb(done, total, speed)` matches between `_emit` (engine) and `_ProgressLine.__call__` (CLI).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-07-myidm-segmented-engine.md`. Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
