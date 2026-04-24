"""CLI: full rebuild of Java AST graph into embedded Kuzu."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from java_ast_graph.extract import extract_file
from java_ast_graph.kuzu_io import default_db_path, init_fresh_db, load_facts
from java_ast_graph.paths import iter_java_files, resolve_source_roots_from_env
from java_ast_graph.resolve import build_edges, build_registry


def run_build(
    *,
    db_path: Path | None = None,
    roots: list[tuple[str, Path]] | None = None,
    quiet: bool = False,
) -> int:
    db_path = db_path or default_db_path()
    roots = roots or resolve_source_roots_from_env()
    files = iter_java_files(roots)
    facts = []
    for label, root, fp in files:
        f = extract_file(fp, label, root)
        facts.append(f)
        if f.error and not quiet:
            print(f"warn: {fp}: {f.error}", file=sys.stderr)

    reg = build_registry(facts)
    edges = build_edges(facts, reg)
    conn = init_fresh_db(db_path)
    try:
        load_facts(conn, facts, reg, edges)
    finally:
        conn.close()
    if not quiet:
        print(
            f"java_ast_graph: wrote {db_path} "
            f"(types={len(reg.fqns)}, files={len({f.file_key for f in facts if not f.error})}, "
            f"extends={len(edges.extends)}, implements={len(edges.implements)}, injects={len(edges.injects)})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build Kuzu Java AST graph (DKB two-pass).")
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Kuzu database path (default: env KUZU_DB_PATH or ./kuzu_java_graph)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Less output.",
    )
    args = p.parse_args(argv)
    if args.db:
        os.environ["KUZU_DB_PATH"] = str(args.db.resolve())
    return run_build(db_path=args.db, quiet=args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
