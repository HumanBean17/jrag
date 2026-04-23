"""Use sqlean as ``sqlite3`` when the stdlib build lacks extension loading (common on macOS).

sqlite-vec requires ``enable_load_extension`` / ``load_extension``. Import this module
before ``import sqlite3`` (or before cocoindex) so ``sys.modules['sqlite3']`` is patched.
Requires ``sqlean.py`` from requirements.txt.
"""
from __future__ import annotations

import sqlite3 as _stdlib_sqlite3
import sys

try:
    import sqlean as _sqlean  # type: ignore[import-untyped]
except ImportError:
    _sqlean = None
if _sqlean is not None and not hasattr(_stdlib_sqlite3.Connection, "enable_load_extension"):
    sys.modules["sqlite3"] = _sqlean
