"""Task 15: cross-language integration acceptance gate (the capstone).

Builds a fresh ladybug graph over a mixed Java+Kotlin fixture and asserts the
six cross-language / Spring-detector-parity criteria end-to-end. This exercises
the full pipeline built by Tasks 1-14; it changes NO production code. If an
assertion fails, the failure points back to the responsible task:

  * Task 1  — synthesized JVM accessors (assertion 4: ``getName`` cross-language)
  * Task 6  — Kotlin type kinds (assertion 2: ``@RestController`` -> CONTROLLER)
  * Task 8  — annotations + use-site (assertion 2: role; assertion 3: ctor DI)
  * Task 10 — Kotlin call-site extraction (assertion 1: ``userService.getById``)
  * Task 11 — Kotlin wired into the flow (assertion 6: both languages indexed)
  * Task 13 — resolution model (assertions 1, 4, 5: resolved cross-language edges)

Assertions (must all hold over a freshly built graph):

  1. CALLS edge Kotlin ``UserController.get`` -> Java ``UserService.getById``
     (resolved, not a phantom target).
  2. ``UserController`` is role ``CONTROLLER`` (Spring detector reused on Kotlin).
  3. INJECTS edge into ``UserController`` with mechanism ``constructor``
     (Kotlin primary-constructor injection), pointing at ``UserService``.
  4. Java ``UserDtoPrinter.render`` -> Kotlin synthesized ``UserDto.getName``
     accessor (B1 parity — the cross-language CALLS to the synthesized JVM getter).
  5. IMPLEMENTS edge Kotlin ``GreeterImpl`` -> Java ``Greeter`` (resolved).
  6. The merged graph is queryable for BOTH languages (``.kt`` and ``.java``
     declared Symbol nodes). LanceDB search-layer parity for both languages is
     pinned by ``test_kotlin_flow.py``; here we prove the merged *graph* layer.
"""
from __future__ import annotations

from pathlib import Path

import ladybug
import pytest

# tree-sitter-kotlin grammar is required to parse the .kt half of the fixture.
pytest.importorskip("tree_sitter_kotlin")
pytest.importorskip("ladybug")

from _builders import build_ladybug_to  # noqa: E402

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "mixed-jvm"


def _connect(db_path: Path) -> ladybug.Connection:
    return ladybug.Connection(ladybug.Database(str(db_path), read_only=True))


def _rows(conn: ladybug.Connection, query: str) -> list:
    result = conn.execute(query)
    out: list = []
    while result.has_next():
        out.append(result.get_next())
    return out


def _scalar(conn: ladybug.Connection, query: str) -> int:
    result = conn.execute(query)
    return int(result.get_next()[0] or 0) if result.has_next() else 0


# Diagnostic query for the INJECTS assertion failure message (kept top-level so
# the f-string in the assertion stays backslash-free under Python 3.11).
_Q_OBSERVED_INJECTS = (
    "MATCH (s:Symbol {fqn: 'com.foo.UserController'})-[r:INJECTS]->(d:Symbol) "
    "RETURN r.mechanism, d.fqn"
)


@pytest.fixture(scope="module")
def mixed_jvm_db(tmp_path_factory) -> Path:
    """Fresh ladybug graph (pass1-3 + write_ladybug) over the mixed-jvm fixture.

    pass3 is sufficient: CALLS (pass3), INJECTS/IMPLEMENTS (pass2), and role
    (materialised in ``write_ladybug``) are the surfaces under test. The DB is
    built in a per-session temp dir — no index is committed under ``tests/``.
    """
    assert _FIXTURE_ROOT.is_dir(), f"missing fixture dir: {_FIXTURE_ROOT}"
    db_path = tmp_path_factory.mktemp("mixed_jvm_graph") / "code_graph.lbug"
    return build_ladybug_to(_FIXTURE_ROOT, db_path, max_pass=3)


# ---------------------------------------------------------------------------
# Assertion 1: CALLS edge Kotlin controller method -> Java service method.
# ---------------------------------------------------------------------------


def test_1_kt_controller_calls_java_service_resolved(mixed_jvm_db: Path) -> None:
    """A CALLS edge from Kotlin ``UserController.get`` to Java
    ``UserService.getById`` that is resolved and targets a real (non-phantom)
    method node — the core cross-language call proof."""
    conn = _connect(mixed_jvm_db)
    n = _scalar(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'com.foo.UserController#' AND src.name = 'get' "
        "AND dst.fqn STARTS WITH 'com.foo.UserService#' AND dst.name = 'getById' "
        "AND c.resolved = true AND dst.resolved = true "
        "RETURN count(*) AS n",
    )
    assert n >= 1, (
        "expected a resolved CALLS edge UserController.get -> UserService.getById; "
        f"got count={n}"
    )


# ---------------------------------------------------------------------------
# Assertion 2: Spring role detector reused on Kotlin (@RestController).
# ---------------------------------------------------------------------------


def test_2_kotlin_rest_controller_role_is_controller(mixed_jvm_db: Path) -> None:
    """The Spring stereotype detector maps Kotlin ``@RestController`` to role
    ``CONTROLLER`` exactly as it does for Java (Spring-detector parity)."""
    conn = _connect(mixed_jvm_db)
    rows = _rows(
        conn,
        "MATCH (s:Symbol {fqn: 'com.foo.UserController'}) "
        "RETURN s.role AS role",
    )
    assert rows, "no Symbol node for com.foo.UserController"
    role = rows[0][0]
    assert role == "CONTROLLER", (
        f"UserController role should be CONTROLLER (Spring parity), got {role!r}"
    )
    # Negative control: the Java @Service keeps its role too (detector unchanged).
    svc = _rows(
        conn,
        "MATCH (s:Symbol {fqn: 'com.foo.UserService'}) RETURN s.role AS role",
    )
    assert svc and svc[0][0] == "SERVICE", f"UserService role drifted: {svc}"


# ---------------------------------------------------------------------------
# Assertion 3: INJECTS edge with mechanism 'constructor' (Kotlin primary ctor).
# ---------------------------------------------------------------------------


def test_3_kt_primary_constructor_injects_constructor(mixed_jvm_db: Path) -> None:
    """An INJECTS edge into ``UserController`` with ``mechanism='constructor'``
    targeting the injected ``UserService`` — Kotlin primary-constructor DI,
    surfaced through the same INJECTS table Java uses."""
    conn = _connect(mixed_jvm_db)
    rows = _rows(
        conn,
        "MATCH (src:Symbol {fqn: 'com.foo.UserController'})-[r:INJECTS]->(dst:Symbol) "
        "WHERE r.mechanism = 'constructor' "
        "RETURN r.mechanism AS mechanism, r.field_or_param AS slot, "
        "dst.fqn AS dst_fqn, dst.resolved AS dst_resolved",
    )
    assert rows, (
        "no constructor-mechanism INJECTS edge into UserController; observed "
        f"INJECTS: {_rows(conn, _Q_OBSERVED_INJECTS)}"
    )
    # The injected slot must resolve to the real Java UserService.
    hit = next((r for r in rows if r[2] == "com.foo.UserService"), None)
    assert hit is not None, (
        f"constructor INJECTS should target com.foo.UserService; got rows={rows}"
    )
    assert hit[3] is True or hit[3] == 1, (
        f"INJECTS dst UserService must be resolved (non-phantom); row={hit}"
    )
    assert hit[1] == "userService", f"expected slot 'userService'; row={hit}"


# ---------------------------------------------------------------------------
# Assertion 4: Java caller -> Kotlin synthesized JVM accessor (B1 parity).
# ---------------------------------------------------------------------------


def test_4_java_calls_kotlin_synthesized_accessor(mixed_jvm_db: Path) -> None:
    """A Java caller ``dto.getName()`` on a Kotlin ``data class UserDto(val name)``
    resolves to the synthesized ``getName`` JVM accessor — the cross-language
    CALLS to a Kotlin-synthesised getter (B1 parity, Task 1)."""
    conn = _connect(mixed_jvm_db)
    # The accessor itself exists as a declared method on the Kotlin UserDto.
    accessor = _scalar(
        conn,
        "MATCH (m:Symbol) "
        "WHERE m.fqn = 'com.foo.UserDto#getName()' AND m.name = 'getName' "
        "AND m.kind = 'method' RETURN count(*)",
    )
    assert accessor >= 1, "Kotlin UserDto should synthesize a getName() accessor"
    # And the Java caller resolves a CALLS edge onto it.
    n = _scalar(
        conn,
        "MATCH (src:Symbol)-[c:CALLS]->(dst:Symbol) "
        "WHERE src.fqn STARTS WITH 'com.foo.UserDtoPrinter#' AND src.name = 'render' "
        "AND dst.name = 'getName' AND dst.fqn = 'com.foo.UserDto#getName()' "
        "AND c.resolved = true AND dst.resolved = true "
        "RETURN count(*) AS n",
    )
    assert n >= 1, (
        "expected resolved CALLS UserDtoPrinter.render -> UserDto.getName(); "
        f"got count={n}"
    )


# ---------------------------------------------------------------------------
# Assertion 5: IMPLEMENTS Kotlin class -> Java interface (resolved).
# ---------------------------------------------------------------------------


def test_5_kt_implements_java_interface_resolved(mixed_jvm_db: Path) -> None:
    """A Kotlin class ``class GreeterImpl : Greeter`` emits a resolved IMPLEMENTS
    edge to the Java ``Greeter`` interface (cross-language supertype resolution)."""
    conn = _connect(mixed_jvm_db)
    n = _scalar(
        conn,
        "MATCH (src:Symbol {fqn: 'com.foo.GreeterImpl'})-[r:IMPLEMENTS]->(dst:Symbol) "
        "WHERE dst.fqn = 'com.foo.Greeter' "
        "AND r.resolved = true AND dst.resolved = true "
        "RETURN count(*) AS n",
    )
    assert n >= 1, (
        "expected resolved IMPLEMENTS GreeterImpl -> com.foo.Greeter; "
        f"got count={n}"
    )


# ---------------------------------------------------------------------------
# Assertion 6: querying the merged graph returns BOTH languages.
# ---------------------------------------------------------------------------


def test_6_merged_graph_queryable_for_both_languages(mixed_jvm_db: Path) -> None:
    """The merged graph holds declared Symbol nodes from BOTH ``.kt`` and
    ``.java`` sources — i.e. the merged corpus is queryable for both languages
    through the same graph store that ``jrag``/MCP read."""
    conn = _connect(mixed_jvm_db)
    filenames = {
        str(r[0])
        for r in _rows(
            conn,
            "MATCH (s:Symbol) WHERE s.kind IN ['class','interface','record','enum'] "
            "RETURN DISTINCT s.filename",
        )
    }
    kt_files = {f for f in filenames if f.endswith(".kt")}
    java_files = {f for f in filenames if f.endswith(".java")}
    assert kt_files, f"no Kotlin (.kt) declared types in graph; filenames={filenames}"
    assert java_files, f"no Java (.java) declared types in graph; filenames={filenames}"
    # Concrete presence: the two headline types from each language are reachable.
    headline = _rows(
        conn,
        "MATCH (s:Symbol) "
        "WHERE s.fqn IN ['com.foo.UserController', 'com.foo.UserService'] "
        "RETURN s.fqn AS fqn, s.filename AS filename ORDER BY s.fqn",
    )
    fqns = {str(r[0]) for r in headline}
    assert fqns == {"com.foo.UserController", "com.foo.UserService"}, headline
