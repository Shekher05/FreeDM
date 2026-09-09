"""Segmented HTTP download engine with resume support."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

# Windows reserved device names: reserved regardless of extension, case-insensitive.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


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


def derive_filename(url: str, resp) -> str:
    """Pick an output filename from Content-Disposition, then the URL, then a default.

    The result is a bare basename safe to join onto the output directory on the
    primary (Windows) platform: directory separators and NTFS alternate-data-stream
    colons are dropped, control characters and trailing dots/spaces stripped, and
    anything that resolves to empty, ``.``/``..``, or a reserved device name
    (``CON``, ``NUL``, ``COM1`` ...) falls back to ``download.bin``.

    RFC 5987 ``filename*=`` is intentionally not handled - out of scope here.
    """
    cd = resp.headers.get("Content-Disposition", "")
    name = ""
    if "filename=" in cd:
        name = cd.split("filename=", 1)[1].split(";")[0].strip().strip('"')
    if not name:
        name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
    # Keep only the basename: no separators, no ADS colon suffix.
    name = name.replace("\\", "/").rsplit("/", 1)[-1].split(":", 1)[0]
    name = _CONTROL_CHARS.sub("", name).rstrip(". ")
    if not name or name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        return "download.bin"
    return name
