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
``ast_java.py`` is NOT extended.

Task 7 populates members: ``function_declaration``/``secondary_constructor``
→ ``MethodDecl``; ``property_declaration`` and ``val``/``var``
``class_parameter`` (primary-constructor properties) → ``FieldDecl`` **plus**
synthesized JVM-accessor ``MethodDecl`` s so cross-language CALLS resolve
(accessors are the only way Java code touches a Kotlin property). Modifier
vocabulary is emitted into the shared ``modifiers`` list using Java literals
(``static``/``final``) plus Kotlin-only ride-alongs (``suspend`` etc.) — the
graph builder reads ``"static" in m.decl.modifiers`` / ``"final" in ...``
directly, so no separate field. Top-level functions/properties land on a
synthetic facade ``TypeDecl`` ``<Basename>Kt`` tagged
``capabilities=["kotlin_facade"]``.

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

import os
import threading

import tree_sitter_kotlin as _ts_kotlin
from tree_sitter import Language, Node, Parser

from java_codebase_rag.ast.ast_java import (
    AnnotationRef,
    CallSite,
    FieldDecl,
    FileImports,
    JavaFileAst,
    MethodDecl,
    ParamDecl,
    TypeDecl,
)

__all__ = ["parse_kotlin", "merge_multifile_facades"]

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


# ---- Task 7: members ----

# Tree-sitter-kotlin 1.1.0 node types that carry a type annotation. ``identifier``
# is NOT in this set — names are plain ``identifier`` nodes (1.1.0 has no
# ``simple_identifier``/``type_identifier``).
_TYPE_NODE_TYPES: frozenset[str] = frozenset(
    {"user_type", "nullable_type", "function_type"}
)

# Kotlin ``function_modifier`` / ``member_modifier`` tokens that ride along in
# the shared ``modifiers`` list for fidelity (no graph consumer checks them).
# ``inheritance_modifier`` open/abstract/final map to the Java ``final`` rule
# below; ``visibility_modifier`` is consumed for accessor decisions only.
_KOTLIN_INHERITANCE_NON_FINAL: frozenset[str] = frozenset({"open", "abstract"})


def _simple_type_name(node: Node | None, src: bytes) -> str:
    """Simple name from a type-annotation node (``user_type`` / ``nullable_type``).

    ``String?`` → ``String``; ``com.foo.Bar`` → ``Bar``; ``List<String>`` →
    ``List``. Returns ``""`` for absent / unrecognised type nodes.
    """
    if node is None:
        return ""
    if node.type == "nullable_type":
        node = next(
            (c for c in node.named_children if c.type == "user_type"), None
        )
        if node is None:
            return ""
    if node.type == "user_type":
        ids = [c for c in node.named_children if c.type == "identifier"]
        return _txt(ids[-1], src) if ids else ""
    return ""


def _type_child(node: Node) -> Node | None:
    """The type-annotation child (``user_type``/``nullable_type``/``function_type``)."""
    for c in node.named_children:
        if c.type in _TYPE_NODE_TYPES:
            return c
    return None


def _has_anon_keyword(node: Node, keyword: str) -> bool:
    """True if ``node`` has an anonymous literal child (e.g. ``var``/``val``)."""
    for c in node.children:
        if not c.is_named and c.type == keyword:
            return True
    return False


def _collect_kotlin_modifiers(node: Node, src: bytes) -> dict:
    """Walk the ``modifiers`` child of a member node and return raw modifier info.

    The 1.1.0 grammar wraps every modifier in ONE ``modifiers`` container whose
    typed sub-containers (``visibility_modifier``/``inheritance_modifier``/
    ``function_modifier``/``member_modifier``/``property_modifier``) each hold a
    single anonymous keyword token — so the sub-container's own text IS the
    keyword (e.g. ``_txt(function_modifier_node) == "suspend"``).
    """
    info: dict = {
        "visibility": None,  # "private"/"public"/"protected"/"internal" or None (default public).
        "inheritance": [],   # open/abstract/final
        "function": [],      # suspend/inline/operator/infix/tailrec/external
        "member": [],        # override/lateinit
        "const": False,
    }
    mods_node = next(
        (c for c in node.named_children if c.type == "modifiers"), None
    )
    if mods_node is None:
        return info
    for mc in mods_node.named_children:
        txt = _txt(mc, src)
        if mc.type == "visibility_modifier":
            info["visibility"] = txt
        elif mc.type == "inheritance_modifier":
            info["inheritance"].append(txt)
        elif mc.type == "function_modifier":
            info["function"].append(txt)
        elif mc.type == "member_modifier":
            info["member"].append(txt)
        elif mc.type == "property_modifier":
            if txt == "const":
                info["const"] = True
            else:
                info["member"].append(txt)
        # ``class_modifier`` is not relevant for members; ignored.
    return info


def _build_member_modifiers(
    info: dict, *, is_static: bool, is_final: bool
) -> list[str]:
    """Build the shared ``modifiers`` list: Java vocab + Kotlin ride-alongs.

    Java vocabulary (the only tokens the graph builder reads):
    * ``"static"`` — companion-object member, top-level facade member, or ``const``.
    * ``"final"`` — Kotlin ``fun``/``val`` default; omitted for ``open``/``abstract``.
    Kotlin-only tokens (``suspend``/``inline``/``operator``/``override``/…) ride
    along in source order; no consumer checks them, but fidelity is preserved.
    Visibility keywords are NOT emitted (used only for accessor decisions).
    """
    mods: list[str] = []
    if is_static:
        mods.append("static")
    if is_final:
        mods.append("final")
    for kw in info["function"]:
        if kw not in mods:
            mods.append(kw)
    for kw in info["member"]:
        if kw not in mods:
            mods.append(kw)
    return mods


# ---- Task 8: annotations (with use-site targets) ----
#
# Grammar (tree-sitter-kotlin 1.1.0), confirmed by probing:
# * An ``annotation`` (singular — NO ``annotations`` plural wrapper) sits inside
#   the ``modifiers`` container of the type / property / function / class_parameter.
# * A function ``parameter``'s annotations live in a SIBLING ``parameter_modifiers``
#   node inside ``function_value_parameters`` (different parent from the property /
#   ctor-param case, which uses ``modifiers`` on the node itself).
# * No-arg:      ``annotation > user_type > identifier``
#   With-args:   ``annotation > constructor_invocation > (user_type, value_arguments)``
# * Use-site target: ``annotation > use_site_target > (field|get|set|param|property,
#   :)`` — the target word is an anonymous token; read the ``use_site_target`` text
#   and strip the trailing ``:``.
# * Annotation simple name = last ``identifier`` of the ``user_type``; qualified =
#   raw text of the ``user_type``.

# Use-site target keywords recognised by the grammar. ``file`` is a file-level
# target handled in Task 9 (file annotations); recorded here for completeness.
_KOTLIN_USE_SITE_TARGETS: frozenset[str] = frozenset(
    {"field", "get", "set", "param", "property", "file"}
)


def _kotlin_use_site_target(ann_node: Node, src: bytes) -> str | None:
    """Read ``annotation > use_site_target`` text (e.g. ``param:`` → ``"param"``)."""
    ust = next(
        (c for c in ann_node.named_children if c.type == "use_site_target"), None
    )
    if ust is None:
        return None
    # ``use_site_target`` text is e.g. ``param:``; strip the trailing colon.
    return _txt(ust, src).rstrip(":").strip() or None


def _kotlin_annotation_value_arguments(
    ann_node: Node, src: bytes
) -> tuple[dict[str, str], dict[str, str]]:
    """Extract ``arguments`` / ``argument_kinds`` from a Kotlin annotation.

    Mirrors the Java arg-extraction shape (``ast_java._parse_annotation_argument_list``)
    but walks Kotlin nodes (``constructor_invocation > value_arguments > value_argument``).
    String literals → kind ``"string"``; enum-like identifiers → ``"enum"``;
    ``collection_literal`` of strings → comma-joined, kind ``"string"``. A bare
    positional argument is keyed under ``"value"``.
    """
    args: dict[str, str] = {}
    kinds: dict[str, str] = {}
    ci = next(
        (c for c in ann_node.named_children if c.type == "constructor_invocation"),
        None,
    )
    if ci is None:
        return args, kinds
    va = next(
        (c for c in ci.named_children if c.type == "value_arguments"), None
    )
    if va is None:
        return args, kinds
    for varg in va.named_children:
        if varg.type != "value_argument":
            continue
        # Named arg: ``key = value`` (an ``identifier`` child + anonymous ``=``).
        key = "value"
        value_node: Node | None = None
        named_key = next(
            (c for c in varg.named_children if c.type == "identifier"), None
        )
        has_eq = any(c.type == "=" for c in varg.children)
        if named_key is not None and has_eq:
            key = _txt(named_key, src)
            # value is the named child after the ``=``
            for c in varg.named_children:
                if c is not named_key:
                    value_node = c
                    break
        else:
            # positional: the first named child that isn't the key
            value_node = named_key if named_key is not None and not has_eq else None
            if value_node is None:
                for c in varg.named_children:
                    value_node = c
                    break
        if value_node is None:
            continue
        val, kind = _kotlin_annotation_value(value_node, src)
        if val is None or kind is None:
            continue
        if key not in args:  # first-wins, mirroring Java positional behaviour
            args[key] = val
            kinds[key] = kind
    return args, kinds


def _kotlin_annotation_value(node: Node, src: bytes) -> tuple[str | None, str | None]:
    """(value, kind) for a Kotlin annotation argument expression.

    Returns one of ``("string")`` / ``("enum")`` kinds:
    * ``string_literal`` → ``string_content`` text, ``"string"``;
    * ``collection_literal`` → comma-joined string-literal children, ``"string"``;
    * ``identifier``/``scoped_identifier``/``field_access``/``callable_reference``
      → last segment, ``"enum"``.
    """
    if node.type == "string_literal":
        for ch in node.named_children:
            if ch.type == "string_content":
                return _txt(ch, src), "string"
        return None, None
    if node.type == "collection_literal":
        parts: list[str] = []
        for ch in node.named_children:
            if ch.type == "string_literal":
                for gc in ch.named_children:
                    if gc.type == "string_content":
                        parts.append(_txt(gc, src))
        if parts:
            return ",".join(parts), "string"
        return None, None
    if node.type in ("identifier", "scoped_identifier", "field_access"):
        raw = _txt(node, src).strip()
        if not raw:
            return None, None
        return raw.rsplit(".", 1)[-1], "enum"
    if node.type == "callable_reference":
        # ``Foo::bar`` → receiver ``Foo``; treat the whole text as enum-like.
        raw = _txt(node, src).strip()
        return (raw.rsplit(".", 1)[-1] if raw else None), "enum"
    return None, None


def _kotlin_annotation_name(
    ann_node: Node, src: bytes
) -> tuple[str, str]:
    """(simple, qualified) from ``annotation > [constructor_invocation >] user_type``."""
    user_type = next(
        (c for c in ann_node.named_children if c.type == "user_type"), None
    )
    if user_type is None:
        ci = next(
            (c for c in ann_node.named_children if c.type == "constructor_invocation"),
            None,
        )
        if ci is not None:
            user_type = next(
                (c for c in ci.named_children if c.type == "user_type"), None
            )
    if user_type is None:
        return "", ""
    ids = [c for c in user_type.named_children if c.type == "identifier"]
    simple = _txt(ids[-1], src) if ids else ""
    return simple, _txt(user_type, src)


def _parse_kotlin_annotation(ann_node: Node, src: bytes) -> AnnotationRef:
    """Build an ``AnnotationRef`` from a Kotlin ``annotation`` node."""
    simple, qualified = _kotlin_annotation_name(ann_node, src)
    args, arg_kinds = _kotlin_annotation_value_arguments(ann_node, src)
    return AnnotationRef(
        name=simple,
        qualified=qualified or simple,
        arguments=args,
        argument_kinds=arg_kinds,
        use_site_target=_kotlin_use_site_target(ann_node, src),
    )


def _kotlin_annotations_from_modifiers(node: Node, src: bytes) -> list[AnnotationRef]:
    """All ``annotation`` children of ``node``'s ``modifiers`` container."""
    mods_node = next(
        (c for c in node.named_children if c.type == "modifiers"), None
    )
    if mods_node is None:
        return []
    return [
        _parse_kotlin_annotation(c, src)
        for c in mods_node.named_children
        if c.type == "annotation"
    ]


# The four annotation-routing slots. ``None`` (no explicit target) routes to the
# caller's chosen ``default_slot``: ``"field"`` for a body property, ``"param"``
# for a primary-constructor parameter (the dominant Spring-Kotlin DI pattern —
# ``@Autowired val r: Repo`` defaults to constructor injection).
_ANN_SLOTS: tuple[str, ...] = ("field", "get", "set", "param")


def _route_kotlin_annotations_by_target(
    anns: list[AnnotationRef], default_slot: str
) -> dict[str, list[AnnotationRef]]:
    """Bucket annotations by ``use_site_target``.

    ``"field"`` / ``"property"`` → ``field``; ``"get"``/``"set"``/``"param"`` →
    themselves; ``None`` / anything else → ``default_slot`` (one of ``_ANN_SLOTS``).
    """
    buckets: dict[str, list[AnnotationRef]] = {s: [] for s in _ANN_SLOTS}
    for a in anns:
        t = a.use_site_target
        if t in ("get", "set", "param"):
            buckets[t].append(a)
        elif t in ("field", "property"):
            buckets["field"].append(a)
        else:
            buckets[default_slot].append(a)
    return buckets


def _attach_targeted_annotations_to_accessors(
    accessors: list[MethodDecl],
    get_anns: list[AnnotationRef],
    set_anns: list[AnnotationRef],
) -> None:
    """Attach get-/set-targeted annotations to the synthesized accessor MethodDecls.

    ``accessors[0]`` is the getter; ``accessors[1]`` (when present) is the setter.
    Privates synthesize no accessors; their get/set annotations are dropped (the
    accessors are not exposed on the JVM surface we model).
    """
    if accessors:
        accessors[0].annotations.extend(get_anns)
    if len(accessors) > 1:
        accessors[1].annotations.extend(set_anns)


def _cap(name: str) -> str:
    """JVM capitalisation: first char uppercased, rest unchanged."""
    return name[:1].upper() + name[1:] if name else name


def _accessor_method_decls(
    prop_name: str,
    type_simple: str,
    is_var: bool,
    info: dict,
    *,
    is_static: bool,
) -> list[MethodDecl]:
    """Synthesize JVM-accessor MethodDecl(s) for a non-private Kotlin property.

    Matches Kotlin's actual JVM codegen (the keyword Java callers use):
    * Boolean property already named ``is*`` → getter keeps the name
      (``isActive`` → ``isActive()``); setter drops the ``is`` (``setActive``).
    * everything else → ``get`` + Name-capitalised (``name`` → ``getName()``;
      Boolean ``foo`` → ``getFoo()``, NOT ``isFoo()``).
    Setter only for ``var``. Getter is always final; setter is not.
    """
    is_bool_is_prefixed = (
        type_simple == "Boolean"
        and prop_name.startswith("is")
        and len(prop_name) > 2
    )
    if is_bool_is_prefixed:
        getter_name = prop_name
        setter_name = "set" + _cap(prop_name[2:])
    else:
        getter_name = "get" + _cap(prop_name)
        setter_name = "set" + _cap(prop_name)

    out: list[MethodDecl] = [
        MethodDecl(
            name=getter_name,
            return_type=type_simple,
            is_constructor=False,
            parameters=[],
            signature=f"{getter_name}()",
            modifiers=_build_member_modifiers(info, is_static=is_static, is_final=True),
        )
    ]
    if is_var:
        out.append(
            MethodDecl(
                name=setter_name,
                return_type="",
                is_constructor=False,
                parameters=[
                    ParamDecl(
                        name="value", type_name=type_simple, type_raw=type_simple
                    )
                ],
                signature=f"{setter_name}({type_simple})",
                modifiers=_build_member_modifiers(
                    info, is_static=is_static, is_final=False
                ),
            )
        )
    return out


def _function_value_parameters(node: Node | None) -> Node | None:
    return next(
        (c for c in (node.named_children if node is not None else [])
         if c.type == "function_value_parameters"),
        None,
    )


def _params_from_function_value_parameters(
    fv_params: Node | None, src: bytes
) -> list[ParamDecl]:
    """``function_value_parameters > parameter > (identifier, type)`` → ParamDecl list.

    A parameter's annotations live in a preceding SIBLING ``parameter_modifiers``
    node (NOT inside ``parameter``); they attach to the ParamDecl with their
    ``use_site_target`` preserved (function params have no field/getter/setter —
    every target, including ``None``, lands on the ParamDecl).
    """
    params: list[ParamDecl] = []
    if fv_params is None:
        return params
    pending_anns: list[AnnotationRef] = []
    saw_param = False
    for c in fv_params.named_children:
        if c.type == "parameter_modifiers":
            # Accumulate; applies to the next ``parameter`` sibling.
            if saw_param:
                pending_anns = []
                saw_param = False
            pending_anns.extend(
                _parse_kotlin_annotation(mc, src)
                for mc in c.named_children
                if mc.type == "annotation"
            )
            continue
        if c.type != "parameter":
            continue
        name = ""
        for pc in c.named_children:
            if pc.type == "identifier":
                name = _txt(pc, src)
                break
        type_node = _type_child(c)
        type_simple = _simple_type_name(type_node, src)
        params.append(
            ParamDecl(
                name=name,
                type_name=type_simple,
                type_raw=_txt(type_node, src) if type_node is not None else type_simple,
                annotations=list(pending_anns),
            )
        )
        pending_anns = []
        saw_param = True
    return params


# ---- Task 10: call-site extraction + constructor delegation ----
#
# Grammar (tree-sitter-kotlin 1.1.0), confirmed by probing:
# * Receiver call ``r.find(1)``:
#   ``call_expression > (navigation_expression > [identifier 'r', ., identifier 'find'],
#   value_arguments > value_argument)``. The ``navigation_expression`` LEFT SPINE
#   (everything before the final ``.``) is the receiver; the LAST ``identifier``
#   is the callee.
# * Chained call ``repo.findById(1).orElse(null)`` nests: the outer
#   ``navigation_expression``'s left spine is itself a ``call_expression``; the
#   walker recurses and emits one CallSite per ``call_expression``.
# * Constructor call ``Other(2)`` (no ``new`` in Kotlin):
#   ``call_expression > (identifier 'Other', value_arguments)`` — callee target
#   is a bare ``identifier`` with NO navigation receiver. Recognised as a
#   constructor ONLY when that identifier names a known type (explicit import or
#   same-CU declaration) — the capitalised-first-letter heuristic is rejected.
# * Bare receiverless call ``helper()`` has the SAME node shape as a constructor
#   call (``call_expression > (identifier, value_arguments)``); discrimination is
#   by the type-name set: known type → constructor, unknown → bare method call
#   (Task 13 resolves against the file facade).
# * Method reference ``obj::foo`` parses as a ``navigation_expression`` whose
#   separator is ``::`` (NOT ``.``) — so a standalone ``navigation_expression``
#   with a ``::`` child is a method reference (arg_count = -1).
#   ``a.b()::foo`` is a ``navigation_expression > (call_expression, ::, identifier)``;
#   ``chained_method_reference`` is True when the spine is a ``call_expression``.
# * Trailing-lambda call ``foo(1) { it }`` wraps as
#   ``call_expression > (call_expression 'foo(1)', annotated_lambda)`` — the
#   wrapper has no clean callee and is skipped; recursing hits the inner
#   ``call_expression`` which emits. ``list.map { it.foo() }`` is
#   ``call_expression > (navigation_expression 'list.map', annotated_lambda)``
#   → emits ``map`` (arg_count 0, no ``value_arguments``) and, inside the lambda,
#   ``foo`` with ``in_lambda=True``.
# * Constructor delegation:
#   - class header ``class D : Base(7)`` →
#     ``class_declaration > delegation_specifiers > delegation_specifier >
#     constructor_invocation > (user_type, value_arguments)``. When there is no
#     explicit ``primary_constructor`` node, an implicit one is synthesised to
#     carry the super-call site.
#   - secondary ``constructor(...) : this(0)`` →
#     ``secondary_constructor > constructor_delegation_call > (this|super,
#     value_arguments)``.


def _value_argument_count(call_or_invocation: Node) -> int:
    """Number of ``value_argument`` children under the node's ``value_arguments``.

    Returns 0 when there is no ``value_arguments`` child (e.g. a trailing-lambda
    call with no parenthesised args).
    """
    va = next(
        (c for c in call_or_invocation.named_children if c.type == "value_arguments"),
        None,
    )
    if va is None:
        return 0
    return sum(1 for c in va.named_children if c.type == "value_argument")


def _split_dot_navigation(nav: Node, src: bytes) -> tuple[str, str]:
    """Split a ``.``-``navigation_expression`` into (receiver_text, callee).

    The receiver is the raw text from the navigation start up to (not including)
    the LAST ``.`` separator; the callee is the last ``identifier`` child. For
    ``r.find`` → (``"r"``, ``"find"``); for ``foo.bar.baz`` → (``"foo.bar"``,
    ``"baz"``); for ``repo.findById(1).orElse`` → (``"repo.findById(1)"``,
    ``"orElse"``). Returns (``""``, ``""``) if no ``identifier`` callee is found.
    """
    ids = [c for c in nav.named_children if c.type == "identifier"]
    if not ids:
        return "", ""
    callee = _txt(ids[-1], src)
    # The last '.' separator (unnamed) marks the receiver/callee boundary.
    dot = next(
        (c for c in nav.children if not c.is_named and c.type == "."), None
    )
    if dot is not None:
        recv = src[nav.start_byte:dot.start_byte].decode("utf-8", errors="replace")
    else:
        recv = ""  # defensive: a '.'-navigation always has a '.', but stay safe.
    return recv, callee


def _navigation_is_method_reference(nav: Node) -> bool:
    """A ``navigation_expression`` is a method reference when it uses ``::``."""
    return any(not c.is_named and c.type == "::" for c in nav.children)


def _collect_kotlin_call_sites(
    body: Node | None,
    src: bytes,
    *,
    caller_fqn: str,
    type_names: frozenset[str],
) -> list[CallSite]:
    """Walk a function/constructor body and collect raw ``CallSite`` records.

    Faithful capture only (Task 10): receiver/callee split, constructor vs bare
    discrimination via the type-name set, arg counts (``-1`` for ``::`` refs),
    ``in_lambda``, ``chained_method_reference``. Static-certainty and facade-call
    resolution are deferred to Task 13; ``is_static_call`` is best-effort True
    only when the receiver of a non-constructor call matches a known type name.
    """
    out: list[CallSite] = []
    if body is None:
        return out

    def emit(site: CallSite) -> None:
        out.append(site)

    def visit(n: Node, lam: bool) -> None:
        t = n.type
        # Lambda body: descend with the in-lambda flag set on every nested call.
        if t in ("lambda_literal", "lambda_expression"):
            for ch in n.children:
                visit(ch, True)
            return
        if t == "call_expression":
            _emit_call_expression_site(n, src, lam=lam, caller_fqn=caller_fqn,
                                       type_names=type_names, emit=emit)
            for ch in n.children:  # recurse for nested calls in receiver/args/lambda
                visit(ch, lam)
            return
        if t == "navigation_expression":
            # Standalone navigation (not the callee-target of a call_expression).
            if _navigation_is_method_reference(n):
                _emit_method_reference_site(n, src, lam=lam,
                                            caller_fqn=caller_fqn, emit=emit)
            # Either way, descend (spine may itself contain call_expressions).
            for ch in n.children:
                visit(ch, lam)
            return
        for ch in n.children:
            visit(ch, lam)

    visit(body, False)
    return out


def _emit_call_expression_site(
    n: Node,
    src: bytes,
    *,
    lam: bool,
    caller_fqn: str,
    type_names: frozenset[str],
    emit,
) -> None:
    """Emit one ``CallSite`` for a ``call_expression`` (if it has a clear callee).

    Shapes:
    * ``call_expression > (navigation_expression, value_arguments [, annotated_lambda])``
      → receiver call.
    * ``call_expression > (identifier, value_arguments)`` → constructor call if
      the identifier is a known type, else a bare receiverless method call.
    * ``call_expression > (call_expression, annotated_lambda)`` → trailing-lambda
      wrapper with no own callee; skip (the inner call_expression emits on recurse).
    """
    named = n.named_children
    if not named:
        return
    first = named[0]
    # Trailing-lambda wrapper: callee lives on the inner call_expression.
    if first.type == "call_expression":
        return
    line = n.start_point[0] + 1
    byte = n.start_byte
    if first.type == "navigation_expression":
        recv, callee = _split_dot_navigation(first, src)
        if not callee:
            return
        # Best-effort static-call flag: receiver text matches a known type name.
        is_static = recv != "" and recv in type_names
        emit(
            CallSite(
                caller_fqn=caller_fqn,
                receiver_expr=recv,
                callee_simple=callee,
                arg_count=_value_argument_count(n),
                is_static_call=is_static,
                is_constructor=False,
                in_lambda=lam,
                line=line,
                byte=byte,
            )
        )
        return
    if first.type == "identifier":
        name = _txt(first, src)
        argc = _value_argument_count(n)
        if name in type_names:
            # Constructor call: ``Other(2)`` where Other is a known type.
            emit(
                CallSite(
                    caller_fqn=caller_fqn,
                    receiver_expr=name,
                    callee_simple="<init>",
                    arg_count=argc,
                    is_static_call=False,
                    is_constructor=True,
                    in_lambda=lam,
                    line=line,
                    byte=byte,
                )
            )
        else:
            # Bare receiverless method call (Task 13 resolves via facade).
            emit(
                CallSite(
                    caller_fqn=caller_fqn,
                    receiver_expr="",
                    callee_simple=name,
                    arg_count=argc,
                    is_static_call=False,
                    is_constructor=False,
                    in_lambda=lam,
                    line=line,
                    byte=byte,
                )
            )
        return
    # Other callee shapes (e.g. ``super_expression``/``this_expression`` direct)
    # have no clean method callee; skip — recursing still visits their children.


def _emit_method_reference_site(
    n: Node, src: bytes, *, lam: bool, caller_fqn: str, emit
) -> None:
    """Emit a method-reference ``CallSite`` (arg_count = -1).

    ``obj::foo`` → callee ``foo``, receiver ``obj``. ``a.b()::foo`` → callee
    ``foo``, receiver ``a.b()``, ``chained_method_reference=True`` because the
    spine is a ``call_expression`` (a call chain).
    """
    ids = [c for c in n.named_children if c.type == "identifier"]
    if not ids:
        return
    callee = _txt(ids[-1], src)
    dc = next((c for c in n.children if not c.is_named and c.type == "::"), None)
    spine_text = (
        src[n.start_byte:dc.start_byte].decode("utf-8", errors="replace")
        if dc is not None
        else ""
    )
    spine_node = n.named_children[0] if n.named_children else None
    chained = spine_node is not None and spine_node.type == "call_expression"
    emit(
        CallSite(
            caller_fqn=caller_fqn,
            receiver_expr=spine_text,
            callee_simple=callee,
            arg_count=-1,
            is_static_call=False,
            is_constructor=False,
            in_lambda=lam,
            line=n.start_point[0] + 1,
            byte=n.start_byte,
            chained_method_reference=chained,
        )
    )


def _primary_ctor_delegation_site(
    class_node: Node, src: bytes, *, caller_fqn: str
) -> CallSite | None:
    """``CallSite`` for class-header constructor delegation ``: Base(x)``/``: Super(x)``.

    Walks ``delegation_specifiers > delegation_specifier > constructor_invocation``;
    the receiver is the ``user_type`` simple name, args counted from
    ``value_arguments``. Returns the first such site, or None when the class
    declares no constructor-style delegation (interface/type supertypes only).
    """
    del_specs = next(
        (c for c in class_node.named_children if c.type == "delegation_specifiers"),
        None,
    )
    if del_specs is None:
        return None
    for spec in del_specs.named_children:
        if spec.type != "delegation_specifier":
            continue
        ci = next(
            (c for c in spec.named_children if c.type == "constructor_invocation"),
            None,
        )
        if ci is None:
            continue
        ut = next((c for c in ci.named_children if c.type == "user_type"), None)
        if ut is None:
            continue
        recv = _simple_type_name(ut, src)
        if not recv:
            continue
        return CallSite(
            caller_fqn=caller_fqn,
            receiver_expr=recv,
            callee_simple="<init>",
            arg_count=_value_argument_count(ci),
            is_static_call=False,
            is_constructor=True,
            in_lambda=False,
            line=class_node.start_point[0] + 1,
            byte=class_node.start_byte,
        )
    return None


def _secondary_ctor_delegation_site(
    ctor_node: Node, src: bytes, *, caller_fqn: str
) -> CallSite | None:
    """``CallSite`` for a secondary-constructor delegation ``: this(...)``/``: super(...)``.

    Source node: ``secondary_constructor > constructor_delegation_call > (this|super,
    value_arguments)``. Receiver text is ``"this"``/``"super"``.
    """
    cdc = next(
        (c for c in ctor_node.named_children if c.type == "constructor_delegation_call"),
        None,
    )
    if cdc is None:
        return None
    is_super = any(c.type == "super" for c in cdc.children)
    is_this = any(c.type == "this" for c in cdc.children)
    recv = "super" if is_super else ("this" if is_this else "")
    return CallSite(
        caller_fqn=caller_fqn,
        receiver_expr=recv,
        callee_simple="<init>",
        arg_count=_value_argument_count(cdc),
        is_static_call=False,
        is_constructor=True,
        in_lambda=False,
        line=ctor_node.start_point[0] + 1,
        byte=ctor_node.start_byte,
    )


def _process_function_declaration(
    node: Node,
    src: bytes,
    *,
    is_static_ctx: bool,
    type_fqn: str,
    type_names: frozenset[str],
) -> MethodDecl:
    """``function_declaration`` → MethodDecl (``is_constructor`` always False in 1.1.0).

    All ``modifiers`` annotations attach to the MethodDecl (functions carry no
    use-site target semantics; their ``use_site_target`` is preserved as-is).
    Task 10 walks the ``function_body`` for ``CallSite`` s attributed to
    ``<type_fqn>#<signature>``.
    """
    name = next(
        (_txt(c, src) for c in node.named_children if c.type == "identifier"), ""
    )
    fv_params = _function_value_parameters(node)
    ret_type_node = next(
        (c for c in node.named_children if c.type in _TYPE_NODE_TYPES), None
    )
    params = _params_from_function_value_parameters(fv_params, src)
    info = _collect_kotlin_modifiers(node, src)
    anns = _kotlin_annotations_from_modifiers(node, src)
    is_final = not (
        _KOTLIN_INHERITANCE_NON_FINAL & set(info["inheritance"])
    )
    mods = _build_member_modifiers(info, is_static=is_static_ctx, is_final=is_final)
    sig = f"{name}({','.join(p.type_name for p in params)})"
    m = MethodDecl(
        name=name,
        return_type=_simple_type_name(ret_type_node, src),
        is_constructor=False,
        parameters=params,
        modifiers=mods,
        annotations=anns,
        signature=sig,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )
    body = next((c for c in node.named_children if c.type == "function_body"), None)
    m.call_sites = _collect_kotlin_call_sites(
        body, src, caller_fqn=f"{type_fqn}#{sig}", type_names=type_names
    )
    return m


def _process_secondary_constructor(
    node: Node,
    src: bytes,
    class_name: str,
    *,
    type_fqn: str,
    type_names: frozenset[str],
) -> MethodDecl:
    """``secondary_constructor`` → constructor MethodDecl (name = enclosing class).

    Task 10 attaches the ``constructor_delegation_call`` site (``: this(...)`` /
    ``: super(...)``) at the constructor's start byte, plus any ``CallSite`` s in
    the optional ``block`` body.
    """
    params = _params_from_function_value_parameters(
        _function_value_parameters(node), src
    )
    anns = _kotlin_annotations_from_modifiers(node, src)
    sig = f"{class_name}({','.join(p.type_name for p in params)})"
    m = MethodDecl(
        name=class_name,
        return_type="",
        is_constructor=True,
        parameters=params,
        annotations=anns,
        signature=sig,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )
    caller = f"{type_fqn}#{sig}"
    sites = _collect_kotlin_call_sites(
        next((c for c in node.named_children if c.type == "block"), None),
        src,
        caller_fqn=caller,
        type_names=type_names,
    )
    del_site = _secondary_ctor_delegation_site(node, src, caller_fqn=caller)
    if del_site is not None:
        sites.append(del_site)
    m.call_sites = sites
    return m


def _process_property_declaration(
    node: Node, src: bytes, *, is_static_ctx: bool
) -> tuple[FieldDecl, list[MethodDecl]]:
    """``property_declaration`` → (FieldDecl, synthesized accessor MethodDecl[s]).

    Accessors are synthesized only for non-private properties (private emits the
    FieldDecl alone). ``const`` forces ``static``.

    Annotation routing (use-site target): ``field``/``property``/``None`` →
    FieldDecl; ``get`` → getter; ``set`` → setter. A ``param`` target is invalid
    on a body property and falls back to the FieldDecl.
    """
    vdecl = next(
        (c for c in node.named_children if c.type == "variable_declaration"), None
    )
    name = ""
    type_simple = ""
    if vdecl is not None:
        name = next(
            (_txt(vc, src) for vc in vdecl.named_children if vc.type == "identifier"),
            "",
        )
        type_simple = _simple_type_name(_type_child(vdecl), src)

    is_var = _has_anon_keyword(node, "var")
    info = _collect_kotlin_modifiers(node, src)
    anns = _kotlin_annotations_from_modifiers(node, src)
    buckets = _route_kotlin_annotations_by_target(anns, default_slot="field")
    is_static = is_static_ctx or info["const"]
    field = FieldDecl(
        name=name,
        type_name=type_simple,
        type_raw=type_simple,
        modifiers=_build_member_modifiers(
            info, is_static=is_static, is_final=(not is_var) or info["const"]
        ),
        annotations=buckets["field"],
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )
    accessors: list[MethodDecl] = []
    if info["visibility"] != "private":
        accessors = _accessor_method_decls(
            name, type_simple, is_var, info, is_static=is_static
        )
        _attach_targeted_annotations_to_accessors(
            accessors, buckets["get"], buckets["set"]
        )
    return field, accessors


def _process_class_parameter(
    node: Node, src: bytes, *, is_static_ctx: bool
) -> tuple[FieldDecl | None, list[MethodDecl], ParamDecl]:
    """A primary-constructor ``class_parameter`` → (field|None, accessors, ctor ParamDecl).

    ``val``/``var`` parameters are properties (field + accessors); a plain
    parameter is just a constructor ParamDecl (no field, no accessor).

    Annotation routing (use-site target): ``param``/``None`` → the ctor ParamDecl
    (None defaults to the ctor-param natural slot — the dominant Spring-Kotlin
    constructor-injection pattern); ``field``/``property`` → FieldDecl; ``get`` →
    getter; ``set`` → setter. A plain (non-val/var) param has only the ParamDecl
    slot, so every annotation lands there regardless of target.
    """
    name = next(
        (_txt(c, src) for c in node.named_children if c.type == "identifier"), ""
    )
    type_simple = _simple_type_name(_type_child(node), src)
    is_var = _has_anon_keyword(node, "var")
    is_val = _has_anon_keyword(node, "val")
    info = _collect_kotlin_modifiers(node, src)
    anns = _kotlin_annotations_from_modifiers(node, src)
    is_static = is_static_ctx or info["const"]

    if not (is_var or is_val):
        # Plain constructor parameter: only a ParamDecl slot exists.
        ctor_param = ParamDecl(
            name=name, type_name=type_simple, type_raw=type_simple, annotations=anns
        )
        return None, [], ctor_param

    buckets = _route_kotlin_annotations_by_target(anns, default_slot="param")
    ctor_param = ParamDecl(
        name=name,
        type_name=type_simple,
        type_raw=type_simple,
        annotations=buckets["param"],
    )
    field = FieldDecl(
        name=name,
        type_name=type_simple,
        type_raw=type_simple,
        modifiers=_build_member_modifiers(
            info, is_static=is_static, is_final=(not is_var) or info["const"]
        ),
        annotations=buckets["field"],
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
    )
    accessors: list[MethodDecl] = []
    if info["visibility"] != "private":
        accessors = _accessor_method_decls(
            name, type_simple, is_var, info, is_static=is_static
        )
        _attach_targeted_annotations_to_accessors(
            accessors, buckets["get"], buckets["set"]
        )
    return field, accessors, ctor_param


def _process_primary_constructor(
    node: Node, src: bytes, class_name: str, *, is_static_ctx: bool
) -> tuple[MethodDecl, list[FieldDecl], list[MethodDecl]]:
    """``primary_constructor`` → (ctor MethodDecl, property fields, accessor methods).

    Emits a constructor MethodDecl whose parameters include EVERY class_parameter
    (properties and plain params), so Java ``new T(...)`` resolves. ``val``/``var``
    parameters additionally contribute a FieldDecl + accessors.
    """
    params_node = next(
        (c for c in node.named_children if c.type == "class_parameters"), None
    )
    ctor_params: list[ParamDecl] = []
    fields: list[FieldDecl] = []
    accessors: list[MethodDecl] = []
    if params_node is not None:
        for c in params_node.named_children:
            if c.type != "class_parameter":
                continue
            field, accs, param = _process_class_parameter(
                c, src, is_static_ctx=is_static_ctx
            )
            ctor_params.append(param)
            if field is not None:
                fields.append(field)
                accessors.extend(accs)
    sig = f"{class_name}({','.join(p.type_name for p in ctor_params)})"
    ctor = MethodDecl(
        name=class_name,
        return_type="",
        is_constructor=True,
        parameters=ctor_params,
        modifiers=[],
        signature=sig,
    )
    return ctor, fields, accessors


def _facade_stem(filename: str) -> str:
    """Filename stem for the top-level facade (``Foo.kt`` → ``Foo``)."""
    base = os.path.basename(filename) if filename else ""
    if base.endswith(".kt"):
        base = base[:-3]
    return base or "File"


# ---- Task 9: @file:JvmName / @file:JvmMultifileClass facade naming ----
#
# Grammar (tree-sitter-kotlin 1.1.0), confirmed by probing:
# * ``file_annotation`` nodes are siblings of ``package_header`` / ``import``
#   directly under ``source_file`` (NOT nested in ``modifiers``).
# * ``@file:JvmName("X")`` →
#   ``file_annotation > constructor_invocation > (user_type > identifier "JvmName",
#   value_arguments > value_argument > string_literal > string_content)``.
# * ``@file:JvmMultifileClass()`` →
#   ``file_annotation > constructor_invocation > user_type > identifier
#   "JvmMultifileClass"`` (empty ``value_arguments``).
# The shared annotation-name / value-argument helpers (Task 8) work unchanged on a
# ``file_annotation`` node because they walk the same ``constructor_invocation``
# child shape.


def _read_file_annotations(root: Node, src: bytes) -> tuple[str, bool]:
    """Return ``(jvm_name, is_multifile)`` from top-level ``file_annotation`` nodes.

    ``@file:JvmName("X")`` → ``jvm_name = "X"`` (first wins if repeated);
    ``@file:JvmMultifileClass()`` → ``is_multifile = True``. Both default to the
    no-op value (``""`` / ``False``) when the annotation is absent.
    """
    jvm_name = ""
    is_multifile = False
    for child in root.named_children:
        if child.type != "file_annotation":
            continue
        simple, _ = _kotlin_annotation_name(child, src)
        if simple == "JvmName" and not jvm_name:
            args, _ = _kotlin_annotation_value_arguments(child, src)
            val = args.get("value", "")
            if val:
                jvm_name = val
        elif simple == "JvmMultifileClass":
            is_multifile = True
    return jvm_name, is_multifile


# ---- Task 8: extends/implements partition (B7-soft) ----
#
# Kotlin surfaces every supertype — class to extend, interface to implement, and
# ``by``-delegation target — as one comma-separated ``delegation_specifiers``
# clause after ``:``. Each ``delegation_specifier`` is one of:
#   * ``user_type``                              (plain interface/type)
#   * ``constructor_invocation > user_type``     (class with ctor call, e.g. ``Base(c)``)
#   * ``explicit_delegation > user_type``        (``I by impl()`` — supertype is ``I``)
# In all cases the supertype simple name lives in a descendant ``user_type``.
#
# Partition rule (no cross-file resolution in the extractor): a supertype whose
# simple name is declared in THIS compilation unit with folded kind in
# {class, record, enum} → extends; declared interface → implements; everything
# else (unknown, or annotation kind) → implements (a spurious IMPLEMENTS is less
# damaging than a false EXTENDS). An interface declaration emits ONLY implements.

# Kotlin folded kinds that count as a class-kind for the extends branch.
_KOTLIN_CLASS_KINDS: frozenset[str] = frozenset({"class", "record", "enum"})


def _pre_scan_kotlin_type_kinds(root: Node, src: bytes) -> dict[str, str]:
    """Map simple type name → folded kind for every declaration in this CU.

    Walks all ``class_declaration`` / ``object_declaration`` / ``companion_object``
    nodes (top-level and nested). Last-wins on name collision (rare; nested names
    shadow). Used by the supertype partition — same-CU resolution only.
    """
    out: dict[str, str] = {}

    def visit(n: Node) -> None:
        t = n.type
        if t == "class_declaration":
            kind = _kotlin_class_kind(n, src)
        elif t in ("object_declaration", "companion_object"):
            kind = "class"
        else:
            kind = ""
        if kind:
            nm = _kotlin_decl_name(n, src)
            if nm:
                out[nm] = kind
        for c in n.children:
            visit(c)

    visit(root)
    return out


def _supertype_simple_name(delegation_specifier: Node, src: bytes) -> str:
    """Head simple name from a ``delegation_specifier`` (generics/nullable stripped).

    Finds the first descendant ``user_type`` (direct, under
    ``constructor_invocation``, or under ``explicit_delegation``) and returns its
    head simple name via ``_simple_type_name``. Returns ``""`` if none found.
    """
    user_type = next(
        (c for c in delegation_specifier.named_children if c.type == "user_type"),
        None,
    )
    if user_type is None:
        for c in delegation_specifier.named_children:
            if c.type in ("constructor_invocation", "explicit_delegation"):
                user_type = next(
                    (gc for gc in c.named_children if gc.type == "user_type"),
                    None,
                )
                if user_type is not None:
                    break
    if user_type is None:
        # Fall back to any nested user_type (defensive — shouldn't happen).
        for c in delegation_specifier.children:
            if c.type == "user_type":
                user_type = c
                break
    return _simple_type_name(user_type, src) if user_type is not None else ""


def _kotlin_extends_implements(
    class_node: Node,
    src: bytes,
    self_kind: str,
    kind_by_simple: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Partition the ``:`` supertype list of a class/interface into (extends, implements).

    Interfaces (``self_kind == "interface"``) emit only ``implements`` regardless
    of the supertypes' own kinds. Generics/nullable are stripped to simple names.
    """
    extends: list[str] = []
    implements: list[str] = []
    del_specs = next(
        (c for c in class_node.named_children if c.type == "delegation_specifiers"),
        None,
    )
    if del_specs is None:
        return extends, implements
    for spec in del_specs.named_children:
        if spec.type != "delegation_specifier":
            continue
        simple = _supertype_simple_name(spec, src)
        if not simple:
            continue
        if self_kind == "interface":
            implements.append(simple)
            continue
        kind = kind_by_simple.get(simple)
        if kind in _KOTLIN_CLASS_KINDS:
            extends.append(simple)
        else:
            # interface, annotation, or unknown → implements (safe default).
            implements.append(simple)
    return extends, implements


def _parse_kotlin_type(
    node: Node,
    src: bytes,
    *,
    package: str,
    outer_fqn: str | None,
    all_types: list[TypeDecl],
    kind_by_simple: dict[str, str],
    type_names: frozenset[str],
    filename: str = "",
) -> TypeDecl | None:
    """Build a ``TypeDecl`` for a Kotlin type declaration node (Tasks 6–8 + 10).

    Recurses into the declaration's body (``class_body`` / ``enum_class_body``)
    for nested ``class_declaration`` / ``object_declaration`` /
    ``companion_object`` nodes and walks member nodes into ``fields`` / ``methods``:
    ``function_declaration`` / ``secondary_constructor`` → methods;
    ``property_declaration`` and ``val``/``var`` primary-constructor parameters →
    field + synthesized accessors. Companion-object direct members carry
    ``"static"``. Task 8 adds type-level ``annotations`` and the
    ``extends``/``implements`` partition of the ``:`` supertype list. Task 10
    populates ``MethodDecl.call_sites`` (function/secondary-ctor bodies + primary
    + secondary constructor delegation); ``type_names`` drives constructor vs
    bare-call discrimination and the best-effort ``is_static_call`` flag.
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

    # Direct members of a `companion_object` compile to static JVM members.
    members_are_static = t == "companion_object"

    # Task 8: type-level annotations + extends/implements partition.
    type_anns = _kotlin_annotations_from_modifiers(node, src)
    if t == "class_declaration":
        extends, implements = _kotlin_extends_implements(
            node, src, self_kind=kind, kind_by_simple=kind_by_simple
        )
    else:
        extends, implements = [], []  # objects/companion have no supertype clause.

    nested: list[TypeDecl] = []
    decl = TypeDecl(
        name=name,
        kind=kind,
        fqn=fqn,
        annotations=type_anns,
        extends=extends,
        implements=implements,
        nested=nested,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        outer_fqn=outer_fqn,
    )
    all_types.append(decl)

    fields: list[FieldDecl] = []
    methods: list[MethodDecl] = []

    # Primary constructor (class header). Only `class_declaration` carries one;
    # it contributes the constructor MethodDecl + any val/var property members.
    # Task 10: when the class header declares constructor-style delegation
    # (``: Base(x)``) but there is no explicit primary_constructor, synthesise an
    # implicit one to carry the super-call CallSite.
    primary_ctor: MethodDecl | None = None
    if t == "class_declaration":
        pc = next(
            (c for c in node.named_children if c.type == "primary_constructor"),
            None,
        )
        if pc is not None:
            primary_ctor, cfields, caccs = _process_primary_constructor(
                pc, src, name, is_static_ctx=members_are_static
            )
            fields.extend(cfields)
            methods.extend(caccs)
        del_site = _primary_ctor_delegation_site(
            node, src, caller_fqn=f"{fqn}#{name}()"
        )
        if del_site is not None:
            if primary_ctor is None:
                # No explicit primary_constructor: synthesise the implicit ctor
                # Kotlin generates, and fix its caller_fqn to the real signature.
                primary_ctor = MethodDecl(
                    name=name,
                    return_type="",
                    is_constructor=True,
                    parameters=[],
                    signature=f"{name}()",
                )
            del_site.caller_fqn = f"{fqn}#{primary_ctor.signature}"
            primary_ctor.call_sites.append(del_site)
        if primary_ctor is not None:
            methods.append(primary_ctor)

    body: Node | None = None
    for c in node.named_children:
        if c.type in ("class_body", "enum_class_body"):
            body = c
            break
    if body is not None:
        for ch in body.named_children:
            ct = ch.type
            if ct == "function_declaration":
                methods.append(
                    _process_function_declaration(
                        ch,
                        src,
                        is_static_ctx=members_are_static,
                        type_fqn=fqn,
                        type_names=type_names,
                    )
                )
            elif ct == "secondary_constructor":
                methods.append(
                    _process_secondary_constructor(
                        ch,
                        src,
                        name,
                        type_fqn=fqn,
                        type_names=type_names,
                    )
                )
            elif ct == "property_declaration":
                field, accs = _process_property_declaration(
                    ch, src, is_static_ctx=members_are_static
                )
                fields.append(field)
                methods.extend(accs)
            elif ct in _KOTLIN_TYPE_NODES:
                child_decl = _parse_kotlin_type(
                    ch,
                    src,
                    package=package,
                    outer_fqn=fqn,
                    all_types=all_types,
                    kind_by_simple=kind_by_simple,
                    type_names=type_names,
                    filename=filename,
                )
                if child_decl is not None:
                    nested.append(child_decl)

    decl.fields = fields
    decl.methods = methods
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

    # Pre-scan declared type kinds (simple name → folded kind) for the same-CU
    # supertype partition (Task 8). Built once from the whole tree.
    kind_by_simple = _pre_scan_kotlin_type_kinds(root, src)

    # Task 10: the set of type names visible in this CU (explicit imports +
    # same-CU declarations) drives constructor-vs-bare-call discrimination and
    # the best-effort ``is_static_call`` flag. Built once, threaded everywhere.
    type_names: frozenset[str] = frozenset(
        set(explicit_imports.keys()) | set(kind_by_simple.keys())
    )

    # Walk top-level type declarations (class_declaration / object_declaration /
    # companion_object) into TypeDecl rows with the folded kind map, now also
    # populating members (Task 7) and annotations/supertypes (Task 8). Top-level
    # functions/properties are collected below onto a synthetic facade TypeDecl.
    top_level_types: list[TypeDecl] = []
    all_types: list[TypeDecl] = []
    for child in root.named_children:
        if child.type in _KOTLIN_TYPE_NODES:
            decl = _parse_kotlin_type(
                child,
                src,
                package=package,
                outer_fqn=None,
                all_types=all_types,
                kind_by_simple=kind_by_simple,
                type_names=type_names,
                filename=filename,
            )
            if decl is not None:
                top_level_types.append(decl)

    # Top-level functions / properties → synthetic facade (Task 9: named via
    # @file:JvmName else `<CapitalisedStem>Kt`; @file:JvmMultifileClass adds the
    # ``kotlin_multifile`` capability so ``merge_multifile_facades`` can group).
    # Facade members are static.
    top_level_funcs = [
        c for c in root.named_children if c.type == "function_declaration"
    ]
    top_level_props = [
        c for c in root.named_children if c.type == "property_declaration"
    ]
    if top_level_funcs or top_level_props:
        jvm_name, is_multifile = _read_file_annotations(root, src)
        # Kotlin's default facade name: capitalise the filename stem's first
        # letter, append ``Kt`` (foo.kt → FooKt, myFile.kt → MyFileKt).
        facade_name = jvm_name or (_cap(_facade_stem(filename)) + "Kt")
        facade_fqn = (
            f"{package}.{facade_name}" if package else facade_name
        )
        capabilities = (
            ["kotlin_facade", "kotlin_multifile"]
            if is_multifile
            else ["kotlin_facade"]
        )
        facade = TypeDecl(
            name=facade_name,
            kind="class",
            fqn=facade_fqn,
            capabilities=capabilities,
        )
        all_types.append(facade)
        top_level_types.append(facade)
        for fn in top_level_funcs:
            facade.methods.append(
                _process_function_declaration(
                    fn,
                    src,
                    is_static_ctx=True,
                    type_fqn=facade_fqn,
                    type_names=type_names,
                )
            )
        for pr in top_level_props:
            field, accs = _process_property_declaration(
                pr, src, is_static_ctx=True
            )
            facade.fields.append(field)
            facade.methods.extend(accs)

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


# ---- Task 9: cross-file multifile-facade merge ----
#
# Kotlin compiles every file annotated with the same ``@file:JvmName("X")`` +
# ``@file:JvmMultifileClass()`` into ONE JVM class ``pkg.X``. The per-file parse
# therefore emits one facade per file, all claiming FQN ``pkg.X``; without a
# merge, downstream ``tables.types[fqn] = entry`` overwrites and one file's
# top-level functions silently vanish from resolution. This helper merges them
# BEFORE the graph builder runs (Task 11 wires it into the index flow).
#
# Capability strings are the ONLY recognition signal (no new field/column):
# ``"kotlin_facade"`` marks a facade; ``"kotlin_multifile"`` marks one that
# participates in the cross-file merge. Two files sharing ``@file:JvmName("X")``
# WITHOUT ``@JvmMultifileClass`` are the illegal/ambiguous same-FQN collision and
# are left alone (both facades survive — never silently dropped).


def _find_facade(ast: JavaFileAst) -> TypeDecl | None:
    """The single ``kotlin_facade`` TypeDecl of an AST, or None (zero or >1)."""
    facades = [t for t in ast.top_level_types if "kotlin_facade" in t.capabilities]
    return facades[0] if len(facades) == 1 else None


def _concat_members_deduped(target: TypeDecl, source: TypeDecl) -> None:
    """Append ``source`` methods/fields onto ``target``, deduping identical members.

    Methods dedupe on ``(name, signature)``; fields on ``(name, type_name)`` (the
    closest field analog of a signature — two top-level properties with the same
    name cannot coexist in one multifile class anyway). First occurrence wins.
    """
    seen_methods = {(m.name, m.signature) for m in target.methods}
    for m in source.methods:
        key = (m.name, m.signature)
        if key not in seen_methods:
            seen_methods.add(key)
            target.methods.append(m)
    seen_fields = {(f.name, f.type_name) for f in target.fields}
    for f in source.fields:
        key = (f.name, f.type_name)
        if key not in seen_fields:
            seen_fields.add(key)
            target.fields.append(f)


def _strip_facade(ast: JavaFileAst, facade: TypeDecl) -> None:
    """Remove ``facade`` from the AST's ``top_level_types`` and ``all_types``."""
    ast.top_level_types = [t for t in ast.top_level_types if t is not facade]
    ast.all_types = [t for t in ast.all_types if t is not facade]


def merge_multifile_facades(asts: list[JavaFileAst]) -> list[JavaFileAst]:
    """Merge ``@file:JvmMultifileClass`` facades that share ``(package, name)``.

    For each group of two-or-more ASTs whose facade carries the
    ``kotlin_multifile`` capability AND shares the same ``(package, facade_name)``,
    keep ONE facade (on the first AST of the group), concatenate every other
    member's facade ``methods``/``fields`` onto it (deduped), and strip the
    duplicate facades from the other ASTs' type lists. Non-facade types are
    untouched in every AST. Non-multifile facades are left as-is — including the
    collision case of two same-``@file:JvmName`` files without
    ``@JvmMultifileClass`` (both survive).

    Returns the reshaped list (same length; merges applied in place on the
    retained ASTs).
    """
    # Group AST indices by (package, facade_name) for multifile facades only.
    groups: dict[tuple[str, str], list[int]] = {}
    for i, ast in enumerate(asts):
        facade = _find_facade(ast)
        if facade is None or "kotlin_multifile" not in facade.capabilities:
            continue
        groups.setdefault((ast.package, facade.name), []).append(i)

    for indices in groups.values():
        if len(indices) <= 1:
            continue  # single multifile file — nothing to merge.
        retained = _find_facade(asts[indices[0]])
        assert retained is not None  # grouped facades exist by construction.
        for other_idx in indices[1:]:
            other = _find_facade(asts[other_idx])
            assert other is not None
            _concat_members_deduped(retained, other)
            _strip_facade(asts[other_idx], other)

    return asts
