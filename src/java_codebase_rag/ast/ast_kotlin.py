"""Kotlin AST extraction on top of tree-sitter (tree-sitter-kotlin PyPI 1.1.0).

Task 5 foundation: parse a single ``.kt`` compilation unit's package and
imports into the existing ``JavaFileAst`` shape (defined in ``ast_java.py``).
The per-thread ``Parser`` TLS mirrors ``ast_java.py``'s idiom verbatim because
``parse_kotlin`` is called from the same concurrent worker threads as
``parse_java`` (cocoindex inflight parallelism via ``asyncio.to_thread``).

Task 6 walks Kotlin type declarations into ``TypeDecl`` rows using the
**folded kind map** — Kotlin kinds reuse the five existing Java
``_TYPE_KINDS`` strings (``class``/``interface``/``enum``/``record``/
``annotation``); no new kind strings are introduced and ``_TYPE_KINDS`` in
``ast_java.py`` is NOT extended. Members (fields/methods) and top-level
functions land in later tasks.

``file_imports.static_methods`` / ``static_wildcards`` stay empty because
Kotlin has no ``import static``.

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
* Type declarations: ``class Foo``, ``interface Bar``, ``enum class E``,
  ``annotation class Ann``, ``data class D`` ALL parse as
  ``class_declaration`` — you DISCRIMINATE the kind via (a) an anonymous
  ``interface`` keyword child → ``interface``; (b) ``modifiers >
  class_modifier`` whose text is ``enum`` / ``annotation`` / ``data`` →
  ``enum`` / ``annotation`` / ``record`` respectively; otherwise ``class``.
  Other ``class_modifier`` values (``sealed``, ``value``, ``inline``) and
  ``inheritance_modifier`` values (``abstract``, ``final``, …) fold to
  ``class``.
* ``object Singleton`` → ``object_declaration`` → kind ``class``.
* ``companion object { … }`` → ``companion_object`` (a DISTINCT node, not a
  modifier): name in optional child ``identifier`` (default ``Companion``);
  becomes a NESTED ``TypeDecl`` under its enclosing type.
* Body is ``class_body`` (or ``enum_class_body`` for enums); nested
  ``class_declaration``/``object_declaration``/``companion_object`` live in
  the body and are attached to the parent's ``nested`` list.
"""
from __future__ import annotations

import threading

import tree_sitter_kotlin as _ts_kotlin
from tree_sitter import Language, Node, Parser

from java_codebase_rag.ast.ast_java import FileImports, JavaFileAst, TypeDecl

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


# Kotlin declaration nodes that map to a ``TypeDecl``. NOTE: ``_TYPE_KINDS`` in
# ``ast_java.py`` is intentionally NOT extended — Kotlin kinds fold into the
# existing five Java kind strings via ``_kotlin_class_kind``.
_KOTLIN_TYPE_NODES: frozenset[str] = frozenset(
    {"class_declaration", "object_declaration", "companion_object"}
)

# ``class_modifier`` values that override the default ``class`` fold. Everything
# else (``sealed``, ``value``, ``inline``, inheritance modifiers like
# ``abstract``/``final``/``open``) folds to ``class`` — DTO/singleton inference
# is unaffected and modifiers are captured in Task 7.
_CLASS_MODIFIER_TO_KIND: dict[str, str] = {
    "enum": "enum",
    "annotation": "annotation",
    "data": "record",  # the non-obvious fold: Kotlin data class ≈ Java record (DTO inference).
}


def _kotlin_class_kind(node: Node, src: bytes) -> str:
    """Fold a ``class_declaration`` into one of the five Java kind strings.

    Discriminator (verified by probing tree-sitter-kotlin 1.1.0):

    * an anonymous ``interface`` keyword child (literal token, not a named
      node) → ``interface``;
    * otherwise scan ``modifiers > class_modifier`` text for ``enum`` /
      ``annotation`` / ``data`` → ``enum`` / ``annotation`` / ``record``;
    * otherwise → ``class``.
    """
    # `interface Foo` exposes `interface` as an anonymous literal-keyword child.
    for c in node.children:
        if not c.is_named and c.type == "interface":
            return "interface"
    for c in node.named_children:
        if c.type != "modifiers":
            continue
        for mc in c.named_children:
            if mc.type == "class_modifier":
                mod = _txt(mc, src)
                if mod in _CLASS_MODIFIER_TO_KIND:
                    return _CLASS_MODIFIER_TO_KIND[mod]
    return "class"


def _kotlin_decl_name(node: Node, src: bytes) -> str:
    """Type name from the ``identifier`` child (companion defaults to 'Companion')."""
    for c in node.named_children:
        if c.type == "identifier":
            return _txt(c, src)
    return "Companion"  # unnamed `companion object { … }`.


def _parse_kotlin_type(
    node: Node,
    src: bytes,
    *,
    package: str,
    outer_fqn: str | None,
    all_types: list[TypeDecl],
) -> TypeDecl | None:
    """Build a ``TypeDecl`` for a Kotlin type declaration node.

    Recurses into the declaration's body (``class_body`` / ``enum_class_body``)
    for nested ``class_declaration`` / ``object_declaration`` /
    ``companion_object`` nodes, attaching each to the parent's ``nested`` list
    and appending every type (including nested) to ``all_types`` in pre-order.

    Members (fields / methods / extends / implements / annotations / modifiers)
    arrive in later tasks; they stay as the ``TypeDecl`` defaults here.
    """
    t = node.type
    if t == "class_declaration":
        kind = _kotlin_class_kind(node, src)
    elif t in ("object_declaration", "companion_object"):
        kind = "class"
    else:
        return None

    name = _kotlin_decl_name(node, src)
    if outer_fqn:
        fqn = f"{outer_fqn}.{name}"
    elif package:
        fqn = f"{package}.{name}"
    else:
        fqn = name

    nested: list[TypeDecl] = []
    decl = TypeDecl(
        name=name,
        kind=kind,
        fqn=fqn,
        nested=nested,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        outer_fqn=outer_fqn,
    )
    all_types.append(decl)

    body: Node | None = None
    for c in node.named_children:
        if c.type in ("class_body", "enum_class_body"):
            body = c
            break
    if body is not None:
        for ch in body.named_children:
            if ch.type in _KOTLIN_TYPE_NODES:
                child_decl = _parse_kotlin_type(
                    ch, src, package=package, outer_fqn=fqn, all_types=all_types
                )
                if child_decl is not None:
                    nested.append(child_decl)
    return decl


def parse_kotlin(source: bytes | str, *, filename: str = "", verbose: bool = False) -> JavaFileAst:
    """Parse a Kotlin file into a ``JavaFileAst``. Never raises on invalid source.

    Populates ``package``, ``imports``, ``wildcard_imports``,
    ``explicit_imports``, and ``file_imports``; tags ``language="kotlin"``;
    sets ``parse_error`` from the tree-sitter error flag. Walks top-level
    type declarations (``class_declaration`` / ``object_declaration``) into
    ``top_level_types`` with the folded kind map; ``all_types`` is the flat
    pre-order list including nested types (``companion_object`` / nested
    ``class_declaration``). Members (fields/methods) arrive in a later task.
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

    # Walk top-level type declarations (class_declaration / object_declaration /
    # companion_object) into TypeDecl rows with the folded kind map. Members
    # and top-level functions arrive in later tasks.
    top_level_types: list[TypeDecl] = []
    all_types: list[TypeDecl] = []
    for child in root.named_children:
        if child.type in _KOTLIN_TYPE_NODES:
            decl = _parse_kotlin_type(
                child, src, package=package, outer_fqn=None, all_types=all_types
            )
            if decl is not None:
                top_level_types.append(decl)

    return JavaFileAst(
        package=package,
        imports=imports,
        wildcard_imports=wildcard_imports,
        explicit_imports=explicit_imports,
        top_level_types=top_level_types,
        all_types=all_types,
        language="kotlin",
        parse_error=root.has_error,
        source_bytes=len(src),
        file_imports=file_imports,
        routes_skipped_unresolved=0,
    )
