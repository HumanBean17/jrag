"""Kotlin AST extraction on top of tree-sitter (tree-sitter-kotlin PyPI 1.1.0).

Task 5 foundation: parse a single ``.kt`` compilation unit's package and
imports into the existing ``JavaFileAst`` shape (defined in ``ast_java.py``).
The per-thread ``Parser`` TLS mirrors ``ast_java.py``'s idiom verbatim because
``parse_kotlin`` is called from the same concurrent worker threads as
``parse_java`` (cocoindex inflight parallelism via ``asyncio.to_thread``).

Declarations (classes / functions / properties) land in Task 6; this task
returns empty ``top_level_types`` / ``all_types`` lists — by design, not by
oversight. ``file_imports.static_methods`` / ``static_wildcards`` stay empty
because Kotlin has no ``import static``.

Grammar-node facts confirmed by probing the installed 1.1.0 binary (NOT the
``fwcd`` grammar; this is the restructured PyPI grammar):

* Root: ``source_file``.
* Package: a top-level ``package_header`` whose child ``qualified_identifier``
  is the dotted path. (There is no ``package_directive``.)
* Imports: top-level ``import`` nodes (not ``import_declaration``); dotted
  path is child ``qualified_identifier``; a wildcard ends the
  ``qualified_identifier`` text with ``.*`` (the ``.`` and ``*`` are unnamed
  siblings after it); an alias is a trailing ``identifier`` sibling after the
  ``as`` keyword.
* Names are ``identifier`` everywhere — there is no ``simple_identifier`` or
  ``type_identifier`` in 1.1.0.
"""
from __future__ import annotations

import threading

import tree_sitter_kotlin as _ts_kotlin
from tree_sitter import Language, Node, Parser

from java_codebase_rag.ast.ast_java import FileImports, JavaFileAst

__all__ = ["parse_kotlin"]

# tree-sitter's ``Parser`` mutates internal state during ``parse()`` and is NOT
# thread-safe, so each OS thread gets its own instance. Mirrors ``ast_java.py``'s
# ``_parser_tls`` / ``_parser()`` exactly. The ``Language`` is immutable and
# shared; per-thread ``Parser`` construction is lazy and cheap (once per thread).
_parser_tls = threading.local()


def _parser() -> Parser:
    p = getattr(_parser_tls, "parser", None)
    if p is None:
        _parser_tls.parser = p = Parser(Language(_ts_kotlin.language()))
    return p


def _txt(node: Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def parse_kotlin(source: bytes | str, *, filename: str = "", verbose: bool = False) -> JavaFileAst:
    """Parse a Kotlin file into a ``JavaFileAst``. Never raises on invalid source.

    Populates ``package``, ``imports``, ``wildcard_imports``,
    ``explicit_imports``, and ``file_imports``; tags ``language="kotlin"``;
    sets ``parse_error`` from the tree-sitter error flag. Returns empty
    ``top_level_types`` / ``all_types`` (declarations are Task 6).
    """
    del verbose  # accepted for signature parity with JavaBackend.parse; no brownfield events yet.

    if isinstance(source, str):
        src = source.encode("utf-8", errors="replace")
    else:
        src = source

    empty = JavaFileAst(
        package="",
        imports=[],
        wildcard_imports=[],
        explicit_imports={},
        top_level_types=[],
        all_types=[],
        language="kotlin",
        parse_error=False,
        source_bytes=len(src),
        file_imports=FileImports(),
        routes_skipped_unresolved=0,
    )

    if not src:
        return empty

    try:
        tree = _parser().parse(src)
    except Exception:
        empty.parse_error = True
        return empty

    root = tree.root_node
    package = ""
    imports: list[str] = []
    wildcard_imports: list[str] = []
    explicit_imports: dict[str, str] = {}

    for child in root.named_children:
        t = child.type
        if t == "package_header":
            for c in child.named_children:
                if c.type == "qualified_identifier":
                    package = _txt(c, src)
                    break
        elif t == "import":
            qi: Node | None = None
            alias: Node | None = None
            has_wild = False
            for c in child.children:
                if c.type == "qualified_identifier":
                    qi = c
                elif c.type == "identifier":
                    # The trailing alias (`import a.B as Q`); the only named
                    # `identifier` sibling is the alias — the path is the
                    # `qualified_identifier` sibling.
                    alias = c
                elif c.type == "*":
                    has_wild = True
            if qi is None:
                continue
            fqn = _txt(qi, src)
            if has_wild:
                imports.append(f"{fqn}.*")
                wildcard_imports.append(fqn)
            else:
                if alias is not None:
                    key = _txt(alias, src)
                    imports.append(f"{fqn} as {key}")
                else:
                    key = fqn.rsplit(".", 1)[-1]
                    imports.append(fqn)
                explicit_imports[key] = fqn

    file_imports = FileImports(
        explicit=explicit_imports,
        # Kotlin has no `import static`: static_methods / static_wildcards stay empty.
    )
    return JavaFileAst(
        package=package,
        imports=imports,
        wildcard_imports=wildcard_imports,
        explicit_imports=explicit_imports,
        top_level_types=[],
        all_types=[],
        language="kotlin",
        parse_error=root.has_error,
        source_bytes=len(src),
        file_imports=file_imports,
        routes_skipped_unresolved=0,
    )
