"""Create / replace embedded Kuzu database and bulk MERGE DKB data."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import kuzu

from java_ast_graph.extract import FileFact, TypeFact
from java_ast_graph.resolve import GraphEdges, SymbolRegistry
from java_ast_graph.schema import SCHEMA_DDL


def _remove_db_path(db_path: Path) -> None:
    if not db_path.exists():
        return
    if db_path.is_dir():
        shutil.rmtree(db_path)
    else:
        db_path.unlink()


def open_connection(db_path: Path) -> tuple[kuzu.Database, kuzu.Connection]:
    """Open or create Kuzu (embedded)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = kuzu.Database(str(db_path))
    return db, kuzu.Connection(db)


def init_fresh_db(db_path: Path) -> kuzu.Connection:
    """Delete existing database files at path, create schema, return connection."""
    if db_path.exists():
        _remove_db_path(db_path)
    db, conn = open_connection(db_path)
    for ddl in SCHEMA_DDL:
        conn.execute(ddl)
    return conn


def _merge_file(conn: kuzu.Connection, ff: FileFact) -> None:
    conn.execute(
        "MERGE (f:File {file_key: $k, rel_path: $r, module_root: $m, module_label: $l})",
        {
            "k": ff.file_key,
            "r": ff.rel_path,
            "m": ff.module_root,
            "l": ff.module_label,
        },
    )


def _merge_package(conn: kuzu.Connection, pid: str) -> None:
    if not pid:
        return
    conn.execute("MERGE (p:Package {pid: $p})", {"p": pid})


def _package_id(fqn: str) -> str:
    if "." not in fqn:
        return ""
    return fqn.rsplit(".", 1)[0]


def _merge_type(conn: kuzu.Connection, t: TypeFact) -> None:
    pid = _package_id(t.fqn) if t.fqn and "." in t.fqn else ""
    conn.execute(
        "MERGE (x:Type {"
        "fqn: $fqn, kind: $kind, simple_name: $sn, package_id: $pkg, file_key: $fk})",
        {
            "fqn": t.fqn,
            "kind": t.kind,
            "sn": t.simple_name,
            "pkg": pid,
            "fk": t.file_key,
        },
    )


def _merge_method(
    conn: kuzu.Connection,
    type_fqn: str,
    file_key: str,
    m,
) -> None:
    mid = f"{type_fqn}#{m.name}#{m.start_line}"
    conn.execute(
        "MERGE (m:Method {"
        "mid: $mid, name: $n, type_fqn: $tf, file_key: $fk, start_line: $sl, is_constructor: $ic})",
        {
            "mid": mid,
            "n": m.name,
            "tf": type_fqn,
            "fk": file_key,
            "sl": m.start_line,
            "ic": m.is_constructor,
        },
    )


def load_facts(
    conn: kuzu.Connection,
    file_facts: list[FileFact],
    _reg: SymbolRegistry,
    edges: GraphEdges,
) -> None:
    packages: set[str] = set()
    for ff in file_facts:
        if ff.error:
            continue
        for t in ff.types:
            p = _package_id(t.fqn) if t.fqn and "." in t.fqn else ""
            if p:
                packages.add(p)
    for p in sorted(packages):
        _merge_package(conn, p)

    for ff in file_facts:
        if ff.error:
            continue
        _merge_file(conn, ff)

    for ff in file_facts:
        if ff.error:
            continue
        for t in ff.types:
            _merge_type(conn, t)
            for m in t.methods:
                _merge_method(conn, t.fqn, t.file_key, m)
            _link_type_to_file_and_pkg(conn, t)

    for e0, e1, _r in edges.extends:
        _merge_extends(conn, e0, e1)
    for e0, e1, _r in edges.implements:
        _merge_implements(conn, e0, e1)
    for e0, e1, _r in edges.injects:
        _merge_injects(conn, e0, e1)
    link_methods_to_types(conn, file_facts)


def _link_type_to_file_and_pkg(conn: kuzu.Connection, t: TypeFact) -> None:
    conn.execute(
        "MATCH (a:Type {fqn: $fqn}), (b:File {file_key: $fk}) "
        "CREATE (a)-[:F_DECLARED_IN]->(b)",
        {"fqn": t.fqn, "fk": t.file_key},
    )
    pid = _package_id(t.fqn) if t.fqn and "." in t.fqn else ""
    if pid:
        conn.execute(
            "MATCH (a:Type {fqn: $fqn}), (b:Package {pid: $p}) "
            "CREATE (a)-[:T_IN_PACKAGE]->(b)",
            {"fqn": t.fqn, "p": pid},
        )


def _merge_extends(conn: kuzu.Connection, from_f: str, to_f: str) -> None:
    conn.execute(
        "MATCH (a:Type {fqn: $a}), (b:Type {fqn: $b}) "
        "CREATE (a)-[:T_EXTENDS {resolved: $r}]->(b)",
        {"a": from_f, "b": to_f, "r": True},
    )


def _merge_implements(conn: kuzu.Connection, from_f: str, to_f: str) -> None:
    conn.execute(
        "MATCH (a:Type {fqn: $a}), (b:Type {fqn: $b}) "
        "CREATE (a)-[:T_IMPLEMENTS {resolved: $r}]->(b)",
        {"a": from_f, "b": to_f, "r": True},
    )


def _merge_injects(conn: kuzu.Connection, from_f: str, to_f: str) -> None:
    conn.execute(
        "MATCH (a:Type {fqn: $a}), (b:Type {fqn: $b}) "
        "CREATE (a)-[:T_INJECTS {resolved: $r}]->(b)",
        {"a": from_f, "b": to_f, "r": True},
    )


def link_methods_to_types(conn: kuzu.Connection, file_facts: list[FileFact]) -> None:
    for ff in file_facts:
        if ff.error:
            continue
        for t in ff.types:
            for m in t.methods:
                mid = f"{t.fqn}#{m.name}#{m.start_line}"
                conn.execute(
                    "MATCH (a:Type {fqn: $tf}), (b:Method {mid: $m}) "
                    "CREATE (a)-[:M_DECLARED {kind: $k}]->(b)",
                    {
                        "tf": t.fqn,
                        "m": mid,
                        "k": "constructor" if m.is_constructor else "method",
                    },
                )


def default_db_path() -> Path:
    raw = os.environ.get("KUZU_DB_PATH", "./kuzu_java_graph")
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
