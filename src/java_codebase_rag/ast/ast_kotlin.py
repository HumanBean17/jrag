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
    """``function_value_parameters > parameter > (identifier, type)`` → ParamDecl list."""
    params: list[ParamDecl] = []
    if fv_params is None:
        return params
    for c in fv_params.named_children:
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
            )
        )
    return params


def _process_function_declaration(
    node: Node, src: bytes, *, is_static_ctx: bool
) -> MethodDecl:
    """``function_declaration`` → MethodDecl (``is_constructor`` always False in 1.1.0)."""
    name = next(
        (_txt(c, src) for c in node.named_children if c.type == "identifier"), ""
    )
    fv_params = _function_value_parameters(node)
    ret_type_node = next(
        (c for c in node.named_children if c.type in _TYPE_NODE_TYPES), None
    )
    params = _params_from_function_value_parameters(fv_params, src)
    info = _collect_kotlin_modifiers(node, src)
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
    sig = f"{class_name}({','.join(p.type_name for p in params)})"
    return MethodDecl(
        name=class_name,
        return_type="",
        is_constructor=True,
        parameters=params,
        modifiers=[],
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
    is_static = is_static_ctx or info["const"]
    field = FieldDecl(
        name=name,
        type_name=type_simple,
        type_raw=type_simple,
        modifiers=_build_member_modifiers(
            info, is_static=is_static, is_final=(not is_var) or info["const"]
        ),
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
    return field, accessors


def _process_class_parameter(
    node: Node, src: bytes, *, is_static_ctx: bool
) -> tuple[FieldDecl | None, list[MethodDecl], ParamDecl]:
    """A primary-constructor ``class_parameter`` → (field|None, accessors, ctor ParamDecl).

    ``val``/``var`` parameters are properties (field + accessors); a plain
    parameter is just a constructor ParamDecl (no field, no accessor).
    """
    name = next(
        (_txt(c, src) for c in node.named_children if c.type == "identifier"), ""
    )
    type_simple = _simple_type_name(_type_child(node), src)
    is_var = _has_anon_keyword(node, "var")
    is_val = _has_anon_keyword(node, "val")
    info = _collect_kotlin_modifiers(node, src)
    is_static = is_static_ctx or info["const"]
    ctor_param = ParamDecl(name=name, type_name=type_simple, type_raw=type_simple)
    if not (is_var or is_val):
        return None, [], ctor_param  # plain constructor parameter, not a property.
    field = FieldDecl(
        name=name,
        type_name=type_simple,
        type_raw=type_simple,
        modifiers=_build_member_modifiers(
            info, is_static=is_static, is_final=(not is_var) or info["const"]
        ),
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


def _parse_kotlin_type(
    node: Node,
    src: bytes,
    *,
    package: str,
    outer_fqn: str | None,
    all_types: list[TypeDecl],
    filename: str = "",
) -> TypeDecl | None:
    """Build a ``TypeDecl`` for a Kotlin type declaration node (Task 6 + 7).

    Recurses into the declaration's body (``class_body`` / ``enum_class_body``)
    for nested ``class_declaration`` / ``object_declaration`` /
    ``companion_object`` nodes (Task 6) and now also walks member nodes into
    ``fields`` / ``methods`` (Task 7): ``function_declaration`` /
    ``secondary_constructor`` → methods; ``property_declaration`` and
    ``val``/``var`` primary-constructor parameters → field + synthesized
    accessors. Companion-object direct members carry ``"static"``.
    ``extends`` / ``implements`` / ``annotations`` / type-level ``modifiers``
    arrive in later tasks (they stay as the ``TypeDecl`` defaults).
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

    # Walk top-level type declarations (class_declaration / object_declaration /
    # companion_object) into TypeDecl rows with the folded kind map, now also
    # populating members (Task 7). Top-level functions/properties are collected
    # below onto a synthetic facade TypeDecl.
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
