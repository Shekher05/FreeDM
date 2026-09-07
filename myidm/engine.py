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
