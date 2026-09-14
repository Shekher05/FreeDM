import json
import os
import threading
import time

import pytest
import requests

from myidm import netcheck, paths, service
from myidm.engine import InsufficientSpace
from myidm.netcheck import BlockedURLError
from myidm.netcheck import assert_allowed_url as _real_assert_allowed_url
from myidm.service import PERSIST_FIELDS, Manager, serve_in_thread


def _poll(mgr, id_, states, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = mgr.snapshot_one(id_)
        if snap and snap["state"] in states:
            return snap
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {states}; last snapshot: {mgr.snapshot_one(id_)}")


def test_add_queues_and_persists(tmp_path, make_server):
    server = make_server(os.urandom(1024))
    mgr = Manager(tmp_path / "queue.json", download_dir=tmp_path / "dl")
    id_ = mgr.add(server.url)

    assert mgr.snapshot_one(id_)["state"] == "queued"
    data = json.loads((tmp_path / "queue.json").read_text())
    assert len(data["downloads"]) == 1
    entry = data["downloads"][0]
    assert set(entry) == set(PERSIST_FIELDS)
    assert entry["id"] == id_
    assert entry["state"] == "queued"


def test_add_never_persists_credentials(tmp_path):
    mgr = Manager(tmp_path / "queue.json", download_dir=tmp_path / "dl")
    mgr.add("http://alice:s3cr3t@example.com/file.zip")
    contents = (tmp_path / "queue.json").read_text()
    assert "s3cr3t" not in contents
    assert "s3cr3t" not in json.dumps(mgr.snapshot())


def test_add_rejects_private_url(tmp_path, monkeypatch):
    monkeypatch.setattr(netcheck, "assert_allowed_url", _real_assert_allowed_url)
    mgr = Manager(tmp_path / "queue.json")
    with pytest.raises(BlockedURLError):
        mgr.add("http://127.0.0.1:9/x")
    assert not (tmp_path / "queue.json").exists()


def test_scheduler_promotes_up_to_max(tmp_path, make_server):
    server = make_server(os.urandom(4 * 1024 * 1024))
    mgr = Manager(tmp_path / "queue.json", max_concurrent=3)
    ids = [mgr.add(server.url, dest_dir=str(tmp_path / f"dl{i}")) for i in range(5)]
    mgr.start()
    try:
        deadline = time.monotonic() + 5
        running_ids = set()
        while time.monotonic() < deadline:
            snaps = {s["id"]: s["state"] for s in mgr.snapshot()}
            running_ids = {i for i in ids if snaps.get(i) == "running"}
            if len(running_ids) == 3:
                break
            time.sleep(0.02)
        assert len(running_ids) == 3
        assert sum(1 for i in ids if i not in running_ids) == 2

        for i in ids:
            snap = _poll(mgr, i, {"done", "error"}, timeout=15)
            assert snap["state"] == "done"
    finally:
        mgr.shutdown()


def test_download_completes_and_checksum_matches(tmp_path, make_server):
    blob = os.urandom(512 * 1024)
    server = make_server(blob)
    dl_dir = tmp_path / "dl"
    mgr = Manager(tmp_path / "queue.json", download_dir=dl_dir)
    id_ = mgr.add(server.url)
    mgr.start()
    try:
        snap = _poll(mgr, id_, {"done", "error"})
        assert snap["state"] == "done"
        out = dl_dir / snap["filename"]
        assert out.read_bytes() == blob
        assert not out.with_name(out.name + ".part").exists()
        assert not out.with_name(out.name + ".myidm.json").exists()
    finally:
        mgr.shutdown()


def test_hard_error_sets_error_state(tmp_path, make_server, monkeypatch):
    server = make_server(os.urandom(1024))
    mgr = Manager(tmp_path / "queue.json", download_dir=tmp_path / "dl")

    def boom(*a, **k):
        raise InsufficientSpace("no room")

    monkeypatch.setattr("myidm.engine.download", boom)
    id_ = mgr.add(server.url)
    mgr.start()
    try:
        snap = _poll(mgr, id_, {"error"})
        assert snap["state"] == "error"
        assert snap["error"]
    finally:
        mgr.shutdown()


def test_error_message_is_redacted(tmp_path, make_server, monkeypatch):
    server = make_server(os.urandom(1024))
    mgr = Manager(tmp_path / "queue.json", download_dir=tmp_path / "dl")

    def boom(*a, **k):
        raise RuntimeError("http://user:pw@h/x failed")

    monkeypatch.setattr("myidm.engine.download", boom)
    id_ = mgr.add(server.url)
    mgr.start()
    try:
        snap = _poll(mgr, id_, {"error"})
        assert "pw" not in snap["error"]
    finally:
        mgr.shutdown()


# --- pause, resume, cancel, host demotion (Milestone 2 Task 6) -------------


def _wrap_cb_with_trigger(monkeypatch, mgr, on_progress):
    """Patch Manager._cb so the returned progress callback also invokes
    `on_progress(mgr, dl.id)` once `done > 0` - synchronous, same thread as the
    engine's own cancel check, so there is no cross-thread timing race."""
    orig_cb = Manager._cb
    fired = threading.Event()

    def _cb(self, dl):
        inner = orig_cb(self, dl)

        def wrapped(done, total, bps):
            inner(done, total, bps)
            if done > 0 and not fired.is_set():
                fired.set()
                on_progress(mgr, dl.id)

        return wrapped

    monkeypatch.setattr(Manager, "_cb", _cb)


def test_pause_keeps_part_then_resume_completes(tmp_path, make_server, monkeypatch):
    blob = os.urandom(4 * 1024 * 1024)
    server = make_server(blob)
    dl_dir = tmp_path / "dl"
    mgr = Manager(tmp_path / "queue.json", download_dir=dl_dir)
    id_ = mgr.add(server.url)
    _wrap_cb_with_trigger(monkeypatch, mgr, lambda m, i: m.pause(i))

    mgr.start()
    try:
        _poll(mgr, id_, {"paused"})
        assert (dl_dir / "file.bin.part").exists()

        server.served_bytes = 0
        mgr.resume(id_)
        snap = _poll(mgr, id_, {"done", "error"}, timeout=15)
        assert snap["state"] == "done"
        out = dl_dir / snap["filename"]
        assert out.read_bytes() == blob
        assert server.served_bytes < len(blob)
    finally:
        mgr.shutdown()


def test_cancel_deletes_files(tmp_path, make_server, monkeypatch):
    blob = os.urandom(4 * 1024 * 1024)
    server = make_server(blob)
    dl_dir = tmp_path / "dl"
    mgr = Manager(tmp_path / "queue.json", download_dir=dl_dir)
    id_ = mgr.add(server.url)
    _wrap_cb_with_trigger(monkeypatch, mgr, lambda m, i: m.cancel(i))

    mgr.start()
    try:
        snap = _poll(mgr, id_, {"cancelled"})
        assert snap["state"] == "cancelled"
        assert not (dl_dir / "file.bin.part").exists()
        assert not (dl_dir / "file.bin.myidm.json").exists()
    finally:
        mgr.shutdown()


def test_pause_queued_never_starts(tmp_path, make_server):
    blob = os.urandom(4 * 1024 * 1024)
    server = make_server(blob)
    mgr = Manager(tmp_path / "queue.json", max_concurrent=3)
    ids = [mgr.add(server.url, dest_dir=str(tmp_path / f"dl{i}")) for i in range(4)]
    target = ids[-1]
    assert mgr.pause(target)
    assert mgr.snapshot_one(target)["state"] == "paused"

    mgr.start()
    try:
        for i in ids[:-1]:
            snap = _poll(mgr, i, {"done", "error"}, timeout=15)
            assert snap["state"] == "done"
        assert mgr.snapshot_one(target)["state"] == "paused"
    finally:
        mgr.shutdown()


def test_host_demotion_retries_with_two_segments(tmp_path, make_server):
    blob = os.urandom(512 * 1024)
    server = make_server(blob, segment_status=403)
    dl_dir = tmp_path / "dl"
    mgr = Manager(tmp_path / "queue.json", download_dir=dl_dir)
    id_ = mgr.add(server.url, segments=8)
    mgr.start()
    try:
        snap = _poll(mgr, id_, {"done", "error"}, timeout=15)
        assert snap["state"] == "done"
        out = dl_dir / snap["filename"]
        assert out.read_bytes() == blob
    finally:
        mgr.shutdown()


# --- restart recovery (Milestone 2 Task 7) ---------------------------------


def _write_queue(state_path, entries):
    state_path.write_text(json.dumps({"downloads": entries}))


def test_restart_converts_running_and_paused_to_queued(tmp_path):
    state_path = tmp_path / "queue.json"
    _write_queue(state_path, [
        {"id": "r1", "url": "http://h/f", "dest_dir": ".", "segments": 8,
         "state": "running", "filename": None, "error": None},
        {"id": "p1", "url": "http://h/f", "dest_dir": ".", "segments": 8,
         "state": "paused", "filename": None, "error": None},
    ])
    mgr = Manager(state_path)
    assert mgr.snapshot_one("r1")["state"] == "queued"
    assert mgr.snapshot_one("p1")["state"] == "queued"


def test_restart_keeps_done_entries(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("engine.download should not run for a done entry")

    monkeypatch.setattr("myidm.engine.download", boom)
    state_path = tmp_path / "queue.json"
    _write_queue(state_path, [
        {"id": "d1", "url": "http://h/f", "dest_dir": ".", "segments": 8,
         "state": "done", "filename": "f.bin", "error": None},
    ])
    mgr = Manager(state_path)
    mgr.start()
    try:
        time.sleep(0.2)  # give the scheduler a moment; it must not touch a done entry
        assert mgr.snapshot_one("d1")["state"] == "done"
    finally:
        mgr.shutdown()


def test_restart_on_corrupt_state(tmp_path):
    state_path = tmp_path / "queue.json"
    state_path.write_text("{ not json")
    mgr = Manager(state_path)  # must not raise
    assert mgr.snapshot() == []


def test_restart_resumes_running_download(tmp_path, make_server, monkeypatch):
    blob = os.urandom(4 * 1024 * 1024)
    server = make_server(blob)
    dl_dir = tmp_path / "dl"
    state_path = tmp_path / "queue.json"

    mgr_a = Manager(state_path, download_dir=dl_dir)
    id_ = mgr_a.add(server.url)
    _wrap_cb_with_trigger(monkeypatch, mgr_a, lambda m, i: m.pause(i))
    mgr_a.start()
    _poll(mgr_a, id_, {"paused"})
    assert (dl_dir / "file.bin.part").exists()
    mgr_a.shutdown()

    server.served_bytes = 0
    mgr_b = Manager(state_path, download_dir=dl_dir)
    assert mgr_b.snapshot_one(id_)["state"] == "queued"  # paused -> queued on load
    mgr_b.start()
    try:
        snap = _poll(mgr_b, id_, {"done", "error"}, timeout=15)
        assert snap["state"] == "done"
        out = dl_dir / snap["filename"]
        assert out.read_bytes() == blob
        assert server.served_bytes < len(blob)
    finally:
        mgr_b.shutdown()


# --- HTTP layer (Milestone 2 Task 8) ---------------------------------------

TOKEN = "test-token-" + "x" * 20


@pytest.fixture
def http(tmp_path):
    mgr = Manager(tmp_path / "queue.json", download_dir=tmp_path / "dl")
    server, port = serve_in_thread(mgr, TOKEN)
    mgr.start()
    base = f"http://127.0.0.1:{port}"
    try:
        yield mgr, base
    finally:
        mgr.shutdown()
        server.shutdown()
        server.server_close()


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


ALL_ROUTES = [
    ("GET", "/downloads"),
    ("GET", "/downloads/nope"),
    ("GET", "/health"),
    ("POST", "/downloads"),
    ("POST", "/downloads/nope/pause"),
    ("POST", "/downloads/nope/resume"),
    ("POST", "/downloads/nope/cancel"),
    ("POST", "/shutdown"),
]


@pytest.mark.parametrize("method,path", ALL_ROUTES)
def test_http_requires_auth(http, method, path):
    _mgr, base = http
    r = requests.request(method, base + path, timeout=5)
    assert r.status_code == 401
    r = requests.request(method, base + path, headers=_auth("wrong-token"), timeout=5)
    assert r.status_code == 401


def test_post_downloads_creates_and_lists(http, make_server):
    mgr, base = http
    server = make_server(os.urandom(1024))
    r = requests.post(base + "/downloads", json={"url": server.url}, headers=_auth(), timeout=5)
    assert r.status_code == 201
    id_ = r.json()["id"]

    r = requests.get(base + "/downloads", headers=_auth(), timeout=5)
    assert r.status_code == 200
    entries = r.json()
    assert any(e["id"] == id_ for e in entries)
    entry = next(e for e in entries if e["id"] == id_)
    assert set(entry) == {"id", "url", "filename", "state", "done", "total", "bps", "error"}

    r = requests.get(f"{base}/downloads/{id_}", headers=_auth(), timeout=5)
    assert r.status_code == 200
    assert r.json()["id"] == id_

    r = requests.get(f"{base}/downloads/nope", headers=_auth(), timeout=5)
    assert r.status_code == 404

    # let the background download finish before make_server tears down -
    # otherwise a still-running fetch hangs against a now-dead server.
    _poll(mgr, id_, {"done", "error"}, timeout=5)


def test_post_downloads_rejects_private_url(http, monkeypatch):
    monkeypatch.setattr(netcheck, "assert_allowed_url", _real_assert_allowed_url)
    _mgr, base = http
    r = requests.post(
        base + "/downloads", json={"url": "http://127.0.0.1:9/x"}, headers=_auth(), timeout=5
    )
    assert r.status_code == 400
    assert r.json()["error"] == "blocked"


def test_post_downloads_clamps_segments(http, make_server):
    mgr, base = http
    server = make_server(os.urandom(1024))
    r = requests.post(
        base + "/downloads",
        json={"url": server.url, "segments": 999},
        headers=_auth(),
        timeout=5,
    )
    assert r.status_code == 201
    _poll(mgr, r.json()["id"], {"done", "error"}, timeout=5)


def test_health_endpoint(http):
    _mgr, base = http
    r = requests.get(base + "/health", headers=_auth(), timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["downloads"] == 0
    assert requests.get(base + "/health", timeout=5).status_code == 401


def test_pause_resume_cancel_over_http(http, make_server):
    mgr, base = http
    blob = os.urandom(4 * 1024 * 1024)
    server = make_server(blob)
    r = requests.post(base + "/downloads", json={"url": server.url}, headers=_auth(), timeout=5)
    id_ = r.json()["id"]

    _poll(mgr, id_, {"running", "paused", "done", "error"}, timeout=5)
    r = requests.post(f"{base}/downloads/{id_}/pause", headers=_auth(), timeout=5)
    assert r.status_code == 202
    snap = _poll(mgr, id_, {"paused", "done"}, timeout=10)
    if snap["state"] == "paused":
        r = requests.post(f"{base}/downloads/{id_}/resume", headers=_auth(), timeout=5)
        assert r.status_code == 202
        snap = _poll(mgr, id_, {"done", "error"}, timeout=15)
        assert snap["state"] == "done"

    r = requests.post(base + "/downloads", json={"url": server.url}, headers=_auth(), timeout=5)
    id2 = r.json()["id"]
    r = requests.post(f"{base}/downloads/{id2}/cancel", headers=_auth(), timeout=5)
    assert r.status_code == 202
    snap = _poll(mgr, id2, {"cancelled", "done"}, timeout=15)

    r = requests.post(f"{base}/downloads/nope/pause", headers=_auth(), timeout=5)
    assert r.status_code == 404


def test_full_flow_add_poll_done_over_http(http, make_server):
    mgr, base = http
    blob = os.urandom(512 * 1024)
    server = make_server(blob)
    r = requests.post(base + "/downloads", json={"url": server.url}, headers=_auth(), timeout=5)
    id_ = r.json()["id"]

    deadline = time.monotonic() + 15
    snap = None
    while time.monotonic() < deadline:
        r = requests.get(f"{base}/downloads/{id_}", headers=_auth(), timeout=5)
        snap = r.json()
        if snap["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert snap["state"] == "done"
    out = mgr.download_dir / snap["filename"]
    assert out.read_bytes() == blob


def test_shutdown_stops_server(http):
    _mgr, base = http
    r = requests.post(base + "/shutdown", headers=_auth(), timeout=5)
    assert r.status_code == 202
    deadline = time.monotonic() + 2
    stopped = False
    while time.monotonic() < deadline:
        try:
            requests.get(base + "/health", headers=_auth(), timeout=0.5)
        except requests.exceptions.RequestException:
            stopped = True
            break
        time.sleep(0.05)
    assert stopped


# --- process management (Milestone 2 Task 9) --------------------------------


def test_run_service_writes_state_and_serves(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    t = threading.Thread(target=service.run_service, daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (tmp_path / "service.port").exists():
            time.sleep(0.02)
        assert (tmp_path / "service.port").exists()

        base_url, token = service.read_endpoint()
        r = requests.get(
            f"{base_url}/health", headers={"Authorization": f"Bearer {token}"}, timeout=5
        )
        assert r.status_code == 200

        r = requests.post(
            f"{base_url}/shutdown", headers={"Authorization": f"Bearer {token}"}, timeout=5
        )
        assert r.status_code == 202
    finally:
        t.join(timeout=5)

    for name in ("service.port", "service.token", "service.pid"):
        assert not (tmp_path / name).exists()


def test_spawn_detached_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    (tmp_path / "service.port").write_text("12345")
    (tmp_path / "service.token").write_text("tok")

    class _Resp:
        status_code = 200

    monkeypatch.setattr(service.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda *a, **k: pytest.fail("should not spawn")
    )
    assert "already running" in service.spawn_detached()


def test_stale_state_is_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    (tmp_path / "service.port").write_text("1")  # nothing listens on it

    def fake_get(*a, **k):
        raise requests.RequestException("connection refused")

    def fake_popen(*a, **k):
        paths.write_atomic(tmp_path / "service.port", "54321")
        paths.write_atomic(tmp_path / "service.token", "tok")

    monkeypatch.setattr(service.requests, "get", fake_get)
    monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
    assert service.spawn_detached() == "http://127.0.0.1:54321"
