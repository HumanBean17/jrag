from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement

# Intel Mac is the one platform the vector stack cannot install on: torch >=2.3 and
# lancedb >=0.26 dropped macOS x86_64 wheels. The vector trio is gated off there so the
# package installs graph-only; every other platform is unchanged.
_INTEL_MAC = "darwin", "x86_64"
_VECTOR_TRIO = {"cocoindex", "lancedb", "sentence-transformers"}


def _deps() -> list[str]:
    data = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    return data["project"]["dependencies"]


def test_published_package_installs_cocoindex_for_lifecycle_commands() -> None:
    cocoindex_deps = [dep for dep in _deps() if dep.startswith("cocoindex")]

    assert cocoindex_deps
    assert any("[lancedb]" in dep for dep in cocoindex_deps)


def test_vector_trio_is_gated_off_on_intel_mac_only() -> None:
    intel_env = {**default_environment(), "sys_platform": "darwin", "platform_machine": "x86_64"}

    for raw in _deps():
        req = Requirement(raw)
        if req.name.lower() not in _VECTOR_TRIO:
            continue
        assert req.marker is not None, f"{req.name}: expected the Intel-Mac marker"
        assert not req.marker.evaluate(environment=intel_env), (
            f"{req.name}: must be EXCLUDED on Intel Mac (darwin/x86_64)"
        )


def test_graph_deps_install_everywhere_including_intel_mac() -> None:
    intel_env = {**default_environment(), "sys_platform": "darwin", "platform_machine": "x86_64"}

    for raw in _deps():
        req = Requirement(raw)
        if req.name.lower() in _VECTOR_TRIO:
            continue
        # Graph deps (ladybug, pyarrow, numpy, tree-sitter, ...) carry no marker and must
        # install on Intel Mac so the graph layer works.
        assert req.marker is None, f"{req.name}: graph dep must not be platform-gated"
        assert req.marker is None or req.marker.evaluate(environment=intel_env)

