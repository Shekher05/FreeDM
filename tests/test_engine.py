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
