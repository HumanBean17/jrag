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
from java_codebase_rag.ast.ast_java import JavaFileAst  # noqa: E402
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
