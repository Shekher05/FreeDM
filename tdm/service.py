"""In-process download queue: persistent `Download` records plus a scheduler
that runs up to `max_concurrent` downloads at once via the M1 engine.

# ponytail: one manager lock + condition - fine at 3 concurrent downloads.
Upgrade path: per-download locks if the scheduler itself becomes a bottleneck.
"""

import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests

from tdm import __version__, engine, netcheck, paths
from tdm.redact import redact, strip_credentials

PERSIST_FIELDS = ("id", "url", "dest_dir", "segments", "state", "filename", "error")
_TERMINAL_STATES = {"done", "error", "cancelled"}
_KEEP_TERMINAL = 50
_NO_RETRY_EXCEPTIONS = (engine.InsufficientSpace, ValueError)  # ValueError covers BlockedURLError
_MAX_BODY = 64 * 1024


@dataclass
class Download:
    id: str
    url: str
    dest_dir: str
    segments: int
    state: str
    filename: str | None = None
    total: int = 0
    done: int = 0
    bps: float = 0.0
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    mode: str | None = field(default=None, repr=False)
    demoted: bool = field(default=False, repr=False)


class Manager:
    def __init__(
        self, state_path: Path, max_concurrent: int = 3, download_dir: Path | None = None
    ) -> None:
        self.state_path = Path(state_path)
        self.max_concurrent = max_concurrent
        self.download_dir = download_dir
        self._cond = threading.Condition()
        self._downloads: dict[str, Download] = {}
        self._host_caps: dict[str, int] = {}
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent)
        self._running = False
        self._scheduler_thread: threading.Thread | None = None
        self._load()

    def _load(self) -> None:
        """Read an existing state file. Missing or corrupt -> start empty.
        `running`/`paused` mean the previous process stopped mid-download (a
        clean shutdown or a hard kill look the same on disk) - re-queue them
        so the scheduler re-runs `engine.download`, which resumes from its own
        sidecar. `done`/`error`/`cancelled` are kept as-is."""
        text = paths.read_or_none(self.state_path)
        if not text:
            return
        try:
            data = json.loads(text)
        except ValueError:
            return
        for entry in data.get("downloads", []):
            try:
                state = "queued" if entry["state"] in ("running", "paused") else entry["state"]
                dl = Download(
                    id=entry["id"],
                    url=entry["url"],
                    dest_dir=entry["dest_dir"],
                    segments=entry["segments"],
                    state=state,
                    filename=entry.get("filename"),
                    error=entry.get("error"),
                )
            except KeyError:
                continue
            self._downloads[dl.id] = dl

    def add(self, url: str, dest_dir: str | None = None, segments: int = 8) -> str:
        netcheck.assert_allowed_url(url)
        dl = Download(
            id=secrets.token_hex(8),
            url=url,
            dest_dir=str(dest_dir if dest_dir is not None else (self.download_dir or ".")),
            segments=segments,
            state="queued",
        )
        with self._cond:
            self._downloads[dl.id] = dl
            self._persist_locked()
            self._cond.notify_all()
        return dl.id

    def snapshot(self) -> list[dict]:
        with self._cond:
            return [self._to_snapshot(dl) for dl in self._downloads.values()]

    def snapshot_one(self, id: str) -> dict | None:
        with self._cond:
            dl = self._downloads.get(id)
            return self._to_snapshot(dl) if dl else None

    @staticmethod
    def _to_snapshot(dl: Download) -> dict:
        return {
            "id": dl.id,
            "url": strip_credentials(dl.url),
            "filename": dl.filename,
            "state": dl.state,
            "done": dl.done,
            "total": dl.total,
            "bps": dl.bps,
            "error": dl.error,
        }

    def pause(self, id: str) -> bool:
        with self._cond:
            dl = self._downloads.get(id)
            if dl is None or dl.state not in ("running", "queued"):
                return False
            if dl.state == "running":
                dl.mode = "pause"
                dl.cancel_event.set()
            else:
                dl.state = "paused"
                self._persist_locked()
            return True

    def resume(self, id: str) -> bool:
        with self._cond:
            dl = self._downloads.get(id)
            if dl is None or dl.state not in ("paused", "error"):
                return False
            dl.state = "queued"
            dl.demoted = False
            dl.error = None
            dl.cancel_event = threading.Event()
            dl.mode = None
            self._persist_locked()
            self._cond.notify_all()
            return True

    def cancel(self, id: str) -> bool:
        with self._cond:
            dl = self._downloads.get(id)
            if dl is None or dl.state in _TERMINAL_STATES:
                return False
            if dl.state == "running":
                dl.mode = "cancel"
                dl.cancel_event.set()
            else:
                dl.state = "cancelled"
                self._persist_locked()
            return True

    def start(self) -> None:
        self._running = True
        self._scheduler_thread = threading.Thread(target=self._scheduler, daemon=True)
        self._scheduler_thread.start()

    def shutdown(self, timeout: float = 15.0) -> None:
        with self._cond:
            self._running = False
            for dl in self._downloads.values():
                if dl.state == "running":
                    dl.mode = "pause"
                    dl.cancel_event.set()
            self._cond.notify_all()
        self._pool.shutdown(wait=True, cancel_futures=False)
        if self._scheduler_thread is not None:
            self._scheduler_thread.join(timeout=timeout)
        with self._cond:
            self._persist_locked()

    def _scheduler(self) -> None:
        with self._cond:
            while self._running:
                running = sum(1 for dl in self._downloads.values() if dl.state == "running")
                queued = next(
                    (dl for dl in self._downloads.values() if dl.state == "queued"), None
                )
                if running < self.max_concurrent and queued is not None:
                    queued.state = "running"
                    self._persist_locked()
                    self._pool.submit(self._run, queued)
                    continue
                self._cond.wait(timeout=0.5)

    def _run(self, dl: Download) -> None:
        host = urlsplit(dl.url).hostname or ""
        segs = min(self._host_caps.get(host, dl.segments), engine.MAX_SEGMENTS)
        try:
            final = engine.download(
                dl.url, dl.dest_dir, segs, progress_cb=self._cb(dl), cancel=dl.cancel_event
            )
        except engine.Cancelled:
            self._on_cancelled(dl)
            return
        except _NO_RETRY_EXCEPTIONS as exc:
            with self._cond:
                dl.state = "error"
                dl.error = redact(str(exc))
                self._persist_locked()
                self._cond.notify_all()
            return
        except Exception as exc:  # noqa: BLE001 - transient failure -> demote-and-retry once
            with self._cond:
                if host not in self._host_caps and not dl.demoted:
                    self._host_caps[host] = 2
                    dl.demoted = True
                    dl.state = "queued"
                else:
                    dl.state = "error"
                    dl.error = redact(str(exc))
                self._persist_locked()
                self._cond.notify_all()
            return
        with self._cond:
            dl.state = "done"
            dl.filename = final.name
            self._persist_locked()
            self._cond.notify_all()

    def _on_cancelled(self, dl: Download) -> None:
        if dl.mode == "cancel":
            self._cleanup_files(dl)
            with self._cond:
                dl.state = "cancelled"
                dl.mode = None
                self._persist_locked()
                self._cond.notify_all()
        else:  # paused - .part and the sidecar are kept for a later resume
            with self._cond:
                dl.state = "paused"
                dl.cancel_event = threading.Event()
                dl.mode = None
                self._persist_locked()
                self._cond.notify_all()

    @staticmethod
    def _cleanup_files(dl: Download) -> None:
        """Best-effort: re-probe to learn the filename `engine.download` would
        have used, then delete its `.part` and sidecar. A cancel that lands
        while the server is unreachable simply leaves them for a later add."""
        try:
            final = Path(dl.dest_dir) / engine._probe(dl.url).filename
            engine._part_path(final).unlink(missing_ok=True)
            engine._meta_path(final).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup, see docstring
            pass

    def _cb(self, dl: Download):
        def _progress(done: int, total: int, bps: float) -> None:
            dl.done, dl.total, dl.bps = done, total, bps

        return _progress

    def _keep(self) -> list[Download]:
        active = [dl for dl in self._downloads.values() if dl.state not in _TERMINAL_STATES]
        terminal = [dl for dl in self._downloads.values() if dl.state in _TERMINAL_STATES]
        return active + terminal[-_KEEP_TERMINAL:]

    def _persist_locked(self) -> None:
        downloads = []
        for dl in self._keep():
            entry = {k: getattr(dl, k) for k in PERSIST_FIELDS}
            entry["url"] = strip_credentials(entry["url"])
            downloads.append(entry)
        paths.write_atomic(self.state_path, json.dumps({"downloads": downloads}))


# --- HTTP layer --------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silence request logging in tests
        pass

    def _authed(self) -> bool:
        presented = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not presented.startswith(prefix):
            return False
        return hmac.compare_digest(self.server.token, presented[len(prefix) :])

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > _MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        return json.loads(raw) if raw else {}

    def do_GET(self) -> None:
        if not self._authed():
            self._send_json(401, {"error": "unauthorized"})
            return
        manager: Manager = self.server.manager
        path = self.path
        if path == "/health":
            self._send_json(
                200, {"ok": True, "version": __version__, "downloads": len(manager.snapshot())}
            )
        elif path == "/downloads":
            self._send_json(200, manager.snapshot())
        elif path.startswith("/downloads/"):
            snap = manager.snapshot_one(path[len("/downloads/") :])
            self._send_json(200, snap) if snap is not None else self._send_json(
                404, {"error": "not found"}
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authed():
            self._send_json(401, {"error": "unauthorized"})
            return
        manager: Manager = self.server.manager
        path = self.path
        if path == "/downloads":
            self._handle_add(manager)
        elif path == "/shutdown":
            self._send_json(202, {"ok": True})
            threading.Thread(target=self._shutdown, args=(self.server,), daemon=True).start()
        elif path.startswith("/downloads/"):
            self._handle_action(manager, path[len("/downloads/") :])
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_add(self, manager: "Manager") -> None:
        try:
            body = self._read_json_body()
        except ValueError:
            self._send_json(400, {"error": "bad request"})
            return
        url = body.get("url")
        if not isinstance(url, str) or not url:
            self._send_json(400, {"error": "missing url"})
            return
        try:
            segments = max(1, min(int(body.get("segments", 8)), engine.MAX_SEGMENTS))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "bad request"})
            return
        try:
            id_ = manager.add(url, dest_dir=body.get("dest_dir"), segments=segments)
        except netcheck.BlockedURLError:
            self._send_json(400, {"error": "blocked"})
            return
        self._send_json(201, {"id": id_})

    def _handle_action(self, manager: "Manager", rest: str) -> None:
        parts = rest.split("/")
        if len(parts) != 2 or parts[1] not in ("pause", "resume", "cancel"):
            self._send_json(404, {"error": "not found"})
            return
        id_, action = parts
        if manager.snapshot_one(id_) is None:
            self._send_json(404, {"error": "not found"})
            return
        getattr(manager, action)(id_)
        self._send_json(202, {"ok": True})

    @staticmethod
    def _shutdown(server: "_Server") -> None:
        server.manager.shutdown()
        server.shutdown()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, manager: Manager, token: str, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), _Handler)
        self.manager = manager
        self.token = token


def serve_in_thread(
    manager: Manager, token: str, host: str = "127.0.0.1", port: int = 0
) -> tuple[_Server, int]:
    """Start `_Server` on its own daemon thread; returns the server (so the
    caller can `.shutdown()` it) and the bound port."""
    server = _Server(manager, token, host, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


# --- process management -------------------------------------------------------

_STATE_FILES = ("service.port", "service.token", "service.pid")


def read_endpoint() -> tuple[str, str] | None:
    """`(base_url, token)` for the running service, or `None` if not running."""
    d = paths.state_dir()
    port = paths.read_or_none(d / "service.port")
    token = paths.read_or_none(d / "service.token")
    if not port or not token:
        return None
    return f"http://127.0.0.1:{port}", token


def _clear_state_files(d: Path) -> None:
    for name in _STATE_FILES:
        (d / name).unlink(missing_ok=True)


def run_service() -> None:
    """Blocking: run the HTTP service in the foreground until shutdown (via
    `POST /shutdown` or SIGTERM/SIGBREAK). This is what `python -m tdm
    _serve` execs as a detached child."""
    d = paths.state_dir()
    token = secrets.token_urlsafe(32)
    manager = Manager(d / "queue.json", download_dir=paths.default_download_dir())
    server = _Server(manager, token)
    paths.write_atomic(d / "service.port", str(server.server_address[1]))
    paths.write_atomic(d / "service.token", token)
    paths.write_atomic(d / "service.pid", str(os.getpid()))
    manager.start()

    def _stop() -> None:
        manager.shutdown()
        server.shutdown()

    def _on_signal(signum, frame) -> None:
        threading.Thread(target=_stop, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _on_signal)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _on_signal)
    except ValueError:
        pass  # not the main thread (e.g. run under a test) - /shutdown still works

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    thread.join()
    manager.shutdown()
    server.server_close()
    _clear_state_files(d)


def spawn_detached() -> str:
    """Start `run_service()` in a detached child process if one isn't already
    healthy; returns a one-line status message."""
    endpoint = read_endpoint()
    if endpoint is not None:
        base_url, token = endpoint
        try:
            r = requests.get(
                f"{base_url}/health", headers={"Authorization": f"Bearer {token}"}, timeout=2
            )
            if r.status_code == 200:
                return f"already running on {base_url}"
        except requests.RequestException:
            pass

    d = paths.state_dir()
    _clear_state_files(d)
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    with open(d / "service.log", "ab") as log_fh:
        subprocess.Popen(
            [sys.executable, "-m", "tdm", "_serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            close_fds=True,
            **kwargs,
        )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        endpoint = read_endpoint()
        if endpoint is not None:
            return endpoint[0]
        time.sleep(0.1)
    raise RuntimeError("service did not start; see service.log")


def stop_service() -> str:
    """Ask the running service to shut down (HTTP first, then a hard kill if
    it doesn't within 10s). Returns a one-line status message."""
    endpoint = read_endpoint()
    if endpoint is None:
        return "not running"
    base_url, token = endpoint
    d = paths.state_dir()
    try:
        requests.post(
            f"{base_url}/shutdown", headers={"Authorization": f"Bearer {token}"}, timeout=5
        )
    except requests.RequestException:
        pass
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and (d / "service.port").exists():
        time.sleep(0.2)
    if (d / "service.port").exists():
        pid = paths.read_or_none(d / "service.pid")
        if pid:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, check=False)
            else:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except OSError:
                    pass
    _clear_state_files(d)
    return "stopped"
