"""Corpus registry loader/validator (Plan 1, Task 2).

Reads ``bench/corpora.yml`` into typed records and enforces the registry's
methodological invariants: pinned source of truth per corpus (git SHA or local
fixture pinned to a repo SHA), checkouts/indexes isolated under ``bench/``, and
a positive ontology version so C4/C5 reproducibility is structurally captured.

Pure validation — no I/O beyond reading the YAML file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

_NAME_RE = re.compile(r"^[a-z0-9-]+$")
CHECKOUTS_ROOT = "bench/checkouts"
INDEXES_ROOT = "bench/indexes"
# Default ontology version when a corpus entry omits the ``index`` block.
# The real value is filled in Task 4 once an index is built and its meta read.
DEFAULT_ONTOLOGY_VERSION = 1


class ConfigError(ValueError):
    """Raised when ``corpora.yml`` violates a registry invariant."""


@dataclass(frozen=True)
class IndexManifest:
    """Per-corpus index metadata. C5 (build-cost) + reproducibility fields."""

    index_dir: str
    ontology_version: int
    build_id: str | None = None
    build_time_s: float | None = None
    on_disk_bytes: int | None = None


@dataclass(frozen=True)
class CorpusRecord:
    """One corpus entry. Exactly one source channel (git XOR local) is set."""

    name: str
    source_kind: str  # "git" | "local"
    git_url: str | None
    commit_sha: str | None
    local_path: str | None
    pinned_repo_sha: str | None
    checkout_path: str
    index: IndexManifest


def validate(record: CorpusRecord) -> None:
    """Raise ``ConfigError`` with a precise message on any invariant violation."""
    if not _NAME_RE.match(record.name):
        raise ConfigError(
            f"corpus name {record.name!r} must match ^[a-z0-9-]+$ (lowercase, digits, hyphens)"
        )

    if record.source_kind == "git":
        if not record.git_url or not record.commit_sha:
            raise ConfigError(
                f"corpus {record.name!r}: source_kind 'git' requires both git_url and commit_sha"
            )
        if record.local_path is not None or record.pinned_repo_sha is not None:
            raise ConfigError(
                f"corpus {record.name!r}: source_kind 'git' must not set local_path/pinned_repo_sha"
            )
    elif record.source_kind == "local":
        if not record.local_path or not record.pinned_repo_sha:
            raise ConfigError(
                f"corpus {record.name!r}: source_kind 'local' requires both "
                "local_path and pinned_repo_sha"
            )
        if record.git_url is not None or record.commit_sha is not None:
            raise ConfigError(
                f"corpus {record.name!r}: source_kind 'local' must not set git_url/commit_sha"
            )
    else:
        raise ConfigError(
            f"corpus {record.name!r}: source_kind {record.source_kind!r} must be 'git' or 'local'"
        )

    if not record.checkout_path.startswith(CHECKOUTS_ROOT + "/"):
        raise ConfigError(
            f"corpus {record.name!r}: checkout_path {record.checkout_path!r} must be under "
            f"{CHECKOUTS_ROOT}/"
        )
    if not record.index.index_dir.startswith(INDEXES_ROOT + "/"):
        raise ConfigError(
            f"corpus {record.name!r}: index.index_dir {record.index.index_dir!r} must be under "
            f"{INDEXES_ROOT}/"
        )
    if record.index.ontology_version < 1:
        raise ConfigError(
            f"corpus {record.name!r}: index.ontology_version must be a positive int "
            f"(got {record.index.ontology_version})"
        )


def _record_from_entry(entry: dict) -> CorpusRecord:
    name = str(entry.get("name", "")).strip()
    if not name:
        raise ConfigError(f"corpus entry missing 'name': {entry!r}")
    source_kind = str(entry.get("source_kind", "")).strip()

    index_block = entry.get("index") or {}
    ontology_version = int(index_block.get("ontology_version", DEFAULT_ONTOLOGY_VERSION))
    index_dir = str(
        index_block.get("index_dir") or f"{INDEXES_ROOT}/{name}"
    )
    index = IndexManifest(
        index_dir=index_dir,
        ontology_version=ontology_version,
        build_id=index_block.get("build_id"),
        build_time_s=index_block.get("build_time_s"),
        on_disk_bytes=index_block.get("on_disk_bytes"),
    )

    checkout_path = str(entry.get("checkout_path") or f"{CHECKOUTS_ROOT}/{name}")

    return CorpusRecord(
        name=name,
        source_kind=source_kind,
        git_url=entry.get("git_url"),
        commit_sha=entry.get("commit_sha"),
        local_path=entry.get("local_path"),
        pinned_repo_sha=entry.get("pinned_repo_sha"),
        checkout_path=checkout_path,
        index=index,
    )


def load_corpora(path: str = "bench/corpora.yml") -> list[CorpusRecord]:
    """Read ``corpora.yml`` -> validated ``CorpusRecord`` list (unique names)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("corpora"), list):
        raise ConfigError(
            f"{path}: expected top-level mapping with a 'corpora:' list"
        )
    entries = raw["corpora"]
    if not entries:
        raise ConfigError(f"{path}: 'corpora:' list is empty")

    records: list[CorpusRecord] = []
    seen: set[str] = set()
    for entry in entries:
        rec = _record_from_entry(entry)
        validate(rec)
        if rec.name in seen:
            raise ConfigError(f"duplicate corpus name {rec.name!r} in {path}")
        seen.add(rec.name)
        records.append(rec)
    return records
