"""Helpers that bridge `ast_java` output with chunk- and graph-level metadata.

Used both by the CocoIndex indexer (for per-chunk enrichment) and by
`build_ast_graph.py` (for service inference + deterministic node ids).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ast_java import JavaFileAst, TypeDecl, infer_role_for_type

__all__ = [
    "ChunkEnrichment",
    "enrich_chunk",
    "service_for_path",
    "symbol_id",
    "phantom_id",
    "SERVICE_MARKERS",
]

SERVICE_MARKERS = (
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "build.sbt",
)


@dataclass
class ChunkEnrichment:
    package: str
    service: str
    primary_type_fqn: str
    primary_type_kind: str
    role: str
    annotations_on_type: list[str]
    symbols: list[str]


def service_for_path(file_path: str, project_root: str | Path | None = None) -> str:
    """Infer the microservice name for a file.

    Rules, first match wins:
      1. Walk parent directories; the nearest one that contains a build marker
         (`pom.xml`, `build.gradle*`, `build.sbt`) wins — its directory name is
         the service.
      2. If no build marker is found, look for a path segment `services/<name>`
         and return `<name>`.
      3. Otherwise return "".

    `project_root` bounds the walk so we don't look above the indexed root.
    """
    p = Path(file_path)
    if project_root is not None:
        root = Path(project_root).resolve()
        try:
            p_abs = (root / p).resolve() if not p.is_absolute() else p.resolve()
            p = p_abs
        except OSError:
            pass
    else:
        root = None

    try:
        parents = list(p.resolve().parents)
    except OSError:
        parents = list(p.parents)

    for parent in parents:
        if root is not None:
            try:
                parent.relative_to(root)
            except ValueError:
                break
        for marker in SERVICE_MARKERS:
            if (parent / marker).is_file():
                return parent.name

    parts = p.parts
    for i, seg in enumerate(parts[:-1]):
        if seg == "services" and i + 1 < len(parts) - 1:
            return parts[i + 1]
    return ""


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
    encl = _enclosing_type(ast, chunk_start_byte, chunk_end_byte)
    if encl is not None:
        ann_names = [a.name for a in encl.annotations]
        return ChunkEnrichment(
            package=ast.package,
            service=service_for_path(file_path, project_root),
            primary_type_fqn=encl.fqn,
            primary_type_kind=encl.kind,
            role=infer_role_for_type(encl),
            annotations_on_type=ann_names,
            symbols=_symbols_in_range(ast, chunk_start_byte, chunk_end_byte),
        )
    return ChunkEnrichment(
        package=ast.package,
        service=service_for_path(file_path, project_root),
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
