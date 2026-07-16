"""Task 14: Kotlin parity for search scoring + chunk heuristics.

Asserts that a Kotlin ``@RestController`` chunk receives the same additive
role/bonus weighting as an equivalent Java controller, and that
``chunk_heuristics`` classifies Kotlin ``import`` / type-declaration lines
(``object``/``class``/``interface``/``fun``) correctly.

These unit tests are dependency-free (no lancedb/torch) so they run on every
install flavor, including graph-only macOS Intel.
"""

from __future__ import annotations

import pytest

from java_codebase_rag.ast.chunk_heuristics import analyze_chunk
from java_codebase_rag.search.search_scoring import (
    _ROLE_SCORE_WEIGHTS,
    _query_tokens,
    _role_weight,
    _symbol_bonus,
)


# ---------- role weighting (search_scoring._role_weight) ----------


def test_kotlin_rest_controller_gets_same_role_weight_as_java() -> None:
    """A Kotlin ``@RestController`` chunk earns the same CONTROLLER bonus as
    an equivalent Java controller — the additive role weighting is keyed off
    the semantic ``language`` field, not just the ``_kind`` table key."""
    java_row = {"_kind": "java", "language": "java", "role": "CONTROLLER"}
    kotlin_row = {"_kind": "kotlin", "language": "kotlin", "role": "CONTROLLER"}

    assert _role_weight(dict(java_row)) == pytest.approx(
        _ROLE_SCORE_WEIGHTS["CONTROLLER"]
    )
    # Kotlin must match Java exactly (both get the +0.10 CONTROLLER bonus).
    assert _role_weight(dict(kotlin_row)) == pytest.approx(
        _role_weight(dict(java_row))
    )
    assert _role_weight(dict(kotlin_row)) > 0.0


def test_kotlin_role_weight_penalizes_dto_like_java() -> None:
    """The negative DTO penalty applies to Kotlin DTOs too (additive, both directions)."""
    kotlin_dto = {"_kind": "kotlin", "language": "kotlin", "role": "DTO"}
    java_dto = {"_kind": "java", "language": "java", "role": "DTO"}

    assert _role_weight(dict(kotlin_dto)) == pytest.approx(
        _role_weight(dict(java_dto))
    )
    assert _role_weight(dict(kotlin_dto)) < 0.0


def test_kotlin_role_weight_respected_when_skip_flag_set() -> None:
    """When the caller locks role via ``_skip_role_weight``, Kotlin is skipped too."""
    kotlin_row = {
        "_kind": "kotlin",
        "language": "kotlin",
        "role": "CONTROLLER",
        "_skip_role_weight": True,
    }
    assert _role_weight(dict(kotlin_row)) == 0.0


# ---------- symbol bonus (search_scoring._symbol_bonus) ----------


def test_kotlin_symbol_bonus_matches_java() -> None:
    """Symbol-name overlap + action-verb bump fires for Kotlin chunks, not just Java."""
    query_toks = _query_tokens("client message arrives")
    shared = {
        "primary_type_fqn": "com.acme.ChatController",
        "symbols": ["processClientMessage(String)", "enqueue(Message)"],
    }
    java_row = {"_kind": "java", "language": "java", **shared}
    kotlin_row = {"_kind": "kotlin", "language": "kotlin", **shared}

    jb = _symbol_bonus(dict(java_row), query_toks)
    kb = _symbol_bonus(dict(kotlin_row), query_toks)

    assert jb > 0.0, "Java baseline should earn a bonus"
    assert kb == pytest.approx(jb), "Kotlin must earn the same bonus as Java"


# ---------- chunk heuristics (chunk_heuristics.analyze_chunk) ----------


def test_chunk_heuristics_kotlin_import_density_fires() -> None:
    """A Kotlin chunk whose lines are mostly ``import`` statements is flagged
    import-heavy (the heuristic fires for Kotlin ``import`` lines, not only Java)."""
    # 4 import lines + 1 fun line: 4/5 = 0.8 >= 0.55 threshold.
    kotlin_src = (
        "package com.acme\n"
        "import com.acme.svc.A\n"
        "import com.acme.svc.B\n"
        "import com.acme.svc.C\n"
        "import com.acme.svc.D\n"
        "fun process() {}\n"
    )
    hints = analyze_chunk(kotlin_src, language="kotlin", kind="kotlin")
    assert hints.import_heavy is True

    # Parity: the same shape Java source is also import-heavy.
    java_src = kotlin_src.replace("fun process() {}", "class App {}")
    java_hints = analyze_chunk(java_src, language="java", kind="java")
    assert java_hints.import_heavy is True


def test_chunk_heuristics_kotlin_object_is_primary_type() -> None:
    """A Kotlin ``object`` declaration is detected as the primary type hint
    (the Java-only regex missed ``object``)."""
    src = "package com.acme\nobject ChatFacade {\n    fun send() {}\n}\n"
    hints = analyze_chunk(src, language="kotlin", kind="kotlin")
    assert hints.primary_type_hint == "ChatFacade"


def test_chunk_heuristics_kotlin_class_is_primary_type() -> None:
    """A Kotlin ``class`` declaration is detected as the primary type hint."""
    src = (
        "package com.acme\n"
        "import com.acme.Svc\n"
        "class GreetingController(private val svc: Svc) {\n"
        "    fun greet() = svc.hello()\n"
        "}\n"
    )
    hints = analyze_chunk(src, language="kotlin", kind="kotlin")
    assert hints.primary_type_hint == "GreetingController"


def test_chunk_heuristics_kotlin_interface_is_primary_type() -> None:
    """A Kotlin ``interface`` declaration is detected as the primary type hint."""
    src = "package com.acme\ninterface Repo<T> {\n    fun find(id: String): T\n}\n"
    hints = analyze_chunk(src, language="kotlin", kind="kotlin")
    assert hints.primary_type_hint == "Repo"


def test_chunk_heuristics_kotlin_detected_via_language_when_kind_is_java() -> None:
    """In production a Kotlin chunk lives in the java LanceDB table, so the row
    carries ``_kind="java"`` while ``language="kotlin"``. The Kotlin branch must
    fire off the ``language`` field in that case too (object detection works)."""
    src = "package com.acme\nobject Facade { fun run() {} }\n"
    hints = analyze_chunk(src, language="kotlin", kind="java")
    assert hints.primary_type_hint == "Facade"


# ---------- Java regression (unchanged behavior) ----------


def test_java_role_weight_unchanged() -> None:
    """Java rows still receive the same role weighting after generalization."""
    row = {"_kind": "java", "language": "java", "role": "SERVICE"}
    assert _role_weight(dict(row)) == pytest.approx(_ROLE_SCORE_WEIGHTS["SERVICE"])


def test_java_chunk_heuristics_unchanged() -> None:
    """Java chunk heuristics (import density + primary type) are unchanged."""
    src = (
        "package com.acme;\n"
        "import com.acme.A;\n"
        "import com.acme.B;\n"
        "import com.acme.C;\n"
        "import com.acme.D;\n"
        "public class App {}\n"
    )
    hints = analyze_chunk(src, language="java", kind="java")
    assert hints.import_heavy is True
    assert hints.primary_type_hint == "App"
