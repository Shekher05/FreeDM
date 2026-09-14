import sys

from tdm.paths import default_download_dir, read_or_none, state_dir, write_atomic


def test_state_dir_uses_localappdata_on_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    d = state_dir()
    assert d == tmp_path / "tdm"
    assert d.is_dir()


def test_state_dir_uses_xdg_state_home_elsewhere(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    d = state_dir()
    assert d == tmp_path / "tdm"
    assert d.is_dir()


def test_write_atomic_round_trips_and_leaves_no_tmp(tmp_path):
    p = tmp_path / "file.txt"
    write_atomic(p, "hello")
    assert read_or_none(p) == "hello"
    assert not p.with_name(p.name + ".tmp").exists()
    write_atomic(p, "world")  # overwrite
    assert read_or_none(p) == "world"


def test_read_or_none_missing_file(tmp_path):
    assert read_or_none(tmp_path / "nope.txt") is None


def test_default_download_dir_not_created_on_import(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    d = default_download_dir()
    assert d == tmp_path / "Downloads" / "tdm"
    assert not d.exists()
