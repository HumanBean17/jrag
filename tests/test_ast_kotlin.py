"""Tests for the Kotlin AST extractor (`ast.ast_kotlin.parse_kotlin`).

Task 5 built the foundation: package + imports into the existing
``JavaFileAst`` shape, a per-thread tree-sitter ``Parser``, and registered
``KotlinBackend`` behind the language-dispatch seam. Task 6 walks Kotlin
type declarations (``class_declaration`` / ``object_declaration`` /
``companion_object``) into ``TypeDecl`` rows with the **folded kind map**
(Kotlin kinds fold into the existing five Java ``_TYPE_KINDS`` — no new
strings). Members (fields/methods) arrive in Task 7; top-level functions
arrive in Task 8.
"""
from __future__ import annotations

import pytest

# The whole module is skipped when the grammar wheel is absent (Intel-Mac
# graph-only installs, minimal CI images, etc.). ``importorskip`` runs at
# collection time and raises ``Skipped`` *before* ``ast_kotlin`` is imported —
# which matters because ``ast_kotlin`` itself does ``import tree_sitter_kotlin``
# at module load. A module-level ``pytestmark`` skipif would only mark test
# *functions* and would not prevent that ``ImportError`` during collection.
# The conditional registration in ``language.py`` mirrors this: no grammar →
# no KotlinBackend.
pytest.importorskip("tree_sitter_kotlin")

from java_codebase_rag.ast.ast_kotlin import parse_kotlin  # noqa: E402
from java_codebase_rag.ast.ast_java import JavaFileAst, TypeDecl  # noqa: E402
from java_codebase_rag.ast.language import backend_for  # noqa: E402


_SOURCE = b"package com.foo\n\nimport com.bar.Baz\nimport com.qux.*\n"


def test_language_tagged_kotlin() -> None:
    """(a) The returned AST carries `language == "kotlin"`."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.language == "kotlin"


def test_package_extracted() -> None:
    """(b) `package_header > qualified_identifier` → package name."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.package == "com.foo"


def test_imports_raw_including_wildcard_suffix() -> None:
    """(c) `imports` is the raw list; wildcards keep the `.*` suffix."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.imports == ["com.bar.Baz", "com.qux.*"]


def test_explicit_imports_map_simple_name_to_fqn() -> None:
    """(d) `explicit_imports`: simple type name → FQN (wildcards excluded)."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.explicit_imports == {"Baz": "com.bar.Baz"}


def test_wildcard_imports_are_package_prefixes() -> None:
    """(e) `wildcard_imports`: package prefix for each `.*` import."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.wildcard_imports == ["com.qux"]


def test_file_imports_populated_no_static() -> None:
    """(f) `file_imports.explicit` mirrors `explicit_imports`; no static (Kotlin has no `import static`)."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.file_imports.explicit == {"Baz": "com.bar.Baz"}
    assert ast.file_imports.static_methods == {}
    assert ast.file_imports.static_wildcards == []


def test_no_declarations_yields_empty_type_lists() -> None:
    """A file with no type declarations yields empty top_level_types/all_types."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.top_level_types == []
    assert ast.all_types == []


# ---- Task 6: type declarations with the folded kind map ----


def test_class_declaration_kind_name_fqn() -> None:
    """(a) `class Foo` in `package com.x` -> one top-level TypeDecl."""
    ast = parse_kotlin(b"package com.x\nclass Foo\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.name == "Foo"
    assert t.kind == "class"
    assert t.fqn == "com.x.Foo"
    assert t.outer_fqn is None
    # Members / extends / implements / modifiers arrive in later tasks (defaults).
    assert t.fields == []
    assert t.methods == []
    assert t.extends == []
    assert t.implements == []
    assert t.modifiers == []
    assert t.annotations == []
    assert t.nested == []
    assert len(ast.all_types) == 1
    assert ast.all_types[0].fqn == "com.x.Foo"


def test_interface_declaration_folded_kind() -> None:
    """(b) `interface Bar` discriminates via anonymous `interface` keyword child."""
    ast = parse_kotlin(b"package com.x\ninterface Bar\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.kind == "interface"
    assert t.name == "Bar"
    assert t.fqn == "com.x.Bar"
    assert t.outer_fqn is None


def test_enum_class_folded_kind() -> None:
    """(c) `enum class E` (class_modifier[enum]) -> kind 'enum'."""
    ast = parse_kotlin(b"package com.x\nenum class E { A, B }\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.kind == "enum"
    assert t.name == "E"
    assert t.fqn == "com.x.E"
    assert t.outer_fqn is None
    # enum_entry constants are NOT types — all_types stays a single entry.
    assert len(ast.all_types) == 1


def test_annotation_class_folded_kind() -> None:
    """(d) `annotation class Ann` (class_modifier[annotation]) -> kind 'annotation'."""
    ast = parse_kotlin(b"package com.x\nannotation class Ann\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.kind == "annotation"
    assert t.name == "Ann"
    assert t.fqn == "com.x.Ann"


def test_data_class_folded_to_record() -> None:
    """(e) `data class D` (class_modifier[data]) -> kind 'record' (DTO fold)."""
    ast = parse_kotlin(b"package com.x\ndata class D(val i: Int)\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.kind == "record"
    assert t.name == "D"
    assert t.fqn == "com.x.D"


def test_object_declaration_folded_to_class() -> None:
    """(f) `object Single` (object_declaration) -> kind 'class'."""
    ast = parse_kotlin(b"package com.x\nobject Single\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.kind == "class"
    assert t.name == "Single"
    assert t.fqn == "com.x.Single"
    assert t.outer_fqn is None


def test_companion_object_nested_under_class() -> None:
    """(g) `class Outer { companion object { } }` -> nested TypeDecl 'Companion'.

    Companion is a distinct `companion_object` node (not a modifier); default
    name 'Companion'; becomes a NESTED TypeDecl under its enclosing type.
    """
    ast = parse_kotlin(
        b"package com.x\nclass Outer { companion object { } }\n", filename="F.kt"
    )
    assert len(ast.top_level_types) == 1
    outer = ast.top_level_types[0]
    assert outer.name == "Outer"
    assert outer.fqn == "com.x.Outer"
    assert outer.outer_fqn is None
    assert len(outer.nested) == 1
    comp = outer.nested[0]
    assert comp.name == "Companion"
    assert comp.kind == "class"
    assert comp.fqn == "com.x.Outer.Companion"
    assert comp.outer_fqn == "com.x.Outer"
    # all_types is flat and contains both the outer type and the companion.
    fqns = {t.fqn for t in ast.all_types}
    assert "com.x.Outer" in fqns
    assert "com.x.Outer.Companion" in fqns


def test_named_companion_object_uses_declared_name() -> None:
    """A named `companion object Named` uses the declared name, not 'Companion'."""
    ast = parse_kotlin(
        b"package com.x\nclass Outer { companion object Named { } }\n", filename="F.kt"
    )
    outer = ast.top_level_types[0]
    assert len(outer.nested) == 1
    comp = outer.nested[0]
    assert comp.name == "Named"
    assert comp.fqn == "com.x.Outer.Named"
    assert comp.outer_fqn == "com.x.Outer"


def test_nested_class_declaration_attached_to_parent() -> None:
    """A nested `class_declaration` is attached to its parent's `nested` list."""
    ast = parse_kotlin(
        b"package com.x\nclass Outer { class Inner { } }\n", filename="F.kt"
    )
    outer = ast.top_level_types[0]
    assert len(outer.nested) == 1
    inner = outer.nested[0]
    assert inner.name == "Inner"
    assert inner.kind == "class"
    assert inner.fqn == "com.x.Outer.Inner"
    assert inner.outer_fqn == "com.x.Outer"
    fqns = {t.fqn for t in ast.all_types}
    assert {"com.x.Outer", "com.x.Outer.Inner"} <= fqns


def test_no_package_top_level_type_fqn_is_simple_name() -> None:
    """A script-style file with no package uses the simple name as the FQN."""
    ast = parse_kotlin(b"class Solo\n", filename="F.kt")
    assert len(ast.top_level_types) == 1
    t = ast.top_level_types[0]
    assert t.fqn == "Solo"
    assert t.outer_fqn is None


def test_parse_error_false_on_valid_source() -> None:
    """(h) Valid source reports `parse_error is False`."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.parse_error is False


def test_aliased_import_records_alias_as_key() -> None:
    """`import com.bar.Baz as Q` maps the alias `Q` → FQN `com.bar.Baz`."""
    src = b"package com.foo\nimport com.bar.Baz as Q\n"
    ast = parse_kotlin(src, filename="F.kt")
    assert ast.explicit_imports == {"Q": "com.bar.Baz"}
    assert ast.file_imports.explicit == {"Q": "com.bar.Baz"}


def test_no_package_header_yields_empty_string() -> None:
    """A script-style file with no package header sets `package == ""`."""
    src = b"import com.bar.Baz\n"
    ast = parse_kotlin(src, filename="F.kt")
    assert ast.package == ""


def test_parse_error_true_on_malformed_source() -> None:
    """Tree-sitter's error flag is surfaced as `parse_error=True`; still returns a JavaFileAst."""
    ast = parse_kotlin(b"class { broken @@@@", filename="F.kt")
    assert ast.parse_error is True
    assert isinstance(ast, JavaFileAst)
    assert ast.language == "kotlin"


def test_returns_javafileast_instance() -> None:
    """The extractor reuses the existing JavaFileAst shape (no new dataclass)."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert isinstance(ast, JavaFileAst)


def test_backend_for_kt_returns_kotlin_backend() -> None:
    """`backend_for("F.kt")` routes to the registered KotlinBackend."""
    backend = backend_for("F.kt")
    assert backend is not None
    assert backend.language_id == "kotlin"
    assert ".kt" in backend.suffixes


def test_kotlin_backend_parse_delegates_to_parse_kotlin() -> None:
    """KotlinBackend.parse produces the same AST as calling parse_kotlin directly."""
    backend = backend_for("F.kt")
    assert backend is not None
    ast = backend.parse(_SOURCE, filename="F.kt")
    assert ast.language == "kotlin"
    assert ast.package == "com.foo"
    assert ast.imports == ["com.bar.Baz", "com.qux.*"]


# ---- Task 7: members (functions, properties + synthesized JVM accessors, modifiers) ----
#
# The graph builder resolves cross-language CALLS to METHODS only; a Kotlin
# property mapped to a lone FieldDecl makes every Java getter/setter call a
# phantom. So non-private properties MUST also emit synthesized JVM-accessor
# MethodDecl(s). The accessor rule matches Kotlin's actual JVM codegen (Calling
# Kotlin from Java → Properties): the ``is``-prefix rule is NAME-based and
# type-agnostic — a property named ``is`` + uppercase keeps the prefix for ANY
# type (``isActive``→``isActive()``, ``isAwesome: String``→``isAwesome()``); a
# property NOT named ``is*`` (e.g. ``b``, or ``issue`` whose 3rd char is
# lowercase) compiles to ``getB()`` / ``getIssue()``.


def _type_by_fqn(ast: JavaFileAst, fqn: str) -> TypeDecl | None:
    for t in ast.all_types:
        if t.fqn == fqn:
            return t
    return None


def test_var_property_synthesizes_getter_and_setter() -> None:
    """(a) class P(var name: String) -> FieldDecl{name,String} + getName()/setName(String)."""
    ast = parse_kotlin(b"package com.x\nclass P(var name: String)\n", filename="P.kt")
    t = ast.top_level_types[0]
    assert any(f.name == "name" and f.type_name == "String" for f in t.fields)

    getter = [m for m in t.methods if m.name == "getName"]
    assert len(getter) == 1
    assert getter[0].is_constructor is False
    assert getter[0].return_type == "String"
    assert getter[0].parameters == []
    assert getter[0].signature == "getName()"

    setter = [m for m in t.methods if m.name == "setName"]
    assert len(setter) == 1
    assert setter[0].is_constructor is False
    assert setter[0].return_type == ""
    assert setter[0].signature == "setName(String)"
    assert len(setter[0].parameters) == 1
    assert setter[0].parameters[0].type_name == "String"
    assert setter[0].parameters[0].name  # non-empty


def test_boolean_non_is_property_uses_get_prefix() -> None:
    """(b, corrected) class P(val b: Boolean) -> getB() (Boolean non-`is*` uses the get prefix)."""
    ast = parse_kotlin(b"package com.x\nclass P(val b: Boolean)\n", filename="P.kt")
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "getB" in names
    # NOT isB — the brief's literal "isB" is wrong; match Kotlin's real codegen.
    assert "isB" not in names
    assert "getName" not in names
    # val -> getter only, no setter.
    assert "setB" not in names


def test_boolean_is_prefixed_property_preserves_is_prefix() -> None:
    """A Boolean property already named `is*` keeps the prefix: isActive -> isActive()/setActive()."""
    ast = parse_kotlin(b"package com.x\nclass P(var isActive: Boolean)\n", filename="P.kt")
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "isActive" in names  # getter preserves the `is` prefix.
    assert "getIsActive" not in names
    assert "setActive" in names  # setter drops the `is` prefix.
    assert "setIsActive" not in names


def test_is_prefixed_string_property_preserves_is_prefix() -> None:
    """Regression: the `is`-prefix rule is NAME-based, NOT Boolean-gated.

    ``val isFoo: String`` → ``isFoo()`` (not ``getIsFoo()``). The docs rule
    (Calling Kotlin from Java → Properties) applies to ANY type whose name is
    ``is`` + uppercase. Previously this wrongly gated on ``type == Boolean``,
    emitting ``getIsFoo()`` and producing phantom cross-language CALLS.
    """
    ast = parse_kotlin(b"package com.x\nclass P(val isFoo: String)\n", filename="P.kt")
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "isFoo" in names  # getter keeps the `is` prefix (name-based rule).
    assert "getIsFoo" not in names
    assert "getisFoo" not in names
    # val -> getter only, no setter.
    assert "setFoo" not in names


def test_is_prefixed_var_string_property_setter_drops_is() -> None:
    """Regression: ``var isAwesome: String`` → ``isAwesome()`` / ``setAwesome()``."""
    ast = parse_kotlin(
        b"package com.x\nclass P(var isAwesome: String)\n", filename="P.kt"
    )
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "isAwesome" in names  # getter keeps the prefix.
    assert "getIsAwesome" not in names
    assert "setAwesome" in names  # setter drops the `is`.
    assert "setIsAwesome" not in names


def test_is_prefixed_var_boolean_property_setter_drops_is() -> None:
    """Regression: ``var isOpen: Boolean`` → ``isOpen()`` / ``setOpen()`` (var)."""
    ast = parse_kotlin(b"package com.x\nclass P(var isOpen: Boolean)\n", filename="P.kt")
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "isOpen" in names
    assert "getIsOpen" not in names
    assert "setOpen" in names
    assert "setIsOpen" not in names


def test_word_boundary_issue_property_uses_get_prefix() -> None:
    """Regression: the ``[2].isupper()`` word-boundary — ``issue`` is NOT ``is``+prefix.

    ``val issue: Boolean`` → ``getIssue()`` (NOT ``issue()``), because the char
    after ``is`` is lowercase ``s`` — the property is not ``is``-prefixed.
    """
    ast = parse_kotlin(b"package com.x\nclass P(val issue: Boolean)\n", filename="P.kt")
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "getIssue" in names
    assert "issue" not in names  # NOT the is-prefix getter.


def test_val_int_property_getter_only() -> None:
    """(c) class P(val i: Int) -> getI() only (no setter for val)."""
    ast = parse_kotlin(b"package com.x\nclass P(val i: Int)\n", filename="P.kt")
    t = ast.top_level_types[0]
    names = {m.name for m in t.methods}
    assert "getI" in names
    assert "setI" not in names


def test_private_property_emits_no_accessor() -> None:
    """(d) private val x -> only FieldDecl; no synthesized accessor."""
    ast = parse_kotlin(b"package com.x\nclass C { private val x: Int = 0 }\n", filename="C.kt")
    t = ast.top_level_types[0]
    assert any(f.name == "x" and f.type_name == "Int" for f in t.fields)
    names = {m.name for m in t.methods}
    assert "getX" not in names
    assert "isX" not in names


def test_function_declaration_becomes_method_decl() -> None:
    """(e) fun go(a: Int): String -> MethodDecl{name,return_type=String,signature=go(Int)}."""
    ast = parse_kotlin(b'package com.x\nclass C { fun go(a: Int): String = "" }\n', filename="C.kt")
    t = ast.top_level_types[0]
    go = [m for m in t.methods if m.name == "go"]
    assert len(go) == 1
    assert go[0].is_constructor is False
    assert go[0].return_type == "String"
    assert go[0].signature == "go(Int)"
    assert len(go[0].parameters) == 1
    assert go[0].parameters[0].name == "a"
    assert go[0].parameters[0].type_name == "Int"


def test_secondary_constructor_is_constructor_method_decl() -> None:
    """(f) constructor(a: Int) -> MethodDecl{is_constructor=True,name=C,signature=C(Int)}."""
    ast = parse_kotlin(b"package com.x\nclass C { constructor(a: Int) }\n", filename="C.kt")
    t = ast.top_level_types[0]
    ctors = [m for m in t.methods if m.is_constructor]
    assert len(ctors) == 1
    assert ctors[0].name == "C"
    assert ctors[0].return_type == ""
    assert ctors[0].signature == "C(Int)"
    assert len(ctors[0].parameters) == 1
    assert ctors[0].parameters[0].type_name == "Int"


def test_companion_property_accessor_is_static() -> None:
    """(g) companion object { val CONST = 1 } -> accessor MethodDecl has 'static' modifier.

    Source is multi-line because tree-sitter-kotlin 1.1.0's ASI fails to recover a
    companion body holding a property when it sits on a single line.
    """
    ast = parse_kotlin(
        b"package com.x\nclass C {\n  companion object {\n    val CONST = 1\n  }\n}\n",
        filename="C.kt",
    )
    comp = _type_by_fqn(ast, "com.x.C.Companion")
    assert comp is not None
    getter = [m for m in comp.methods if m.name == "getCONST"]
    assert len(getter) == 1
    assert "static" in getter[0].modifiers


def test_suspend_function_ride_along_modifier() -> None:
    """(h) suspend fun s() -> modifiers contains 'suspend' (ride-along); method exists."""
    ast = parse_kotlin(b"package com.x\nclass C { suspend fun s() {} }\n", filename="C.kt")
    t = ast.top_level_types[0]
    s = [m for m in t.methods if m.name == "s"]
    assert len(s) == 1
    assert "suspend" in s[0].modifiers
    # Kotlin fun is final by default (no `open`).
    assert "final" in s[0].modifiers


def test_top_level_function_lands_on_facade_type() -> None:
    """Top-level fun -> facade TypeDecl <Basename>Kt (capabilities=['kotlin_facade']); method is static."""
    ast = parse_kotlin(
        b'package com.x\nfun topLevel(a: Int): String = ""\n', filename="Foo.kt"
    )
    facade = next((t for t in ast.top_level_types if "kotlin_facade" in t.capabilities), None)
    assert facade is not None
    assert facade.name == "FooKt"
    assert facade.fqn == "com.x.FooKt"
    assert facade.kind == "class"
    tf = [m for m in facade.methods if m.name == "topLevel"]
    assert len(tf) == 1
    assert "static" in tf[0].modifiers
    assert tf[0].signature == "topLevel(Int)"
    assert tf[0].return_type == "String"
    # Facade is also in the flat all_types list.
    assert _type_by_fqn(ast, "com.x.FooKt") is facade


def test_val_getter_final_var_setter_not_final() -> None:
    """Modifier vocabulary: val getter -> 'final'; var setter -> not 'final'."""
    ast = parse_kotlin(
        b"package com.x\nclass C { val a: Int = 0; var b: Int = 0 }\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    get_a = next(m for m in t.methods if m.name == "getA")
    assert "final" in get_a.modifiers
    set_b = next(m for m in t.methods if m.name == "setB")
    assert "final" not in set_b.modifiers


def test_open_function_omits_final() -> None:
    """open fun -> 'final' omitted (overridable)."""
    ast = parse_kotlin(b"package com.x\nopen class O { open fun f() {} }\n", filename="O.kt")
    t = ast.top_level_types[0]
    f = next(m for m in t.methods if m.name == "f")
    assert "final" not in f.modifiers


def test_primary_constructor_emits_constructor_method_decl() -> None:
    """class P(var name: String, extra: Int) -> primary ctor MethodDecl{P, is_constructor, sig P(String,Int)}.

    Plain params (no val/val) are NOT properties but still appear in the ctor signature.
    """
    ast = parse_kotlin(
        b"package com.x\nclass P(var name: String, extra: Int)\n", filename="P.kt"
    )
    t = ast.top_level_types[0]
    ctor = [m for m in t.methods if m.is_constructor and m.name == "P"]
    assert len(ctor) == 1
    assert ctor[0].signature == "P(String,Int)"
    # `extra` (plain param) is NOT a property -> only `name` is a field.
    assert {f.name for f in t.fields} == {"name"}


def test_nullable_property_type_strips_question_mark() -> None:
    """A nullable property type `String?` yields simple type_name 'String'."""
    ast = parse_kotlin(b"package com.x\nclass C(var n: String?)\n", filename="C.kt")
    t = ast.top_level_types[0]
    n = [f for f in t.fields if f.name == "n"]
    assert len(n) == 1
    assert n[0].type_name == "String"
    getter = next(m for m in t.methods if m.name == "getN")
    assert getter.return_type == "String"


# ---- Task 8: annotations (with use-site targets) + extends/implements partition ----
#
# Use-site target routing is the crux of Spring-Kotlin DI: it decides whether
# `@Autowired` becomes a constructor-INJECTS (param target) or field-INJECTS
# (field target) edge. Routing rules:
#   field/property/None (property)        -> FieldDecl.annotations
#   None (primary-ctor param)             -> ParamDecl.annotations  (natural slot)
#   param                                 -> ParamDecl.annotations
#   get                                   -> synthesized getter MethodDecl.annotations
#   set                                   -> synthesized setter MethodDecl.annotations
# Default-target resolution via the annotation's own @Target meta is OUT OF SCOPE
# (documented approximation); explicit use_site_target always wins.

from java_codebase_rag.ast.ast_java import AnnotationRef  # noqa: E402
from java_codebase_rag.ast.ast_java import parse_java  # noqa: E402


def _ann(refs: list[AnnotationRef], name: str) -> AnnotationRef | None:
    """First annotation matching simple name, or None."""
    for a in refs:
        if a.name == name:
            return a
    return None


def test_type_level_annotation_no_target() -> None:
    """(a) @RestController class H -> TypeDecl.annotations has RestController(target=None)."""
    ast = parse_kotlin(b"package com.x\n@RestController\nclass H\n", filename="H.kt")
    t = ast.top_level_types[0]
    a = _ann(t.annotations, "RestController")
    assert a is not None
    assert a.qualified == "RestController"
    assert a.use_site_target is None


def test_type_level_qualified_annotation_name() -> None:
    """@org.springframework.stereotype.Service class S -> simple 'Service', qualified raw text."""
    ast = parse_kotlin(
        b"package com.x\n@org.springframework.stereotype.Service\nclass S\n",
        filename="S.kt",
    )
    t = ast.top_level_types[0]
    a = _ann(t.annotations, "Service")
    assert a is not None
    assert a.qualified == "org.springframework.stereotype.Service"
    assert a.use_site_target is None


def test_type_level_annotation_with_args() -> None:
    """@Component(value = "x") class A -> arguments['value'] == 'x', kind 'string'."""
    ast = parse_kotlin(
        b'package com.x\n@Component(value = "x")\nclass A\n', filename="A.kt"
    )
    t = ast.top_level_types[0]
    a = _ann(t.annotations, "Component")
    assert a is not None
    assert a.arguments.get("value") == "x"
    assert a.argument_kinds.get("value") == "string"


def test_param_target_routes_to_ctor_param_not_accessor() -> None:
    """(b) class C(@param:Autowired val r: Repo) -> ParamDecl has Autowired(target=param).

    The synthesized getR accessor must NOT carry it; the field also must NOT.
    """
    ast = parse_kotlin(
        b"package com.x\nclass C(@param:Autowired val r: Repo)\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    ctor = next(m for m in t.methods if m.is_constructor and m.name == "C")
    assert len(ctor.parameters) == 1
    p = ctor.parameters[0]
    assert p.name == "r"
    a = _ann(p.annotations, "Autowired")
    assert a is not None
    assert a.use_site_target == "param"
    # The synthesized getR accessor does NOT carry Autowired.
    get_r = [m for m in t.methods if m.name == "getR"]
    assert len(get_r) == 1
    assert _ann(get_r[0].annotations, "Autowired") is None
    # The property FieldDecl does NOT carry it either (param target only).
    field = next(f for f in t.fields if f.name == "r")
    assert _ann(field.annotations, "Autowired") is None


def test_get_target_routes_to_synthesized_getter() -> None:
    """(c) @get:Column val n -> FieldDecl has no Column; getN MethodDecl has Column(target=get)."""
    ast = parse_kotlin(
        b"package com.x\nclass C { @get:Column val n: Int = 0 }\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    field = next(f for f in t.fields if f.name == "n")
    assert _ann(field.annotations, "Column") is None
    get_n = [m for m in t.methods if m.name == "getN"]
    assert len(get_n) == 1
    a = _ann(get_n[0].annotations, "Column")
    assert a is not None
    assert a.use_site_target == "get"


def test_set_target_routes_to_synthesized_setter() -> None:
    """@set:Inject var n -> setter setN MethodDecl has Inject(target=set); getter does not."""
    ast = parse_kotlin(
        b"package com.x\nclass C { @set:Inject var n: Int = 0 }\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    set_n = [m for m in t.methods if m.name == "setN"]
    assert len(set_n) == 1
    a = _ann(set_n[0].annotations, "Inject")
    assert a is not None
    assert a.use_site_target == "set"
    get_n = next(m for m in t.methods if m.name == "getN")
    assert _ann(get_n.annotations, "Inject") is None


def test_field_target_routes_to_property_field() -> None:
    """@field:Autowired val r in body -> FieldDecl has Autowired(target=field); accessor does not."""
    ast = parse_kotlin(
        b"package com.x\nclass C { @field:Autowired val r: Repo = Repo() }\n",
        filename="C.kt",
    )
    t = ast.top_level_types[0]
    field = next(f for f in t.fields if f.name == "r")
    a = _ann(field.annotations, "Autowired")
    assert a is not None
    assert a.use_site_target == "field"
    get_r = next(m for m in t.methods if m.name == "getR")
    assert _ann(get_r.annotations, "Autowired") is None


def test_no_target_on_primary_ctor_param_routes_to_paramdecl() -> None:
    """class C(@Autowired val r: Repo) (no explicit target) -> ParamDecl (natural ctor-param slot)."""
    ast = parse_kotlin(
        b"package com.x\nclass C(@Autowired val r: Repo)\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    ctor = next(m for m in t.methods if m.is_constructor and m.name == "C")
    p = ctor.parameters[0]
    a = _ann(p.annotations, "Autowired")
    assert a is not None
    assert a.use_site_target is None  # natural slot, no explicit target


def test_no_target_on_body_property_routes_to_field() -> None:
    """A body property @Autowired val r (no target) -> FieldDecl (natural property slot)."""
    ast = parse_kotlin(
        b"package com.x\nclass C { @Autowired val r: Repo = Repo() }\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    field = next(f for f in t.fields if f.name == "r")
    a = _ann(field.annotations, "Autowired")
    assert a is not None
    assert a.use_site_target is None


def test_function_annotation_attaches_to_method() -> None:
    """@Scheduled fun go() -> MethodDecl.annotations has Scheduled (functions have no use-site)."""
    ast = parse_kotlin(
        b"package com.x\nclass C { @Scheduled fun go() {} }\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    go = next(m for m in t.methods if m.name == "go")
    a = _ann(go.annotations, "Scheduled")
    assert a is not None
    assert a.use_site_target is None


def test_function_parameter_annotation_attaches_to_paramdecl() -> None:
    """fun f(@Ann x: Int) -> the x ParamDecl carries Ann (from parameter_modifiers)."""
    ast = parse_kotlin(
        b"package com.x\nclass C { fun f(@Ann x: Int) {} }\n", filename="C.kt"
    )
    t = ast.top_level_types[0]
    f = next(m for m in t.methods if m.name == "f")
    assert len(f.parameters) == 1
    a = _ann(f.parameters[0].annotations, "Ann")
    assert a is not None


def test_extends_implements_partition_same_file_class_and_interface() -> None:
    """(d) class D : Base(c), Iface (Base is a same-file class, Iface an interface).

    extends == ['Base']; implements == ['Iface']. The (c) ctor delegation is ignored.
    """
    src = (
        b"package com.x\nclass Base(val x: Int)\ninterface Iface\n"
        b"class D(val c: Int) : Base(c), Iface\n"
    )
    ast = parse_kotlin(src, filename="D.kt")
    d = next(t for t in ast.top_level_types if t.name == "D")
    assert d.extends == ["Base"]
    assert d.implements == ["Iface"]


def test_extends_implements_unknown_defaults_to_implements() -> None:
    """(e) class D : ExternalBase (not declared in CU) -> implements == ['ExternalBase'], extends == []."""
    ast = parse_kotlin(
        b"package com.x\nclass D(val c: Int) : ExternalBase(c)\n", filename="D.kt"
    )
    d = ast.top_level_types[0]
    assert d.extends == []
    assert d.implements == ["ExternalBase"]


def test_interface_supertypes_all_implements() -> None:
    """interface Foo : Bar, Baz -> only implements (interfaces have no extends)."""
    src = b"package com.x\ninterface Bar\ninterface Baz\ninterface Foo : Bar, Baz\n"
    ast = parse_kotlin(src, filename="Foo.kt")
    foo = next(t for t in ast.top_level_types if t.name == "Foo")
    assert foo.extends == []
    assert foo.implements == ["Bar", "Baz"]


def test_enum_supertype_same_file_classifies_as_extends() -> None:
    """A same-file enum class used as a supertype -> extends (kind 'enum' is a class-kind)."""
    src = b"package com.x\nenum class E { A }\nclass D : E\n"
    ast = parse_kotlin(src, filename="D.kt")
    d = next(t for t in ast.top_level_types if t.name == "D")
    assert d.extends == ["E"]
    assert d.implements == []


def test_data_class_supertype_classifies_as_extends() -> None:
    """A same-file data class (folded kind 'record') supertype -> extends."""
    src = b"package com.x\ndata class Dc(val i: Int)\nclass D : Dc(1)\n"
    ast = parse_kotlin(src, filename="D.kt")
    d = next(t for t in ast.top_level_types if t.name == "D")
    assert d.extends == ["Dc"]
    assert d.implements == []


def test_supertype_generics_stripped_to_simple_name() -> None:
    """class D : Base<String>, Iface<Int> -> simple names ['Base'], ['Iface'] (generics stripped)."""
    src = b"package com.x\nclass Base<T>\ninterface Iface<T>\nclass D : Base<String>(), Iface<Int>\n"
    ast = parse_kotlin(src, filename="D.kt")
    d = next(t for t in ast.top_level_types if t.name == "D")
    assert d.extends == ["Base"]
    assert d.implements == ["Iface"]


def test_class_with_no_supertypes_has_empty_lists() -> None:
    """class C {} -> extends == [] and implements == [] (no delegation_specifiers)."""
    ast = parse_kotlin(b"package com.x\nclass C\n", filename="C.kt")
    assert ast.top_level_types[0].extends == []
    assert ast.top_level_types[0].implements == []


def test_java_annotation_ref_use_site_target_is_none() -> None:
    """(f) Regression: a Java AnnotationRef built by parse_java has use_site_target is None."""
    ast = parse_java(
        b'package com.x;\n@org.springframework.stereotype.Service\n'
        b'public class S { public S(@org.springframework.beans.factory.annotation.Autowired Repo r) {} }\n',
        filename="S.java",
    )
    t = ast.top_level_types[0]
    svc = _ann(t.annotations, "Service")
    assert svc is not None
    assert svc.use_site_target is None
    ctor = next(m for m in t.methods if m.is_constructor)
    aw = _ann(ctor.parameters[0].annotations, "Autowired")
    assert aw is not None
    assert aw.use_site_target is None


def test_annotation_ref_field_default_is_none() -> None:
    """The AnnotationRef.use_site_target field defaults to None when constructed plainly."""
    a = AnnotationRef(name="X", qualified="X")
    assert a.use_site_target is None


# ---- Task 9: @file:JvmName / @file:JvmMultifileClass facade naming + cross-file merge ----
#
# Two Kotlin files that share @file:JvmName("X") + @file:JvmMultifileClass() compile
# into ONE JVM class pkg.X. The per-file parse emits one facade per file all claiming
# FQN pkg.X; `merge_multifile_facades` concatenates their members onto a single
# retained facade so cross-language CALLS resolve. Default facade naming: the Kotlin
# compiler capitalises the filename stem's first letter and appends `Kt`
# (foo.kt -> FooKt, myFile.kt -> MyFileKt).

from java_codebase_rag.ast.ast_kotlin import merge_multifile_facades  # noqa: E402


def test_jvmname_file_annotation_overrides_facade_name() -> None:
    """(a) @file:JvmName("Custom") + top-level fun go() -> facade 'Custom' (not FooKt)."""
    ast = parse_kotlin(
        b'@file:JvmName("Custom")\npackage com.x\nfun go() {}\n', filename="foo.kt"
    )
    facade = next(
        (t for t in ast.top_level_types if "kotlin_facade" in t.capabilities), None
    )
    assert facade is not None
    assert facade.name == "Custom"
    assert facade.fqn == "com.x.Custom"
    assert "kotlin_multifile" not in facade.capabilities
    assert [m.name for m in facade.methods if m.name == "go"] == ["go"]
    assert facade.name != "FooKt"  # NOT the default stem-based name.


def test_default_facade_name_capitalizes_first_letter() -> None:
    """(b) bar.kt with no file-annotation + top-level fun -> facade 'BarKt' (capitalised)."""
    ast = parse_kotlin(b"package com.x\nfun go() {}\n", filename="bar.kt")
    facade = next(t for t in ast.top_level_types if "kotlin_facade" in t.capabilities)
    assert facade.name == "BarKt"
    assert facade.fqn == "com.x.BarKt"


def test_jvmmultifileclass_adds_multifile_capability() -> None:
    """@file:JvmName("X") @file:JvmMultifileClass() -> capabilities carries both flags."""
    ast = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun go() {}\n',
        filename="a.kt",
    )
    facade = next(t for t in ast.top_level_types if "kotlin_facade" in t.capabilities)
    assert "kotlin_facade" in facade.capabilities
    assert "kotlin_multifile" in facade.capabilities
    assert facade.name == "X"
    assert facade.fqn == "com.x.X"


def test_jvmname_without_multifile_has_only_facade_capability() -> None:
    """@file:JvmName("X") alone (no @JvmMultifileClass) -> capabilities == ['kotlin_facade']."""
    ast = parse_kotlin(
        b'@file:JvmName("X")\npackage com.x\nfun go() {}\n', filename="a.kt"
    )
    facade = next(t for t in ast.top_level_types if "kotlin_facade" in t.capabilities)
    assert facade.capabilities == ["kotlin_facade"]


def test_merge_multifile_facades_concats_members_into_one() -> None:
    """(c) Two multifile files sharing JvmName("X") -> ONE facade X with both fns."""
    ast_a = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun a() {}\n',
        filename="a.kt",
    )
    ast_b = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun b() {}\n',
        filename="b.kt",
    )
    result = merge_multifile_facades([ast_a, ast_b])
    assert len(result) == 2  # same length; ASTs reshaped in place.
    # Exactly ONE facade named X across all top_level_types (was two before merge).
    facades = [t for ast in result for t in ast.top_level_types if t.name == "X"]
    assert len(facades) == 1
    merged = facades[0]
    assert {"a", "b"} <= {m.name for m in merged.methods}
    assert "kotlin_multifile" in merged.capabilities


def test_merge_multifile_facades_dedupes_identical_members() -> None:
    """Two multifile files with an identical fn signature -> deduped to one method."""
    ast_a = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun go(a: Int): String = ""\n',
        filename="a.kt",
    )
    ast_b = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun go(a: Int): String = ""\n',
        filename="b.kt",
    )
    result = merge_multifile_facades([ast_a, ast_b])
    facades = [t for ast in result for t in ast.top_level_types if t.name == "X"]
    assert len(facades) == 1
    go_methods = [m for m in facades[0].methods if m.name == "go"]
    assert len(go_methods) == 1  # identical name+signature deduped.


def test_merge_non_multifile_same_jvmname_keeps_both() -> None:
    """(d) Two files sharing JvmName("X") WITHOUT @JvmMultifileClass -> both facades survive.

    This is the illegal/ambiguous same-FQN collision; the merge does NOT silently
    drop one — both facades stay so the conflict is visible downstream.
    """
    ast_a = parse_kotlin(
        b'@file:JvmName("X")\npackage com.x\nfun a() {}\n', filename="a.kt"
    )
    ast_b = parse_kotlin(
        b'@file:JvmName("X")\npackage com.x\nfun b() {}\n', filename="b.kt"
    )
    result = merge_multifile_facades([ast_a, ast_b])
    facades = [t for ast in result for t in ast.top_level_types if t.name == "X"]
    assert len(facades) == 2  # no merge; both survive.


def test_merge_leaves_non_facade_types_untouched() -> None:
    """Non-facade top-level types survive the merge unchanged in every AST."""
    ast_a = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun a() {}\nclass KeepA\n',
        filename="a.kt",
    )
    ast_b = parse_kotlin(
        b'@file:JvmName("X")\n@file:JvmMultifileClass()\npackage com.x\nfun b() {}\nclass KeepB\n',
        filename="b.kt",
    )
    result = merge_multifile_facades([ast_a, ast_b])
    all_names = {t.name for ast in result for t in ast.top_level_types}
    assert {"KeepA", "KeepB"} <= all_names


def test_merge_with_no_multifile_facades_returns_input_as_is() -> None:
    """No multifile facades in the input -> output unchanged (distinct default facades)."""
    ast_a = parse_kotlin(b"package com.x\nfun a() {}\n", filename="a.kt")
    ast_b = parse_kotlin(b"package com.x\nfun b() {}\n", filename="b.kt")
    result = merge_multifile_facades([ast_a, ast_b])
    assert len(result) == 2
    facades = [
        t for ast in result for t in ast.top_level_types if "kotlin_facade" in t.capabilities
    ]
    assert len(facades) == 2  # AKt + BKt, no merge.


# ---- Task 10: call-site extraction + constructor delegation ----


def _call_sites_of(ast: JavaFileAst, type_fqn: str, method_name: str) -> list:
    """All CallSites of method `method_name` on the type whose FQN is `type_fqn`."""
    t = _type_by_fqn(ast, type_fqn)
    assert t is not None, f"type {type_fqn} not found"
    methods = [m for m in t.methods if m.name == method_name]
    assert methods, f"method {method_name} not on {type_fqn}"
    return methods[0].call_sites


def _facade_method(ast: JavaFileAst, name: str):
    facade = next(
        t for t in ast.top_level_types if "kotlin_facade" in t.capabilities
    )
    methods = [m for m in facade.methods if m.name == name]
    assert methods, f"facade method {name} not found"
    return methods[0]


def test_receiver_call_split_into_receiver_and_callee() -> None:
    """r.find(1) -> CallSite{callee=find, receiver=r, arg_count=1, is_constructor=False}."""
    ast = parse_kotlin(
        b"class C(val r: Repo) { fun go() { r.find(1) } }", filename="C.kt"
    )
    sites = _call_sites_of(ast, "C", "go")
    finds = [s for s in sites if s.callee_simple == "find"]
    assert len(finds) == 1
    s = finds[0]
    assert s.receiver_expr == "r"
    assert s.arg_count == 1
    assert s.is_constructor is False
    assert s.is_static_call is False
    assert s.caller_fqn == "C#go()"


def test_constructor_call_when_callee_is_known_type() -> None:
    """Other(2) where Other is a same-CU declared type -> ctor CallSite{callee=<init>, receiver=Other}.

    The capitalized-first-letter heuristic is explicitly NOT used: ``Other`` is
    recognised as a constructor target because it is declared in this CU.
    """
    ast = parse_kotlin(
        b"class Other\nclass C(val r: Repo) { fun go() { r.find(1); Other(2) } }",
        filename="C.kt",
    )
    sites = _call_sites_of(ast, "C", "go")
    inits = [
        s for s in sites if s.is_constructor and s.callee_simple == "<init>"
    ]
    assert len(inits) == 1
    s = inits[0]
    assert s.receiver_expr == "Other"
    assert s.arg_count == 1
    assert s.is_constructor is True


def test_bare_receiverless_call_is_not_constructor() -> None:
    """helper() -> CallSite{callee=helper, receiver='', is_constructor=False}.

    A bare call to a non-type name stays a receiverless method call (Task 13
    resolves against the file facade). Capitalized heuristic is not used, so a
    bare unknown name is NOT mistaken for a constructor.
    """
    ast = parse_kotlin(b"fun util() { helper() }", filename="F.kt")
    util = _facade_method(ast, "util")
    helpers = [s for s in util.call_sites if s.callee_simple == "helper"]
    assert len(helpers) == 1
    assert helpers[0].receiver_expr == ""
    assert helpers[0].is_constructor is False


def test_constructor_delegation_in_class_header() -> None:
    """class D : Base(7) -> ctor MethodDecl.call_sites has CallSite{receiver=Base, arg_count=1}.

    There is no explicit primary_constructor, so an implicit one is synthesised
    carrying the super-call delegation site.
    """
    ast = parse_kotlin(b"class Base\nclass D : Base(7) {}", filename="D.kt")
    d = _type_by_fqn(ast, "D")
    assert d is not None
    ctors = [m for m in d.methods if m.is_constructor]
    assert len(ctors) == 1
    dels = [
        s
        for s in ctors[0].call_sites
        if s.callee_simple == "<init>" and s.receiver_expr == "Base"
    ]
    assert len(dels) == 1
    assert dels[0].is_constructor is True
    assert dels[0].arg_count == 1


def test_chained_call_emits_two_sites() -> None:
    """repo.findById(1).orElse(null) -> CallSites for findById AND orElse."""
    ast = parse_kotlin(
        b"fun f() { repo.findById(1).orElse(null) }", filename="F.kt"
    )
    f = _facade_method(ast, "f")
    callees = {s.callee_simple: s for s in f.call_sites}
    assert "findById" in callees
    assert callees["findById"].receiver_expr == "repo"
    assert callees["findById"].arg_count == 1
    assert callees["findById"].is_constructor is False
    assert "orElse" in callees
    assert callees["orElse"].receiver_expr == "repo.findById(1)"


def test_method_reference_arg_count_minus_one() -> None:
    """obj::foo -> CallSite{callee=foo, receiver=obj, arg_count=-1, not chained}."""
    ast = parse_kotlin(b"fun f() { val r = obj::foo }", filename="F.kt")
    f = _facade_method(ast, "f")
    refs = [
        s for s in f.call_sites if s.callee_simple == "foo" and s.arg_count == -1
    ]
    assert len(refs) == 1
    assert refs[0].receiver_expr == "obj"
    assert refs[0].is_constructor is False
    assert refs[0].chained_method_reference is False


def test_chained_method_reference_flag() -> None:
    """a.b()::foo -> chained_method_reference=True (spine is a call chain)."""
    ast = parse_kotlin(b"fun f() { val r = a.b()::foo }", filename="F.kt")
    f = _facade_method(ast, "f")
    refs = [
        s for s in f.call_sites if s.callee_simple == "foo" and s.arg_count == -1
    ]
    assert len(refs) == 1
    assert refs[0].chained_method_reference is True


def test_call_inside_lambda_marked_in_lambda() -> None:
    """A call inside a lambda_literal -> in_lambda=True; the enclosing call is not."""
    ast = parse_kotlin(b"fun f() { list.map { it.foo() } }", filename="F.kt")
    f = _facade_method(ast, "f")
    foos = [s for s in f.call_sites if s.callee_simple == "foo"]
    assert len(foos) == 1
    assert foos[0].in_lambda is True
    maps = [s for s in f.call_sites if s.callee_simple == "map"]
    assert len(maps) == 1
    assert maps[0].in_lambda is False


def test_is_static_call_when_receiver_is_imported_type() -> None:
    """Helper.doThing() where Helper is imported -> is_static_call=True (best-effort).

    Capitalized heuristic is rejected; the receiver must match an explicit import
    or same-CU declared type for is_static_call to be True.
    """
    ast = parse_kotlin(
        b"import com.util.Helper\nclass C { fun go() { Helper.doThing() } }",
        filename="C.kt",
    )
    sites = _call_sites_of(ast, "C", "go")
    dts = [s for s in sites if s.callee_simple == "doThing"]
    assert len(dts) == 1
    assert dts[0].receiver_expr == "Helper"
    assert dts[0].is_static_call is True
    assert dts[0].is_constructor is False


def test_secondary_constructor_delegation_this() -> None:
    """constructor(x: Int) : this(0) -> CallSite{callee=<init>, receiver=this, arg_count=1}."""
    ast = parse_kotlin(
        b"class C { constructor(x: Int) : this(0) {} }", filename="C.kt"
    )
    c = _type_by_fqn(ast, "C")
    assert c is not None
    ctors = [m for m in c.methods if m.is_constructor]
    assert len(ctors) == 1
    this_inits = [
        s
        for s in ctors[0].call_sites
        if s.receiver_expr == "this" and s.callee_simple == "<init>"
    ]
    assert len(this_inits) == 1
    assert this_inits[0].arg_count == 1
    assert this_inits[0].is_constructor is True
