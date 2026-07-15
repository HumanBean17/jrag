"""Tests for the Kotlin AST extractor (`ast.ast_kotlin.parse_kotlin`).

Task 5 builds the foundation: package + imports into the existing
``JavaFileAst`` shape, a per-thread tree-sitter ``Parser``, and registers
``KotlinBackend`` behind the language-dispatch seam. Declarations
(classes/functions) come in Task 6, so ``top_level_types`` / ``all_types``
are empty lists here.
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


def test_type_lists_empty_in_foundation_task() -> None:
    """(g) Declarations come in Task 6; lists are empty now."""
    ast = parse_kotlin(_SOURCE, filename="F.kt")
    assert ast.top_level_types == []
    assert ast.all_types == []


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
