"""Tests for ``bench.oracle.jqa_runner`` — jqassistant rules on synthetic fixtures.

Each rule is exercised against a tiny fixture whose relationships are known.
Marked ``requires_jqa`` so the suite skips cleanly on hosts lacking the
jqassistant CLI / a JDK.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bench.oracle.jqa_runner import run_rule

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "synthetic"
RULES = Path(__file__).resolve().parents[2] / "bench" / "oracle" / "jqassistant_rules"


@pytest.mark.requires_jqa
def test_implements_rule(jqassistant_bin):
    rows = run_rule(
        FIXTURES / "implements_demo",
        RULES / "implements.cypher",
        jqassistant_bin=jqassistant_bin,
    )
    pairs = {(r["implementer_fqn"], r["interface_fqn"]) for r in rows}
    assert ("impl.Cat", "impl.Animal") in pairs
    assert ("impl.Dog", "impl.Animal") in pairs
    # Plant does not implement Animal.
    assert not any(impl == "impl.Plant" and iface == "impl.Animal" for impl, iface in pairs)


@pytest.mark.requires_jqa
def test_injects_rule(jqassistant_bin):
    rows = run_rule(
        FIXTURES / "injects_demo",
        RULES / "injects.cypher",
        jqassistant_bin=jqassistant_bin,
    )
    pairs = {(r["injector_fqn"], r["injected_type_fqn"]) for r in rows}
    assert ("inj.Cart", "inj.PricingService") in pairs


@pytest.mark.requires_jqa
def test_calls_out_rule(jqassistant_bin):
    rows = run_rule(
        FIXTURES / "calls_demo",
        RULES / "calls_out.cypher",
        params={"caller": "call.Caller"},
        jqassistant_bin=jqassistant_bin,
    )
    callees = {r["callee_fqn"] for r in rows}
    assert callees == {"call.Callee"}


@pytest.mark.requires_jqa
def test_calls_in_rule(jqassistant_bin):
    rows = run_rule(
        FIXTURES / "calls_demo",
        RULES / "calls_in.cypher",
        params={"callee": "call.Callee"},
        jqassistant_bin=jqassistant_bin,
    )
    callers = {r["caller_fqn"] for r in rows}
    assert callers == {"call.Caller"}


@pytest.mark.requires_jqa
def test_role_controllers_rule(jqassistant_bin):
    rows = run_rule(
        FIXTURES / "roles_demo",
        RULES / "role_controllers.cypher",
        jqassistant_bin=jqassistant_bin,
    )
    fqns = {r["fqn"] for r in rows}
    assert "role.CatController" in fqns
    assert "role.DogController" in fqns
    assert "role.NotAController" not in fqns


@pytest.mark.requires_jqa
def test_transitive_blast_rule(jqassistant_bin):
    rows = run_rule(
        FIXTURES / "blast_demo",
        RULES / "transitive_blast.cypher",
        params={"seed": "blast.C"},
        jqassistant_bin=jqassistant_bin,
    )
    impacted = {r["impacted_fqn"] for r in rows}
    # B depends on C (depth 1); A depends on B->C (depth 2).
    assert impacted == {"blast.B", "blast.A"}
