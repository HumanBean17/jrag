"""Make the standalone ``bench`` package importable under pytest (src-layout).

``bench/`` lives at the repo root as a sibling of ``src/`` and is deliberately
NOT part of the ``java_codebase_rag`` distribution, so it is not on any
installed path. Insert the repo root so ``import bench.<module>`` resolves.

NOTE: ``tests/bench/`` deliberately has NO ``__init__.py``. ``pytest.ini`` sets
``pythonpath = src tests``; if ``tests/bench/__init__.py`` existed, the regular
package ``bench`` would resolve to the *test* dir and shadow the source package.
Without it, ``tests/bench`` is only a namespace candidate and the regular
``bench/`` package at the repo root wins. Do not re-add ``__init__.py`` here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
