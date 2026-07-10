"""Runtime socket/pid/state path derivation for the jrag watch daemon.

Pure-path logic only — no sockets, no I/O except mkdir for the runtime dir.
No dependencies on other watch modules.
"""

import hashlib
import sys
from pathlib import Path

import getpass
import tempfile


def runtime_dir() -> Path:
    """Return the per-user runtime directory, created if missing.

    Resolution order:
    1. env XDG_RUNTIME_DIR
    2. env TMPDIR
    3. ~/Library/Caches/JragWatch on macOS (sys.platform == "darwin")
    4. tempfile.gettempdir() / f"jrag-watch-{getpass.getuser()}"

    The directory is created with parents=True, exist_ok=True before returning.
    """
    # 1. XDG_RUNTIME_DIR
    if "XDG_RUNTIME_DIR" in __import__("os").environ:
        rt_dir = Path(__import__("os").environ["XDG_RUNTIME_DIR"])
    # 2. TMPDIR
    elif "TMPDIR" in __import__("os").environ:
        rt_dir = Path(__import__("os").environ["TMPDIR"])
    # 3. macOS-specific path
    elif sys.platform == "darwin":
        home = Path(__import__("os").environ.get("HOME", Path.home()))
        rt_dir = home / "Library" / "Caches" / "JragWatch"
    # 4. Fallback
    else:
        rt_dir = Path(tempfile.gettempdir()) / f"jrag-watch-{getpass.getuser()}"

    # Create if missing
    rt_dir.mkdir(parents=True, exist_ok=True)
    return rt_dir


def project_key(index_dir: Path) -> str:
    """Return the first 12 hex characters of SHA256 of the resolved index_dir path.

    The path is resolved before hashing so symlinks/relative paths are stable.
    """
    resolved = str(index_dir.resolve())
    hash_hex = hashlib.sha256(resolved.encode()).hexdigest()
    return hash_hex[:12]


def socket_path(index_dir: Path) -> Path:
    """Return the Unix socket path for a given index_dir.

    Format: runtime_dir() / "jrag-watch-{project_key(index_dir)}.sock"
    """
    return runtime_dir() / f"jrag-watch-{project_key(index_dir)}.sock"


def pid_path(index_dir: Path) -> Path:
    """Return the pidfile path for a given index_dir.

    Format: runtime_dir() / "jrag-watch-{project_key(index_dir)}.pid"
    """
    return runtime_dir() / f"jrag-watch-{project_key(index_dir)}.pid"


def state_path(index_dir: Path) -> Path:
    """Return the state file path for a given index_dir.

    Format: runtime_dir() / "jrag-watch-{project_key(index_dir)}.state"
    """
    return runtime_dir() / f"jrag-watch-{project_key(index_dir)}.state"
