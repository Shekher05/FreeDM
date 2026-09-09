from itertools import pairwise

import pytest

from myidm.engine import derive_filename, split_ranges


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


def test_derive_filename_strips_ads_colon():
    assert derive_filename("http://h/report.pdf:evil", _FakeResp({})) == "report.pdf"
    resp = _FakeResp({"Content-Disposition": 'filename="data.zip:$DATA"'})
    assert derive_filename("http://h/", resp) == "data.zip"


def test_derive_filename_strips_control_chars():
    resp = _FakeResp({"Content-Disposition": 'filename="a\x00b\x1f.zip"'})
    assert derive_filename("http://h/", resp) == "ab.zip"


def test_derive_filename_strips_trailing_dot_and_space():
    resp = _FakeResp({"Content-Disposition": 'filename="evil.  "'})
    assert derive_filename("http://h/", resp) == "evil"


def test_derive_filename_rejects_dotdot():
    assert derive_filename("http://h/%2e%2e", _FakeResp({})) == "download.bin"


def test_derive_filename_rejects_windows_reserved():
    assert derive_filename("http://h/CON", _FakeResp({})) == "download.bin"
    assert derive_filename("http://h/nul.txt", _FakeResp({})) == "download.bin"
    resp = _FakeResp({"Content-Disposition": 'filename="COM1"'})
    assert derive_filename("http://h/", resp) == "download.bin"


def test_split_ranges_even():
    assert split_ranges(100, 4) == [(0, 24), (25, 49), (50, 74), (75, 99)]


def test_split_ranges_remainder_on_last():
    r = split_ranges(103, 4)
    assert r[0] == (0, 24)
    assert r[-1][1] == 102
    for a, b in pairwise(r):
        assert b[0] == a[1] + 1


def test_split_ranges_more_segments_than_bytes():
    assert split_ranges(3, 8) == [(0, 0), (1, 1), (2, 2)]


def test_split_ranges_rejects_zero():
    with pytest.raises(ValueError):
        split_ranges(0, 4)
