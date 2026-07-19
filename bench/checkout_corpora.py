"""Corpus checkout/pin (Plan 1, Task 3).

Materializes each corpus into ``bench/checkouts/<name>/`` at a pinned revision:
git corpora are cloned and checked out to their commit SHA (detached HEAD);
local corpora are copied from the fixture tree (excluding build artifacts).
Idempotent unless ``force``. All subprocess failures are wrapped in
``CheckoutError`` naming the corpus.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bench.load_corpora import CHECKOUTS_ROOT, INDEXES_ROOT, CorpusRecord, load_corpora

# Build artifacts / VCS dirs never belong in a frozen source checkout.
_LOCAL_IGNORE = shutil.ignore_patterns("target", "build", "out", "node_modules", ".git", "*.class")


class CheckoutError(RuntimeError):
    """Raised when a corpus cannot be checked out at its pinned revision."""


def _run_git(args: list[str], *, cwd: str | Path | None = None) -> str:
    res = subprocess.run(
        ["git", *args], cwd=(str(cwd) if cwd is not None else None),
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise CheckoutError(f"git {' '.join(args)} failed (rc={res.returncode}): {res.stderr.strip()}")
    return res.stdout.strip()


def _checkout_git(record: CorpusRecord, force: bool) -> str:
    target = Path(record.checkout_path)
    sha = record.commit_sha
    assert sha is not None  # validate() guarantees this for git corpora

    if target.exists() and any(target.iterdir()):
        current = ""
        if (target / ".git").exists():
            try:
                current = _run_git(["rev-parse", "HEAD"], cwd=target)
            except CheckoutError:
                current = ""
        if current == sha and not force:
            return str(target.resolve())
        # Wrong SHA or force: re-clone clean.
        shutil.rmtree(target)

    target.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["clone", "--quiet", record.git_url, str(target)])
    _run_git(["-c", "advice.detachedHead=false", "checkout", "--quiet", sha], cwd=target)
    return str(target.resolve())


def _checkout_local(record: CorpusRecord, force: bool) -> str:
    target = Path(record.checkout_path)
    src = Path(record.local_path)
    assert src is not None  # validate() guarantees this for local corpora
    if not src.is_dir():
        raise CheckoutError(f"corpus {record.name!r}: local_path {src} does not exist")

    if target.exists() and any(target.iterdir()):
        if not force:
            return str(target.resolve())
        shutil.rmtree(target)

    shutil.copytree(src, target, ignore=_LOCAL_IGNORE)
    return str(target.resolve())


def checkout_all(
    corpora_path: str = "bench/corpora.yml",
    force: bool = False,
    *,
    checkouts_root: str = CHECKOUTS_ROOT,
    indexes_root: str = INDEXES_ROOT,
) -> dict[str, str]:
    """Check out every corpus at its pinned revision -> ``{name: abs checkout_path}``."""
    records = load_corpora(
        corpora_path, checkouts_root=checkouts_root, indexes_root=indexes_root
    )
    result: dict[str, str] = {}
    for record in records:
        try:
            if record.source_kind == "git":
                result[record.name] = _checkout_git(record, force)
            elif record.source_kind == "local":
                result[record.name] = _checkout_local(record, force)
            else:  # pragma: no cover - validate() rejects this upstream
                raise CheckoutError(f"unknown source_kind {record.source_kind!r}")
        except Exception as exc:  # wrap any failure with the corpus name
            raise CheckoutError(f"corpus {record.name!r}: {exc}") from exc
    return result


if __name__ == "__main__":  # pragma: no cover
    import sys

    args = sys.argv[1:]
    force = "--force" in args
    out = checkout_all(force=force)
    for name, path in out.items():
        print(f"{name}\t{path}")
