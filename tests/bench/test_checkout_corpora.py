"""Tests for ``bench.checkout_corpora`` — clone/pin (git) + copy (local)."""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from bench.checkout_corpora import CheckoutError, checkout_all


def _write_corpora(tmp_path: Path, body: str) -> tuple[str, Path]:
    yml = tmp_path / "corpora.yml"
    yml.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    ck = tmp_path / "checkouts"
    return str(yml), ck


def test_local_copy_idempotent(tmp_path):
    src = tmp_path / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "Foo.java").write_text("package pkg; class Foo {}", encoding="utf-8")
    (src / "target").mkdir()
    (src / "target" / "Foo.class").write_text("fake bytecode", encoding="utf-8")

    yml, ck = _write_corpora(
        tmp_path,
        f"""
        corpora:
          - name: demo
            source_kind: local
            local_path: {src}
            pinned_repo_sha: abcdef
            index:
              ontology_version: 19
        """,
    )

    result = checkout_all(yml, checkouts_root=str(ck))
    target = Path(result["demo"])
    assert target.is_dir()
    assert (target / "pkg" / "Foo.java").read_text() == "package pkg; class Foo {}"
    # target/ build dirs are excluded from the checkout
    assert not (target / "target").exists()

    copied_mtime = (target / "pkg" / "Foo.java").stat().st_mtime_ns

    # Second call without force is a no-op.
    checkout_all(yml, checkouts_root=str(ck))
    assert (target / "pkg" / "Foo.java").stat().st_mtime_ns == copied_mtime

    # force=True re-copies.
    checkout_all(yml, force=True, checkouts_root=str(ck))
    assert (target / "pkg" / "Foo.java").stat().st_mtime_ns >= copied_mtime


def test_git_pin(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
    (origin / "Hello.java").write_text("class Hello {}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=origin, check=True)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00"}
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=origin, check=True, env=env)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True, text=True, check=True
    ).stdout.strip()

    yml, ck = _write_corpora(
        tmp_path,
        f"""
        corpora:
          - name: demo
            source_kind: git
            git_url: file://{origin}
            commit_sha: {sha}
            index:
              ontology_version: 19
        """,
    )

    result = checkout_all(yml, checkouts_root=str(ck))
    target = Path(result["demo"])
    checked = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=target, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert checked == sha
    assert (target / "Hello.java").exists()


def test_git_bad_sha_raises(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=origin, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=origin, check=True)
    (origin / "A.java").write_text("class A {}", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=origin, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=origin, check=True)

    yml, ck = _write_corpora(
        tmp_path,
        f"""
        corpora:
          - name: demo
            source_kind: git
            git_url: file://{origin}
            commit_sha: deadbeefdeadbeefdeadbeefdeadbeefdeadbeef
            index:
              ontology_version: 19
        """,
    )
    with pytest.raises(CheckoutError) as exc:
        checkout_all(yml, checkouts_root=str(ck))
    assert "demo" in str(exc.value)
