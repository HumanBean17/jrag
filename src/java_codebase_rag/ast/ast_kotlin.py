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
    FieldDecl,
    FileImports,
    JavaFileAst,
    MethodDecl,
    ParamDecl,
    TypeDecl,
)

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


def _process_function_declaration(
    node: Node, src: bytes, *, is_static_ctx: bool
) -> MethodDecl:
    """``function_declaration`` → MethodDecl (``is_constructor`` always False in 1.1.0).

    All ``modifiers`` annotations attach to the MethodDecl (functions carry no
    use-site target semantics; their ``use_site_target`` is preserved as-is).
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
    return MethodDecl(
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


def _process_secondary_constructor(
    node: Node, src: bytes, class_name: str
) -> MethodDecl:
    """``secondary_constructor`` → constructor MethodDecl (name = enclosing class)."""
    params = _params_from_function_value_parameters(
        _function_value_parameters(node), src
    )
    anns = _kotlin_annotations_from_modifiers(node, src)
    sig = f"{class_name}({','.join(p.type_name for p in params)})"
    return MethodDecl(
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
    filename: str = "",
) -> TypeDecl | None:
    """Build a ``TypeDecl`` for a Kotlin type declaration node (Tasks 6 + 7 + 8).

    Recurses into the declaration's body (``class_body`` / ``enum_class_body``)
    for nested ``class_declaration`` / ``object_declaration`` /
    ``companion_object`` nodes and walks member nodes into ``fields`` / ``methods``:
    ``function_declaration`` / ``secondary_constructor`` → methods;
    ``property_declaration`` and ``val``/``var`` primary-constructor parameters →
    field + synthesized accessors. Companion-object direct members carry
    ``"static"``. Task 8 adds type-level ``annotations`` and the
    ``extends``/``implements`` partition of the ``:`` supertype list.
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
    if t == "class_declaration":
        pc = next(
            (c for c in node.named_children if c.type == "primary_constructor"),
            None,
        )
        if pc is not None:
            ctor, cfields, caccs = _process_primary_constructor(
                pc, src, name, is_static_ctx=members_are_static
            )
            fields.extend(cfields)
            methods.extend(caccs)
            methods.append(ctor)

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
                        ch, src, is_static_ctx=members_are_static
                    )
                )
            elif ct == "secondary_constructor":
                methods.append(_process_secondary_constructor(ch, src, name))
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
                filename=filename,
            )
            if decl is not None:
                top_level_types.append(decl)

    # Top-level functions / properties → synthetic facade `<Basename>Kt` (Task 9
    # refines via @file:JvmName / multifile). Facade members are static.
    top_level_funcs = [
        c for c in root.named_children if c.type == "function_declaration"
    ]
    top_level_props = [
        c for c in root.named_children if c.type == "property_declaration"
    ]
    if top_level_funcs or top_level_props:
        facade_name = f"{_facade_stem(filename)}Kt"
        facade_fqn = (
            f"{package}.{facade_name}" if package else facade_name
        )
        facade = TypeDecl(
            name=facade_name,
            kind="class",
            fqn=facade_fqn,
            capabilities=["kotlin_facade"],
        )
        all_types.append(facade)
        top_level_types.append(facade)
        for fn in top_level_funcs:
            facade.methods.append(
                _process_function_declaration(fn, src, is_static_ctx=True)
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
