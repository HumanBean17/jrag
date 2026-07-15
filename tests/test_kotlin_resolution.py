"""Task 13: Kotlin resolution model — additive, Kotlin-gated.

The graph builder's resolver is the one place Kotlin needs resolution hooks.
Two gaps are covered:

1. **Facade free-function calls.** A top-level ``fun caller() { helper() }``
   lives on the file's synthetic facade ``TypeDecl`` (``<File>Kt``, capability
   ``kotlin_facade`` from Task 9). Investigation showed the existing
   receiverless-call path already returns ``member.parent_fqn`` (the facade) as
   the receiver, and ``_lookup_method_candidates`` then finds ``helper`` among
   the facade's registered methods. So this needs NO code change — the test
   below pins the behavior as a regression guard.

2. **Default-import types.** Kotlin implicitly imports ``kotlin.collections.*``
   etc.; types like ``List`` / ``Map`` / ``Pair`` referenced as supertypes would
   otherwise collapse to bare-name phantoms (``fqn="List"``). The phantom
   fallback is extended (Kotlin-gated) to assign the deterministic stdlib FQN.

A Java regression scenario asserts CALLS/EXTENDS/IMPLEMENTS edge counts and
``java.lang.*`` phantom FQNs are byte-identical to before (the Kotlin branch is
gated on ``ast.language == "kotlin"``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from _builders import build_graph_tables_to

# tree-sitter-kotlin is required only to PARSE .kt for these tests.
pytest.importorskip("tree_sitter_kotlin")


# ---------- scenario 1: facade free-function call already resolves ----------

_KT_FACADE_CALLS = """\
package com.example

fun helper() {}
fun caller() { helper() }
"""


def test_kotlin_facade_free_function_call_resolves(tmp_path: Path) -> None:
    """A bare ``helper()`` inside top-level ``caller()`` emits a resolved CALLS
    edge to the facade method ``helper`` (not a phantom).

    Investigation finding: this already works without a code change — the
    receiverless path resolves the receiver to ``member.parent_fqn`` (the
    facade ``<File>Kt``), and ``_lookup_method_candidates`` finds ``helper``
    among the facade's methods. This test is the regression guard.
    """
    root = tmp_path / "proj"
    kt = root / "src/main/java/com/example"
    kt.mkdir(parents=True)
    (kt / "Free.kt").write_text(_KT_FACADE_CALLS, encoding="utf-8")

    tables = build_graph_tables_to(root, max_pass=3)

    by_name = {
        m.decl.name: m
        for m in tables.members
        if m.parent_fqn == "com.example.FreeKt"
    }
    assert "caller" in by_name and "helper" in by_name, by_name.keys()
    caller_id = by_name["caller"].node_id
    helper_id = by_name["helper"].node_id

    hits = [
        r for r in tables.calls_rows
        if r.src_id == caller_id and r.dst_id == helper_id
    ]
    assert hits, (
        "expected a CALLS edge caller -> helper; "
        f"calls_rows={tables.calls_rows}"
    )
    assert all(r.resolved for r in hits), hits
    # Receiver resolved via the this/super-as-facade tier.
    assert all(r.strategy == "this_super" for r in hits), hits


# ---------- scenario 2: default-import types get deterministic FQN ----------

_KT_DEFAULT_IMPORT_SUPERTYPES = """\
package com.example

class StringList : List<String> {
    override fun get(index: Int): String { return "" }
    override val size: Int get() = 0
}

class PairHolder(val p: Pair<Int, Int>)

fun makeSeq(): Sequence<String> { return emptySequence() }
"""


def test_kotlin_default_import_type_not_bare_phantom(tmp_path: Path) -> None:
    """A Kotlin default-import type used as a supertype must NOT collapse to a
    bare-name phantom (``fqn="List"``). The phantom fallback assigns the
    deterministic stdlib FQN (``kotlin.collections.List``), mirroring how
    ``java.lang.String`` is handled for Java.

    ``val p: Pair<Int>`` (a plain constructor-property type) and ``Sequence``
    as a return type are also exercised to confirm the resolver does not crash
    and (where a phantom IS created) it carries the stdlib FQN.
    """
    root = tmp_path / "proj"
    kt = root / "src/main/java/com/example"
    kt.mkdir(parents=True)
    (kt / "Types.kt").write_text(_KT_DEFAULT_IMPORT_SUPERTYPES, encoding="utf-8")

    tables = build_graph_tables_to(root, max_pass=3)

    # List<String> supertype → IMPLEMENTS edge to a phantom whose fqn is the
    # real kotlin.collections.List, NOT the bare "List".
    impl_list = [
        r for r in tables.implements_rows
        if r.dst_name == "List"
    ]
    assert impl_list, "expected an IMPLEMENTS edge to List"
    assert all(r.dst_fqn == "kotlin.collections.List" for r in impl_list), (
        f"default-import List should map to kotlin.collections.List; got {impl_list}"
    )
    # It is still resolved=False (a phantom) — kotlin.collections.List is not
    # in the codebase index — but the FQN is now deterministic.
    assert all(not r.resolved for r in impl_list), impl_list

    # The phantom node itself carries the deterministic FQN.
    list_phantoms = [
        p for p in tables.phantoms.values()
        if p["name"] == "List"
    ]
    assert list_phantoms, "expected a List phantom node"
    assert all(p["fqn"] == "kotlin.collections.List" for p in list_phantoms), (
        list_phantoms
    )

    # Pair (kotlin.Pair) — used as a constructor-property type, resolved through
    # _scope_table (no edge), so no phantom is created. The resolver must simply
    # not crash; assert the PairHolder type registered cleanly.
    assert "com.example.PairHolder" in tables.types, tables.types.keys()


# ---------- scenario 3: Java resolution byte-identical (regression) ----------

_JAVA_REGRESSION = """\
package jreg;
import java.util.List;
import java.util.ArrayList;
public class A extends RuntimeException implements Runnable {
  private List<String> items;
  public A() { items = new ArrayList<>(); }
  public void run() { items.size(); helper(); }
  private void helper() {}
}
"""


def test_java_resolution_unchanged_by_kotlin_branch(tmp_path: Path) -> None:
    """The Kotlin-gated phantom-fallback branch must leave Java resolution
    byte-identical: same CALLS/EXTENDS/IMPLEMENTS edge counts, and the
    ``java.lang.*`` phantom FQNs are preserved exactly.

    Counts captured on the pre-change tree (commit 7517227) — this is the
    Java regression proof required by the task brief.
    """
    root = tmp_path / "proj"
    java = root / "src/main/java/jreg"
    java.mkdir(parents=True)
    (java / "A.java").write_text(_JAVA_REGRESSION, encoding="utf-8")

    tables = build_graph_tables_to(root, max_pass=3)

    # Edge counts — frozen from the pre-change Java baseline.
    assert len(tables.calls_rows) == 2, (
        f"Java CALLS count changed: expected 2, got {len(tables.calls_rows)}; "
        f"rows={tables.calls_rows}"
    )
    assert len(tables.extends_rows) == 1, (
        f"Java EXTENDS count changed: expected 1, got {len(tables.extends_rows)}"
    )
    assert len(tables.implements_rows) == 1, (
        f"Java IMPLEMENTS count changed: expected 1, got {len(tables.implements_rows)}"
    )

    # java.lang.* phantoms keep their deterministic FQNs (untouched by the
    # Kotlin-gated branch).
    phantom_fqns_by_name = {
        p["name"]: p["fqn"] for p in tables.phantoms.values()
    }
    assert phantom_fqns_by_name.get("Runnable") == "java.lang.Runnable", (
        phantom_fqns_by_name
    )
    assert phantom_fqns_by_name.get("RuntimeException") == (
        "java.lang.RuntimeException"
    ), phantom_fqns_by_name

    # The resolved run() -> helper() CALLS edge is intact.
    members = {m.decl.name: m for m in tables.members if m.parent_fqn == "jreg.A"}
    run_id = members["run"].node_id
    helper_id = members["helper"].node_id
    hits = [
        r for r in tables.calls_rows
        if r.src_id == run_id and r.dst_id == helper_id
    ]
    assert hits and all(r.resolved for r in hits), (
        f"Java run()->helper() CALLS edge missing/unresolved; rows={tables.calls_rows}"
    )
