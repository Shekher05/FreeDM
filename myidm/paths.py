"""State-directory resolution and small atomic-file helpers shared by the
service and client."""

import os
import sys
from pathlib import Path


def state_dir() -> Path:
    """Where the service keeps its queue, port, token, and pid files."""
    if sys.platform.startswith("win"):
        local = os.environ.get("LOCALAPPDATA")
        d = Path(local) / "myidm" if local else Path.home() / ".myidm"
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        d = Path(base) / "myidm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_atomic(path: Path, text: str) -> None:
    """Temp file + ``os.replace``, then best-effort ``chmod 0o600``."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_or_none(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def default_download_dir() -> Path:
    """Not created here - the caller (the download itself) creates it on use."""
    return Path.home() / "Downloads" / "myidm"
