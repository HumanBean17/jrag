"""Helpers that bridge `ast_java` output with chunk- and graph-level metadata.

Used both by the CocoIndex indexer (for per-chunk enrichment) and by
`build_ast_graph.py` (for module / microservice inference and deterministic
node ids).

Two location concepts are tracked per file:

- **module** — the *innermost* build-marker ancestor (Maven / Gradle /
  SBT). Same as the legacy `service` field. Useful for module-scoped
  search inside a microservice.
- **microservice** — the *outermost* build-marker ancestor under
  `project_root`. Represents one deployable / repo. Resolution order:
    1. explicit override list (env var or config file at project root);
    2. outermost build marker between `project_root` and the file;
    3. first path segment under `project_root`;
    4. empty.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ast_java import JavaFileAst, TypeDecl, infer_role_for_type

__all__ = [
    "ChunkEnrichment",
    "enrich_chunk",
    "module_for_path",
    "microservice_for_path",
    "load_microservice_overrides",
    "symbol_id",
    "phantom_id",
    "BUILD_MARKERS",
    "MICROSERVICE_ROOTS_ENV",
    "CONFIG_FILENAMES",
]

BUILD_MARKERS = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
)

MICROSERVICE_ROOTS_ENV = "LANCEDB_MCP_MICROSERVICE_ROOTS"

# Recognised config filenames at `project_root` (first match wins).
CONFIG_FILENAMES = (".lancedb-mcp.yml", ".lancedb-mcp.yaml")


@dataclass
class ChunkEnrichment:
    package: str
    module: str
    microservice: str
    primary_type_fqn: str
    primary_type_kind: str
    role: str
    annotations_on_type: list[str]
    symbols: list[str]
    capabilities: list[str] = field(default_factory=list)


# ---------- microservice override loading ----------


def _parse_csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


@lru_cache(maxsize=64)
def _load_config_microservice_roots(project_root_str: str) -> tuple[str, ...]:
    """Read `microservice_roots` from `.lancedb-mcp.yml` at project_root.

    Cached per project_root to avoid re-reading on every chunk. Failures
    (file missing, malformed YAML, missing key) silently return an empty
    tuple — config is strictly opt-in.
    """
    root = Path(project_root_str)
    for name in CONFIG_FILENAMES:
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            import yaml  # PyYAML; already a transitive dep of cocoindex
        except ImportError:
            return ()
        try:
            data = yaml.safe_load(candidate.read_text(encoding="utf-8"))
        except Exception:
            return ()
        if not isinstance(data, dict):
            return ()
        raw = data.get("microservice_roots")
        if isinstance(raw, str):
            return tuple(_parse_csv(raw))
        if isinstance(raw, list):
            return tuple(str(x).strip() for x in raw if str(x).strip())
        return ()
    return ()


def load_microservice_overrides(project_root: str | Path | None) -> tuple[str, ...]:
    """Combined override list (env var ++ config file).

    Env var `LANCEDB_MCP_MICROSERVICE_ROOTS` takes precedence; both
    sources are merged in declaration order, deduplicated.
    """
    out: list[str] = []
    seen: set[str] = set()

    env_raw = os.environ.get(MICROSERVICE_ROOTS_ENV, "").strip()
    for name in _parse_csv(env_raw):
        if name not in seen:
            seen.add(name)
            out.append(name)

    if project_root is not None:
        try:
            root_str = str(Path(project_root).resolve())
        except OSError:
            root_str = str(project_root)
        for name in _load_config_microservice_roots(root_str):
            if name not in seen:
                seen.add(name)
                out.append(name)

    return tuple(out)


# ---------- path -> module / microservice ----------


def _resolve_with_root(
    file_path: str, project_root: str | Path | None,
) -> tuple[Path, Path | None]:
    p = Path(file_path)
    if project_root is None:
        try:
            return p.resolve(), None
        except OSError:
            return p, None
    root = Path(project_root).resolve()
    try:
        p_abs = (root / p).resolve() if not p.is_absolute() else p.resolve()
    except OSError:
        p_abs = p
    return p_abs, root


def _bounded_parents(p: Path, root: Path | None) -> list[Path]:
    """Parents of `p`, stopping at (and not crossing above) `root`."""
    try:
        parents = list(p.parents)
    except OSError:
        return []
    if root is None:
        return parents
    bounded: list[Path] = []
    for parent in parents:
        bounded.append(parent)
        if parent == root:
            break
    return bounded


def _has_build_marker(directory: Path) -> bool:
    for marker in BUILD_MARKERS:
        if (directory / marker).is_file():
            return True
    return False


def module_for_path(file_path: str, project_root: str | Path | None = None) -> str:
    """Innermost build-marker ancestor's directory name.

    Returns "" when no build marker is found between the file and
    `project_root` (inclusive).
    """
    p, root = _resolve_with_root(file_path, project_root)
    for parent in _bounded_parents(p, root):
        if _has_build_marker(parent):
            return parent.name
    return ""


def microservice_for_path(
    file_path: str, project_root: str | Path | None = None,
) -> str:
    """Outermost build-marker ancestor under `project_root`.

    Resolution order, first hit wins:

    1. Explicit override (env var + config file). The override is a list
       of directory names; the first one that appears in the file's
       ancestry (under `project_root`) wins.
    2. Outermost build-marker ancestor between `project_root` and `file`
       (i.e. the build marker closest to `project_root`).
    3. First path segment under `project_root`.
    4. "" — when none of the above apply (typically: file *is*
       `project_root`, or `project_root` is None and the file path
       has no parents).
    """
    p, root = _resolve_with_root(file_path, project_root)
    parents = _bounded_parents(p, root)

    overrides = load_microservice_overrides(project_root)
    if overrides:
        # Walk from outermost to innermost so a nested override (rare)
        # still works when the user lists a deeper directory.
        override_set = set(overrides)
        for parent in reversed(parents):
            if parent.name in override_set:
                return parent.name
        # Fall through to structural inference if no override matched.

    outermost_marker: Path | None = None
    for parent in parents:
        if _has_build_marker(parent):
            outermost_marker = parent
    if outermost_marker is not None and (root is None or outermost_marker != root):
        return outermost_marker.name

    if root is not None:
        # First path segment under `root`. parents are ordered
        # innermost-first; the candidate is the parent immediately
        # below `root`.
        for parent in parents:
            try:
                rel = parent.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) == 1:
                return parts[0]

    return ""


# ---------- chunk enrichment ----------


def _flatten_types(ast: JavaFileAst) -> list[TypeDecl]:
    return list(ast.all_types)


def _enclosing_type(ast: JavaFileAst, start: int, end: int) -> TypeDecl | None:
    """Smallest TypeDecl whose [start_byte, end_byte] contains chunk range.

    Falls back to largest overlap if nothing fully encloses.
    """
    best: TypeDecl | None = None
    best_span = -1
    for t in _flatten_types(ast):
        if t.start_byte <= start and end <= t.end_byte:
            span = t.end_byte - t.start_byte
            if best is None or span < best_span or best_span < 0:
                best = t
                best_span = span
    if best is not None:
        return best

    overlap_best: TypeDecl | None = None
    overlap_size = 0
    for t in _flatten_types(ast):
        o = max(0, min(end, t.end_byte) - max(start, t.start_byte))
        if o > overlap_size:
            overlap_size = o
            overlap_best = t
    return overlap_best


def _symbols_in_range(ast: JavaFileAst, start: int, end: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in _flatten_types(ast):
        if t.end_byte < start or t.start_byte > end:
            continue
        if start <= t.start_byte <= end and t.name not in seen:
            out.append(t.name)
            seen.add(t.name)
        for f in t.fields:
            if start <= f.start_byte <= end and f.name not in seen:
                out.append(f.name)
                seen.add(f.name)
        for m in t.methods:
            if start <= m.start_byte <= end and m.name not in seen:
                out.append(m.name)
                seen.add(m.name)
    return out


def enrich_chunk(
    ast: JavaFileAst,
    *,
    chunk_start_byte: int,
    chunk_end_byte: int,
    file_path: str,
    project_root: str | Path | None = None,
) -> ChunkEnrichment:
    """Compute enrichment metadata for a single chunk of a parsed Java file."""
    module = module_for_path(file_path, project_root)
    microservice = microservice_for_path(file_path, project_root)
    encl = _enclosing_type(ast, chunk_start_byte, chunk_end_byte)
    if encl is not None:
        ann_names = [a.name for a in encl.annotations]
        return ChunkEnrichment(
            package=ast.package,
            module=module,
            microservice=microservice,
            primary_type_fqn=encl.fqn,
            primary_type_kind=encl.kind,
            role=infer_role_for_type(encl),
            annotations_on_type=ann_names,
            symbols=_symbols_in_range(ast, chunk_start_byte, chunk_end_byte),
            capabilities=list(encl.capabilities),
        )
    return ChunkEnrichment(
        package=ast.package,
        module=module,
        microservice=microservice,
        primary_type_fqn="",
        primary_type_kind="",
        role="OTHER",
        annotations_on_type=[],
        symbols=_symbols_in_range(ast, chunk_start_byte, chunk_end_byte),
    )


def symbol_id(kind: str, fqn: str, file_path: str = "", start_byte: int = 0) -> str:
    """Deterministic SHA1-based id for Kuzu Symbol nodes."""
    key = f"{kind}|{fqn}|{file_path}|{start_byte}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()


def phantom_id(simple_or_fqn: str) -> str:
    """Id for unresolved/external type targets (phantom Symbol rows)."""
    key = f"class|__phantom.{simple_or_fqn}|".encode("utf-8")
    return hashlib.sha1(key).hexdigest()
