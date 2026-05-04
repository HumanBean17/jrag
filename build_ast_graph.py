#!/usr/bin/env python3
"""Three-pass AST-derived Knowledge Base builder (Kuzu).

Walks a Java source tree with `tree_sitter_java`, writes a deterministic graph of:
    Symbol nodes: package, file, class, interface, enum, record, annotation, method, constructor
    Rel tables:   EXTENDS, IMPLEMENTS, INJECTS, DECLARES, CALLS

Pass 1 builds every node and in-memory resolution indexes.
Pass 2 resolves each extends/implements/injection target using Java's lookup order
(same file → explicit import → same package → wildcard import → java.lang → phantom).
Pass 3 resolves static call sites into confidence-scored CALLS edges and DECLARES.

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
import logging
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import kuzu

from ast_java import (
    ONTOLOGY_VERSION,
    AnnotationRef,
    CallSite,
    FieldDecl,
    JavaFileAst,
    MethodDecl,
    TypeDecl,
    injection_annotation_names,
    lombok_required_args_annotations,
    parse_java,
)
from graph_enrich import (
    collect_annotation_meta_chain,
    load_brownfield_overrides,
    microservice_for_path,
    module_for_path,
    phantom_id,
    resolve_role_and_capabilities,
    symbol_id,
)
from java_index_v1_common import (
    COMMON_EXCLUDED_PATH_PATTERNS,
    compile_excluded_glob_patterns,
    iter_java_source_files,
)

log = logging.getLogger(__name__)

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
    module: str
    microservice: str
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
    module: str
    microservice: str
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
class CallsRow:
    src_id: str
    dst_id: str
    call_site_line: int = 0
    call_site_byte: int = 0
    arg_count: int = 0
    confidence: float = 0.0
    strategy: str = "phantom"
    source: str = "static"
    resolved: bool = True


@dataclass
class DeclaresRow:
    src_id: str
    dst_id: str


@dataclass
class CallResolutionStats:
    total: int = 0
    by_strategy: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    phantom_chained: int = 0
    phantom_other: int = 0


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
    calls_rows: list[CallsRow] = field(default_factory=list)
    declares_rows: list[DeclaresRow] = field(default_factory=list)
    methods_by_type: dict[str, list[MemberEntry]] = field(default_factory=dict)
    parse_errors: int = 0
    skipped_files: int = 0


# ---------- file walk (see `java_index_v1_common.iter_java_source_files`) ----------


# ---------- pass 1 ----------


def _register_type(
    tables: GraphTables,
    decl: TypeDecl,
    *,
    file_path: str,
    module: str,
    microservice: str,
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
        module=module,
        microservice=microservice,
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
            file_path=file_path, module=module, microservice=microservice,
            node_id=mid,
        ))

    for nested in decl.nested:
        _register_type(
            tables, nested, file_path=file_path,
            module=module, microservice=microservice, outer_fqn=decl.fqn,
        )

    return entry


def pass1_parse(root: Path, tables: GraphTables, *, verbose: bool) -> dict[str, JavaFileAst]:
    """Walk files, parse them, populate node indexes. Returns path -> AST."""
    asts: dict[str, JavaFileAst] = {}
    excludes = compile_excluded_glob_patterns(COMMON_EXCLUDED_PATH_PATTERNS)
    t0 = time.time()
    n_files = 0
    for p in iter_java_source_files(root, excludes):
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
        module = module_for_path(str(p), root)
        microservice = microservice_for_path(str(p), root)
        asts[rel] = ast

        # file node
        file_id = symbol_id("file", rel, rel, 0)
        tables.files[rel] = file_id

        # package node (created lazily; nodes deduped by id)
        if ast.package and ast.package not in tables.packages:
            tables.packages[ast.package] = symbol_id("package", ast.package, "", 0)

        for t in ast.top_level_types:
            _register_type(
                tables, t, file_path=rel,
                module=module, microservice=microservice, outer_fqn=None,
            )

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
            "module": "",
            "microservice": "",
            "filename": "",
            "start_line": 0,
            "end_line": 0,
            "start_byte": 0,
            "end_byte": 0,
            "modifiers": [],
            "annotations": [],
            "capabilities": [],
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


# ---------- pass 3: call graph ----------


def _build_member_indexes(tables: GraphTables) -> None:
    tables.methods_by_type = {}
    for m in tables.members:
        tables.methods_by_type.setdefault(m.parent_fqn, []).append(m)


def _direct_supertype_fqns(entry: TypeIndexEntry, tables: GraphTables) -> list[str]:
    out: list[str] = []
    for r in tables.extends_rows:
        if r.src_id == entry.node_id and r.dst_fqn in tables.types:
            out.append(r.dst_fqn)
    for r in tables.implements_rows:
        if r.src_id == entry.node_id and r.dst_fqn in tables.types:
            out.append(r.dst_fqn)
    return out


def _first_supertype_fqn(tables: GraphTables, type_fqn: str) -> str | None:
    entry = tables.types.get(type_fqn)
    if entry is None:
        return None
    for r in tables.extends_rows:
        if r.src_id == entry.node_id and r.dst_fqn in tables.types:
            return r.dst_fqn
    for r in tables.implements_rows:
        if r.src_id == entry.node_id and r.dst_fqn in tables.types:
            return r.dst_fqn
    return None


def _is_chained_receiver_text(receiver_expr: str) -> bool:
    """Heuristic: call chain or complex expr (contains a completed call)."""
    s = receiver_expr.strip()
    return "(" in s and ")" in s


def _resolve_this_super_field_chain(
    expr: str,
    *,
    member: MemberEntry,
    ast: JavaFileAst,
    tables: GraphTables,
) -> str | None:
    """Resolve `this.a.b` / `super.a` (no calls) to the final field's type FQN."""
    s = expr.strip()
    if "(" in s or ")" in s or "." not in s:
        return None
    entry = tables.types.get(member.parent_fqn)
    if entry is None:
        return None
    parts = s.split(".")
    if len(parts) < 2:
        return None
    if parts[0] == "this":
        cur = entry
    elif parts[0] == "super":
        sup = _first_supertype_fqn(tables, member.parent_fqn)
        if sup is None or sup not in tables.types:
            return None
        cur = tables.types[sup]
    else:
        return None
    for fname in parts[1:]:
        fld = next((f for f in cur.decl.fields if f.name == fname), None)
        if fld is None:
            return None
        resolved = _resolve_simple(fld.type_name, current=cur, ast=ast, tables=tables)
        if resolved is None:
            return None
        cur = resolved
    return cur.decl.fqn


def _scope_table(member: MemberEntry, ast: JavaFileAst, tables: GraphTables) -> dict[str, str]:
    """Map simple variable/field/param name -> resolved declaring type FQN."""
    scope: dict[str, str] = {}
    entry = tables.types.get(member.parent_fqn)
    if entry is None:
        return scope

    def add_fields(tentry: TypeIndexEntry) -> None:
        for f in tentry.decl.fields:
            resolved = _resolve_simple(f.type_name, current=tentry, ast=ast, tables=tables)
            if resolved is not None:
                scope[f.name] = resolved.decl.fqn

    add_fields(entry)
    seen: set[str] = {member.parent_fqn}
    queue = list(_direct_supertype_fqns(entry, tables))
    while queue:
        sup = queue.pop()
        if sup in seen or sup not in tables.types:
            continue
        seen.add(sup)
        te = tables.types[sup]
        for f in te.decl.fields:
            if f.name not in scope:
                resolved = _resolve_simple(f.type_name, current=te, ast=ast, tables=tables)
                if resolved is not None:
                    scope[f.name] = resolved.decl.fqn
        queue.extend(_direct_supertype_fqns(te, tables))

    for p in member.decl.parameters:
        resolved = _resolve_simple(p.type_name, current=entry, ast=ast, tables=tables)
        if resolved is not None:
            scope[p.name] = resolved.decl.fqn

    # Locals shadow fields and parameters (same simple name → local wins).
    for name, t_simple in member.decl.local_vars:
        resolved = _resolve_simple(t_simple, current=entry, ast=ast, tables=tables)
        if resolved is not None:
            scope[name] = resolved.decl.fqn

    return scope


def _lookup_method_candidates(
    type_fqn: str,
    callee_simple: str,
    arg_count: int,
    tables: GraphTables,
    ast: JavaFileAst,
    *,
    visited: set[str] | None = None,
) -> tuple[list[MemberEntry], bool]:
    """Return (candidates, used_name_only_fallback). Walks type + supertypes."""
    if visited is None:
        visited = set()
    exact: list[MemberEntry] = []
    name_only: list[MemberEntry] = []

    def collect_on_type(tfqn: str) -> None:
        nonlocal exact, name_only
        for m in tables.methods_by_type.get(tfqn, ()):
            if callee_simple == "<init>":
                if not m.decl.is_constructor:
                    continue
                np = len(m.decl.parameters)
                if arg_count < 0:
                    name_only.append(m)
                elif np == arg_count:
                    exact.append(m)
                else:
                    name_only.append(m)
                continue
            if m.decl.is_constructor:
                continue
            if m.decl.name != callee_simple:
                continue
            np = len(m.decl.parameters)
            if arg_count < 0:
                name_only.append(m)
            elif np == arg_count:
                exact.append(m)
            else:
                name_only.append(m)

    queue = [type_fqn]
    while queue:
        tfqn = queue.pop(0)
        if tfqn in visited or tfqn not in tables.types:
            continue
        visited.add(tfqn)
        collect_on_type(tfqn)
        te = tables.types[tfqn]
        for sup in _direct_supertype_fqns(te, tables):
            if sup not in visited:
                queue.append(sup)

    if exact:
        return exact, False
    if name_only:
        return name_only, True
    return [], False


def _static_wildcard_resolve(
    callee_simple: str,
    ast: JavaFileAst,
    tables: GraphTables,
    current: TypeIndexEntry,
) -> str | None:
    for tw in ast.file_imports.static_wildcards:
        if tw not in tables.types:
            continue
        for m in tables.methods_by_type.get(tw, ()):
            if m.decl.name != callee_simple or m.decl.is_constructor:
                continue
            if "static" not in m.decl.modifiers:
                continue
            return tw
    return None


def _unique_type_simple_resolve(simple: str, tables: GraphTables) -> str | None:
    """Return the type FQN iff exactly one indexed type uses `simple` as `decl.name`.

    Used only for receiver / static-qualifier disambiguation. Do not use the
    method index here: an unresolved identifier that equals some method's
    simple name elsewhere in the project is not evidence about the receiver type.
    """
    hits = tables.by_simple_name.get(simple, [])
    if len(hits) != 1:
        return None
    return hits[0].decl.fqn


def _suffix_resolve(receiver_simple: str, tables: GraphTables) -> str | None:
    matches = [fq for fq in tables.types if fq.endswith("." + receiver_simple)]
    if len(matches) != 1:
        return None
    return matches[0]


def _resolve_receiver_type(
    call: CallSite,
    *,
    scope: dict[str, str],
    member: MemberEntry,
    ast: JavaFileAst,
    tables: GraphTables,
) -> tuple[str | None, str, float]:
    """Returns (receiver_type_fqn_or_none, strategy, confidence)."""
    expr = call.receiver_expr.strip()
    callee = call.callee_simple

    if not expr and not call.is_static_call:
        if callee in ast.file_imports.static_methods:
            full = ast.file_imports.static_methods[callee]
            if "." in full:
                type_fqn = full.rsplit(".", 1)[0]
                return type_fqn, "static_import", 0.95
        sw = _static_wildcard_resolve(callee, ast, tables, tables.types[member.parent_fqn])
        if sw is not None:
            return sw, "static_import_wildcard", 0.85

    if call.is_static_call and expr:
        if _is_chained_receiver_text(expr):
            return None, "chained_receiver", 0.0
        entry = tables.types.get(member.parent_fqn)
        if entry is None:
            return None, "chained_receiver", 0.0
        bare_static = expr.split("<", 1)[0].strip()
        resolved = _resolve_simple(bare_static, current=entry, ast=ast, tables=tables)
        if resolved is not None:
            return resolved.decl.fqn, "import_map", 0.95
        # External type not in the index but FQN is deterministic via an explicit import.
        # e.g. `import java.util.Objects; Objects.requireNonNull(x)` — we know the FQN
        # is "java.util.Objects" even though the type isn't indexed; return it so the
        # edge carries the correct receiver-tier confidence rather than collapsing to phantom.
        if bare_static in ast.explicit_imports:
            return ast.explicit_imports[bare_static], "import_map", 0.95
        uq = _unique_type_simple_resolve(expr, tables)
        if uq is not None:
            return uq, "unique_type_name", 0.75
        sf = _suffix_resolve(expr, tables)
        if sf is not None:
            return sf, "suffix", 0.55
        return None, "phantom", 0.0

    if expr in ("", "this") or (not expr and call.is_static_call is False and not call.receiver_expr):
        return member.parent_fqn, "this_super", 0.95

    if expr == "super":
        sup = _first_supertype_fqn(tables, member.parent_fqn)
        if sup is not None:
            return sup, "this_super", 0.95
        # No indexed supertype — implicit super to java.lang.Object.
        # Keep strategy='implicit_super' and confidence=0.90 so this path is
        # distinguishable from a genuinely unresolvable receiver.
        return "java.lang.Object", "implicit_super", 0.90

    if _is_chained_receiver_text(expr):
        return None, "chained_receiver", 0.0

    entry = tables.types.get(member.parent_fqn)
    if entry is None:
        return None, "phantom", 0.0

    bare = expr.split("<", 1)[0].strip()
    if bare in scope:
        return scope[bare], "import_map", 0.95

    chain = _resolve_this_super_field_chain(expr, member=member, ast=ast, tables=tables)
    if chain is not None:
        return chain, "import_map", 0.95

    resolved = _resolve_simple(bare, current=entry, ast=ast, tables=tables)
    if resolved is not None:
        return resolved.decl.fqn, "import_map", 0.95

    if entry.package:
        cand = f"{entry.package}.{bare}"
        if cand in tables.types:
            return cand, "same_module", 0.90

    uq = _unique_type_simple_resolve(bare, tables)
    if uq is not None:
        return uq, "unique_type_name", 0.75

    sf = _suffix_resolve(bare, tables)
    if sf is not None:
        return sf, "suffix", 0.55

    return None, "phantom", 0.0


def _phantom_method_id(
    tables: GraphTables,
    *,
    receiver_fqn: str | None,
    receiver_expr: str,
    callee: str,
    arg_count: int,
) -> str:
    # Phantom node identity for a resolved receiver omits call-site arity so
    # method references (arg_count=-1) and normal invocations share one Symbol
    # per (receiver_fqn, callee) when the callee is not indexed (D1).
    if receiver_fqn:
        fqn = f"{receiver_fqn}#{callee}(?)"
        sig = f"{callee}(?)"
    else:
        expr_short = (receiver_expr[:50] if receiver_expr else "?")
        arity = "(?)" if arg_count < 0 else f"({arg_count})"
        fqn = f"?{expr_short}#{callee}{arity}"
        sig = f"{callee}{arity}"
    pid = phantom_id(fqn)
    if pid not in tables.phantoms:
        tables.phantoms[pid] = {
            "id": pid,
            "kind": "method",
            "name": callee,
            "fqn": fqn,
            "package": "",
            "module": "",
            "microservice": "",
            "filename": "",
            "start_line": 0,
            "end_line": 0,
            "start_byte": 0,
            "end_byte": 0,
            "modifiers": [],
            "annotations": [],
            "capabilities": [],
            "role": "OTHER",
            "signature": sig,
            "parent_id": "",
            "resolved": False,
        }
    return pid


def _emit_call_edge(
    tables: GraphTables,
    stats: CallResolutionStats,
    *,
    src_id: str,
    dst_id: str,
    call: CallSite,
    confidence: float,
    strategy: str,
    resolved: bool,
    edge_arg_count: int | None = None,
) -> None:
    arity = call.arg_count if edge_arg_count is None else edge_arg_count
    tables.calls_rows.append(CallsRow(
        src_id=src_id,
        dst_id=dst_id,
        call_site_line=call.line,
        call_site_byte=call.byte,
        arg_count=arity,
        confidence=confidence,
        strategy=strategy,
        source="static",
        resolved=resolved,
    ))
    stats.total += 1
    stats.by_strategy[strategy] += 1
    if strategy == "chained_receiver":
        stats.phantom_chained += 1
    elif strategy == "phantom":
        # Only count as phantom_other when the receiver itself was unresolvable.
        # High-confidence edges with phantom callees (resolved=False, strategy!=phantom)
        # are not noise — they are known external calls with good receiver resolution.
        stats.phantom_other += 1


def _resolve_and_emit_call(
    call: CallSite,
    member: MemberEntry,
    ast: JavaFileAst,
    tables: GraphTables,
    stats: CallResolutionStats,
) -> None:
    scope = _scope_table(member, ast, tables)
    recv_type, strat, conf = _resolve_receiver_type(call, scope=scope, member=member, ast=ast, tables=tables)

    if strat == "chained_receiver":
        pid = _phantom_method_id(
            tables, receiver_fqn=None, receiver_expr=call.receiver_expr,
            callee=call.callee_simple, arg_count=call.arg_count,
        )
        _emit_call_edge(
            tables, stats, src_id=member.node_id, dst_id=pid, call=call,
            confidence=0.0, strategy="chained_receiver", resolved=False,
        )
        return

    if recv_type is None:
        pid = _phantom_method_id(
            tables, receiver_fqn=None, receiver_expr=call.receiver_expr,
            callee=call.callee_simple, arg_count=call.arg_count,
        )
        _emit_call_edge(
            tables, stats, src_id=member.node_id, dst_id=pid, call=call,
            confidence=0.0, strategy="phantom", resolved=False,
        )
        return

    candidates, name_only_fb = _lookup_method_candidates(
        recv_type, call.callee_simple, call.arg_count, tables, ast,
    )

    # Compute the call-shape strategy / confidence override BEFORE the
    # empty-candidates check so they are preserved even when the callee cannot
    # be located on the resolved receiver type (B3 fix).
    edge_conf = conf
    if call.arg_count < 0:
        edge_strat = "method_reference"
    elif call.callee_simple == "<init>" and call.receiver_expr == "super" and (
        call.byte == member.decl.start_byte and call.line == member.decl.start_line
    ):
        # Synthesized implicit-super site from _parse_method.
        edge_strat = "implicit_super"
        edge_conf = 0.90
    elif call.callee_simple == "<init>":
        # new Foo(…), this(…), super(…) — confidence inherited from receiver tier.
        edge_strat = "constructor"
    elif name_only_fb and len(candidates) > 1:
        edge_strat = "overload_ambiguous"
    elif name_only_fb and len(candidates) == 1:
        # Name-only fallback with a single candidate — not ambiguous.
        edge_strat = strat
    else:
        edge_strat = strat

    if not candidates:
        # Receiver was resolved but the callee method isn't indexed on that type
        # (e.g. JDK / Spring / external library).  Preserve the receiver-tier
        # strategy and confidence — only resolved=False signals the phantom callee
        # (B3 fix: do NOT downgrade to confidence=0.0 / strategy='phantom' here).
        pid = _phantom_method_id(
            tables, receiver_fqn=recv_type, receiver_expr=call.receiver_expr,
            callee=call.callee_simple, arg_count=call.arg_count,
        )
        _emit_call_edge(
            tables, stats, src_id=member.node_id, dst_id=pid, call=call,
            confidence=edge_conf, strategy=edge_strat, resolved=False,
        )
        return

    if len(candidates) == 1:
        ref_arity: int | None = None
        if call.arg_count < 0:
            ref_arity = len(candidates[0].decl.parameters)
        _emit_call_edge(
            tables, stats, src_id=member.node_id, dst_id=candidates[0].node_id, call=call,
            confidence=edge_conf, strategy=edge_strat, resolved=True,
            edge_arg_count=ref_arity,
        )
        return

    for c in candidates:
        ref_arity_multi: int | None = len(c.decl.parameters) if call.arg_count < 0 else None
        _emit_call_edge(
            tables, stats, src_id=member.node_id, dst_id=c.node_id, call=call,
            confidence=edge_conf, strategy="overload_ambiguous", resolved=True,
            edge_arg_count=ref_arity_multi,
        )


def _resolve_method_calls(
    member: MemberEntry,
    ast: JavaFileAst,
    tables: GraphTables,
    stats: CallResolutionStats,
) -> None:
    for call in member.decl.call_sites:
        try:
            _resolve_and_emit_call(call, member, ast, tables, stats)
        except Exception as e:
            log.warning("call resolution failed for %s: %s", member.decl.signature, e)


def _process_file_calls(
    file_ast: JavaFileAst,
    file_path: str,
    tables: GraphTables,
    stats: CallResolutionStats,
) -> None:
    for member in tables.members:
        if member.file_path != file_path:
            continue
        try:
            _resolve_method_calls(member, file_ast, tables, stats)
        except Exception as e:
            log.warning("Failed to extract calls from %s#%s: %s", member.parent_fqn, member.decl.signature, e)


def pass3_calls(tables: GraphTables, asts: dict[str, JavaFileAst], *, verbose: bool) -> None:
    _build_member_indexes(tables)
    stats = CallResolutionStats()
    for rel_path, file_ast in asts.items():
        try:
            _process_file_calls(file_ast, rel_path, tables, stats)
        except Exception as e:
            log.error("Call extraction failed for %s: %s", rel_path, e)
    pct = 100.0 * stats.phantom_chained / max(1, stats.total)
    msg = (
        f"Call resolution: {stats.total} sites, {stats.phantom_chained} chained phantoms "
        f"({pct:.1f}%), strategies: {dict(stats.by_strategy)}"
    )
    log.info(msg)
    if verbose:
        print(f"[pass3] {msg}", file=sys.stderr)


# ---------- Kuzu write ----------


_SCHEMA_NODE = (
    "CREATE NODE TABLE Symbol("
    "id STRING PRIMARY KEY, "
    "kind STRING, name STRING, fqn STRING, package STRING, "
    "module STRING, microservice STRING, "
    "filename STRING, start_line INT64, end_line INT64, "
    "start_byte INT64, end_byte INT64, "
    "modifiers STRING[], annotations STRING[], capabilities STRING[], "
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
_SCHEMA_DECLARES = "CREATE REL TABLE DECLARES(FROM Symbol TO Symbol)"
_SCHEMA_CALLS = (
    "CREATE REL TABLE CALLS(FROM Symbol TO Symbol, "
    "call_site_line INT64, call_site_byte INT64, arg_count INT64, "
    "confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN)"
)


def _drop_all(conn: kuzu.Connection) -> None:
    for stmt in (
        "DROP TABLE IF EXISTS EXTENDS",
        "DROP TABLE IF EXISTS IMPLEMENTS",
        "DROP TABLE IF EXISTS INJECTS",
        "DROP TABLE IF EXISTS CALLS",
        "DROP TABLE IF EXISTS DECLARES",
        "DROP TABLE IF EXISTS Symbol",
        "DROP TABLE IF EXISTS GraphMeta",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass


def _create_schema(conn: kuzu.Connection) -> None:
    for stmt in (
        _SCHEMA_NODE,
        _SCHEMA_META,
        _SCHEMA_EXTENDS,
        _SCHEMA_IMPLEMENTS,
        _SCHEMA_INJECTS,
        _SCHEMA_DECLARES,
        _SCHEMA_CALLS,
    ):
        conn.execute(stmt)


def _node_row(**kwargs) -> dict:
    base = {
        "kind": "", "name": "", "fqn": "", "package": "",
        "module": "", "microservice": "",
        "filename": "", "start_line": 0, "end_line": 0,
        "start_byte": 0, "end_byte": 0,
        "modifiers": [], "annotations": [], "capabilities": [],
        "role": "OTHER", "signature": "", "parent_id": "", "resolved": True,
    }
    base.update(kwargs)
    return base


_CREATE_SYMBOL = (
    "CREATE (:Symbol {id: $id, kind: $kind, name: $name, fqn: $fqn, "
    "package: $package, module: $module, microservice: $microservice, "
    "filename: $filename, "
    "start_line: $start_line, end_line: $end_line, "
    "start_byte: $start_byte, end_byte: $end_byte, "
    "modifiers: $modifiers, annotations: $annotations, capabilities: $capabilities, "
    "role: $role, signature: $signature, parent_id: $parent_id, resolved: $resolved})"
)


def _write_nodes(
    conn: kuzu.Connection,
    tables: GraphTables,
    *,
    project_root: Path,
    meta_chain: dict[str, frozenset[str]] | None,
) -> None:
    overrides = load_brownfield_overrides(project_root)
    mch = meta_chain
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
        role, capabilities = resolve_role_and_capabilities(
            d,
            overrides=overrides,
            meta_chain=mch,
        )
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=entry.node_id, kind=d.kind, name=d.name, fqn=d.fqn,
            package=entry.package,
            module=entry.module, microservice=entry.microservice,
            filename=entry.file_path,
            start_line=d.start_line, end_line=d.end_line,
            start_byte=d.start_byte, end_byte=d.end_byte,
            modifiers=list(d.modifiers),
            annotations=[a.name for a in d.annotations],
            capabilities=capabilities,
            role=role,
            signature="",
            parent_id=tables.types[entry.outer_fqn].node_id if entry.outer_fqn and entry.outer_fqn in tables.types else "",
        ))
    # members (methods / constructors)
    for m in tables.members:
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=m.node_id, kind=m.kind, name=m.decl.name,
            fqn=f"{m.parent_fqn}#{m.decl.signature}",
            package=tables.types[m.parent_fqn].package if m.parent_fqn in tables.types else "",
            module=m.module, microservice=m.microservice,
            filename=m.file_path,
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
_CREATE_DECL = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:DECLARES]->(b)"
)
_CREATE_CALL = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:CALLS {"
    "call_site_line: $line, call_site_byte: $byte, arg_count: $argc, "
    "confidence: $conf, strategy: $strat, source: $src_kind, resolved: $resolved"
    "}]->(b)"
)


def _populate_declares_rows(tables: GraphTables) -> None:
    tables.declares_rows = [
        DeclaresRow(src_id=m.parent_id, dst_id=m.node_id) for m in tables.members
    ]


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

    for row in tables.declares_rows:
        conn.execute(_CREATE_DECL, {"src": row.src_id, "dst": row.dst_id})

    seen_calls: set[tuple[str, str, int, int]] = set()
    unique_calls: list[CallsRow] = []
    for row in tables.calls_rows:
        key = (row.src_id, row.dst_id, row.arg_count, row.call_site_line)
        if key not in seen_calls:
            seen_calls.add(key)
            unique_calls.append(row)

    for row in unique_calls:
        conn.execute(_CREATE_CALL, {
            "src": row.src_id, "dst": row.dst_id,
            "line": row.call_site_line,
            "byte": row.call_site_byte,
            "argc": row.arg_count,
            "conf": row.confidence,
            "strat": row.strategy,
            "src_kind": row.source,
            "resolved": row.resolved,
        })


def _write_meta(conn: kuzu.Connection, tables: GraphTables, source_root: Path) -> None:
    import json
    seen_calls: set[tuple[str, str, int, int]] = set()
    calls_unique = 0
    for row in tables.calls_rows:
        key = (row.src_id, row.dst_id, row.arg_count, row.call_site_line)
        if key not in seen_calls:
            seen_calls.add(key)
            calls_unique += 1
    counts = {
        "packages": len(tables.packages),
        "files": len(tables.files),
        "types": len(tables.types),
        "members": len(tables.members),
        "phantoms": len(tables.phantoms),
        "extends": len(tables.extends_rows),
        "implements": len(tables.implements_rows),
        "injects": len(tables.injects_rows),
        "declares": len(tables.declares_rows),
        "calls": calls_unique,
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


def write_kuzu(
    db_path: Path,
    tables: GraphTables,
    *,
    source_root: Path,
    verbose: bool,
    meta_chain: dict[str, frozenset[str]] | None = None,
) -> None:
    if meta_chain is None:
        meta_chain = collect_annotation_meta_chain(
            str(source_root.resolve()),
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(db_path))
    conn = kuzu.Connection(db)
    _drop_all(conn)
    _create_schema(conn)
    t0 = time.time()
    _write_nodes(
        conn,
        tables,
        project_root=source_root,
        meta_chain=meta_chain,
    )
    if verbose:
        print(f"[write] nodes written in {time.time() - t0:.2f}s", file=sys.stderr)
    _populate_declares_rows(tables)
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
    pass3_calls(tables, asts, verbose=args.verbose)
    write_kuzu(kuzu_path, tables, source_root=root, verbose=args.verbose)
    if args.verbose:
        print(f"[done] kuzu at {kuzu_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
