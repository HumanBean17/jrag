"""Skip the whole ``tests/watch`` tree on non-Unix platforms.

``jrag watch`` is Unix-only by design — it serves over ``AF_UNIX`` sockets and
takes its project lock via ``fcntl.flock`` (see ``watch/lock.py``, which raises
``WatchUnsupportedPlatform`` when ``fcntl`` is unavailable). The product fails
cleanly on Windows; these tests exercise the Unix primitives directly (they
``socket.AF_UNIX`` and rely on ``fcntl`` semantics), so skip the entire tree on
platforms without ``fcntl`` instead of erroring on import / attribute access.
"""
import pytest


def _has_fcntl() -> bool:
    try:
        import fcntl  # noqa: F401
        return True
    except ImportError:
        return False


_UNIX_ONLY = pytest.mark.skip(
    reason="jrag watch is Unix-only (AF_UNIX sockets + fcntl flock); see watch/lock.py",
)


def pytest_collection_modifyitems(config, items):  # noqa: D401
    if _has_fcntl():
        return
    for item in items:
        item.add_marker(_UNIX_ONLY)
