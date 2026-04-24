#!/usr/bin/env python3
"""Two-pass AST-derived Knowledge Base builder (Kuzu).

Walks a Java source tree with `tree_sitter_java`, writes a deterministic graph of:
    Symbol nodes: package, file, class, interface, enum, record, annotation, method, constructor
    Rel tables:   EXTENDS, IMPLEMENTS, INJECTS

Pass 1 builds every node and in-memory resolution indexes.
Pass 2 resolves each extends/implements/injection target using Java's lookup order
(same file → explicit import → same package → wildcard import → java.lang → phantom).

Usage:
    build_ast_graph.py --source-root <repo> [--kuzu-path <dir>] [--verbose]

Default KUZU path resolution order:
    --kuzu-path CLI arg
    KUZU_DB_PATH env var
    ${LANCEDB_URI%/}/code_graph.kuzu (if LANCEDB_URI is a local dir)
    ./lancedb_data/code_graph.kuzu

The Kuzu DB is dropped and rebuilt on every run (Phase 1 is a full rebuild).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import kuzu

from ast_java import (
    ONTOLOGY_VERSION,
    AnnotationRef,
    FieldDecl,
    JavaFileAst,
    MethodDecl,
    TypeDecl,
    infer_role,
    injection_annotation_names,
    lombok_required_args_annotations,
    parse_java,
)
from graph_enrich import phantom_id, service_for_path, symbol_id
from java_index_v1_common import COMMON_EXCLUDED_PATH_PATTERNS

_JAVA_LANG_SIMPLE = frozenset({
    "Object", "String", "Integer", "Long", "Short", "Byte", "Boolean", "Double",
    "Float", "Character", "Number", "Void", "Class", "Enum", "Record",
    "Throwable", "Exception", "RuntimeException", "Error", "Thread", "Runnable",
    "Iterable", "Comparable", "CharSequence", "StringBuilder", "StringBuffer",
    "Math", "System", "AutoCloseable", "Cloneable",
})


# ---------- dataclasses ----------


@dataclass
class TypeIndexEntry:
    """Pass-1 record for a type declaration + any methods/constructors inside it."""
    decl: TypeDecl
    file_path: str
    service: str
    package: str
    outer_fqn: str | None
    node_id: str


@dataclass
class MemberEntry:
    kind: str  # method | constructor
    decl: MethodDecl
    parent_id: str
    parent_fqn: str
    file_path: str
    service: str
    node_id: str


@dataclass
class EdgeRow:
    src_id: str
    dst_id: str
    dst_name: str
    dst_fqn: str
    resolved: bool


@dataclass
class InjectsRow(EdgeRow):
    mechanism: str = ""
    annotation: str = ""
    field_or_param: str = ""


@dataclass
class GraphTables:
    types: dict[str, TypeIndexEntry] = field(default_factory=dict)  # fqn -> entry
    by_simple_name: dict[str, list[TypeIndexEntry]] = field(default_factory=dict)
    by_package: dict[str, list[TypeIndexEntry]] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # path -> node id
    packages: dict[str, str] = field(default_factory=dict)  # pkg -> node id
    members: list[MemberEntry] = field(default_factory=list)
    phantoms: dict[str, dict] = field(default_factory=dict)  # id -> row
    extends_rows: list[EdgeRow] = field(default_factory=list)
    implements_rows: list[EdgeRow] = field(default_factory=list)
    injects_rows: list[InjectsRow] = field(default_factory=list)
    parse_errors: int = 0
    skipped_files: int = 0


# ---------- file walk ----------


def _compile_excludes(patterns: Iterable[str]) -> list[str]:
    return list(patterns)


def _path_excluded(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def _iter_java_files(root: Path, excludes: list[str]) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        # prune dotfiles + excluded dir names early
        dirnames[:] = [d for d in dirnames if d not in (".git", "target", "build", "node_modules", ".venv", ".idea")]
        for fn in filenames:
            if not fn.endswith(".java"):
                continue
            p = Path(dirpath) / fn
            rel = p.resolve().relative_to(root.resolve()).as_posix() if p.resolve().is_relative_to(root.resolve()) else p.as_posix()
            if _path_excluded(rel, excludes) or _path_excluded(f"**/{rel}", excludes):
                continue
            yield p


# ---------- pass 1 ----------


def _register_type(
    tables: GraphTables,
    decl: TypeDecl,
    *,
    file_path: str,
    service: str,
    outer_fqn: str | None,
) -> TypeIndexEntry:
    package = decl.fqn.rsplit(".", 1)[0] if "." in decl.fqn and outer_fqn is None else (
        outer_fqn.rsplit(".", 1)[0] if outer_fqn and "." in outer_fqn else ""
    )
    # top-level: package = fqn - name; nested: inherit from outer
    if outer_fqn is None:
        package = decl.fqn[: -(len(decl.name) + 1)] if decl.fqn.endswith("." + decl.name) else ""
    else:
        # walk outward to find a top-level fqn; package is everything before its simple name
        top = outer_fqn
        while top in tables.types and tables.types[top].outer_fqn:
            top = tables.types[top].outer_fqn  # type: ignore[assignment]
        package = top[: top.rfind(".")] if "." in top else ""

    node_id = symbol_id(decl.kind, decl.fqn, file_path, decl.start_byte)
    entry = TypeIndexEntry(
        decl=decl,
        file_path=file_path,
        service=service,
        package=package,
        outer_fqn=outer_fqn,
        node_id=node_id,
    )
    tables.types[decl.fqn] = entry
    tables.by_simple_name.setdefault(decl.name, []).append(entry)
    tables.by_package.setdefault(package, []).append(entry)

    for m in decl.methods:
        kind = "constructor" if m.is_constructor else "method"
        mid = symbol_id(kind, f"{decl.fqn}#{m.signature}", file_path, m.start_byte)
        tables.members.append(MemberEntry(
            kind=kind, decl=m, parent_id=node_id, parent_fqn=decl.fqn,
            file_path=file_path, service=service, node_id=mid,
        ))

    for nested in decl.nested:
        _register_type(tables, nested, file_path=file_path, service=service, outer_fqn=decl.fqn)

    return entry


def pass1_parse(root: Path, tables: GraphTables, *, verbose: bool) -> dict[str, JavaFileAst]:
    """Walk files, parse them, populate node indexes. Returns path -> AST."""
    asts: dict[str, JavaFileAst] = {}
    excludes = _compile_excludes(COMMON_EXCLUDED_PATH_PATTERNS)
    t0 = time.time()
    n_files = 0
    for p in _iter_java_files(root, excludes):
        n_files += 1
        try:
            content = p.read_bytes()
        except OSError:
            tables.skipped_files += 1
            continue
        if not content.strip():
            continue
        try:
            ast = parse_java(content)
        except Exception:
            tables.parse_errors += 1
            continue
        if ast.parse_error:
            tables.parse_errors += 1
            # Still index what tree-sitter gave us; robust to syntax errors.
        try:
            rel = p.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel = p.as_posix()
        service = service_for_path(str(p), root)
        asts[rel] = ast

        # file node
        file_id = symbol_id("file", rel, rel, 0)
        tables.files[rel] = file_id

        # package node (created lazily; nodes deduped by id)
        if ast.package and ast.package not in tables.packages:
            tables.packages[ast.package] = symbol_id("package", ast.package, "", 0)

        for t in ast.top_level_types:
            _register_type(tables, t, file_path=rel, service=service, outer_fqn=None)

    if verbose:
        elapsed = time.time() - t0
        print(f"[pass1] parsed {n_files} files in {elapsed:.2f}s: "
              f"{len(tables.types)} types, {len(tables.members)} members, "
              f"{tables.parse_errors} parse errors, {tables.skipped_files} skipped",
              file=sys.stderr)
    return asts


# ---------- pass 2: resolution + edges ----------


def _resolve_simple(
    name: str,
    *,
    current: TypeIndexEntry,
    ast: JavaFileAst,
    tables: GraphTables,
) -> TypeIndexEntry | None:
    """Java-ish name resolution. Returns a known TypeIndexEntry or None (phantom)."""
    # Strip trailing generics the caller may have left in, defensively.
    bare = name.split("<", 1)[0].strip()
    if not bare:
        return None

    # 0. Nested inside the same top-level hierarchy — try `Outer.Bare` fqn.
    outer = current.outer_fqn
    top_fqn = current.decl.fqn
    while outer is not None and outer in tables.types:
        candidate = f"{outer}.{bare}"
        if candidate in tables.types:
            return tables.types[candidate]
        top_fqn = outer
        outer = tables.types[outer].outer_fqn

    # 1. Same-file siblings (same outer as `current`).
    same_outer = current.outer_fqn or current.package
    for e in tables.by_simple_name.get(bare, ()):
        e_parent = e.outer_fqn or e.package
        if e.file_path == current.file_path and e_parent == same_outer:
            return e

    # 2. Explicit import.
    if bare in ast.explicit_imports:
        fq = ast.explicit_imports[bare]
        if fq in tables.types:
            return tables.types[fq]
        # Known FQN (outside our codebase) → unresolved; caller will phantom-ise.
        return None

    # 3. Same package.
    if current.package:
        candidate = f"{current.package}.{bare}"
        if candidate in tables.types:
            return tables.types[candidate]

    # 4. Wildcard imports.
    for wild in ast.wildcard_imports:
        candidate = f"{wild}.{bare}"
        if candidate in tables.types:
            return tables.types[candidate]

    # 5. java.lang best-effort (unresolved but deterministic phantom).
    return None


def _phantom_target(
    tables: GraphTables,
    simple: str,
    ast: JavaFileAst,
    *,
    current: TypeIndexEntry,
) -> tuple[str, str, str]:
    """Produce (id, simple, fqn-or-best-guess) for an unresolved type reference.

    The fqn falls back through: explicit import → wildcard → java.lang → bare name.
    """
    bare = simple.split("<", 1)[0].strip()
    guess_fqn = bare
    if bare in ast.explicit_imports:
        guess_fqn = ast.explicit_imports[bare]
    elif bare in _JAVA_LANG_SIMPLE:
        guess_fqn = f"java.lang.{bare}"
    elif ast.wildcard_imports:
        # Pick first wildcard as a hint (imperfect but useful for display).
        guess_fqn = f"{ast.wildcard_imports[0]}.{bare}"

    pid = phantom_id(guess_fqn)
    if pid not in tables.phantoms:
        tables.phantoms[pid] = {
            "id": pid,
            "kind": "class",
            "name": bare,
            "fqn": guess_fqn,
            "package": guess_fqn.rsplit(".", 1)[0] if "." in guess_fqn else "",
            "service": "",
            "filename": "",
            "start_line": 0,
            "end_line": 0,
            "start_byte": 0,
            "end_byte": 0,
            "modifiers": [],
            "annotations": [],
            "role": "OTHER",
            "signature": "",
            "parent_id": "",
            "resolved": False,
        }
    return pid, bare, guess_fqn


def _edge_for(
    *,
    src: TypeIndexEntry,
    target_simple: str,
    ast: JavaFileAst,
    tables: GraphTables,
) -> tuple[str, str, str, bool]:
    resolved = _resolve_simple(target_simple, current=src, ast=ast, tables=tables)
    if resolved is not None:
        return resolved.node_id, resolved.decl.name, resolved.decl.fqn, True
    pid, simple, fqn = _phantom_target(tables, target_simple, ast, current=src)
    return pid, simple, fqn, False


def _emit_extends_implements(
    entry: TypeIndexEntry,
    ast: JavaFileAst,
    tables: GraphTables,
    *,
    seen_ext: set[tuple[str, str]],
    seen_impl: set[tuple[str, str]],
) -> None:
    for name in entry.decl.extends:
        dst_id, dst_simple, dst_fqn, ok = _edge_for(
            src=entry, target_simple=name, ast=ast, tables=tables,
        )
        key = (entry.node_id, dst_id)
        if key in seen_ext:
            continue
        seen_ext.add(key)
        tables.extends_rows.append(EdgeRow(
            src_id=entry.node_id, dst_id=dst_id,
            dst_name=dst_simple, dst_fqn=dst_fqn, resolved=ok,
        ))

    for name in entry.decl.implements:
        dst_id, dst_simple, dst_fqn, ok = _edge_for(
            src=entry, target_simple=name, ast=ast, tables=tables,
        )
        key = (entry.node_id, dst_id)
        if key in seen_impl:
            continue
        seen_impl.add(key)
        tables.implements_rows.append(EdgeRow(
            src_id=entry.node_id, dst_id=dst_id,
            dst_name=dst_simple, dst_fqn=dst_fqn, resolved=ok,
        ))


def _emit_injects(
    entry: TypeIndexEntry,
    ast: JavaFileAst,
    tables: GraphTables,
    *,
    seen: set[tuple[str, str, str, str]],
) -> None:
    if entry.decl.kind == "interface":
        return

    ann_names = [a.name for a in entry.decl.annotations]
    inject_set = injection_annotation_names()
    lombok_rac = lombok_required_args_annotations()
    has_lombok_rac = any(a in lombok_rac for a in ann_names)

    def _add(
        target: str, mechanism: str, annotation: str, slot: str,
    ) -> None:
        dst_id, dst_simple, dst_fqn, ok = _edge_for(
            src=entry, target_simple=target, ast=ast, tables=tables,
        )
        key = (entry.node_id, dst_id, mechanism, slot)
        if key in seen:
            return
        seen.add(key)
        tables.injects_rows.append(InjectsRow(
            src_id=entry.node_id, dst_id=dst_id,
            dst_name=dst_simple, dst_fqn=dst_fqn, resolved=ok,
            mechanism=mechanism, annotation=annotation, field_or_param=slot,
        ))

    # Field injection: @Autowired / @Inject / @Resource.
    for f in entry.decl.fields:
        annotated = next((a.name for a in f.annotations if a.name in inject_set), None)
        if annotated:
            _add(f.type_name, "field", annotated, f.name)

    # Lombok: @RequiredArgsConstructor -> each `final` non-static field becomes an injection;
    # @AllArgsConstructor -> every non-static field.
    if has_lombok_rac:
        all_args = "AllArgsConstructor" in ann_names
        for f in entry.decl.fields:
            if "static" in f.modifiers:
                continue
            if not all_args and "final" not in f.modifiers:
                continue
            _add(f.type_name, "lombok_required_args",
                 "AllArgsConstructor" if all_args else "RequiredArgsConstructor",
                 f.name)

    # Constructor injection:
    ctors = [m for m in entry.decl.methods if m.is_constructor]
    if ctors:
        chosen = None
        autowired = [c for c in ctors if any(a.name == "Autowired" for a in c.annotations)]
        if autowired:
            chosen = autowired[0]
        elif len(ctors) == 1 and ctors[0].parameters:
            chosen = ctors[0]
        if chosen is not None:
            annotation = "Autowired" if any(a.name == "Autowired" for a in chosen.annotations) else ""
            for p in chosen.parameters:
                _add(p.type_name, "constructor", annotation, p.name)

    # Setter injection: setXxx annotated @Autowired with 1 parameter.
    for m in entry.decl.methods:
        if m.is_constructor or not m.name.startswith("set") or len(m.parameters) != 1:
            continue
        if any(a.name == "Autowired" for a in m.annotations):
            _add(m.parameters[0].type_name, "setter", "Autowired",
                 m.parameters[0].name)


def pass2_edges(tables: GraphTables, asts: dict[str, JavaFileAst], *, verbose: bool) -> None:
    t0 = time.time()
    seen_ext: set[tuple[str, str]] = set()
    seen_impl: set[tuple[str, str]] = set()
    seen_inj: set[tuple[str, str, str, str]] = set()
    for fqn, entry in tables.types.items():
        ast = asts.get(entry.file_path)
        if ast is None:
            continue
        _emit_extends_implements(entry, ast, tables, seen_ext=seen_ext, seen_impl=seen_impl)
        _emit_injects(entry, ast, tables, seen=seen_inj)
    if verbose:
        elapsed = time.time() - t0
        print(f"[pass2] emitted {len(tables.extends_rows)} EXTENDS, "
              f"{len(tables.implements_rows)} IMPLEMENTS, "
              f"{len(tables.injects_rows)} INJECTS, "
              f"{len(tables.phantoms)} phantoms in {elapsed:.2f}s",
              file=sys.stderr)


# ---------- Kuzu write ----------


_SCHEMA_NODE = (
    "CREATE NODE TABLE Symbol("
    "id STRING PRIMARY KEY, "
    "kind STRING, name STRING, fqn STRING, package STRING, service STRING, "
    "filename STRING, start_line INT64, end_line INT64, "
    "start_byte INT64, end_byte INT64, "
    "modifiers STRING[], annotations STRING[], "
    "role STRING, signature STRING, parent_id STRING, resolved BOOLEAN"
    ")"
)

_SCHEMA_META = (
    "CREATE NODE TABLE GraphMeta("
    "key STRING PRIMARY KEY, "
    "ontology_version INT64, built_at INT64, source_root STRING, "
    "counts_json STRING, parse_errors INT64"
    ")"
)

_SCHEMA_EXTENDS = (
    "CREATE REL TABLE EXTENDS(FROM Symbol TO Symbol, "
    "dst_name STRING, dst_fqn STRING, resolved BOOLEAN)"
)
_SCHEMA_IMPLEMENTS = (
    "CREATE REL TABLE IMPLEMENTS(FROM Symbol TO Symbol, "
    "dst_name STRING, dst_fqn STRING, resolved BOOLEAN)"
)
_SCHEMA_INJECTS = (
    "CREATE REL TABLE INJECTS(FROM Symbol TO Symbol, "
    "dst_name STRING, dst_fqn STRING, resolved BOOLEAN, "
    "mechanism STRING, annotation STRING, field_or_param STRING)"
)


def _drop_all(conn: kuzu.Connection) -> None:
    for stmt in (
        "DROP TABLE IF EXISTS EXTENDS",
        "DROP TABLE IF EXISTS IMPLEMENTS",
        "DROP TABLE IF EXISTS INJECTS",
        "DROP TABLE IF EXISTS Symbol",
        "DROP TABLE IF EXISTS GraphMeta",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass


def _create_schema(conn: kuzu.Connection) -> None:
    for stmt in (_SCHEMA_NODE, _SCHEMA_META, _SCHEMA_EXTENDS, _SCHEMA_IMPLEMENTS, _SCHEMA_INJECTS):
        conn.execute(stmt)


def _node_row(**kwargs) -> dict:
    base = {
        "kind": "", "name": "", "fqn": "", "package": "", "service": "",
        "filename": "", "start_line": 0, "end_line": 0,
        "start_byte": 0, "end_byte": 0,
        "modifiers": [], "annotations": [],
        "role": "OTHER", "signature": "", "parent_id": "", "resolved": True,
    }
    base.update(kwargs)
    return base


_CREATE_SYMBOL = (
    "CREATE (:Symbol {id: $id, kind: $kind, name: $name, fqn: $fqn, "
    "package: $package, service: $service, filename: $filename, "
    "start_line: $start_line, end_line: $end_line, "
    "start_byte: $start_byte, end_byte: $end_byte, "
    "modifiers: $modifiers, annotations: $annotations, "
    "role: $role, signature: $signature, parent_id: $parent_id, resolved: $resolved})"
)


def _write_nodes(conn: kuzu.Connection, tables: GraphTables) -> None:
    # packages
    for pkg, pid in tables.packages.items():
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=pid, kind="package", name=pkg.rsplit(".", 1)[-1], fqn=pkg, package=pkg,
        ))
    # files
    for path, fid in tables.files.items():
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=fid, kind="file", name=Path(path).name, fqn=path, filename=path,
        ))
    # types
    for entry in tables.types.values():
        d = entry.decl
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=entry.node_id, kind=d.kind, name=d.name, fqn=d.fqn,
            package=entry.package, service=entry.service,
            filename=entry.file_path,
            start_line=d.start_line, end_line=d.end_line,
            start_byte=d.start_byte, end_byte=d.end_byte,
            modifiers=list(d.modifiers),
            annotations=[a.name for a in d.annotations],
            role=infer_role([a.name for a in d.annotations]),
            signature="",
            parent_id=tables.types[entry.outer_fqn].node_id if entry.outer_fqn and entry.outer_fqn in tables.types else "",
        ))
    # members (methods / constructors)
    for m in tables.members:
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=m.node_id, kind=m.kind, name=m.decl.name,
            fqn=f"{m.parent_fqn}#{m.decl.signature}",
            package=tables.types[m.parent_fqn].package if m.parent_fqn in tables.types else "",
            service=m.service, filename=m.file_path,
            start_line=m.decl.start_line, end_line=m.decl.end_line,
            start_byte=m.decl.start_byte, end_byte=m.decl.end_byte,
            modifiers=list(m.decl.modifiers),
            annotations=[a.name for a in m.decl.annotations],
            signature=m.decl.signature, parent_id=m.parent_id,
        ))
    # phantoms
    for pid, row in tables.phantoms.items():
        conn.execute(_CREATE_SYMBOL, row)


_CREATE_EXT = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:EXTENDS {dst_name: $dst_name, dst_fqn: $dst_fqn, resolved: $resolved}]->(b)"
)
_CREATE_IMPL = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:IMPLEMENTS {dst_name: $dst_name, dst_fqn: $dst_fqn, resolved: $resolved}]->(b)"
)
_CREATE_INJ = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:INJECTS {dst_name: $dst_name, dst_fqn: $dst_fqn, resolved: $resolved, "
    "mechanism: $mechanism, annotation: $annotation, field_or_param: $field_or_param}]->(b)"
)


def _write_edges(conn: kuzu.Connection, tables: GraphTables) -> None:
    for r in tables.extends_rows:
        conn.execute(_CREATE_EXT, {
            "src": r.src_id, "dst": r.dst_id,
            "dst_name": r.dst_name, "dst_fqn": r.dst_fqn, "resolved": r.resolved,
        })
    for r in tables.implements_rows:
        conn.execute(_CREATE_IMPL, {
            "src": r.src_id, "dst": r.dst_id,
            "dst_name": r.dst_name, "dst_fqn": r.dst_fqn, "resolved": r.resolved,
        })
    for r in tables.injects_rows:
        conn.execute(_CREATE_INJ, {
            "src": r.src_id, "dst": r.dst_id,
            "dst_name": r.dst_name, "dst_fqn": r.dst_fqn, "resolved": r.resolved,
            "mechanism": r.mechanism, "annotation": r.annotation,
            "field_or_param": r.field_or_param,
        })


def _write_meta(conn: kuzu.Connection, tables: GraphTables, source_root: Path) -> None:
    import json
    counts = {
        "packages": len(tables.packages),
        "files": len(tables.files),
        "types": len(tables.types),
        "members": len(tables.members),
        "phantoms": len(tables.phantoms),
        "extends": len(tables.extends_rows),
        "implements": len(tables.implements_rows),
        "injects": len(tables.injects_rows),
    }
    conn.execute(
        "CREATE (:GraphMeta {key: $k, ontology_version: $ov, built_at: $t, "
        "source_root: $sr, counts_json: $cj, parse_errors: $pe})",
        {
            "k": "graph",
            "ov": ONTOLOGY_VERSION,
            "t": int(time.time()),
            "sr": str(source_root.resolve()),
            "cj": json.dumps(counts),
            "pe": tables.parse_errors,
        },
    )


def write_kuzu(db_path: Path, tables: GraphTables, *, source_root: Path, verbose: bool) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    _drop_all(conn)
    _create_schema(conn)
    t0 = time.time()
    _write_nodes(conn, tables)
    if verbose:
        print(f"[write] nodes written in {time.time() - t0:.2f}s", file=sys.stderr)
    t1 = time.time()
    _write_edges(conn, tables)
    if verbose:
        print(f"[write] edges written in {time.time() - t1:.2f}s", file=sys.stderr)
    _write_meta(conn, tables, source_root)
    conn.close()


# ---------- CLI ----------


def _default_kuzu_path() -> Path:
    env = os.environ.get("KUZU_DB_PATH", "").strip()
    if env:
        return Path(os.path.expanduser(env))
    lance = os.environ.get("LANCEDB_URI", "").strip()
    if lance and not lance.startswith(("s3://", "gs://", "az://")):
        return Path(os.path.expanduser(lance.rstrip("/"))) / "code_graph.kuzu"
    return Path("./lancedb_data/code_graph.kuzu")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an AST-derived Kuzu graph for Java sources.")
    parser.add_argument("--source-root", default=None, help="Repository / monorepo root to scan for .java (defaults to current working directory)")
    parser.add_argument("--kuzu-path", default=None, help="Kuzu DB directory (defaults to $KUZU_DB_PATH or LANCEDB_URI-derived)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    root = Path(args.source_root).expanduser().resolve() if args.source_root else Path.cwd().resolve()
    if not root.is_dir():
        print(f"source-root not a directory: {root}", file=sys.stderr)
        return 2

    kuzu_path = Path(args.kuzu_path).expanduser() if args.kuzu_path else _default_kuzu_path()

    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=args.verbose)
    pass2_edges(tables, asts, verbose=args.verbose)
    write_kuzu(kuzu_path, tables, source_root=root, verbose=args.verbose)
    if args.verbose:
        print(f"[done] kuzu at {kuzu_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
