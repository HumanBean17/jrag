from __future__ import annotations

import tomllib
from pathlib import Path


def test_published_package_installs_cocoindex_for_lifecycle_commands() -> None:
    data = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    deps = data["project"]["dependencies"]

    cocoindex_deps = [dep for dep in deps if dep.startswith("cocoindex")]

    assert cocoindex_deps
    assert any("[lancedb]" in dep for dep in cocoindex_deps)
