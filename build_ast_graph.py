#!/usr/bin/env python3
"""Four-pass AST-derived Knowledge Base builder (Kuzu).

Walks a Java source tree with `tree_sitter_java`, writes a deterministic graph of:
    Symbol nodes: package, file, class, interface, enum, record, annotation, method, constructor
    Route nodes:  declaration-site routes (Spring MVC/WebFlux, Feign, Kafka, …)
    Rel tables:   EXTENDS, IMPLEMENTS, INJECTS, DECLARES, OVERRIDES, CALLS, EXPOSES

Pass 1 builds every node and in-memory resolution indexes.
Pass 2 resolves each extends/implements/injection target using Java's lookup order
(same file → explicit import → same package → wildcard import → java.lang → phantom).
Pass 3 resolves static call sites into confidence-scored CALLS edges and DECLARES.
Pass 4 emits Route rows plus Symbol→Route EXPOSES edges from literal annotation metadata.

Usage:
    build_ast_graph.py --source-root <repo> [--kuzu-path <path>] [--verbose]

Default Kuzu database path resolution order:
    --kuzu-path CLI arg (path passed to kuzu.Database(...))
    JAVA_CODEBASE_RAG_INDEX_DIR/code_graph.kuzu (if set and local)
    ./.java-codebase-rag/code_graph.kuzu under cwd

The Kuzu DB is dropped and rebuilt on every run (Phase 1 is a full rebuild).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import kuzu

from ast_java import (
    ONTOLOGY_VERSION,
    CallSite,
    JavaFileAst,
    MethodDecl,
    OutgoingCallDecl,
    TypeDecl,
    injection_annotation_names,
    lombok_required_args_annotations,
    parse_java,
)
from graph_enrich import (
    _load_config_cross_service_resolution,
    collect_annotation_meta_chain,
    load_brownfield_overrides,
    microservice_for_path,
    module_for_path,
    phantom_id,
    resolve_async_producer_for_method,
    resolve_http_client_for_method,
    resolve_role_and_capabilities,
    resolve_routes_for_method,
    symbol_id,
)
from path_filtering import LayeredIgnore, iter_java_source_files
from java_ontology import VALID_CLIENT_KINDS, VALID_HTTP_CALL_MATCHES, VALID_PRODUCER_KINDS

log = logging.getLogger(__name__)

_VERBOSE_STDERR_LOCK = threading.Lock()

_PASS1_START = "[graph] pass 1 · parsing Java files"
_PASS2_START = "[graph] pass 2 · emitting EXTENDS / IMPLEMENTS / DECLARES rows"
_PASS3_START = "[graph] pass 3 · call resolution (outgoing calls per site)"
_PASS4_START = "[graph] pass 4 · route and EXPOSES extraction"
_PASS5_START = "[graph] pass 5 · imperative HTTP_CALLS / ASYNC_CALLS edges"
_PASS6_START = "[graph] pass 6 · cross-service call-edge matching"
_WRITE_START = "[graph] writing · Kuzu graph to disk"


def _verbose_stderr_line(content: str) -> None:
    with _VERBOSE_STDERR_LOCK:
        print(content, file=sys.stderr, flush=True)


class _VerbosePassHeartbeats:
    """Emit ``[tag] running … Ns elapsed`` every 5s on stderr while in scope (verbose only)."""

    def __init__(self, tag: str, *, verbose: bool) -> None:
        self._tag = tag
        self._verbose = verbose
        self._thr: threading.Thread | None = None
        self._stop: threading.Event | None = None

    def __enter__(self) -> None:
        if not self._verbose:
            return None
        self._stop = threading.Event()
        stop = self._stop
        tag = self._tag

        def worker() -> None:
            t0 = time.monotonic()
            while not stop.wait(timeout=5.0):
                elapsed = int(time.monotonic() - t0)
                _verbose_stderr_line(f"{tag} · {elapsed}s elapsed")

        self._thr = threading.Thread(target=worker, name=f"hb-{tag}", daemon=True)
        self._thr.start()
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._thr is not None and self._stop is not None:
            self._stop.set()
            self._thr.join(timeout=2.0)
        return False


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
    callee_declaring_role: str = "OTHER"


@dataclass
class UnresolvedCallSiteRow:
    id: str
    caller_id: str
    call_site_line: int
    call_site_byte: int
    arg_count: int
    callee_simple: str
    receiver_expr: str
    reason: str


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
    callee_unresolved: int = 0
    skipped_cross_service: int = 0


@dataclass
class RouteRow:
    id: str
    kind: str
    framework: str
    method: str
    path: str
    path_template: str
    path_regex: str
    topic: str
    broker: str
    feign_name: str
    feign_url: str
    microservice: str
    module: str
    filename: str
    start_line: int
    end_line: int
    resolved: bool
    # B2a brownfield composition (PR-A3); not persisted on Kuzu `Route` nodes.
    source_layer: str = "builtin"


@dataclass
class ExposesRow:
    symbol_id: str
    route_id: str
    confidence: float
    strategy: str


@dataclass
class RouteExtractionStats:
    routes_skipped_unresolved: int = 0
    by_framework: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    routes_resolved_pct: float = 100.0
    # Percentage of emitted `Route` rows whose `source_layer` is not `builtin`.
    # Brownfield layers: `layer_b_ann`, `layer_a_meta`, `layer_c_source`, `layer_b_fqn`.
    routes_from_brownfield_pct: float = 0.0
    routes_by_layer: dict[str, int] = field(default_factory=dict)
    exposes_suppressed_feign: int = 0


@dataclass
class HttpCallRow:
    client_id: str
    route_id: str
    confidence: float
    strategy: str
    method_call: str
    raw_uri: str
    match: str


@dataclass
class AsyncCallRow:
    producer_id: str
    route_id: str
    confidence: float
    strategy: str
    direction: str
    raw_topic: str
    match: str


@dataclass
class ClientRow:
    id: str
    client_kind: str
    target_service: str
    path: str
    path_template: str
    path_regex: str
    method: str
    member_fqn: str
    member_id: str
    microservice: str
    module: str
    filename: str
    start_line: int
    end_line: int
    resolved: bool
    source_layer: str


@dataclass
class DeclaresClientRow:
    symbol_id: str
    client_id: str
    confidence: float
    strategy: str


@dataclass
class ProducerRow:
    id: str
    producer_kind: str
    topic: str
    broker: str
    direction: str
    member_fqn: str
    member_id: str
    microservice: str
    module: str
    filename: str
    start_line: int
    end_line: int
    resolved: bool
    source_layer: str


@dataclass
class DeclaresProducerRow:
    symbol_id: str
    producer_id: str
    confidence: float
    strategy: str


@dataclass
class ClientExtractionStats:
    clients_total: int = 0
    declares_client_total: int = 0
    clients_by_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class ProducerExtractionStats:
    producers_total: int = 0
    declares_producer_total: int = 0
    producers_by_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@dataclass
class CallEdgeStats:
    http_calls_total: int = 0
    async_calls_total: int = 0
    http_calls_by_client_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    async_calls_by_client_kind: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    http_calls_by_strategy: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    async_calls_by_strategy: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    http_calls_skipped_unresolved: int = 0
    async_calls_skipped_unresolved: int = 0
    http_clients_from_brownfield_pct: float = 0.0
    async_producers_from_brownfield_pct: float = 0.0
    http_calls_match_breakdown: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    async_calls_match_breakdown: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    cross_service_calls_total: int = 0


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
    unresolved_call_site_rows: list[UnresolvedCallSiteRow] = field(default_factory=list)
    declares_rows: list[DeclaresRow] = field(default_factory=list)
    routes_rows: list[RouteRow] = field(default_factory=list)
    exposes_rows: list[ExposesRow] = field(default_factory=list)
    http_call_rows: list[HttpCallRow] = field(default_factory=list)
    async_call_rows: list[AsyncCallRow] = field(default_factory=list)
    client_rows: list[ClientRow] = field(default_factory=list)
    declares_client_rows: list[DeclaresClientRow] = field(default_factory=list)
    producer_rows: list[ProducerRow] = field(default_factory=list)
    declares_producer_rows: list[DeclaresProducerRow] = field(default_factory=list)
    overrides_rows: list[DeclaresRow] = field(default_factory=list)
    route_stats: RouteExtractionStats = field(default_factory=RouteExtractionStats)
    call_edge_stats: CallEdgeStats = field(default_factory=CallEdgeStats)
    client_stats: ClientExtractionStats = field(default_factory=ClientExtractionStats)
    producer_stats: ProducerExtractionStats = field(default_factory=ProducerExtractionStats)
    methods_by_type: dict[str, list[MemberEntry]] = field(default_factory=dict)
    parse_errors: int = 0
    skipped_files: int = 0
    pass3_skipped_cross_service: int = 0
    pass3_unresolved_phantom_receiver: int = 0
    pass3_unresolved_chained: int = 0
    cross_service_resolution: str = "auto"
    # Populated in _write_nodes (same overrides + meta_chain as Symbol.role).
    type_role_by_node_id: dict[str, str] = field(default_factory=dict)


# ---------- per-file dependency tracking (sidecar .deps.json) ----------


@dataclass
class FileDeps:
    """Per-file dependency metadata for incremental rebuild closure expansion."""

    ext_hash: str = ""
    declares: list[str] = field(default_factory=list)
    injects: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    uses_anno: list[str] = field(default_factory=list)
    overrides: list[str] = field(default_factory=list)
    declares_clients: list[str] = field(default_factory=list)
    declares_producers: list[str] = field(default_factory=list)


# ---------- file walk (see `path_filtering.iter_java_source_files`) ----------


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
    ignore = LayeredIgnore(root)
    t0 = time.time()
    n_files = 0
    if verbose:
        _verbose_stderr_line(_PASS1_START)
    slow_sec = 0.0
    raw_slow = os.environ.get("JAVA_CODEBASE_RAG_TEST_GRAPH_SLOW_SEC", "").strip()
    if raw_slow:
        try:
            slow_sec = float(raw_slow)
        except ValueError:
            slow_sec = 0.0
    with _VerbosePassHeartbeats("[graph] pass 1", verbose=verbose):
        if verbose and slow_sec > 0:
            time.sleep(slow_sec)
        for p in iter_java_source_files(root, ignore=ignore):
            n_files += 1
            try:
                content = p.read_bytes()
            except OSError:
                tables.skipped_files += 1
                continue
            if not content.strip():
                continue
            try:
                rel = p.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = p.as_posix()
            try:
                ast = parse_java(content, filename=rel, verbose=verbose)
            except Exception:
                tables.parse_errors += 1
                continue
            if ast.parse_error:
                tables.parse_errors += 1
                # Still index what tree-sitter gave us; robust to syntax errors.
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
        _verbose_stderr_line(
            f"[graph] pass 1 · parsed {n_files} files in {elapsed:.2f}s: "
            f"{len(tables.types)} types, {len(tables.members)} members, "
            f"{tables.parse_errors} parse errors, {tables.skipped_files} skipped",
        )
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
    while outer is not None and outer in tables.types:
        candidate = f"{outer}.{bare}"
        if candidate in tables.types:
            return tables.types[candidate]
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
    if verbose:
        _verbose_stderr_line(_PASS2_START)
    with _VerbosePassHeartbeats("[graph] pass 2", verbose=verbose):
        for fqn, entry in tables.types.items():
            ast = asts.get(entry.file_path)
            if ast is None:
                continue
            _emit_extends_implements(entry, ast, tables, seen_ext=seen_ext, seen_impl=seen_impl)
            _emit_injects(entry, ast, tables, seen=seen_inj)
    if verbose:
        elapsed = time.time() - t0
        _verbose_stderr_line(
            f"[graph] pass 2 · emitted {len(tables.extends_rows)} EXTENDS, "
            f"{len(tables.implements_rows)} IMPLEMENTS, "
            f"{len(tables.injects_rows)} INJECTS, "
            f"{len(tables.phantoms)} phantoms in {elapsed:.2f}s",
        )


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
    """Return (candidates, used_name_only_fallback). Walks type + supertypes.

    When ``used_name_only_fallback`` is true and ``len(candidates) == 1``, the
    caller may reuse the receiver-resolution strategy (see ``_resolve_and_emit_call``)
    instead of tagging ``overload_ambiguous``.
    """
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
        # Synthetic anonymous classes (`….<anon:byte>`): unqualified instance calls
        # may target the lexically enclosing type (D3), e.g. `pingFromAnon()` from
        # `NestedCalls` inside `new Runnable() { void run() { … } }`.
        if ".<anon:" in tfqn and te.outer_fqn and te.outer_fqn not in visited:
            queue.append(te.outer_fqn)

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

    effective_static = call.is_static_call
    if call.is_static_call and expr and not _is_chained_receiver_text(expr):
        bare_for_static = expr.split("<", 1)[0].strip()
        if bare_for_static and "." not in bare_for_static and bare_for_static in scope:
            effective_static = False

    if not expr and not call.is_static_call:
        if callee in ast.file_imports.static_methods:
            full = ast.file_imports.static_methods[callee]
            if "." in full:
                type_fqn = full.rsplit(".", 1)[0]
                return type_fqn, "static_import", 0.95
        sw = _static_wildcard_resolve(callee, ast, tables, tables.types[member.parent_fqn])
        if sw is not None:
            return sw, "static_import_wildcard", 0.85

    if effective_static and expr:
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

    if expr in ("", "this"):
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


def _method_signature_matches_call(member: MemberEntry, call: CallSite) -> bool:
    if call.arg_count < 0:
        return True
    return len(member.decl.parameters) == call.arg_count


def _is_strict_supertype_of(tables: GraphTables, super_fqn: str, subtype_fqn: str) -> bool:
    if super_fqn == subtype_fqn:
        return False
    entry = tables.types.get(subtype_fqn)
    if entry is None:
        return False
    visited: set[str] = set()
    queue = list(_direct_supertype_fqns(entry, tables))
    while queue:
        tfqn = queue.pop(0)
        if tfqn == super_fqn:
            return True
        if tfqn in visited or tfqn not in tables.types:
            continue
        visited.add(tfqn)
        queue.extend(_direct_supertype_fqns(tables.types[tfqn], tables))
    return False


def _callee_declaring_role_at_write(
    tables: GraphTables,
    dst_id: str,
    *,
    member_by_id: dict[str, MemberEntry],
) -> str:
    """Match parent declaring-type Symbol.role (brownfield + meta_chain included)."""
    if dst_id in tables.phantoms:
        return "OTHER"
    member = member_by_id.get(dst_id)
    if member is None:
        return "OTHER"
    return tables.type_role_by_node_id.get(member.parent_id, "OTHER")


def _collapse_supertype_duplicates(
    candidates: list[MemberEntry],
    recv_type_fqn: str,
    call: CallSite,
    tables: GraphTables,
) -> list[MemberEntry]:
    """§3.3.1 supertype-walk dedup — collapse interface + concrete duplicate sites."""
    if len(candidates) <= 1:
        return candidates
    concrete_on_receiver = [
        c for c in candidates
        if c.parent_fqn == recv_type_fqn and _method_signature_matches_call(c, call)
    ]
    if len(concrete_on_receiver) != 1:
        return candidates
    concrete = concrete_on_receiver[0]
    supertypes = [
        c for c in candidates
        if c is not concrete
        and _is_strict_supertype_of(tables, c.parent_fqn, recv_type_fqn)
        and c.decl.signature == concrete.decl.signature
    ]
    if not supertypes:
        return candidates
    allowed_ids = {concrete.node_id, *(c.node_id for c in supertypes)}
    if any(c.node_id not in allowed_ids for c in candidates):
        return candidates
    log.debug(
        "pass3 supertype dedup %s -> %s",
        [c.node_id for c in candidates],
        concrete.node_id,
    )
    return [concrete]


def _unresolved_call_site_id(caller_id: str, call: CallSite) -> str:
    return f"ucs:{caller_id}:{call.line}:{call.byte}"


def _emit_unresolved_call_site(
    tables: GraphTables,
    stats: CallResolutionStats,
    *,
    caller_id: str,
    call: CallSite,
    reason: str,
) -> None:
    tables.unresolved_call_site_rows.append(UnresolvedCallSiteRow(
        id=_unresolved_call_site_id(caller_id, call),
        caller_id=caller_id,
        call_site_line=call.line,
        call_site_byte=call.byte,
        arg_count=call.arg_count,
        callee_simple=call.callee_simple,
        receiver_expr=call.receiver_expr or "",
        reason=reason,
    ))
    if reason == "chained_receiver":
        stats.phantom_chained += 1
    else:
        stats.phantom_other += 1


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
    if not resolved:
        stats.callee_unresolved += 1


def _resolve_and_emit_call(
    call: CallSite,
    member: MemberEntry,
    ast: JavaFileAst,
    tables: GraphTables,
    stats: CallResolutionStats,
    *,
    scope: dict[str, str],
) -> None:
    """Emit CALLS rows for one call site.

    Candidate selection uses ``_lookup_method_candidates`` (exact arity first, then
    name-only fallback on the type + supertype walk).

    When ``used_name_only_fallback`` is true and exactly one name-only candidate
    exists, the edge ``strategy`` reuses the receiver-resolution tier (``strat``)
    rather than ``overload_ambiguous``: arity at the call site did not match any
    overload, but only one method of that name exists — the callee is unambiguous.
    """
    recv_type, strat, conf = _resolve_receiver_type(call, scope=scope, member=member, ast=ast, tables=tables)

    if strat == "chained_receiver":
        _emit_unresolved_call_site(
            tables, stats, caller_id=member.node_id, call=call, reason="chained_receiver",
        )
        return

    if recv_type is None:
        _emit_unresolved_call_site(
            tables, stats,
            caller_id=member.node_id,
            call=call,
            reason="phantom_unresolved_receiver",
        )
        return

    candidates, name_only_fb = _lookup_method_candidates(
        recv_type, call.callee_simple, call.arg_count, tables, ast,
    )

    # Guard relies on `_lookup_method_candidates` returning a same-ms candidate when one exists; revisit if pass3 scopes lookups per-microservice.
    if member.microservice:
        same_ms = [c for c in candidates if c.microservice == member.microservice]
        if same_ms and len(same_ms) != len(candidates):
            for c in candidates:
                if c.microservice and c.microservice != member.microservice:
                    log.warning(
                        "skipping cross-microservice CALLS edge %s -> %s "
                        "(caller=%s, callee=%s)",
                        f"{member.parent_fqn}#{member.decl.signature}",
                        f"{c.parent_fqn}#{c.decl.signature}",
                        member.microservice, c.microservice,
                    )
                    stats.skipped_cross_service += 1
            candidates = same_ms

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

    if len(candidates) > 1 and edge_strat != "overload_ambiguous":
        candidates = _collapse_supertype_duplicates(candidates, recv_type, call, tables)

    if len(candidates) == 1:
        candidate = candidates[0]
        ref_arity: int | None = None
        if call.arg_count < 0:
            ref_arity = len(candidate.decl.parameters)
        _emit_call_edge(
            tables, stats, src_id=member.node_id, dst_id=candidate.node_id, call=call,
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
    scope = _scope_table(member, ast, tables)
    for call in member.decl.call_sites:
        try:
            _resolve_and_emit_call(call, member, ast, tables, stats, scope=scope)
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
    if verbose:
        _verbose_stderr_line(_PASS3_START)
    _build_member_indexes(tables)
    stats = CallResolutionStats()
    with _VerbosePassHeartbeats("[graph] pass 3", verbose=verbose):
        for rel_path, file_ast in asts.items():
            try:
                _process_file_calls(file_ast, rel_path, tables, stats)
            except Exception as e:
                log.error("Call extraction failed for %s: %s", rel_path, e)
    denom_calls = max(1, stats.total)
    denom_sites = max(1, stats.total + stats.phantom_chained + stats.phantom_other)
    pct_chained = 100.0 * stats.phantom_chained / denom_sites
    pct_callee_unres = 100.0 * stats.callee_unresolved / denom_calls
    pct_phantom_recv = 100.0 * stats.phantom_other / denom_sites
    tables.pass3_skipped_cross_service = int(stats.skipped_cross_service)
    tables.pass3_unresolved_phantom_receiver = int(stats.phantom_other)
    tables.pass3_unresolved_chained = int(stats.phantom_chained)
    msg = (
        f"Call resolution: {stats.total} CALLS rows, {stats.phantom_chained} chained unresolved "
        f"({pct_chained:.1f}%), {stats.callee_unresolved} unresolved callee on CALLS "
        f"({pct_callee_unres:.1f}%), {stats.phantom_other} phantom-receiver unresolved "
        f"({pct_phantom_recv:.1f}%), {stats.skipped_cross_service} skipped cross-service, "
        f"strategies: {dict(stats.by_strategy)}"
    )
    log.info(msg)
    if verbose:
        _verbose_stderr_line(f"[graph] pass 3 · {msg}")


_PATH_VAR_SEG = re.compile(r"^\{([^:{}]+)(?::([^}]*))?\}$")  # whole path segment


def _normalize_path(raw_path: str) -> tuple[str, str]:
    """Return `(path_template, path_regex)` for a servlet-style path pattern.

    `/api/users/{id}` → ``("/api/users/{}", "^/api/users/[^/]+/?$")``.
    `{id:\\d+}` constraints strip to ``{}`` in the template while preserving the
    regex constraint for that segment. Deterministic for shared use by B2b/B6.
    """
    raw_path = (raw_path or "").strip()
    if not raw_path:
        return "", ""
    p = raw_path if raw_path.startswith("/") else "/" + raw_path
    trimmed = p.rstrip("/")
    if trimmed == "":
        return "/", "^/?$"
    segments = [s for s in trimmed.split("/") if s != ""]
    tmpl_parts: list[str] = []
    re_parts: list[str] = []
    for seg in segments:
        m = _PATH_VAR_SEG.fullmatch(seg)
        if m:
            tmpl_parts.append("{}")
            constraint = m.group(2)
            re_parts.append(constraint if constraint else "[^/]+")
        else:
            tmpl_parts.append(seg)
            re_parts.append(re.escape(seg))
    tmpl = "/" + "/".join(tmpl_parts)
    body = "/".join(re_parts)
    if not body.startswith("/"):
        body = "/" + body
    return tmpl, f"^{body}/?$"


def _route_id(
    framework: str,
    kind: str,
    http_method: str,
    path_template: str,
    path_raw: str,
    topic: str,
    broker: str,
    microservice: str,
) -> str:
    """Stable id; `path_raw` disambiguates HTTP routes when `path_template` is empty (SpEL / const)."""
    path_key = path_template if path_template else path_raw
    key = (
        f"{framework}|{kind}|{http_method}|{path_key}|"
        f"{topic}|{broker}|{microservice}"
    )
    return f"r:{hashlib.sha1(key.encode()).hexdigest()[:16]}"


def _client_id(
    *,
    microservice: str,
    member_fqn: str,
    client_kind: str,
    path: str,
    method: str,
) -> str:
    key = f"{microservice}|{member_fqn}|{client_kind}|{path}|{method}"
    return f"c:{hashlib.sha1(key.encode()).hexdigest()[:16]}"


def _producer_id(
    *,
    microservice: str,
    member_fqn: str,
    producer_kind: str,
    topic: str,
) -> str:
    # Topic-level identity per method+kind; broker is intentionally omitted so the same
    # resolved topic on one method shares one Producer node across call sites.
    key = f"{microservice}|{member_fqn}|{producer_kind}|{topic}"
    return f"p:{hashlib.sha1(key.encode()).hexdigest()[:16]}"


def _client_source_layer(strategy: str) -> str:
    if strategy in {"layer_a_meta", "layer_b_ann", "layer_b_fqn", "layer_c_source"}:
        return strategy
    # Some caller extraction paths emit client kind as strategy; treat those
    # as builtin-source declarations instead of warning on every row.
    if strategy in VALID_CLIENT_KINDS:
        return "builtin"
    if strategy != "builtin":
        log.warning("unknown client source strategy %r, falling back to builtin", strategy)
    return "builtin"


def _producer_source_layer(strategy: str) -> str:
    if strategy in {"layer_a_meta", "layer_b_ann", "layer_b_fqn", "layer_c_source"}:
        return strategy
    if strategy in VALID_PRODUCER_KINDS:
        return "builtin"
    if strategy != "builtin":
        log.warning("unknown producer source strategy %r, falling back to builtin", strategy)
    return "builtin"


_ROUTE_LAYER_RANK: dict[str, int] = {
    "builtin": 0,
    "layer_b_ann": 1,
    "layer_a_meta": 2,
    "layer_c_source": 3,
    "layer_b_fqn": 4,
}


def pass4_routes(
    tables: GraphTables,
    asts: dict[str, JavaFileAst],
    *,
    source_root: Path,
    verbose: bool,
) -> None:
    stats = tables.route_stats
    overrides = load_brownfield_overrides(source_root)
    try:
        prs = str(source_root.resolve())
    except OSError:
        prs = str(source_root)
    tables.cross_service_resolution = _load_config_cross_service_resolution(prs)
    meta_chain = collect_annotation_meta_chain(prs)
    if verbose:
        _verbose_stderr_line(_PASS4_START)
    with _VerbosePassHeartbeats("[graph] pass 4", verbose=verbose):

        for ast in asts.values():
            stats.routes_skipped_unresolved += ast.routes_skipped_unresolved

        routes_by_id: dict[str, RouteRow] = {}
        exposes_seen: set[tuple[str, str]] = set()

        http_kinds = frozenset({"http_endpoint", "http_consumer"})

        for member in sorted(tables.members, key=lambda m: m.node_id):
            if member.decl.is_constructor:
                continue
            ast = asts.get(member.file_path)
            if ast is None:
                continue
            type_decl = tables.types[member.parent_fqn].decl
            final_routes = resolve_routes_for_method(
                method_decl=member.decl,
                enclosing_type=type_decl,
                overrides=overrides,
                meta_chain=meta_chain,
                builtin_routes=member.decl.routes,
            )
            if not final_routes:
                continue
            for decl in final_routes:
                path_template, path_regex = ("", "")
                if decl.kind in http_kinds:
                    if decl.resolved and decl.resolution_strategy in (
                        "annotation",
                        "codebase_route",
                    ):
                        path_template, path_regex = _normalize_path(decl.path)
                    else:
                        path_template, path_regex = "", ""
                rid = _route_id(
                    decl.framework,
                    decl.kind,
                    decl.http_method,
                    path_template,
                    decl.path,
                    decl.topic,
                    decl.broker,
                    member.microservice,
                )
                layer = decl.route_source_layer
                if rid not in routes_by_id:
                    routes_by_id[rid] = RouteRow(
                        id=rid,
                        kind=decl.kind,
                        framework=decl.framework,
                        method=decl.http_method,
                        path=decl.path,
                        path_template=path_template,
                        path_regex=path_regex,
                        topic=decl.topic,
                        broker=decl.broker,
                        feign_name=decl.feign_name,
                        feign_url=decl.feign_url,
                        microservice=member.microservice,
                        module=member.module,
                        filename=decl.filename,
                        start_line=decl.start_line,
                        end_line=decl.end_line,
                        resolved=decl.resolved,
                        source_layer=layer,
                    )
                else:
                    prev = routes_by_id[rid]
                    if _ROUTE_LAYER_RANK.get(layer, 0) > _ROUTE_LAYER_RANK.get(
                        prev.source_layer,
                        0,
                    ):
                        routes_by_id[rid] = replace(prev, source_layer=layer)
                ek = (member.node_id, rid)
                if ek not in exposes_seen:
                    route_kind = routes_by_id[rid].kind
                    if route_kind == "http_consumer":
                        stats.exposes_suppressed_feign += 1
                        continue
                    exposes_seen.add(ek)
                    tables.exposes_rows.append(
                        ExposesRow(
                            symbol_id=member.node_id,
                            route_id=rid,
                            confidence=decl.confidence,
                            strategy=decl.resolution_strategy,
                        ),
                    )

        tables.routes_rows = sorted(routes_by_id.values(), key=lambda r: r.id)

        for row in tables.routes_rows:
            stats.by_framework[row.framework] += 1
            stats.by_kind[row.kind] += 1

        n_routes = len(tables.routes_rows)
        if n_routes:
            stats.routes_resolved_pct = 100.0 * sum(
                1 for r in tables.routes_rows if r.resolved
            ) / n_routes
            stats.routes_from_brownfield_pct = 100.0 * sum(
                1 for r in tables.routes_rows if r.source_layer != "builtin"
            ) / n_routes
        else:
            stats.routes_resolved_pct = 100.0
            stats.routes_from_brownfield_pct = 0.0

        by_layer: dict[str, int] = defaultdict(int)
        for row in tables.routes_rows:
            by_layer[row.source_layer] += 1
        stats.routes_by_layer = dict(sorted(by_layer.items()))

    msg = (
        f"Route extraction: emitted={n_routes}, exposes={len(tables.exposes_rows)}, "
        f"exposes_suppressed_feign={stats.exposes_suppressed_feign}, "
        f"skipped_unresolved={stats.routes_skipped_unresolved}, "
        f"routes_resolved_pct={stats.routes_resolved_pct:.1f}, "
        f"routes_from_brownfield_pct={stats.routes_from_brownfield_pct:.1f}, "
        f"by_framework={dict(stats.by_framework)}"
    )
    log.info(msg)
    if verbose:
        _verbose_stderr_line(f"[graph] pass 4 · {msg}")


def pass5_imperative_edges(
    tables: GraphTables,
    asts: dict[str, JavaFileAst],
    *,
    source_root: Path,
    verbose: bool,
) -> None:
    del asts
    overrides = load_brownfield_overrides(source_root)
    try:
        prs = str(source_root.resolve())
    except OSError:
        prs = str(source_root)
    tables.cross_service_resolution = _load_config_cross_service_resolution(prs)
    meta_chain = collect_annotation_meta_chain(prs)
    routes_by_id = {r.id: r for r in tables.routes_rows}
    existing_route_ids = set(routes_by_id)
    http_seen: set[tuple[str, str]] = set()
    async_seen: set[tuple[str, str]] = set()
    client_seen: set[str] = set()
    producer_seen: set[str] = set()
    declares_client_seen: set[tuple[str, str]] = set()
    declares_producer_seen: set[tuple[str, str]] = set()
    route_rows = list(tables.routes_rows)

    def _micro_factor(member: MemberEntry) -> float:
        ms = microservice_for_path(member.file_path, source_root)
        return 1.0 if ms else 0.85

    def _append_route(row: RouteRow) -> None:
        if row.id in existing_route_ids:
            return
        existing_route_ids.add(row.id)
        routes_by_id[row.id] = row
        route_rows.append(row)

    def _phantom_http_route_id(call: OutgoingCallDecl) -> str:
        if call.path_template_call and call.method_call:
            return _route_id("", "http_endpoint", call.method_call, call.path_template_call, call.path_template_call, "", "", "")
        uniq = hashlib.sha1(f"{call.filename}:{call.start_line}:{call.raw_uri}".encode()).hexdigest()[:12]
        return f"r:phantom:{uniq}"

    def _phantom_async_route_id(call: OutgoingCallDecl) -> str:
        if call.topic_call:
            return _route_id("", "kafka_topic", "", "", "", call.topic_call, call.broker_call, "")
        uniq = hashlib.sha1(f"{call.filename}:{call.start_line}:{call.raw_topic}".encode()).hexdigest()[:12]
        return f"r:phantom:{uniq}"

    if verbose:
        _verbose_stderr_line(_PASS5_START)
    with _VerbosePassHeartbeats("[graph] pass 5", verbose=verbose):
        for member in sorted(tables.members, key=lambda x: x.node_id):
            if member.decl.is_constructor:
                continue
            type_decl = tables.types[member.parent_fqn].decl
            final_http_calls = resolve_http_client_for_method(
                method_decl=member.decl,
                enclosing_type=type_decl,
                overrides=overrides,
                meta_chain=meta_chain,
                builtin_calls=member.decl.outgoing_calls,
            )
            final_async_calls = resolve_async_producer_for_method(
                method_decl=member.decl,
                enclosing_type=type_decl,
                overrides=overrides,
                meta_chain=meta_chain,
                builtin_calls=member.decl.outgoing_calls,
            )
            micro_factor = _micro_factor(member)
            for call in final_http_calls + final_async_calls:
                if call.channel == "http":
                    client_path = (call.path_template_call or "").strip()
                    client_method = (call.method_call or "").strip().upper()
                    # Keep normalized path fields on Client now so LC3 filter semantics
                    # (`path_prefix`) can use persisted columns without extra transforms.
                    client_path_template = ""
                    client_path_regex = ""
                    if client_path:
                        client_path_template, client_path_regex = _normalize_path(client_path)
                    cid = _client_id(
                        microservice=member.microservice,
                        member_fqn=call.method_fqn,
                        client_kind=call.client_kind,
                        path=client_path,
                        method=client_method,
                    )
                    if cid not in client_seen:
                        client_seen.add(cid)
                        tables.client_rows.append(
                            ClientRow(
                                id=cid,
                                client_kind=call.client_kind,
                                target_service=call.feign_target_name,
                                path=client_path,
                                path_template=client_path_template,
                                path_regex=client_path_regex,
                                method=client_method,
                                member_fqn=call.method_fqn,
                                member_id=member.node_id,
                                microservice=member.microservice,
                                module=member.module,
                                filename=call.filename,
                                start_line=call.start_line,
                                end_line=call.end_line,
                                resolved=call.resolved,
                                source_layer=_client_source_layer(call.resolution_strategy),
                            ),
                        )
                    dkey = (member.node_id, cid)
                    if dkey not in declares_client_seen:
                        declares_client_seen.add(dkey)
                        tables.declares_client_rows.append(
                            DeclaresClientRow(
                                symbol_id=member.node_id,
                                client_id=cid,
                                confidence=call.confidence_base,
                                strategy=call.resolution_strategy,
                            ),
                        )
                    rid = ""
                    strategy = call.resolution_strategy
                    if call.client_kind == "feign_method":
                        exposing = next((e for e in tables.exposes_rows if e.symbol_id == member.node_id), None)
                        if exposing is not None:
                            rid = exposing.route_id
                    if not rid:
                        rid = _phantom_http_route_id(call)
                        _append_route(
                            RouteRow(
                                id=rid,
                                kind="http_endpoint",
                                framework="",
                                method=call.method_call,
                                path=call.path_template_call,
                                path_template=call.path_template_call,
                                path_regex="",
                                topic="",
                                broker="",
                                feign_name=call.feign_target_name,
                                feign_url=call.feign_target_url,
                                microservice="",
                                module="",
                                filename=call.filename,
                                start_line=call.start_line,
                                end_line=call.end_line,
                                resolved=False,
                                source_layer="builtin",
                            )
                        )
                    key = (cid, rid)
                    if key in http_seen:
                        continue
                    http_seen.add(key)
                    conf = call.confidence_base * 0.3 * micro_factor
                    tables.http_call_rows.append(
                        HttpCallRow(
                            client_id=cid,
                            route_id=rid,
                            confidence=conf,
                            strategy=strategy,
                            method_call=call.method_call,
                            raw_uri=call.raw_uri,
                            match="unresolved",
                        )
                    )
                    tables.call_edge_stats.http_calls_total += 1
                    tables.call_edge_stats.http_calls_by_client_kind[call.client_kind] += 1
                    tables.call_edge_stats.http_calls_by_strategy[strategy] += 1
                elif call.channel == "async":
                    topic_atom = (call.topic_call or "").strip()
                    pid = _producer_id(
                        microservice=member.microservice,
                        member_fqn=call.method_fqn,
                        producer_kind=call.client_kind,
                        topic=topic_atom,
                    )
                    if pid not in producer_seen:
                        producer_seen.add(pid)
                        tables.producer_rows.append(
                            ProducerRow(
                                id=pid,
                                producer_kind=call.client_kind,
                                topic=topic_atom,
                                broker=call.broker_call,
                                direction="producer",
                                member_fqn=call.method_fqn,
                                member_id=member.node_id,
                                microservice=member.microservice,
                                module=member.module,
                                filename=call.filename,
                                start_line=call.start_line,
                                end_line=call.end_line,
                                resolved=call.resolved,
                                source_layer=_producer_source_layer(call.resolution_strategy),
                            ),
                        )
                    dpkey = (member.node_id, pid)
                    if dpkey not in declares_producer_seen:
                        declares_producer_seen.add(dpkey)
                        tables.declares_producer_rows.append(
                            DeclaresProducerRow(
                                symbol_id=member.node_id,
                                producer_id=pid,
                                confidence=call.confidence_base,
                                strategy=call.resolution_strategy,
                            ),
                        )
                    rid = _phantom_async_route_id(call)
                    _append_route(
                        RouteRow(
                            id=rid,
                            kind="kafka_topic",
                            framework="",
                            method="",
                            path="",
                            path_template="",
                            path_regex="",
                            topic=call.topic_call,
                            broker=call.broker_call,
                            feign_name="",
                            feign_url="",
                            microservice="",
                            module="",
                            filename=call.filename,
                            start_line=call.start_line,
                            end_line=call.end_line,
                            resolved=False,
                            source_layer="builtin",
                        )
                    )
                    key = (pid, rid)
                    if key in async_seen:
                        continue
                    async_seen.add(key)
                    conf = call.confidence_base * 0.3 * micro_factor
                    strategy = call.resolution_strategy
                    tables.async_call_rows.append(
                        AsyncCallRow(
                            producer_id=pid,
                            route_id=rid,
                            confidence=conf,
                            strategy=strategy,
                            direction="producer",
                            raw_topic=call.raw_topic,
                            match="unresolved",
                        )
                    )
                    tables.call_edge_stats.async_calls_total += 1
                    tables.call_edge_stats.async_calls_by_client_kind[call.client_kind] += 1
                    tables.call_edge_stats.async_calls_by_strategy[strategy] += 1

        tables.routes_rows = sorted(route_rows, key=lambda r: r.id)
        tables.client_rows = sorted(tables.client_rows, key=lambda c: c.id)
        tables.declares_client_rows = sorted(
            tables.declares_client_rows,
            key=lambda e: (e.symbol_id, e.client_id),
        )
        tables.client_stats.clients_total = len(tables.client_rows)
        tables.client_stats.declares_client_total = len(tables.declares_client_rows)
        tables.client_stats.clients_by_kind = defaultdict(int)
        for row in tables.client_rows:
            tables.client_stats.clients_by_kind[row.client_kind] += 1
        tables.producer_rows = sorted(tables.producer_rows, key=lambda p: p.id)
        tables.declares_producer_rows = sorted(
            tables.declares_producer_rows,
            key=lambda e: (e.symbol_id, e.producer_id),
        )
        tables.producer_stats.producers_total = len(tables.producer_rows)
        tables.producer_stats.declares_producer_total = len(tables.declares_producer_rows)
        tables.producer_stats.producers_by_kind = defaultdict(int)
        for row in tables.producer_rows:
            tables.producer_stats.producers_by_kind[row.producer_kind] += 1
        brownfield_strategies = frozenset(
            (
                "layer_b_ann",
                "layer_a_meta",
                "layer_c_source",
                "layer_b_fqn",
                "codebase_client",
                "codebase_producer",
            ),
        )
        if tables.call_edge_stats.http_calls_total:
            n_http = sum(
                v for k, v in tables.call_edge_stats.http_calls_by_strategy.items()
                if k in brownfield_strategies
            )
            tables.call_edge_stats.http_clients_from_brownfield_pct = (
                100.0 * float(n_http) / float(tables.call_edge_stats.http_calls_total)
            )
        if tables.call_edge_stats.async_calls_total:
            n_async = sum(
                v for k, v in tables.call_edge_stats.async_calls_by_strategy.items()
                if k in brownfield_strategies
            )
            tables.call_edge_stats.async_producers_from_brownfield_pct = (
                100.0 * float(n_async) / float(tables.call_edge_stats.async_calls_total)
            )
    if verbose:
        http_client = dict(sorted(tables.call_edge_stats.http_calls_by_client_kind.items()))
        async_client = dict(sorted(tables.call_edge_stats.async_calls_by_client_kind.items()))
        http_strategy = dict(sorted(tables.call_edge_stats.http_calls_by_strategy.items()))
        async_strategy = dict(sorted(tables.call_edge_stats.async_calls_by_strategy.items()))
        _verbose_stderr_line(
            f"[graph] pass 5 · HTTP_CALLS: {len(tables.http_call_rows)} edges, "
            f"ASYNC_CALLS: {len(tables.async_call_rows)} edges; "
            f"http_by_client_kind={http_client}, async_by_client_kind={async_client}, "
            f"http_by_strategy={http_strategy}, async_by_strategy={async_strategy}",
        )


def _match_call_edge(
    call: OutgoingCallDecl,
    routes: list[RouteRow],
    caller_microservice: str,
) -> tuple[str, list[RouteRow]]:
    """Return (match_outcome, candidate_routes) for an outgoing call."""
    if (
        (not call.resolved)
        and call.path_template_call == ""
        and call.topic_call == ""
    ):
        return "unresolved", []

    candidates: list[RouteRow] = []
    if call.client_kind == "feign_method":
        # Prefer endpoint matching by target service + path/method for Feign declarations.
        path_value = call.path_template_call
        method_value = call.method_call
        if path_value:
            for r in routes:
                if r.kind != "http_endpoint":
                    continue
                if call.feign_target_name and r.microservice != call.feign_target_name:
                    continue
                if not (r.method == "" or method_value == "" or r.method == method_value):
                    continue
                if not r.path_regex:
                    continue
                try:
                    if re.fullmatch(r.path_regex, path_value or "") is None:
                        continue
                except re.error:
                    continue
                candidates.append(r)
        if not candidates:
            # Fallback for legacy/manual routes that only expose Feign target names.
            candidates = [
                r for r in routes
                if r.feign_name and call.feign_target_name and r.feign_name == call.feign_target_name
            ]
    elif call.channel == "http":
        path_value = call.path_template_call
        method_value = call.method_call
        for r in routes:
            if r.kind != "http_endpoint":
                continue
            if not (r.method == "" or method_value == "" or r.method == method_value):
                continue
            if not r.path_regex:
                continue
            try:
                if re.fullmatch(r.path_regex, path_value or "") is None:
                    continue
            except re.error:
                continue
            candidates.append(r)
    elif call.channel == "async":
        candidates = [
            r for r in routes
            if r.topic == call.topic_call and r.broker == call.broker_call
        ]

    if not candidates:
        return "phantom", []
    if len(candidates) > 1:
        return "ambiguous", candidates
    if candidates[0].microservice and candidates[0].microservice == caller_microservice:
        return "intra_service", candidates
    return "cross_service", candidates


_BROWNFIELD_LAYERS = frozenset({
    "layer_c_source",
    "layer_b_ann",
    "layer_b_fqn",
    "layer_a_meta",
})


def _is_brownfield_sourced(
    call_strategy: str,
    candidates: list[RouteRow],
) -> bool:
    """Both sides must come from brownfield layers for an edge to count as
    authoritative under brownfield_only mode."""
    if not candidates:
        return False
    if call_strategy not in _BROWNFIELD_LAYERS:
        return False
    return all(
        getattr(c, "source_layer", "builtin") in _BROWNFIELD_LAYERS
        for c in candidates
    )


def pass6_match_edges(
    tables: GraphTables,
    *,
    verbose: bool,
) -> None:
    match_factor: dict[str, float] = {
        "cross_service": 1.0,
        "intra_service": 0.6,
        "ambiguous": 0.5,
        "phantom": 0.4,
        "unresolved": 0.3,
    }
    route_by_id = {r.id: r for r in tables.routes_rows}
    all_routes = [r for r in tables.routes_rows if r.microservice]
    member_by_id = {m.node_id: m for m in tables.members}
    clients_by_id = {c.id: c for c in tables.client_rows}
    producers_by_id = {p.id: p for p in tables.producer_rows}
    client_hints_by_member: dict[str, list[ClientRow]] = defaultdict(list)
    for edge in tables.declares_client_rows:
        client = clients_by_id.get(edge.client_id)
        if client is None:
            continue
        # `DECLARES_CLIENT.symbol_id` targets `Symbol.id` for member symbols,
        # and member symbols are emitted with `id == MemberEntry.node_id`.
        client_hints_by_member[edge.symbol_id].append(client)
    for member_symbol_id in list(client_hints_by_member.keys()):
        # Deterministic fallback when a method carries multiple feign declarations.
        client_hints_by_member[member_symbol_id].sort(key=lambda c: c.id)

    # Pass 6 is idempotent for full rebuilds: each run fully re-derives match outcomes.
    # If incremental rebuild lands later (Tier-2 follow-up), this reset must remain pass-scoped.
    tables.call_edge_stats.http_calls_match_breakdown.clear()
    tables.call_edge_stats.async_calls_match_breakdown.clear()
    tables.call_edge_stats.cross_service_calls_total = 0

    brownfield_only = tables.cross_service_resolution == "brownfield_only"
    suppressed_auto_cross_http: list[str] = []
    suppressed_auto_cross_async: list[str] = []
    suppressed_auto_cross_count = 0

    def _micro_factor(member: MemberEntry | None) -> float:
        return 1.0 if (member and member.microservice) else 0.85

    if verbose:
        _verbose_stderr_line(_PASS6_START)
    with _VerbosePassHeartbeats("[graph] pass 6", verbose=verbose):
        for row in tables.http_call_rows:
            if row.match != "unresolved":
                continue
            client = clients_by_id.get(row.client_id)
            member = member_by_id.get(client.member_id) if client else None
            base = row.confidence / max(1e-9, (0.3 * _micro_factor(member)))
            src_route = route_by_id.get(row.route_id)
            if src_route is None and member is not None:
                # Recover feign caller hints from persisted caller-side Client declarations.
                for client in client_hints_by_member.get(member.node_id, ()):
                    if client.client_kind != "feign_method":
                        continue
                    path_template, path_regex = _normalize_path(client.path)
                    src_route = RouteRow(
                        id="",
                        kind="http_consumer",
                        framework="feign",
                        method=client.method,
                        path=client.path,
                        path_template=path_template,
                        path_regex=path_regex,
                        topic="",
                        broker="",
                        feign_name=client.target_service,
                        # `Client` stores service-name hints, not feign URL; matcher keys off feign_name.
                        feign_url="",
                        microservice=member.microservice,
                        module=member.module,
                        filename=client.filename,
                        start_line=client.start_line,
                        end_line=client.end_line,
                        resolved=client.resolved,
                        source_layer=client.source_layer,
                    )
                    break
            # Feign caller hints are synthesized as transient `http_consumer` routes in pass6;
            # synthetic phantoms from imperative clients are `http_endpoint` even when `feign_name` is populated from
            # `@CodebaseHttpClient.targetService` / YAML hints — those must path-match like RestTemplate.
            _feign_like = (
                src_route is not None
                and src_route.kind == "http_consumer"
                and bool(src_route.feign_name)
            )
            call = OutgoingCallDecl(
                method_fqn=f"{member.parent_fqn}#{member.decl.signature}" if member else "",
                method_sig=member.decl.signature if member else "",
                client_kind="feign_method" if _feign_like else "rest_template",
                channel="http",
                feign_target_name=src_route.feign_name if src_route else "",
                feign_target_url=src_route.feign_url if src_route else "",
                path_template_call=src_route.path_template if src_route else "",
                method_call=row.method_call,
                topic_call="",
                broker_call="",
                raw_uri=row.raw_uri,
                raw_topic="",
                resolution_strategy=row.strategy,
                confidence_base=base,
                resolved=(row.strategy != "unresolved"),
                filename=member.file_path if member else "",
                start_line=member.decl.start_line if member else 0,
                end_line=member.decl.end_line if member else 0,
            )
            outcome, candidates = _match_call_edge(call, all_routes, member.microservice if member else "")
            if (
                brownfield_only
                and outcome == "cross_service"
                and not _is_brownfield_sourced(row.strategy, candidates)
            ):
                outcome = "unresolved"
                candidates = []
                suppressed_auto_cross_count += 1
                if len(suppressed_auto_cross_http) < 5:
                    suppressed_auto_cross_http.append(call.method_fqn)
            if outcome in VALID_HTTP_CALL_MATCHES:
                row.match = outcome
            if outcome in ("cross_service", "intra_service") and len(candidates) == 1:
                row.route_id = candidates[0].id
            row.confidence = call.confidence_base * match_factor[row.match] * _micro_factor(member)
            tables.call_edge_stats.http_calls_match_breakdown[row.match] += 1
            if row.match == "cross_service":
                tables.call_edge_stats.cross_service_calls_total += 1

        for row in tables.async_call_rows:
            if row.match != "unresolved":
                continue
            producer = producers_by_id.get(row.producer_id)
            member = member_by_id.get(producer.member_id) if producer else None
            base = row.confidence / max(1e-9, (0.3 * _micro_factor(member)))
            src_route = route_by_id.get(row.route_id)
            async_kind = producer.producer_kind if producer else "kafka_send"
            call = OutgoingCallDecl(
                method_fqn=f"{member.parent_fqn}#{member.decl.signature}" if member else "",
                method_sig=member.decl.signature if member else "",
                client_kind=async_kind,
                channel="async",
                feign_target_name="",
                feign_target_url="",
                path_template_call="",
                method_call="",
                topic_call=src_route.topic if src_route else "",
                broker_call=src_route.broker if src_route else "",
                raw_uri="",
                raw_topic=row.raw_topic,
                resolution_strategy=row.strategy,
                confidence_base=base,
                resolved=(row.strategy != "unresolved"),
                filename=member.file_path if member else "",
                start_line=member.decl.start_line if member else 0,
                end_line=member.decl.end_line if member else 0,
            )
            outcome, candidates = _match_call_edge(call, all_routes, member.microservice if member else "")
            if (
                brownfield_only
                and outcome == "cross_service"
                and not _is_brownfield_sourced(row.strategy, candidates)
            ):
                outcome = "unresolved"
                candidates = []
                suppressed_auto_cross_count += 1
                if len(suppressed_auto_cross_async) < 5:
                    suppressed_auto_cross_async.append(call.method_fqn)
            if outcome in VALID_HTTP_CALL_MATCHES:
                row.match = outcome
            if outcome in ("cross_service", "intra_service") and len(candidates) == 1:
                row.route_id = candidates[0].id
            row.confidence = call.confidence_base * match_factor[row.match] * _micro_factor(member)
            tables.call_edge_stats.async_calls_match_breakdown[row.match] += 1
            if row.match == "cross_service":
                tables.call_edge_stats.cross_service_calls_total += 1

        inbound_route_ids = {r.route_id for r in tables.http_call_rows} | {r.route_id for r in tables.async_call_rows}
        tables.routes_rows = sorted(
            [
                r for r in tables.routes_rows
                if not (
                    (r.microservice == "")
                    and (r.framework == "")
                    and (not r.resolved)
                    and (r.id not in inbound_route_ids)
                )
            ],
            key=lambda r: r.id,
        )

    if verbose:
        if brownfield_only:
            n_bf = tables.call_edge_stats.cross_service_calls_total
            first_http = ", ".join(suppressed_auto_cross_http)
            first_async = ", ".join(suppressed_auto_cross_async)
            _verbose_stderr_line(
                f"[graph] pass 6 · cross_service_resolution=brownfield_only:\n"
                f"        {n_bf} cross_service edges from brownfield layers,\n"
                f"        {suppressed_auto_cross_count} auto-cross-service candidates suppressed -> unresolved\n"
                f"        (first 5 http: {first_http})\n"
                f"        (first 5 async: {first_async})",
            )
        _verbose_stderr_line(
            f"[graph] pass 6 · http_match={dict(sorted(tables.call_edge_stats.http_calls_match_breakdown.items()))}, "
            f"async_match={dict(sorted(tables.call_edge_stats.async_calls_match_breakdown.items()))}, "
            f"cross_service_calls_total={tables.call_edge_stats.cross_service_calls_total}",
        )


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
    "counts_json STRING, parse_errors INT64, "
    "routes_total INT64, exposes_total INT64, "
    # JSON map {framework: count}; STRING avoids Kuzu Python MAP↔STRUCT binder mismatch.
    "routes_by_framework STRING, "
    "routes_resolved_pct DOUBLE, "
    "routes_from_brownfield_pct DOUBLE, "
    "routes_by_layer STRING, "
    "clients_total INT64, "
    "declares_client_total INT64, "
    "clients_by_kind STRING, "
    "producers_total INT64, "
    "declares_producer_total INT64, "
    "producers_by_kind STRING, "
    "http_calls_total INT64, "
    "async_calls_total INT64, "
    "http_calls_by_strategy STRING, "
    "async_calls_by_strategy STRING, "
    "http_calls_resolved_pct DOUBLE, "
    "async_calls_resolved_pct DOUBLE, "
    "http_clients_from_brownfield_pct DOUBLE, "
    "async_producers_from_brownfield_pct DOUBLE, "
    "http_calls_match_breakdown STRING, "
    "async_calls_match_breakdown STRING, "
    "cross_service_calls_total INT64, "
    "pass3_skipped_cross_service INT64, "
    "pass3_unresolved_phantom_receiver INT64, "
    "pass3_unresolved_chained INT64, "
    "pass4_exposes_suppressed_feign INT64, "
    "cross_service_resolution STRING, "
    "last_rebuild_mode STRING"
    ")"
)

_SCHEMA_ROUTE = (
    "CREATE NODE TABLE Route("
    "id STRING, kind STRING, framework STRING, "
    "method STRING, path STRING, path_template STRING, path_regex STRING, "
    "topic STRING, broker STRING, "
    "feign_name STRING, feign_url STRING, "
    "microservice STRING, module STRING, "
    "filename STRING, start_line INT64, end_line INT64, "
    "resolved BOOLEAN, "
    "PRIMARY KEY(id))"
)

_SCHEMA_CLIENT = (
    "CREATE NODE TABLE Client("
    "id STRING, client_kind STRING, target_service STRING, "
    "path STRING, path_template STRING, path_regex STRING, method STRING, "
    "member_fqn STRING, member_id STRING, "
    "microservice STRING, module STRING, filename STRING, "
    "start_line INT64, end_line INT64, resolved BOOLEAN, source_layer STRING, "
    "PRIMARY KEY(id))"
)

_SCHEMA_PRODUCER = (
    "CREATE NODE TABLE Producer("
    "id STRING, producer_kind STRING, topic STRING, broker STRING, direction STRING, "
    "member_fqn STRING, member_id STRING, "
    "microservice STRING, module STRING, filename STRING, "
    "start_line INT64, end_line INT64, resolved BOOLEAN, source_layer STRING, "
    "PRIMARY KEY(id))"
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
_SCHEMA_OVERRIDES = "CREATE REL TABLE OVERRIDES(FROM Symbol TO Symbol)"
_SCHEMA_CALLS = (
    "CREATE REL TABLE CALLS(FROM Symbol TO Symbol, "
    "call_site_line INT64, call_site_byte INT64, arg_count INT64, "
    "confidence DOUBLE, strategy STRING, source STRING, resolved BOOLEAN, "
    "callee_declaring_role STRING)"
)
_SCHEMA_UNRESOLVED_CALL_SITE = (
    "CREATE NODE TABLE UnresolvedCallSite("
    "id STRING, caller_id STRING, call_site_line INT64, call_site_byte INT64, "
    "arg_count INT64, callee_simple STRING, receiver_expr STRING, reason STRING, "
    "PRIMARY KEY(id))"
)
_SCHEMA_UNRESOLVED_AT = "CREATE REL TABLE UNRESOLVED_AT(FROM Symbol TO UnresolvedCallSite)"
_SCHEMA_EXPOSES = (
    "CREATE REL TABLE EXPOSES(FROM Symbol TO Route, "
    "confidence DOUBLE, strategy STRING)"
)
_SCHEMA_DECLARES_CLIENT = (
    "CREATE REL TABLE DECLARES_CLIENT(FROM Symbol TO Client, "
    "confidence DOUBLE, strategy STRING)"
)
_SCHEMA_DECLARES_PRODUCER = (
    "CREATE REL TABLE DECLARES_PRODUCER(FROM Symbol TO Producer, "
    "confidence DOUBLE, strategy STRING)"
)
_SCHEMA_HTTP_CALLS = (
    "CREATE REL TABLE HTTP_CALLS(FROM Client TO Route, "
    "confidence DOUBLE, strategy STRING, "
    "method_call STRING, raw_uri STRING, match STRING)"
)
_SCHEMA_ASYNC_CALLS = (
    "CREATE REL TABLE ASYNC_CALLS(FROM Producer TO Route, "
    "confidence DOUBLE, strategy STRING, "
    "direction STRING, raw_topic STRING, match STRING)"
)


def _drop_all(conn: kuzu.Connection) -> None:
    for stmt in (
        "DROP TABLE IF EXISTS DECLARES_CLIENT",
        "DROP TABLE IF EXISTS DECLARES_PRODUCER",
        "DROP TABLE IF EXISTS HTTP_CALLS",
        "DROP TABLE IF EXISTS ASYNC_CALLS",
        "DROP TABLE IF EXISTS EXPOSES",
        "DROP TABLE IF EXISTS UNRESOLVED_AT",
        "DROP TABLE IF EXISTS EXTENDS",
        "DROP TABLE IF EXISTS IMPLEMENTS",
        "DROP TABLE IF EXISTS INJECTS",
        "DROP TABLE IF EXISTS CALLS",
        "DROP TABLE IF EXISTS OVERRIDES",
        "DROP TABLE IF EXISTS DECLARES",
        "DROP TABLE IF EXISTS UnresolvedCallSite",
        "DROP TABLE IF EXISTS Symbol",
        "DROP TABLE IF EXISTS Route",
        "DROP TABLE IF EXISTS Client",
        "DROP TABLE IF EXISTS Producer",
        "DROP TABLE IF EXISTS GraphMeta",
    ):
        try:
            conn.execute(stmt)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Symmetric delete helpers (PR-T2)
# ---------------------------------------------------------------------------

def _del_count(
    conn: kuzu.Connection, count_q: str, del_q: str, fp: str
) -> int:
    r = conn.execute(count_q, {"fp": fp})
    n = int(r.get_next()[0]) if r.has_next() else 0
    if n > 0:
        conn.execute(del_q, {"fp": fp})
    return n


def delete_symbols_for_file(conn: kuzu.Connection, file_path: str) -> int:
    for edge in (
        "DECLARES", "EXTENDS", "IMPLEMENTS", "INJECTS", "CALLS", "OVERRIDES",
    ):
        conn.execute(
            f"MATCH (a:Symbol)-[e:{edge}]->(b:Symbol) "
            "WHERE a.filename = $fp OR b.filename = $fp DELETE e",
            {"fp": file_path},
        )
    conn.execute(
        "MATCH (s:Symbol)-[e:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
        "WHERE s.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (s:Symbol), (u:UnresolvedCallSite) "
        "WHERE s.filename = $fp AND u.caller_id = s.id DELETE u",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (s:Symbol)-[e:EXPOSES]->(r:Route) "
        "WHERE s.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (s:Symbol)-[e:DECLARES_CLIENT]->(c:Client) "
        "WHERE s.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (s:Symbol)-[e:DECLARES_PRODUCER]->(p:Producer) "
        "WHERE s.filename = $fp DELETE e",
        {"fp": file_path},
    )
    return _del_count(
        conn,
        "MATCH (s:Symbol) WHERE s.filename = $fp RETURN count(s) AS n",
        "MATCH (s:Symbol) WHERE s.filename = $fp DELETE s",
        file_path,
    )


def delete_extends_for_file(conn: kuzu.Connection, file_path: str) -> int:
    return _del_count(
        conn,
        "MATCH (a:Symbol)-[e:EXTENDS]->(b:Symbol) "
        "WHERE a.filename = $fp RETURN count(e) AS n",
        "MATCH (a:Symbol)-[e:EXTENDS]->(b:Symbol) "
        "WHERE a.filename = $fp DELETE e",
        file_path,
    )


def delete_implements_for_file(conn: kuzu.Connection, file_path: str) -> int:
    return _del_count(
        conn,
        "MATCH (a:Symbol)-[e:IMPLEMENTS]->(b:Symbol) "
        "WHERE a.filename = $fp RETURN count(e) AS n",
        "MATCH (a:Symbol)-[e:IMPLEMENTS]->(b:Symbol) "
        "WHERE a.filename = $fp DELETE e",
        file_path,
    )


def delete_injects_for_file(conn: kuzu.Connection, file_path: str) -> int:
    return _del_count(
        conn,
        "MATCH (a:Symbol)-[e:INJECTS]->(b:Symbol) "
        "WHERE a.filename = $fp RETURN count(e) AS n",
        "MATCH (a:Symbol)-[e:INJECTS]->(b:Symbol) "
        "WHERE a.filename = $fp DELETE e",
        file_path,
    )


def delete_calls_for_file(conn: kuzu.Connection, file_path: str) -> int:
    conn.execute(
        "MATCH (s:Symbol)-[e:UNRESOLVED_AT]->(u:UnresolvedCallSite) "
        "WHERE s.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (s:Symbol), (u:UnresolvedCallSite) "
        "WHERE s.filename = $fp AND u.caller_id = s.id DELETE u",
        {"fp": file_path},
    )
    return _del_count(
        conn,
        "MATCH (a:Symbol)-[e:CALLS]->(b:Symbol) "
        "WHERE a.filename = $fp RETURN count(e) AS n",
        "MATCH (a:Symbol)-[e:CALLS]->(b:Symbol) "
        "WHERE a.filename = $fp DELETE e",
        file_path,
    )


def delete_routes_for_file(conn: kuzu.Connection, file_path: str) -> int:
    conn.execute(
        "MATCH (s:Symbol)-[e:EXPOSES]->(r:Route) "
        "WHERE r.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
        "WHERE r.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
        "WHERE r.filename = $fp DELETE e",
        {"fp": file_path},
    )
    return _del_count(
        conn,
        "MATCH (r:Route) WHERE r.filename = $fp RETURN count(r) AS n",
        "MATCH (r:Route) WHERE r.filename = $fp DELETE r",
        file_path,
    )


def delete_clients_for_file(conn: kuzu.Connection, file_path: str) -> int:
    conn.execute(
        "MATCH (s:Symbol)-[e:DECLARES_CLIENT]->(c:Client) "
        "WHERE c.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
        "WHERE c.filename = $fp DELETE e",
        {"fp": file_path},
    )
    return _del_count(
        conn,
        "MATCH (c:Client) WHERE c.filename = $fp RETURN count(c) AS n",
        "MATCH (c:Client) WHERE c.filename = $fp DELETE c",
        file_path,
    )


def delete_producers_for_file(conn: kuzu.Connection, file_path: str) -> int:
    conn.execute(
        "MATCH (s:Symbol)-[e:DECLARES_PRODUCER]->(p:Producer) "
        "WHERE p.filename = $fp DELETE e",
        {"fp": file_path},
    )
    conn.execute(
        "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
        "WHERE p.filename = $fp DELETE e",
        {"fp": file_path},
    )
    return _del_count(
        conn,
        "MATCH (p:Producer) WHERE p.filename = $fp RETURN count(p) AS n",
        "MATCH (p:Producer) WHERE p.filename = $fp DELETE p",
        file_path,
    )


def delete_http_calls_for_file(conn: kuzu.Connection, file_path: str) -> int:
    return _del_count(
        conn,
        "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
        "WHERE c.filename = $fp RETURN count(e) AS n",
        "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
        "WHERE c.filename = $fp DELETE e",
        file_path,
    )


def delete_async_calls_for_file(conn: kuzu.Connection, file_path: str) -> int:
    return _del_count(
        conn,
        "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
        "WHERE p.filename = $fp RETURN count(e) AS n",
        "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
        "WHERE p.filename = $fp DELETE e",
        file_path,
    )


def delete_overrides_for_file(conn: kuzu.Connection, file_path: str) -> int:
    return _del_count(
        conn,
        "MATCH (a:Symbol)-[e:OVERRIDES]->(b:Symbol) "
        "WHERE a.filename = $fp RETURN count(e) AS n",
        "MATCH (a:Symbol)-[e:OVERRIDES]->(b:Symbol) "
        "WHERE a.filename = $fp DELETE e",
        file_path,
    )


def delete_all_for_file(
    conn: kuzu.Connection, file_path: str
) -> dict[str, int]:
    return {
        "http_calls": delete_http_calls_for_file(conn, file_path),
        "async_calls": delete_async_calls_for_file(conn, file_path),
        "routes": delete_routes_for_file(conn, file_path),
        "clients": delete_clients_for_file(conn, file_path),
        "producers": delete_producers_for_file(conn, file_path),
        "calls": delete_calls_for_file(conn, file_path),
        "extends": delete_extends_for_file(conn, file_path),
        "implements": delete_implements_for_file(conn, file_path),
        "injects": delete_injects_for_file(conn, file_path),
        "overrides": delete_overrides_for_file(conn, file_path),
        "symbols": delete_symbols_for_file(conn, file_path),
    }


# ---------------------------------------------------------------------------
# Incremental rebuild (PR-T3)
# ---------------------------------------------------------------------------


def expand_to_closure(
    changed_paths: set[str],
    deps_index: DepsIndex,
) -> set[str]:
    """Expand changed_paths to include all transitively affected files.

    Implements the 8 closure rules from proposal §2.3 using inverted .deps.json
    maps.  Rule 5 (brownfield-override) forces full rebuild at the caller level,
    not here.
    """
    if not changed_paths:
        return set()

    files = deps_index.files

    # --- build inverse maps ---
    # type FQN → file that declares it
    declared_by: dict[str, str] = {}
    for fp, deps in files.items():
        for fqn in deps.declares:
            declared_by[fqn] = fp

    # annotation simple name → set of files using it
    anno_users: dict[str, set[str]] = defaultdict(set)
    for fp, deps in files.items():
        for anno in deps.uses_anno:
            anno_users[anno].add(fp)

    # method/type FQN → set of files calling it (strip method part for type lookup)
    callers_of: dict[str, set[str]] = defaultdict(set)
    for fp, deps in files.items():
        for callee in deps.calls:
            callers_of[callee].add(fp)
            # also index the declaring type so type-level changes reach callers
            type_fqn = callee.split("#")[0]
            if type_fqn:
                callers_of[type_fqn].add(fp)

    # type FQN → set of files extending it
    extenders_of: dict[str, set[str]] = defaultdict(set)
    for fp, deps in files.items():
        for ext in deps.extends:
            extenders_of[ext].add(fp)

    # type FQN → set of files injecting it
    injectors_of: dict[str, set[str]] = defaultdict(set)
    for fp, deps in files.items():
        for inj in deps.injects:
            injectors_of[inj].add(fp)

    # method FQN → set of files overriding it
    overriders_of: dict[str, set[str]] = defaultdict(set)
    for fp, deps in files.items():
        for ov in deps.overrides:
            overriders_of[ov].add(fp)
            type_fqn = ov.split("#")[0]
            if type_fqn:
                overriders_of[type_fqn].add(fp)

    # member FQN → file declaring client/producer (inverse of declares_clients/producers)
    client_producer_declaring_files: dict[str, str] = {}
    for fp, deps in files.items():
        for mfqn in deps.declares_clients + deps.declares_producers:
            client_producer_declaring_files[mfqn] = fp

    # --- fixed-point expansion ---
    dirty: set[str] = {p for p in changed_paths if p in files}
    frontier = set(dirty)
    while frontier:
        new_frontier: set[str] = set()
        for fp in frontier:
            deps = files.get(fp)
            if deps is None:
                continue

            # Rule 1: Inverse-INJECTS — files that inject symbols declared in fp
            for fqn in deps.declares:
                for other in injectors_of.get(fqn, ()):
                    if other not in dirty:
                        new_frontier.add(other)

            # Rule 2: Inverse-EXTENDS / Inverse-IMPLEMENTS — files that extend/implement from fp
            for fqn in deps.declares:
                for other in extenders_of.get(fqn, ()):
                    if other not in dirty:
                        new_frontier.add(other)

            # Rule 3: Inverse-CALLS — files that call symbols declared in fp
            for fqn in deps.declares:
                for other in callers_of.get(fqn, ()):
                    if other not in dirty:
                        new_frontier.add(other)

            # Rule 4: Meta-annotation closure — if fp declares an @interface, dirty its users
            for fqn in deps.declares:
                simple = fqn.rsplit(".", 1)[-1]
                for other in anno_users.get(simple, ()):
                    if other not in dirty:
                        new_frontier.add(other)

            # Rule 6: Route resolution — files that extend types from fp (already covered
            # by rule 2) plus files whose calls reference methods from fp (already covered
            # by rule 3). No additional expansion needed.

            # Rule 7: Inverse-OVERRIDES — files that override methods declared in fp
            for fqn in deps.declares:
                for other in overriders_of.get(fqn, ()):
                    if other not in dirty:
                        new_frontier.add(other)

            # Rule 8: Inverse-DECLARES_CLIENT/PRODUCER — already covered by rule 3
            # (callers of methods in fp). The client/producer nodes are re-emitted
            # when their declaring method's file is re-processed.

            # Also: forward deps — if fp injects/extends/calls something, and that
            # thing is in another file, that file may need re-processing for resolution.
            for fqn in deps.injects + deps.extends + deps.calls:
                type_fqn = fqn.split("#")[0]
                declaring_file = declared_by.get(type_fqn)
                if declaring_file and declaring_file not in dirty:
                    new_frontier.add(declaring_file)

            # Forward overrides: if fp overrides something in another file
            for ov in deps.overrides:
                type_fqn = ov.split("#")[0]
                declaring_file = declared_by.get(type_fqn)
                if declaring_file and declaring_file not in dirty:
                    new_frontier.add(declaring_file)

        dirty |= new_frontier
        frontier = new_frontier

    return dirty


def pass1_parse_subset(
    root: Path,
    dirty: set[str],
    *,
    verbose: bool,
) -> dict[str, JavaFileAst]:
    """Re-parse only dirty files. Returns path -> AST."""
    asts: dict[str, JavaFileAst] = {}
    t0 = time.time()
    n_files = 0
    if verbose:
        _verbose_stderr_line(f"[graph] incremental pass 1 · parsing {len(dirty)} dirty files")
    for rel_path in sorted(dirty):
        p = root / rel_path
        if not p.is_file():
            continue
        n_files += 1
        try:
            content = p.read_bytes()
        except OSError:
            continue
        if not content.strip():
            continue
        try:
            ast = parse_java(content, filename=rel_path, verbose=verbose)
        except Exception:
            continue
        asts[rel_path] = ast
    if verbose:
        elapsed = time.time() - t0
        _verbose_stderr_line(
            f"[graph] incremental pass 1 · parsed {n_files} files in {elapsed:.2f}s",
        )
    return asts


def pass2_edges_subset(
    tables: GraphTables,
    asts: dict[str, JavaFileAst],
    dirty: set[str],
    *,
    verbose: bool,
) -> None:
    """Re-emit EXTENDS/IMPLEMENTS/INJECTS edges for types in dirty files."""
    if verbose:
        _verbose_stderr_line("[graph] incremental pass 2 · emitting edges for dirty files")
    seen_ext: set[tuple[str, str]] = set()
    seen_impl: set[tuple[str, str]] = set()
    seen_inj: set[tuple[str, str, str, str]] = set()
    for fqn, entry in tables.types.items():
        if entry.file_path not in dirty:
            continue
        ast = asts.get(entry.file_path)
        if ast is None:
            continue
        _emit_extends_implements(entry, ast, tables, seen_ext=seen_ext, seen_impl=seen_impl)
        _emit_injects(entry, ast, tables, seen=seen_inj)


def pass3_calls_subset(
    tables: GraphTables,
    asts: dict[str, JavaFileAst],
    dirty: set[str],
    *,
    verbose: bool,
) -> None:
    """Re-emit CALLS + UnresolvedCallSite for dirty caller files."""
    if verbose:
        _verbose_stderr_line("[graph] incremental pass 3 · resolving calls for dirty files")
    _build_member_indexes(tables)
    stats = CallResolutionStats()
    for rel_path, file_ast in asts.items():
        if rel_path not in dirty:
            continue
        try:
            _process_file_calls(file_ast, rel_path, tables, stats)
        except Exception as e:
            log.error("Call extraction failed for %s: %s", rel_path, e)


def pass4_routes_subset(
    tables: GraphTables,
    asts: dict[str, JavaFileAst],
    dirty: set[str],
    *,
    source_root: Path,
    verbose: bool,
) -> None:
    """Re-emit Route/EXPOSES for methods in dirty files."""
    if verbose:
        _verbose_stderr_line("[graph] incremental pass 4 · extracting routes for dirty files")
    stats = tables.route_stats
    overrides = load_brownfield_overrides(source_root)
    try:
        prs = str(source_root.resolve())
    except OSError:
        prs = str(source_root)
    tables.cross_service_resolution = _load_config_cross_service_resolution(prs)
    meta_chain = collect_annotation_meta_chain(prs)

    routes_by_id: dict[str, RouteRow] = {}
    exposes_seen: set[tuple[str, str]] = set()
    http_kinds = frozenset({"http_endpoint", "http_consumer"})

    for member in sorted(tables.members, key=lambda m: m.node_id):
        if member.file_path not in dirty:
            continue
        if member.decl.is_constructor:
            continue
        ast = asts.get(member.file_path)
        if ast is None:
            continue
        type_decl = tables.types[member.parent_fqn].decl
        final_routes = resolve_routes_for_method(
            method_decl=member.decl,
            enclosing_type=type_decl,
            overrides=overrides,
            meta_chain=meta_chain,
            builtin_routes=member.decl.routes,
        )
        if not final_routes:
            continue
        for decl in final_routes:
            path_template, path_regex = ("", "")
            if decl.kind in http_kinds:
                if decl.resolved and decl.resolution_strategy in (
                    "annotation",
                    "codebase_route",
                ):
                    path_template, path_regex = _normalize_path(decl.path)
            rid = _route_id(
                decl.framework, decl.kind, decl.http_method,
                path_template, decl.path, decl.topic, decl.broker,
                member.microservice,
            )
            layer = decl.route_source_layer
            if rid not in routes_by_id:
                routes_by_id[rid] = RouteRow(
                    id=rid, kind=decl.kind, framework=decl.framework,
                    method=decl.http_method, path=decl.path,
                    path_template=path_template, path_regex=path_regex,
                    topic=decl.topic, broker=decl.broker,
                    feign_name=decl.feign_name, feign_url=decl.feign_url,
                    microservice=member.microservice, module=member.module,
                    filename=decl.filename,
                    start_line=decl.start_line, end_line=decl.end_line,
                    resolved=decl.resolved, source_layer=layer,
                )
            else:
                prev = routes_by_id[rid]
                if _ROUTE_LAYER_RANK.get(layer, 0) > _ROUTE_LAYER_RANK.get(
                    prev.source_layer, 0,
                ):
                    routes_by_id[rid] = replace(prev, source_layer=layer)
            ek = (member.node_id, rid)
            if ek not in exposes_seen:
                route_kind = routes_by_id[rid].kind
                if route_kind == "http_consumer":
                    stats.exposes_suppressed_feign += 1
                    continue
                exposes_seen.add(ek)
                tables.exposes_rows.append(
                    ExposesRow(
                        symbol_id=member.node_id, route_id=rid,
                        confidence=decl.confidence,
                        strategy=decl.resolution_strategy,
                    ),
                )

    tables.routes_rows = sorted(routes_by_id.values(), key=lambda r: r.id)
    for row in tables.routes_rows:
        stats.by_framework[row.framework] += 1
        stats.by_kind[row.kind] += 1
    n_routes = len(tables.routes_rows)
    if n_routes:
        stats.routes_resolved_pct = 100.0 * sum(
            1 for r in tables.routes_rows if r.resolved
        ) / n_routes
    else:
        stats.routes_resolved_pct = 100.0
    stats.routes_from_brownfield_pct = 0.0
    by_layer: dict[str, int] = defaultdict(int)
    for row in tables.routes_rows:
        by_layer[row.source_layer] += 1
    stats.routes_by_layer = dict(sorted(by_layer.items()))


def pass5_imperative_edges_subset(
    tables: GraphTables,
    dirty: set[str],
    *,
    source_root: Path,
    verbose: bool,
) -> None:
    """Re-emit Client/Producer/HTTP_CALLS/ASYNC_CALLS for members in dirty files."""
    if verbose:
        _verbose_stderr_line("[graph] incremental pass 5 · extracting callers for dirty files")
    overrides = load_brownfield_overrides(source_root)
    try:
        prs = str(source_root.resolve())
    except OSError:
        prs = str(source_root)
    tables.cross_service_resolution = _load_config_cross_service_resolution(prs)
    meta_chain = collect_annotation_meta_chain(prs)
    routes_by_id = {r.id: r for r in tables.routes_rows}
    existing_route_ids = set(routes_by_id)
    http_seen: set[tuple[str, str]] = set()
    async_seen: set[tuple[str, str]] = set()
    client_seen: set[str] = set()
    producer_seen: set[str] = set()
    declares_client_seen: set[tuple[str, str]] = set()
    declares_producer_seen: set[tuple[str, str]] = set()
    route_rows = list(tables.routes_rows)

    def _micro_factor(member: MemberEntry) -> float:
        ms = microservice_for_path(member.file_path, source_root)
        return 1.0 if ms else 0.85

    def _append_route(row: RouteRow) -> None:
        if row.id in existing_route_ids:
            return
        existing_route_ids.add(row.id)
        routes_by_id[row.id] = row
        route_rows.append(row)

    def _phantom_http_route_id(call: OutgoingCallDecl) -> str:
        if call.path_template_call and call.method_call:
            return _route_id("", "http_endpoint", call.method_call, call.path_template_call, call.path_template_call, "", "", "")
        uniq = hashlib.sha1(f"{call.filename}:{call.start_line}:{call.raw_uri}".encode()).hexdigest()[:12]
        return f"r:phantom:{uniq}"

    def _phantom_async_route_id(call: OutgoingCallDecl) -> str:
        if call.topic_call:
            return _route_id("", "kafka_topic", "", "", "", call.topic_call, call.broker_call, "")
        uniq = hashlib.sha1(f"{call.filename}:{call.start_line}:{call.raw_topic}".encode()).hexdigest()[:12]
        return f"r:phantom:{uniq}"

    for member in sorted(tables.members, key=lambda x: x.node_id):
        if member.file_path not in dirty:
            continue
        if member.decl.is_constructor:
            continue
        type_decl = tables.types[member.parent_fqn].decl
        final_http_calls = resolve_http_client_for_method(
            method_decl=member.decl,
            enclosing_type=type_decl,
            overrides=overrides,
            meta_chain=meta_chain,
            builtin_calls=member.decl.outgoing_calls,
        )
        final_async_calls = resolve_async_producer_for_method(
            method_decl=member.decl,
            enclosing_type=type_decl,
            overrides=overrides,
            meta_chain=meta_chain,
            builtin_calls=member.decl.outgoing_calls,
        )
        micro_factor = _micro_factor(member)
        for call in final_http_calls + final_async_calls:
            if call.channel == "http":
                client_path = (call.path_template_call or "").strip()
                client_method = (call.method_call or "").strip().upper()
                client_path_template = ""
                client_path_regex = ""
                if client_path:
                    client_path_template, client_path_regex = _normalize_path(client_path)
                cid = _client_id(
                    microservice=member.microservice,
                    member_fqn=call.method_fqn,
                    client_kind=call.client_kind,
                    path=client_path,
                    method=client_method,
                )
                if cid not in client_seen:
                    client_seen.add(cid)
                    tables.client_rows.append(
                        ClientRow(
                            id=cid, client_kind=call.client_kind,
                            target_service=call.feign_target_name,
                            path=client_path,
                            path_template=client_path_template,
                            path_regex=client_path_regex,
                            method=client_method,
                            member_fqn=call.method_fqn,
                            member_id=member.node_id,
                            microservice=member.microservice,
                            module=member.module,
                            filename=call.filename,
                            start_line=call.start_line,
                            end_line=call.end_line,
                            resolved=call.resolved,
                            source_layer=_client_source_layer(call.resolution_strategy),
                        ),
                    )
                dkey = (member.node_id, cid)
                if dkey not in declares_client_seen:
                    declares_client_seen.add(dkey)
                    tables.declares_client_rows.append(
                        DeclaresClientRow(
                            symbol_id=member.node_id,
                            client_id=cid,
                            confidence=call.confidence_base,
                            strategy=call.resolution_strategy,
                        ),
                    )
                rid = ""
                strategy = call.resolution_strategy
                if call.client_kind == "feign_method":
                    exposing = next(
                        (e for e in tables.exposes_rows if e.symbol_id == member.node_id),
                        None,
                    )
                    if exposing is not None:
                        rid = exposing.route_id
                if not rid:
                    rid = _phantom_http_route_id(call)
                    _append_route(
                        RouteRow(
                            id=rid, kind="http_endpoint", framework="",
                            method=call.method_call, path=call.path_template_call,
                            path_template=call.path_template_call, path_regex="",
                            topic="", broker="",
                            feign_name=call.feign_target_name,
                            feign_url=call.feign_target_url,
                            microservice="", module="",
                            filename=call.filename,
                            start_line=call.start_line, end_line=call.end_line,
                            resolved=False, source_layer="builtin",
                        ),
                    )
                key = (cid, rid)
                if key in http_seen:
                    continue
                http_seen.add(key)
                conf = call.confidence_base * 0.3 * micro_factor
                tables.http_call_rows.append(
                    HttpCallRow(
                        client_id=cid, route_id=rid,
                        confidence=conf, strategy=strategy,
                        method_call=call.method_call, raw_uri=call.raw_uri,
                        match="unresolved",
                    ),
                )
                tables.call_edge_stats.http_calls_total += 1
                tables.call_edge_stats.http_calls_by_client_kind[call.client_kind] += 1
                tables.call_edge_stats.http_calls_by_strategy[strategy] += 1
            elif call.channel == "async":
                topic_atom = (call.topic_call or "").strip()
                pid = _producer_id(
                    microservice=member.microservice,
                    member_fqn=call.method_fqn,
                    producer_kind=call.client_kind,
                    topic=topic_atom,
                )
                if pid not in producer_seen:
                    producer_seen.add(pid)
                    tables.producer_rows.append(
                        ProducerRow(
                            id=pid, producer_kind=call.client_kind,
                            topic=topic_atom, broker=call.broker_call,
                            direction="producer",
                            member_fqn=call.method_fqn,
                            member_id=member.node_id,
                            microservice=member.microservice,
                            module=member.module,
                            filename=call.filename,
                            start_line=call.start_line,
                            end_line=call.end_line,
                            resolved=call.resolved,
                            source_layer=_producer_source_layer(call.resolution_strategy),
                        ),
                    )
                dpkey = (member.node_id, pid)
                if dpkey not in declares_producer_seen:
                    declares_producer_seen.add(dpkey)
                    tables.declares_producer_rows.append(
                        DeclaresProducerRow(
                            symbol_id=member.node_id,
                            producer_id=pid,
                            confidence=call.confidence_base,
                            strategy=call.resolution_strategy,
                        ),
                    )
                rid = _phantom_async_route_id(call)
                _append_route(
                    RouteRow(
                        id=rid, kind="kafka_topic", framework="",
                        method="", path="", path_template="", path_regex="",
                        topic=call.topic_call, broker=call.broker_call,
                        feign_name="", feign_url="",
                        microservice="", module="",
                        filename=call.filename,
                        start_line=call.start_line, end_line=call.end_line,
                        resolved=False, source_layer="builtin",
                    ),
                )
                key = (pid, rid)
                if key in async_seen:
                    continue
                async_seen.add(key)
                conf = call.confidence_base * 0.3 * micro_factor
                strategy = call.resolution_strategy
                tables.async_call_rows.append(
                    AsyncCallRow(
                        producer_id=pid, route_id=rid,
                        confidence=conf, strategy=strategy,
                        direction="producer", raw_topic=call.raw_topic,
                        match="unresolved",
                    ),
                )
                tables.call_edge_stats.async_calls_total += 1
                tables.call_edge_stats.async_calls_by_client_kind[call.client_kind] += 1
                tables.call_edge_stats.async_calls_by_strategy[strategy] += 1

    tables.routes_rows = sorted(route_rows, key=lambda r: r.id)
    tables.client_rows = sorted(tables.client_rows, key=lambda c: c.id)
    tables.declares_client_rows = sorted(
        tables.declares_client_rows,
        key=lambda e: (e.symbol_id, e.client_id),
    )
    tables.client_stats.clients_total = len(tables.client_rows)
    tables.client_stats.declares_client_total = len(tables.declares_client_rows)
    tables.client_stats.clients_by_kind = defaultdict(int)
    for row in tables.client_rows:
        tables.client_stats.clients_by_kind[row.client_kind] += 1
    tables.producer_rows = sorted(tables.producer_rows, key=lambda p: p.id)
    tables.declares_producer_rows = sorted(
        tables.declares_producer_rows,
        key=lambda e: (e.symbol_id, e.producer_id),
    )
    tables.producer_stats.producers_total = len(tables.producer_rows)
    tables.producer_stats.declares_producer_total = len(tables.declares_producer_rows)
    tables.producer_stats.producers_by_kind = defaultdict(int)
    for row in tables.producer_rows:
        tables.producer_stats.producers_by_kind[row.producer_kind] += 1


def _load_remaining_from_db(
    conn: kuzu.Connection,
    dirty: set[str],
) -> GraphTables:
    """Load all remaining (non-dirty) data from DB into a fresh GraphTables."""
    tables = GraphTables()

    # Routes
    try:
        r = conn.execute(
            "MATCH (r:Route) RETURN r.id, r.kind, r.framework, r.method, "
            "r.path, r.path_template, r.path_regex, r.topic, r.broker, "
            "r.feign_name, r.feign_url, r.microservice, r.module, "
            "r.filename, r.start_line, r.end_line, r.resolved"
        )
        while r.has_next():
            row = r.get_next()
            tables.routes_rows.append(RouteRow(
                id=row[0], kind=row[1], framework=row[2], method=row[3],
                path=row[4], path_template=row[5], path_regex=row[6],
                topic=row[7], broker=row[8], feign_name=row[9], feign_url=row[10],
                microservice=row[11], module=row[12], filename=row[13],
                start_line=row[14], end_line=row[15], resolved=row[16],
            ))
    except Exception:
        pass

    # Clients
    try:
        r = conn.execute(
            "MATCH (c:Client) RETURN c.id, c.client_kind, c.target_service, "
            "c.path, c.path_template, c.path_regex, c.method, "
            "c.member_fqn, c.member_id, c.microservice, c.module, "
            "c.filename, c.start_line, c.end_line, c.resolved, c.source_layer"
        )
        while r.has_next():
            row = r.get_next()
            tables.client_rows.append(ClientRow(
                id=row[0], client_kind=row[1], target_service=row[2],
                path=row[3], path_template=row[4], path_regex=row[5],
                method=row[6], member_fqn=row[7], member_id=row[8],
                microservice=row[9], module=row[10], filename=row[11],
                start_line=row[12], end_line=row[13], resolved=row[14],
                source_layer=row[15],
            ))
    except Exception:
        pass

    # Producers
    try:
        r = conn.execute(
            "MATCH (p:Producer) RETURN p.id, p.producer_kind, p.topic, p.broker, "
            "p.direction, p.member_fqn, p.member_id, p.microservice, p.module, "
            "p.filename, p.start_line, p.end_line, p.resolved, p.source_layer"
        )
        while r.has_next():
            row = r.get_next()
            tables.producer_rows.append(ProducerRow(
                id=row[0], producer_kind=row[1], topic=row[2], broker=row[3],
                direction=row[4], member_fqn=row[5], member_id=row[6],
                microservice=row[7], module=row[8], filename=row[9],
                start_line=row[10], end_line=row[11], resolved=row[12],
                source_layer=row[13],
            ))
    except Exception:
        pass

    # HTTP_CALLS
    try:
        r = conn.execute(
            "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) "
            "RETURN c.id, r.id, e.confidence, e.strategy, "
            "e.method_call, e.raw_uri, e.match"
        )
        while r.has_next():
            row = r.get_next()
            tables.http_call_rows.append(HttpCallRow(
                client_id=row[0], route_id=row[1],
                confidence=row[2], strategy=row[3],
                method_call=row[4], raw_uri=row[5], match=row[6],
            ))
    except Exception:
        pass

    # ASYNC_CALLS
    try:
        r = conn.execute(
            "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) "
            "RETURN p.id, r.id, e.confidence, e.strategy, "
            "e.direction, e.raw_topic, e.match"
        )
        while r.has_next():
            row = r.get_next()
            tables.async_call_rows.append(AsyncCallRow(
                producer_id=row[0], route_id=row[1],
                confidence=row[2], strategy=row[3],
                direction=row[4], raw_topic=row[5], match=row[6],
            ))
    except Exception:
        pass

    # Members (needed for pass6 member_by_id)
    try:
        r = conn.execute(
            "MATCH (s:Symbol) WHERE s.kind = 'method' OR s.kind = 'constructor' "
            "RETURN s.id, s.kind, s.name, s.fqn, s.package, "
            "s.module, s.microservice, s.filename, "
            "s.start_line, s.end_line, s.start_byte, s.end_byte, "
            "s.modifiers, s.annotations, s.signature, s.parent_id, s.resolved"
        )
        while r.has_next():
            row = r.get_next()
            member = MemberEntry(
                kind=row[1],
                decl=MethodDecl(
                    name=row[2],
                    signature=row[14],
                    start_line=row[8],
                    end_line=row[9],
                    start_byte=row[10],
                    end_byte=row[11],
                    is_constructor=(row[1] == "constructor"),
                    modifiers=list(row[12]) if row[12] else [],
                    annotations=[],
                    parameters=[],
                    call_sites=[],
                    routes=[],
                    outgoing_calls=[],
                    local_vars=[],
                ),
                parent_id=row[15],
                parent_fqn=row[3].split("#")[0] if "#" in row[3] else "",
                file_path=row[7],
                module=row[5],
                microservice=row[6],
                node_id=row[0],
            )
            tables.members.append(member)
    except Exception:
        pass

    # EXPOSES
    try:
        r = conn.execute(
            "MATCH (s:Symbol)-[e:EXPOSES]->(r:Route) "
            "RETURN s.id, r.id, e.confidence, e.strategy"
        )
        while r.has_next():
            row = r.get_next()
            tables.exposes_rows.append(ExposesRow(
                symbol_id=row[0], route_id=row[1],
                confidence=row[2], strategy=row[3],
            ))
    except Exception:
        pass

    # DECLARES_CLIENT (for client_hints_by_member in pass6)
    try:
        r = conn.execute(
            "MATCH (s:Symbol)-[e:DECLARES_CLIENT]->(c:Client) "
            "RETURN s.id, c.id, e.confidence, e.strategy"
        )
        while r.has_next():
            row = r.get_next()
            tables.declares_client_rows.append(DeclaresClientRow(
                symbol_id=row[0], client_id=row[1],
                confidence=row[2], strategy=row[3],
            ))
    except Exception:
        pass

    # DECLARES_PRODUCER (for producer_hints_by_member in pass6)
    try:
        r = conn.execute(
            "MATCH (s:Symbol)-[e:DECLARES_PRODUCER]->(p:Producer) "
            "RETURN s.id, p.id, e.confidence, e.strategy"
        )
        while r.has_next():
            row = r.get_next()
            tables.declares_producer_rows.append(DeclaresProducerRow(
                symbol_id=row[0], producer_id=row[1],
                confidence=row[2], strategy=row[3],
            ))
    except Exception:
        pass

    return tables


def _merge_tables(base: GraphTables, partial: GraphTables) -> GraphTables:
    """Merge partial (dirty-file) data into base (remaining from DB).

    Returns a new GraphTables with combined data.  Pass6 requires the full set.
    """
    merged = GraphTables()
    merged.routes_rows = sorted(
        base.routes_rows + partial.routes_rows, key=lambda r: r.id,
    )
    merged.client_rows = sorted(
        base.client_rows + partial.client_rows, key=lambda c: c.id,
    )
    merged.producer_rows = sorted(
        base.producer_rows + partial.producer_rows, key=lambda p: p.id,
    )
    merged.http_call_rows = base.http_call_rows + partial.http_call_rows
    merged.async_call_rows = base.async_call_rows + partial.async_call_rows
    merged.members = base.members + partial.members
    merged.exposes_rows = base.exposes_rows + partial.exposes_rows
    merged.declares_client_rows = base.declares_client_rows + partial.declares_client_rows
    merged.declares_producer_rows = base.declares_producer_rows + partial.declares_producer_rows
    merged.cross_service_resolution = partial.cross_service_resolution or base.cross_service_resolution
    return merged


def _delete_all_http_async_calls(conn: kuzu.Connection) -> None:
    """Delete ALL HTTP_CALLS and ASYNC_CALLS from DB (pre-pass6 rewrite)."""
    try:
        conn.execute("MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route) DELETE e")
    except Exception:
        pass
    try:
        conn.execute("MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route) DELETE e")
    except Exception:
        pass


def _write_call_edges_fresh(conn: kuzu.Connection, tables: GraphTables) -> None:
    """Write ALL HTTP_CALLS and ASYNC_CALLS from tables (after pass6)."""
    for row in tables.http_call_rows:
        conn.execute(_CREATE_HTTP_CALL, {
            "cid": row.client_id, "rid": row.route_id,
            "confidence": row.confidence, "strategy": row.strategy,
            "method_call": row.method_call, "raw_uri": row.raw_uri,
            "match": row.match,
        })
    for row in tables.async_call_rows:
        conn.execute(_CREATE_ASYNC_CALL, {
            "pid": row.producer_id, "rid": row.route_id,
            "confidence": row.confidence, "strategy": row.strategy,
            "direction": row.direction, "raw_topic": row.raw_topic,
            "match": row.match,
        })


def _prune_phantom_routes(conn: kuzu.Connection, tables: GraphTables) -> None:
    """Delete phantom routes that pass6 removed from tables.routes_rows."""
    inbound_ids = {r.route_id for r in tables.http_call_rows} | {r.route_id for r in tables.async_call_rows}
    surviving = {
        r.id for r in tables.routes_rows
        if not (
            r.microservice == ""
            and r.framework == ""
            and not r.resolved
            and r.id not in inbound_ids
        )
    }
    try:
        r = conn.execute("MATCH (r:Route) RETURN r.id")
        db_ids: set[str] = set()
        while r.has_next():
            db_ids.add(r.get_next()[0])
    except Exception:
        return
    for rid in db_ids - surviving:
        # Delete edges first
        try:
            conn.execute(
                "MATCH (s:Symbol)-[e:EXPOSES]->(r:Route {id: $rid}) DELETE e",
                {"rid": rid},
            )
        except Exception:
            pass
        try:
            conn.execute(
                "MATCH (c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid}) DELETE e",
                {"rid": rid},
            )
        except Exception:
            pass
        try:
            conn.execute(
                "MATCH (p:Producer)-[e:ASYNC_CALLS]->(r:Route {id: $rid}) DELETE e",
                {"rid": rid},
            )
        except Exception:
            pass
        try:
            conn.execute("MATCH (r:Route {id: $rid}) DELETE r", {"rid": rid})
        except Exception:
            pass


def _write_meta_incremental(
    conn: kuzu.Connection,
    source_root: Path,
    *,
    verbose: bool,
) -> None:
    """Write GraphMeta by querying live DB for global stats (incremental mode)."""

    def _count(q: str) -> int:
        try:
            r = conn.execute(q)
            return int(r.get_next()[0]) if r.has_next() else 0
        except Exception:
            return 0

    def _count_json(q: str) -> str:
        try:
            r = conn.execute(q)
            d: dict[str, int] = {}
            while r.has_next():
                k, v = r.get_next()
                if k:
                    d[str(k)] = int(v)
            return json.dumps(dict(sorted(d.items())))
        except Exception:
            return "{}"

    routes_total = _count("MATCH (r:Route) RETURN count(r)")
    exposes_total = _count("MATCH ()-[e:EXPOSES]->() RETURN count(e)")
    clients_total = _count("MATCH (c:Client) RETURN count(c)")
    declares_client_total = _count("MATCH ()-[e:DECLARES_CLIENT]->() RETURN count(e)")
    producers_total = _count("MATCH (p:Producer) RETURN count(p)")
    declares_producer_total = _count("MATCH ()-[e:DECLARES_PRODUCER]->() RETURN count(e)")
    http_calls_total = _count("MATCH ()-[e:HTTP_CALLS]->() RETURN count(e)")
    async_calls_total = _count("MATCH ()-[e:ASYNC_CALLS]->() RETURN count(e)")
    packages_total = _count("MATCH (s:Symbol) WHERE s.kind = 'package' RETURN count(s)")
    files_total = _count("MATCH (s:Symbol) WHERE s.kind = 'file' RETURN count(s)")
    types_total = _count(
        "MATCH (s:Symbol) WHERE s.kind IN ['class','interface','enum','record','annotation'] RETURN count(s)"
    )
    members_total = _count(
        "MATCH (s:Symbol) WHERE s.kind IN ['method','constructor'] RETURN count(s)"
    )
    phantoms_total = _count("MATCH (s:Symbol) WHERE s.resolved = false RETURN count(s)")
    extends_total = _count("MATCH ()-[e:EXTENDS]->() RETURN count(e)")
    implements_total = _count("MATCH ()-[e:IMPLEMENTS]->() RETURN count(e)")
    injects_total = _count("MATCH ()-[e:INJECTS]->() RETURN count(e)")
    declares_total = _count("MATCH ()-[e:DECLARES]->() RETURN count(e)")
    overrides_total = _count("MATCH ()-[e:OVERRIDES]->() RETURN count(e)")
    calls_total = _count("MATCH ()-[e:CALLS]->() RETURN count(e)")

    routes_fw = _count_json(
        "MATCH (r:Route) RETURN r.framework AS k, count(r) AS v"
    )
    routes_by_layer = _count_json(
        "MATCH (r:Route) WHERE r.source_layer IS NOT NULL RETURN r.source_layer AS k, count(r) AS v"
    )
    clients_by_kind = _count_json(
        "MATCH (c:Client) RETURN c.client_kind AS k, count(c) AS v"
    )
    producers_by_kind = _count_json(
        "MATCH (p:Producer) RETURN p.producer_kind AS k, count(p) AS v"
    )
    http_by_strategy = _count_json(
        "MATCH ()-[e:HTTP_CALLS]->() RETURN e.strategy AS k, count(e) AS v"
    )
    async_by_strategy = _count_json(
        "MATCH ()-[e:ASYNC_CALLS]->() RETURN e.strategy AS k, count(e) AS v"
    )
    http_match_breakdown = _count_json(
        "MATCH ()-[e:HTTP_CALLS]->() RETURN e.match AS k, count(e) AS v"
    )
    async_match_breakdown = _count_json(
        "MATCH ()-[e:ASYNC_CALLS]->() RETURN e.match AS k, count(e) AS v"
    )

    routes_resolved = _count("MATCH (r:Route) WHERE r.resolved = true RETURN count(r)")
    routes_resolved_pct = (100.0 * routes_resolved / routes_total) if routes_total else 100.0
    brownfield_routes = _count(
        "MATCH (r:Route) WHERE r.source_layer IS NOT NULL AND r.source_layer != 'builtin' RETURN count(r)"
    )
    routes_from_brownfield_pct = (100.0 * brownfield_routes / routes_total) if routes_total else 0.0

    cross_service_total = _count(
        "MATCH ()-[e:HTTP_CALLS]->() WHERE e.match = 'cross_service' RETURN count(e)"
    ) + _count(
        "MATCH ()-[e:ASYNC_CALLS]->() WHERE e.match = 'cross_service' RETURN count(e)"
    )

    http_resolved = _count(
        "MATCH ()-[e:HTTP_CALLS]->() WHERE e.strategy != 'unresolved' RETURN count(e)"
    )
    http_resolved_pct = float(http_resolved) / float(http_calls_total) if http_calls_total else 0.0
    async_resolved = _count(
        "MATCH ()-[e:ASYNC_CALLS]->() WHERE e.strategy != 'unresolved' RETURN count(e)"
    )
    async_resolved_pct = float(async_resolved) / float(async_calls_total) if async_calls_total else 0.0

    http_brownfield = _count(
        "MATCH ()-[e:HTTP_CALLS]->() WHERE e.strategy IN "
        "['layer_b_ann','layer_a_meta','layer_c_source','layer_b_fqn','codebase_client'] "
        "RETURN count(e)"
    )
    http_brownfield_pct = (100.0 * http_brownfield / http_calls_total) if http_calls_total else 0.0
    async_brownfield = _count(
        "MATCH ()-[e:ASYNC_CALLS]->() WHERE e.strategy IN "
        "['layer_b_ann','layer_a_meta','layer_c_source','layer_b_fqn','codebase_producer'] "
        "RETURN count(e)"
    )
    async_brownfield_pct = (100.0 * async_brownfield / async_calls_total) if async_calls_total else 0.0

    counts = {
        "packages": packages_total,
        "files": files_total,
        "types": types_total,
        "members": members_total,
        "phantoms": phantoms_total,
        "extends": extends_total,
        "implements": implements_total,
        "injects": injects_total,
        "declares": declares_total,
        "overrides": overrides_total,
        "calls": calls_total,
        "routes": routes_total,
        "exposes": exposes_total,
        "clients": clients_total,
        "declares_client": declares_client_total,
        "producers": producers_total,
        "declares_producer": declares_producer_total,
        "http_calls": http_calls_total,
        "async_calls": async_calls_total,
    }

    cross_service_resolution = "auto"
    try:
        r = conn.execute(
            "MATCH (m:GraphMeta {key: 'graph'}) RETURN m.cross_service_resolution"
        )
        if r.has_next():
            cross_service_resolution = r.get_next()[0] or "auto"
    except Exception:
        pass

    # Delete old meta before writing new
    conn.execute("MATCH (m:GraphMeta {key: 'graph'}) DELETE m")

    conn.execute(
        "CREATE (:GraphMeta {key: $k, ontology_version: $ov, built_at: $t, "
        "source_root: $sr, counts_json: $cj, parse_errors: $pe, "
        "routes_total: $routes_total, exposes_total: $exposes_total, "
        "routes_by_framework: $routes_by_framework, routes_resolved_pct: $routes_resolved_pct, "
        "routes_from_brownfield_pct: $routes_from_brownfield_pct, routes_by_layer: $routes_by_layer, "
        "clients_total: $clients_total, declares_client_total: $declares_client_total, "
        "clients_by_kind: $clients_by_kind, "
        "producers_total: $producers_total, declares_producer_total: $declares_producer_total, "
        "producers_by_kind: $producers_by_kind, "
        "http_calls_total: $http_calls_total, async_calls_total: $async_calls_total, "
        "http_calls_by_strategy: $http_calls_by_strategy, async_calls_by_strategy: $async_calls_by_strategy, "
        "http_calls_resolved_pct: $http_calls_resolved_pct, async_calls_resolved_pct: $async_calls_resolved_pct, "
        "http_clients_from_brownfield_pct: $http_clients_from_brownfield_pct, "
        "async_producers_from_brownfield_pct: $async_producers_from_brownfield_pct, "
        "http_calls_match_breakdown: $http_calls_match_breakdown, "
        "async_calls_match_breakdown: $async_calls_match_breakdown, "
        "cross_service_calls_total: $cross_service_calls_total, "
        "pass3_skipped_cross_service: $pass3_skipped_cross_service, "
        "pass3_unresolved_phantom_receiver: $pass3_unresolved_phantom_receiver, "
        "pass3_unresolved_chained: $pass3_unresolved_chained, "
        "pass4_exposes_suppressed_feign: $pass4_exposes_suppressed_feign, "
        "cross_service_resolution: $cross_service_resolution, "
        "last_rebuild_mode: $last_rebuild_mode})",
        {
            "k": "graph",
            "ov": ONTOLOGY_VERSION,
            "t": int(time.time()),
            "sr": str(source_root.resolve()),
            "cj": json.dumps(counts),
            "pe": 0,
            "routes_total": routes_total,
            "exposes_total": exposes_total,
            "routes_by_framework": routes_fw,
            "routes_resolved_pct": routes_resolved_pct,
            "routes_from_brownfield_pct": routes_from_brownfield_pct,
            "routes_by_layer": routes_by_layer,
            "clients_total": clients_total,
            "declares_client_total": declares_client_total,
            "clients_by_kind": clients_by_kind,
            "producers_total": producers_total,
            "declares_producer_total": declares_producer_total,
            "producers_by_kind": producers_by_kind,
            "http_calls_total": http_calls_total,
            "async_calls_total": async_calls_total,
            "http_calls_by_strategy": http_by_strategy,
            "async_calls_by_strategy": async_by_strategy,
            "http_calls_resolved_pct": http_resolved_pct,
            "async_calls_resolved_pct": async_resolved_pct,
            "http_clients_from_brownfield_pct": http_brownfield_pct,
            "async_producers_from_brownfield_pct": async_brownfield_pct,
            "http_calls_match_breakdown": http_match_breakdown,
            "async_calls_match_breakdown": async_match_breakdown,
            "cross_service_calls_total": cross_service_total,
            "pass3_skipped_cross_service": 0,
            "pass3_unresolved_phantom_receiver": 0,
            "pass3_unresolved_chained": 0,
            "pass4_exposes_suppressed_feign": 0,
            "cross_service_resolution": cross_service_resolution,
            "last_rebuild_mode": "incremental",
        },
    )


def _node_exists(conn: kuzu.Connection, kind: str, node_id: str) -> bool:
    try:
        r = conn.execute(
            f"MATCH (n:{kind} {{id: $id}}) RETURN count(n)", {"id": node_id}
        )
        return r.has_next() and int(r.get_next()[0]) > 0
    except Exception:
        return False


def _write_nodes_incremental(
    conn: kuzu.Connection,
    tables: GraphTables,
    *,
    project_root: Path,
    meta_chain: dict[str, frozenset[str]] | None,
) -> None:
    """Like _write_nodes but skips nodes whose primary key already exists in DB."""
    overrides = load_brownfield_overrides(project_root)
    try:
        prs = str(project_root.resolve())
    except OSError:
        prs = str(project_root)
    tables.cross_service_resolution = _load_config_cross_service_resolution(prs)

    # Pre-collect existing IDs to avoid per-query overhead
    existing_ids: set[str] = set()
    for kind in ("Symbol", "Route", "Client", "Producer", "UnresolvedCallSite"):
        try:
            r = conn.execute(f"MATCH (n:{kind}) RETURN n.id")
            while r.has_next():
                existing_ids.add(r.get_next()[0])
        except Exception:
            pass

    mch = meta_chain
    for pkg, pid in tables.packages.items():
        if pid not in existing_ids:
            conn.execute(_CREATE_SYMBOL, _node_row(
                id=pid, kind="package", name=pkg.rsplit(".", 1)[-1], fqn=pkg, package=pkg,
            ))
            existing_ids.add(pid)
    for path, fid in tables.files.items():
        if fid not in existing_ids:
            conn.execute(_CREATE_SYMBOL, _node_row(
                id=fid, kind="file", name=Path(path).name, fqn=path, filename=path,
            ))
            existing_ids.add(fid)
    for entry in tables.types.values():
        if entry.node_id in existing_ids:
            continue
        d = entry.decl
        role, capabilities = resolve_role_and_capabilities(
            d, overrides=overrides, meta_chain=mch,
        )
        tables.type_role_by_node_id[entry.node_id] = role
        conn.execute(_CREATE_SYMBOL, _node_row(
            id=entry.node_id, kind=d.kind, name=d.name, fqn=d.fqn,
            package=entry.package, module=entry.module, microservice=entry.microservice,
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
        existing_ids.add(entry.node_id)
    for m in tables.members:
        if m.node_id in existing_ids:
            continue
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
        existing_ids.add(m.node_id)
    for pid, row in tables.phantoms.items():
        if pid not in existing_ids:
            conn.execute(_CREATE_SYMBOL, row)
            existing_ids.add(pid)


def _write_routes_and_exposes_incremental(
    conn: kuzu.Connection, tables: GraphTables,
) -> None:
    """Like _write_routes_and_exposes but skips nodes whose PK already exists."""
    existing_route_ids: set[str] = set()
    try:
        r = conn.execute("MATCH (r:Route) RETURN r.id")
        while r.has_next():
            existing_route_ids.add(r.get_next()[0])
    except Exception:
        pass
    existing_client_ids: set[str] = set()
    try:
        r = conn.execute("MATCH (c:Client) RETURN c.id")
        while r.has_next():
            existing_client_ids.add(r.get_next()[0])
    except Exception:
        pass
    existing_producer_ids: set[str] = set()
    try:
        r = conn.execute("MATCH (p:Producer) RETURN p.id")
        while r.has_next():
            existing_producer_ids.add(r.get_next()[0])
    except Exception:
        pass

    for row in tables.routes_rows:
        if row.id not in existing_route_ids:
            conn.execute(_CREATE_ROUTE, {
                "id": row.id, "kind": row.kind, "framework": row.framework,
                "method": row.method, "path": row.path,
                "path_template": row.path_template, "path_regex": row.path_regex,
                "topic": row.topic, "broker": row.broker,
                "feign_name": row.feign_name, "feign_url": row.feign_url,
                "microservice": row.microservice, "module": row.module,
                "filename": row.filename,
                "start_line": row.start_line, "end_line": row.end_line,
                "resolved": row.resolved,
            })
    for row in tables.exposes_rows:
        conn.execute(_CREATE_EXPOSES, {
            "sid": row.symbol_id, "rid": row.route_id,
            "confidence": row.confidence, "strategy": row.strategy,
        })
    for row in tables.client_rows:
        if row.id not in existing_client_ids:
            conn.execute(_CREATE_CLIENT, asdict(row))
    for row in tables.declares_client_rows:
        conn.execute(_CREATE_DECLARES_CLIENT, {
            "sid": row.symbol_id, "cid": row.client_id,
            "confidence": row.confidence, "strategy": row.strategy,
        })
    for row in tables.producer_rows:
        if row.id not in existing_producer_ids:
            conn.execute(_CREATE_PRODUCER, asdict(row))
    for row in tables.declares_producer_rows:
        conn.execute(_CREATE_DECLARES_PRODUCER, {
            "sid": row.symbol_id, "pid": row.producer_id,
            "confidence": row.confidence, "strategy": row.strategy,
        })


def build_ast_graph_incremental(
    source_root: Path,
    kuzu_path: Path,
    changed_paths: set[str],
    *,
    verbose: bool = False,
) -> str | None:
    """Incremental Kuzu rebuild. Returns None on fallback-needed, "incremental" on success."""
    deps_path = kuzu_path.parent / ".deps.json"
    deps_index = _read_dependency_index(deps_path)
    if deps_index is None:
        if verbose:
            _verbose_stderr_line("[graph] incremental · .deps.json missing or stale, falling back to full")
        return None

    # Heuristic: skip incremental if >50% files are dirty
    dirty = expand_to_closure(changed_paths, deps_index)
    total = len(deps_index.files)
    if total and len(dirty) > 0.5 * total:
        if verbose:
            _verbose_stderr_line(
                f"[graph] incremental · dirty set {len(dirty)}/{total} > 50%, falling back to full"
            )
        return None

    if verbose:
        _verbose_stderr_line(
            f"[graph] incremental · {len(changed_paths)} changed, "
            f"{len(dirty)} after closure expansion"
        )

    db = kuzu.Database(str(kuzu_path))
    conn = kuzu.Connection(db)
    try:
        conn.execute("BEGIN TRANSACTION")
    except Exception:
        # Kuzu may not support explicit transactions; continue without
        pass

    try:
        # Delete dirty-file data from DB
        if verbose:
            _verbose_stderr_line("[graph] incremental · deleting dirty-file data from DB")
        for fp in sorted(dirty):
            counts = delete_all_for_file(conn, fp)
            if verbose:
                dirty_counts = {k: v for k, v in counts.items() if v > 0}
                if dirty_counts:
                    _verbose_stderr_line(f"  {fp}: {dirty_counts}")

        # Run pass1-5 subset
        partial = GraphTables()
        asts = pass1_parse_subset(source_root, dirty, verbose=verbose)

        # Register types (pass1 equivalent)
        for rel_path, ast in asts.items():
            module = module_for_path(str(source_root / rel_path), source_root)
            microservice = microservice_for_path(str(source_root / rel_path), source_root)
            file_id = symbol_id("file", rel_path, rel_path, 0)
            partial.files[rel_path] = file_id
            if ast.package and ast.package not in partial.packages:
                partial.packages[ast.package] = symbol_id("package", ast.package, "", 0)
            for t in ast.top_level_types:
                _register_type(
                    partial, t, file_path=rel_path,
                    module=module, microservice=microservice, outer_fqn=None,
                )

        pass2_edges_subset(partial, asts, dirty, verbose=verbose)
        pass3_calls_subset(partial, asts, dirty, verbose=verbose)
        pass4_routes_subset(partial, asts, dirty, source_root=source_root, verbose=verbose)
        pass5_imperative_edges_subset(partial, dirty, source_root=source_root, verbose=verbose)

        # Load remaining non-dirty data from DB for pass6
        remaining = _load_remaining_from_db(conn, dirty)
        full = _merge_tables(remaining, partial)

        # Run pass6 globally on full data
        pass6_match_edges(full, verbose=verbose)

        # Write partial (dirty-file) data to DB
        meta_chain = collect_annotation_meta_chain(str(source_root.resolve()))
        _write_nodes_incremental(conn, partial, project_root=source_root, meta_chain=meta_chain)
        _populate_declares_rows(partial)
        _populate_overrides_rows(partial)
        _write_edges(conn, partial)
        _write_routes_and_exposes_incremental(conn, partial)

        # Rewrite ALL HTTP_CALLS/ASYNC_CALLS with pass6 outcomes
        _delete_all_http_async_calls(conn)
        _write_call_edges_fresh(conn, full)
        _prune_phantom_routes(conn, full)

        # Write meta (queries live DB for global stats)
        _write_meta_incremental(conn, source_root, verbose=verbose)

        # Merge deps: update dirty entries, preserve unchanged
        new_deps = _build_file_deps(partial, source_root)
        merged_files = dict(deps_index.files)
        for fp, deps in new_deps.items():
            merged_files[fp] = deps
        merged_index = DepsIndex(
            version=_DEPS_VERSION,
            ontology_version=ONTOLOGY_VERSION,
            files=merged_files,
        )
        _write_dependency_index_data(kuzu_path, merged_index)

        try:
            conn.execute("COMMIT")
        except Exception:
            pass
        conn.close()

        if verbose:
            _verbose_stderr_line("[graph] incremental · done")
        return "incremental"

    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        conn.close()
        if verbose:
            _verbose_stderr_line("[graph] incremental · failed, falling back to full rebuild")
        raise


def _create_schema(conn: kuzu.Connection) -> None:
    for stmt in (
        _SCHEMA_NODE,
        _SCHEMA_UNRESOLVED_CALL_SITE,
        _SCHEMA_ROUTE,
        _SCHEMA_CLIENT,
        _SCHEMA_PRODUCER,
        _SCHEMA_META,
        _SCHEMA_EXTENDS,
        _SCHEMA_IMPLEMENTS,
        _SCHEMA_INJECTS,
        _SCHEMA_DECLARES,
        _SCHEMA_OVERRIDES,
        _SCHEMA_CALLS,
        _SCHEMA_UNRESOLVED_AT,
        _SCHEMA_EXPOSES,
        _SCHEMA_DECLARES_CLIENT,
        _SCHEMA_DECLARES_PRODUCER,
        _SCHEMA_HTTP_CALLS,
        _SCHEMA_ASYNC_CALLS,
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
    try:
        prs = str(project_root.resolve())
    except OSError:
        prs = str(project_root)
    tables.cross_service_resolution = _load_config_cross_service_resolution(prs)
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
        tables.type_role_by_node_id[entry.node_id] = role
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
_CREATE_OVERRIDES = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:OVERRIDES]->(b)"
)
_CREATE_CALL = (
    "MATCH (a:Symbol {id: $src}), (b:Symbol {id: $dst}) "
    "CREATE (a)-[:CALLS {"
    "call_site_line: $line, call_site_byte: $byte, arg_count: $argc, "
    "confidence: $conf, strategy: $strat, source: $src_kind, resolved: $resolved, "
    "callee_declaring_role: $callee_declaring_role"
    "}]->(b)"
)

_CREATE_ROUTE = (
    "CREATE (:Route {"
    "id: $id, kind: $kind, framework: $framework, method: $method, "
    "path: $path, path_template: $path_template, path_regex: $path_regex, "
    "topic: $topic, broker: $broker, feign_name: $feign_name, feign_url: $feign_url, "
    "microservice: $microservice, module: $module, filename: $filename, "
    "start_line: $start_line, end_line: $end_line, resolved: $resolved"
    "})"
)
_CREATE_CLIENT = (
    "CREATE (:Client {"
    "id: $id, client_kind: $client_kind, target_service: $target_service, "
    "path: $path, path_template: $path_template, path_regex: $path_regex, method: $method, "
    "member_fqn: $member_fqn, member_id: $member_id, "
    "microservice: $microservice, module: $module, filename: $filename, "
    "start_line: $start_line, end_line: $end_line, resolved: $resolved, source_layer: $source_layer"
    "})"
)

_CREATE_EXPOSES = (
    "MATCH (s:Symbol {id: $sid}), (r:Route {id: $rid}) "
    "CREATE (s)-[:EXPOSES {confidence: $confidence, strategy: $strategy}]->(r)"
)
_CREATE_DECLARES_CLIENT = (
    "MATCH (s:Symbol {id: $sid}), (c:Client {id: $cid}) "
    "CREATE (s)-[:DECLARES_CLIENT {confidence: $confidence, strategy: $strategy}]->(c)"
)
_CREATE_PRODUCER = (
    "CREATE (:Producer {"
    "id: $id, producer_kind: $producer_kind, topic: $topic, broker: $broker, "
    "direction: $direction, member_fqn: $member_fqn, member_id: $member_id, "
    "microservice: $microservice, module: $module, filename: $filename, "
    "start_line: $start_line, end_line: $end_line, resolved: $resolved, "
    "source_layer: $source_layer"
    "})"
)
_CREATE_DECLARES_PRODUCER = (
    "MATCH (s:Symbol {id: $sid}), (p:Producer {id: $pid}) "
    "CREATE (s)-[:DECLARES_PRODUCER {confidence: $confidence, strategy: $strategy}]->(p)"
)
_CREATE_HTTP_CALL = (
    "MATCH (c:Client {id: $cid}), (r:Route {id: $rid}) "
    "CREATE (c)-[:HTTP_CALLS {confidence: $confidence, strategy: $strategy, "
    "method_call: $method_call, raw_uri: $raw_uri, match: $match}]->(r)"
)
_CREATE_ASYNC_CALL = (
    "MATCH (p:Producer {id: $pid}), (r:Route {id: $rid}) "
    "CREATE (p)-[:ASYNC_CALLS {confidence: $confidence, strategy: $strategy, "
    "direction: $direction, raw_topic: $raw_topic, match: $match}]->(r)"
)


def _populate_declares_rows(tables: GraphTables) -> None:
    tables.declares_rows = [
        DeclaresRow(src_id=m.parent_id, dst_id=m.node_id) for m in tables.members
    ]


def _direct_supertype_ids(tables: GraphTables, type_id: str) -> list[str]:
    out: list[str] = []
    for r in tables.extends_rows:
        if r.src_id == type_id:
            out.append(r.dst_id)
    for r in tables.implements_rows:
        if r.src_id == type_id:
            out.append(r.dst_id)
    return out


def _populate_overrides_rows(tables: GraphTables) -> None:
    """Materialize (subtype_method)-[:OVERRIDES]->(supertype_method) for one supertype hop.

    Matches ``KuzuGraph.override_axis_rollup_for`` (direct ``IMPLEMENTS`` / ``EXTENDS``
    only, same ``signature``, distinct method ids, non-static instance methods).
    """
    by_declaring_type: dict[str, list[MemberEntry]] = defaultdict(list)
    for m in tables.members:
        by_declaring_type[m.parent_id].append(m)
    pairs: set[tuple[str, str]] = set()
    for m in tables.members:
        if m.kind != "method" or "static" in m.decl.modifiers:
            continue
        impl_tid = m.parent_id
        for sup_id in _direct_supertype_ids(tables, impl_tid):
            for other in by_declaring_type.get(sup_id, ()):
                if other.kind != "method":
                    continue
                if other.decl.signature != m.decl.signature:
                    continue
                if other.node_id == m.node_id:
                    continue
                pairs.add((m.node_id, other.node_id))
    tables.overrides_rows = [
        DeclaresRow(src_id=a, dst_id=b) for a, b in sorted(pairs)
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

    for row in tables.overrides_rows:
        conn.execute(_CREATE_OVERRIDES, {"src": row.src_id, "dst": row.dst_id})

    seen_calls: set[tuple[str, str, int, int]] = set()
    unique_calls: list[CallsRow] = []
    for row in tables.calls_rows:
        key = (row.src_id, row.dst_id, row.arg_count, row.call_site_line)
        if key not in seen_calls:
            seen_calls.add(key)
            unique_calls.append(row)

    member_by_id = {m.node_id: m for m in tables.members}
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
            "callee_declaring_role": _callee_declaring_role_at_write(
                tables, row.dst_id, member_by_id=member_by_id,
            ),
        })

    _CREATE_UNRESOLVED = (
        "CREATE (:UnresolvedCallSite {"
        "id: $id, caller_id: $caller_id, call_site_line: $line, call_site_byte: $byte, "
        "arg_count: $argc, callee_simple: $callee, receiver_expr: $recv, reason: $reason"
        "})"
    )
    _CREATE_UNRESOLVED_AT = (
        "MATCH (a:Symbol {id: $caller}), (u:UnresolvedCallSite {id: $ucs}) "
        "CREATE (a)-[:UNRESOLVED_AT]->(u)"
    )
    seen_ucs: set[str] = set()
    for row in tables.unresolved_call_site_rows:
        if row.id in seen_ucs:
            continue
        seen_ucs.add(row.id)
        conn.execute(_CREATE_UNRESOLVED, {
            "id": row.id,
            "caller_id": row.caller_id,
            "line": row.call_site_line,
            "byte": row.call_site_byte,
            "argc": row.arg_count,
            "callee": row.callee_simple,
            "recv": row.receiver_expr,
            "reason": row.reason,
        })
        conn.execute(_CREATE_UNRESOLVED_AT, {"caller": row.caller_id, "ucs": row.id})


def _write_routes_and_exposes(conn: kuzu.Connection, tables: GraphTables) -> None:
    for row in tables.routes_rows:
        conn.execute(_CREATE_ROUTE, {
            "id": row.id,
            "kind": row.kind,
            "framework": row.framework,
            "method": row.method,
            "path": row.path,
            "path_template": row.path_template,
            "path_regex": row.path_regex,
            "topic": row.topic,
            "broker": row.broker,
            "feign_name": row.feign_name,
            "feign_url": row.feign_url,
            "microservice": row.microservice,
            "module": row.module,
            "filename": row.filename,
            "start_line": row.start_line,
            "end_line": row.end_line,
            "resolved": row.resolved,
        })
    for row in tables.exposes_rows:
        conn.execute(_CREATE_EXPOSES, {
            "sid": row.symbol_id,
            "rid": row.route_id,
            "confidence": row.confidence,
            "strategy": row.strategy,
        })
    for row in tables.client_rows:
        conn.execute(_CREATE_CLIENT, asdict(row))
    for row in tables.declares_client_rows:
        conn.execute(_CREATE_DECLARES_CLIENT, {
            "sid": row.symbol_id,
            "cid": row.client_id,
            "confidence": row.confidence,
            "strategy": row.strategy,
        })
    for row in tables.producer_rows:
        conn.execute(_CREATE_PRODUCER, asdict(row))
    for row in tables.declares_producer_rows:
        conn.execute(_CREATE_DECLARES_PRODUCER, {
            "sid": row.symbol_id,
            "pid": row.producer_id,
            "confidence": row.confidence,
            "strategy": row.strategy,
        })
    for row in tables.http_call_rows:
        conn.execute(_CREATE_HTTP_CALL, {
            "cid": row.client_id,
            "rid": row.route_id,
            "confidence": row.confidence,
            "strategy": row.strategy,
            "method_call": row.method_call,
            "raw_uri": row.raw_uri,
            "match": row.match,
        })
    for row in tables.async_call_rows:
        conn.execute(_CREATE_ASYNC_CALL, {
            "pid": row.producer_id,
            "rid": row.route_id,
            "confidence": row.confidence,
            "strategy": row.strategy,
            "direction": row.direction,
            "raw_topic": row.raw_topic,
            "match": row.match,
        })


def _write_meta(conn: kuzu.Connection, tables: GraphTables, source_root: Path) -> None:
    seen_calls: set[tuple[str, str, int, int]] = set()
    calls_unique = 0
    for row in tables.calls_rows:
        key = (row.src_id, row.dst_id, row.arg_count, row.call_site_line)
        if key not in seen_calls:
            seen_calls.add(key)
            calls_unique += 1
    st = tables.route_stats
    routes_fw = dict(sorted(st.by_framework.items()))
    call_stats = tables.call_edge_stats
    client_stats = tables.client_stats
    producer_stats = tables.producer_stats
    http_by_strategy = dict(sorted(call_stats.http_calls_by_strategy.items()))
    async_by_strategy = dict(sorted(call_stats.async_calls_by_strategy.items()))
    http_match = dict(sorted(call_stats.http_calls_match_breakdown.items()))
    async_match = dict(sorted(call_stats.async_calls_match_breakdown.items()))
    http_resolved_pct = 0.0
    async_resolved_pct = 0.0
    if call_stats.http_calls_total:
        # PR-D1 definition: "resolved_pct" is strategy-based (strategy != 'unresolved'),
        # not match-based (all PR-D1 edges keep match='unresolved').
        resolved_http = sum(v for k, v in call_stats.http_calls_by_strategy.items() if k != "unresolved")
        http_resolved_pct = float(resolved_http) / float(call_stats.http_calls_total)
    if call_stats.async_calls_total:
        resolved_async = sum(v for k, v in call_stats.async_calls_by_strategy.items() if k != "unresolved")
        async_resolved_pct = float(resolved_async) / float(call_stats.async_calls_total)
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
        "overrides": len(tables.overrides_rows),
        "calls": calls_unique,
        "routes": len(tables.routes_rows),
        "exposes": len(tables.exposes_rows),
        "clients": len(tables.client_rows),
        "declares_client": len(tables.declares_client_rows),
        "producers": len(tables.producer_rows),
        "declares_producer": len(tables.declares_producer_rows),
        "http_calls": len(tables.http_call_rows),
        "async_calls": len(tables.async_call_rows),
    }
    routes_layer = dict(sorted(st.routes_by_layer.items()))
    clients_by_kind = dict(sorted(client_stats.clients_by_kind.items()))
    producers_by_kind = dict(sorted(producer_stats.producers_by_kind.items()))
    conn.execute(
        "CREATE (:GraphMeta {key: $k, ontology_version: $ov, built_at: $t, "
        "source_root: $sr, counts_json: $cj, parse_errors: $pe, "
        "routes_total: $routes_total, exposes_total: $exposes_total, "
        "routes_by_framework: $routes_by_framework, routes_resolved_pct: $routes_resolved_pct, "
        "routes_from_brownfield_pct: $routes_from_brownfield_pct, routes_by_layer: $routes_by_layer, "
        "clients_total: $clients_total, declares_client_total: $declares_client_total, "
        "clients_by_kind: $clients_by_kind, "
        "producers_total: $producers_total, declares_producer_total: $declares_producer_total, "
        "producers_by_kind: $producers_by_kind, "
        "http_calls_total: $http_calls_total, async_calls_total: $async_calls_total, "
        "http_calls_by_strategy: $http_calls_by_strategy, async_calls_by_strategy: $async_calls_by_strategy, "
        "http_calls_resolved_pct: $http_calls_resolved_pct, async_calls_resolved_pct: $async_calls_resolved_pct, "
        "http_clients_from_brownfield_pct: $http_clients_from_brownfield_pct, "
        "async_producers_from_brownfield_pct: $async_producers_from_brownfield_pct, "
        "http_calls_match_breakdown: $http_calls_match_breakdown, "
        "async_calls_match_breakdown: $async_calls_match_breakdown, "
        "cross_service_calls_total: $cross_service_calls_total, "
        "pass3_skipped_cross_service: $pass3_skipped_cross_service, "
        "pass3_unresolved_phantom_receiver: $pass3_unresolved_phantom_receiver, "
        "pass3_unresolved_chained: $pass3_unresolved_chained, "
        "pass4_exposes_suppressed_feign: $pass4_exposes_suppressed_feign, "
        "cross_service_resolution: $cross_service_resolution, "
        "last_rebuild_mode: $last_rebuild_mode})",
        {
            "k": "graph",
            "ov": ONTOLOGY_VERSION,
            "t": int(time.time()),
            "sr": str(source_root.resolve()),
            "cj": json.dumps(counts),
            "pe": tables.parse_errors,
            "routes_total": len(tables.routes_rows),
            "exposes_total": len(tables.exposes_rows),
            "routes_by_framework": json.dumps(routes_fw),
            "routes_resolved_pct": float(st.routes_resolved_pct),
            "routes_from_brownfield_pct": float(st.routes_from_brownfield_pct),
            "routes_by_layer": json.dumps(routes_layer),
            "clients_total": int(client_stats.clients_total),
            "declares_client_total": int(client_stats.declares_client_total),
            "clients_by_kind": json.dumps(clients_by_kind),
            "producers_total": int(producer_stats.producers_total),
            "declares_producer_total": int(producer_stats.declares_producer_total),
            "producers_by_kind": json.dumps(producers_by_kind),
            "http_calls_total": call_stats.http_calls_total,
            "async_calls_total": call_stats.async_calls_total,
            "http_calls_by_strategy": json.dumps(http_by_strategy),
            "async_calls_by_strategy": json.dumps(async_by_strategy),
            "http_calls_resolved_pct": http_resolved_pct,
            "async_calls_resolved_pct": async_resolved_pct,
            "http_clients_from_brownfield_pct": call_stats.http_clients_from_brownfield_pct,
            "async_producers_from_brownfield_pct": call_stats.async_producers_from_brownfield_pct,
            "http_calls_match_breakdown": json.dumps(http_match),
            "async_calls_match_breakdown": json.dumps(async_match),
            "cross_service_calls_total": int(call_stats.cross_service_calls_total),
            "pass3_skipped_cross_service": int(tables.pass3_skipped_cross_service),
            "pass3_unresolved_phantom_receiver": int(tables.pass3_unresolved_phantom_receiver),
            "pass3_unresolved_chained": int(tables.pass3_unresolved_chained),
            "pass4_exposes_suppressed_feign": int(st.exposes_suppressed_feign),
            "cross_service_resolution": str(tables.cross_service_resolution),
            "last_rebuild_mode": "full",
        },
    )


# ---------- dependency index (sidecar .deps.json) ----------


def _build_file_deps(tables: GraphTables, source_root: Path) -> dict[str, FileDeps]:
    """Build per-file dependency metadata from GraphTables for the sidecar index."""
    # node_id -> file_path lookup
    node_file: dict[str, str] = {}
    for entry in tables.types.values():
        node_file[entry.node_id] = entry.file_path
    for m in tables.members:
        node_file[m.node_id] = m.file_path

    # node_id -> identifier (type FQN or "TypeFQN#method()" for members)
    node_fqn: dict[str, str] = {}
    for entry in tables.types.values():
        node_fqn[entry.node_id] = entry.decl.fqn
    for m in tables.members:
        node_fqn[m.node_id] = f"{m.parent_fqn}#{m.decl.signature}"

    deps: dict[str, FileDeps] = {}
    for fp in tables.files:
        deps[fp] = FileDeps()

    # ext_hash — read file from disk and hash
    for fp in deps:
        full = source_root / fp
        try:
            raw = full.read_bytes()
            deps[fp].ext_hash = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        except OSError:
            deps[fp].ext_hash = ""

    # declares — type FQNs declared in this file
    for entry in tables.types.values():
        fp = entry.file_path
        if fp in deps:
            deps[fp].declares.append(entry.outer_fqn or entry.decl.fqn)

    # injects — FQNs of injected symbols (from injects_rows)
    for row in tables.injects_rows:
        fp = node_file.get(row.src_id)
        if fp and fp in deps:
            deps[fp].injects.append(row.dst_fqn)

    # extends — FQNs of extended types (from extends_rows)
    for row in tables.extends_rows:
        fp = node_file.get(row.src_id)
        if fp and fp in deps:
            deps[fp].extends.append(row.dst_fqn)

    # calls — callee identifiers (method/type FQNs from calls_rows)
    for row in tables.calls_rows:
        fp = node_file.get(row.src_id)
        if fp and fp in deps:
            callee = node_fqn.get(row.dst_id)
            if callee is not None:
                deps[fp].calls.append(callee)

    # uses_anno — annotation simple names from types + members (deduplicated)
    for entry in tables.types.values():
        fp = entry.file_path
        if fp in deps:
            for anno in entry.decl.annotations:
                if anno.name not in deps[fp].uses_anno:
                    deps[fp].uses_anno.append(anno.name)
    for m in tables.members:
        fp = m.file_path
        if fp in deps:
            for anno in m.decl.annotations:
                if anno.name not in deps[fp].uses_anno:
                    deps[fp].uses_anno.append(anno.name)

    # overrides — overridden method FQNs (from overrides_rows)
    for row in tables.overrides_rows:
        fp = node_file.get(row.src_id)
        if fp and fp in deps:
            overridden = node_fqn.get(row.dst_id)
            if overridden is not None:
                deps[fp].overrides.append(overridden)

    # declares_clients — member FQNs declaring HTTP clients
    for row in tables.client_rows:
        fp = row.filename
        if fp in deps:
            deps[fp].declares_clients.append(row.member_fqn)

    # declares_producers — member FQNs declaring async producers
    for row in tables.producer_rows:
        fp = row.filename
        if fp in deps:
            deps[fp].declares_producers.append(row.member_fqn)

    return deps


_DEPS_VERSION = 1


def _write_dependency_index(
    db_path: Path,
    tables: GraphTables,
    source_root: Path,
) -> None:
    """Write sidecar .deps.json alongside the Kuzu database."""
    deps = _build_file_deps(tables, source_root)
    idx = DepsIndex(
        version=_DEPS_VERSION,
        ontology_version=ONTOLOGY_VERSION,
        files=deps,
    )
    _write_dependency_index_data(db_path, idx)


def _write_dependency_index_data(db_path: Path, idx: DepsIndex) -> None:
    """Write a pre-built DepsIndex to the sidecar .deps.json."""
    payload = {
        "version": idx.version,
        "ontology_version": idx.ontology_version,
        "files": {fp: asdict(d) for fp, d in sorted(idx.files.items())},
    }
    deps_path = db_path.parent / ".deps.json"
    tmp = deps_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.rename(deps_path)


@dataclass
class DepsIndex:
    version: int
    ontology_version: int
    files: dict[str, FileDeps]


def _read_dependency_index(deps_path: Path) -> DepsIndex | None:
    """Read and validate sidecar .deps.json. Returns None on missing/corrupt/stale."""
    if not deps_path.is_file():
        return None
    try:
        raw = json.loads(deps_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    try:
        if raw.get("version") != _DEPS_VERSION:
            return None
        if raw.get("ontology_version") != ONTOLOGY_VERSION:
            return None
        files: dict[str, FileDeps] = {}
        for fp, obj in raw.get("files", {}).items():
            files[fp] = FileDeps(
                ext_hash=obj.get("ext_hash", ""),
                declares=obj.get("declares", []),
                injects=obj.get("injects", []),
                extends=obj.get("extends", []),
                calls=obj.get("calls", []),
                uses_anno=obj.get("uses_anno", []),
                overrides=obj.get("overrides", []),
                declares_clients=obj.get("declares_clients", []),
                declares_producers=obj.get("declares_producers", []),
            )
    except Exception:
        return None
    return DepsIndex(
        version=raw["version"],
        ontology_version=raw["ontology_version"],
        files=files,
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
    if verbose:
        _verbose_stderr_line(_WRITE_START)
    with _VerbosePassHeartbeats("[graph] writing", verbose=verbose):
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
            _verbose_stderr_line(f"[graph] writing · nodes written in {time.time() - t0:.2f}s")
        _populate_declares_rows(tables)
        _populate_overrides_rows(tables)
        t1 = time.time()
        _write_edges(conn, tables)
        if verbose:
            _verbose_stderr_line(f"[graph] writing · edges written in {time.time() - t1:.2f}s")
        t2 = time.time()
        _write_routes_and_exposes(conn, tables)
        if verbose:
            _verbose_stderr_line(f"[graph] writing · routes/exposes written in {time.time() - t2:.2f}s")
        _write_meta(conn, tables, source_root)
        conn.close()
        _write_dependency_index(db_path, tables, source_root)


# ---------- CLI ----------


def _default_kuzu_path() -> Path:
    idx = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if idx and not idx.startswith(("s3://", "gs://", "az://")):
        return Path(os.path.expanduser(idx.rstrip("/"))) / "code_graph.kuzu"
    return Path.cwd() / ".java-codebase-rag" / "code_graph.kuzu"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an AST-derived Kuzu graph for Java sources.")
    parser.add_argument("--source-root", default=None, help="Repository / monorepo root to scan for .java (defaults to current working directory)")
    parser.add_argument(
        "--kuzu-path",
        default=None,
        help=(
            "Kuzu database path (file/dir as used by kuzu.Database; "
            "default: $JAVA_CODEBASE_RAG_INDEX_DIR/code_graph.kuzu or ./.java-codebase-rag/code_graph.kuzu)"
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--changed-paths",
        default=None,
        help=(
            "Path to a file containing newline-separated changed file paths "
            "(internal flag for incremental rebuild)"
        ),
    )
    args = parser.parse_args()

    root = Path(args.source_root).expanduser().resolve() if args.source_root else Path.cwd().resolve()
    if not root.is_dir():
        print(f"source-root not a directory: {root}", file=sys.stderr)
        return 2

    kuzu_path = Path(args.kuzu_path).expanduser() if args.kuzu_path else _default_kuzu_path()

    if args.changed_paths:
        # Incremental rebuild mode
        cp_file = Path(args.changed_paths)
        if not cp_file.is_file():
            print(f"changed-paths file not found: {cp_file}", file=sys.stderr)
            return 2
        changed = set(
            line.strip() for line in cp_file.read_text().splitlines() if line.strip()
        )
        if not changed:
            if args.verbose:
                _verbose_stderr_line("[graph] · empty changed-paths, falling back to full rebuild")
            # Fall through to full rebuild
        else:
            try:
                result = build_ast_graph_incremental(
                    root, kuzu_path, changed, verbose=args.verbose,
                )
                if result is not None:
                    if args.verbose:
                        _verbose_stderr_line(f"[graph] done · incremental · kuzu at {kuzu_path}")
                    return 0
                if args.verbose:
                    _verbose_stderr_line("[graph] · incremental declined, falling back to full rebuild")
            except Exception as exc:
                if args.verbose:
                    _verbose_stderr_line(f"[graph] · incremental failed ({exc}), falling back to full rebuild")

    # Full rebuild
    tables = GraphTables()
    asts = pass1_parse(root, tables, verbose=args.verbose)
    pass2_edges(tables, asts, verbose=args.verbose)
    pass3_calls(tables, asts, verbose=args.verbose)
    pass4_routes(tables, asts, source_root=root, verbose=args.verbose)
    pass5_imperative_edges(tables, asts, source_root=root, verbose=args.verbose)
    pass6_match_edges(tables, verbose=args.verbose)
    write_kuzu(kuzu_path, tables, source_root=root, verbose=args.verbose)
    if args.verbose:
        _verbose_stderr_line(f"[graph] done · kuzu at {kuzu_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
