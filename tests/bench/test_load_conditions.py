"""Tests for ``bench.load_conditions`` — executable condition isolation (A/B/C/D)."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench.load_conditions import (
    ALL_JRAG_TOOLS,
    JRAG_GRAPH_TOOLS,
    JRAG_VECTOR_TOOLS,
    ConfigError,
    Condition,
    load_conditions,
    to_flags,
)


def _touch_prompts(tmp_path: Path) -> dict[str, Path]:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    paths = {}
    for letter, slug in [("A", "A_lexical"), ("B", "B_vector_only"),
                         ("C", "C_raw_agent"), ("D", "D_jrag_full")]:
        p = prompts / f"{slug}.md"
        p.write_text(f"preamble {letter}\n\n## Your tools\n\nstub\n", encoding="utf-8")
        paths[letter] = p
    return paths


def _write_conditions(tmp_path: Path, body: str) -> str:
    yml = tmp_path / "conditions.yml"
    yml.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return str(yml)


CONDITIONS_BODY = """
conditions:
  - id: A
    name: Lexical
    mcp_servers: []
    allowed_tools: [Grep, Glob, Read, Bash]
    disallowed_tools: []
    prompt_file: {A}
  - id: B
    name: Vector-only
    mcp_servers: [jrag]
    allowed_tools: [Read, mcp__jrag__search]
    disallowed_tools: [mcp__jrag__find, mcp__jrag__describe, mcp__jrag__neighbors, mcp__jrag__resolve]
    prompt_file: {B}
  - id: C
    name: Raw agent
    mcp_servers: []
    allowed_tools: [Read, Glob, Bash]
    disallowed_tools: []
    prompt_file: {C}
  - id: D
    name: jrag full
    mcp_servers: [jrag]
    allowed_tools: [Read, Grep, Glob, mcp__jrag__find, mcp__jrag__describe, mcp__jrag__neighbors, mcp__jrag__resolve, mcp__jrag__search]
    disallowed_tools: []
    prompt_file: {D}
"""


def _load_all(tmp_path, **fmt):
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{**{k: str(paths[k]) for k in paths}, **fmt})
    return load_conditions(_write_conditions(tmp_path, body)), paths


def test_constants():
    assert JRAG_GRAPH_TOOLS == [
        "mcp__jrag__find", "mcp__jrag__describe", "mcp__jrag__neighbors", "mcp__jrag__resolve",
    ]
    assert JRAG_VECTOR_TOOLS == ["mcp__jrag__search"]
    assert set(ALL_JRAG_TOOLS) == set(JRAG_GRAPH_TOOLS) | set(JRAG_VECTOR_TOOLS)


def test_flags_A_no_mcp(tmp_path):
    conds, _ = _load_all(tmp_path)
    a = next(c for c in conds if c.id == "A")
    f = to_flags(a, jrag_mcp_config_path="bench/mcp/jrag.json")
    assert f.mcp_config_arg is None
    assert f.allowed_tools == ["Grep", "Glob", "Read", "Bash"]
    assert f.disallowed_tools == []
    assert "preamble A" in f.append_system_prompt


def test_flags_B_denies_graph_keeps_vector(tmp_path):
    conds, _ = _load_all(tmp_path)
    b = next(c for c in conds if c.id == "B")
    f = to_flags(b, jrag_mcp_config_path="bench/mcp/jrag.json")
    assert set(f.disallowed_tools) == set(JRAG_GRAPH_TOOLS)
    assert f.mcp_config_arg == "bench/mcp/jrag.json"
    assert "mcp__jrag__search" not in f.disallowed_tools


def test_flags_D_denies_nothing_of_jrag(tmp_path):
    conds, _ = _load_all(tmp_path)
    d = next(c for c in conds if c.id == "D")
    f = to_flags(d)
    assert set(f.disallowed_tools) & set(ALL_JRAG_TOOLS) == set()
    assert f.mcp_config_arg == "bench/mcp/jrag.json"


def test_validate_rejects_B_keeping_a_graph_tool(tmp_path):
    conds, paths = _load_all(tmp_path)
    b = next(c for c in conds if c.id == "B")
    # Drop neighbors from B's deny list -> invariant violated.
    bad = Condition(
        id=b.id, name=b.name, mcp_servers=b.mcp_servers,
        allowed_tools=b.allowed_tools,
        disallowed_tools=[t for t in b.disallowed_tools if t != "mcp__jrag__neighbors"],
        prompt_file=b.prompt_file,
    )
    with pytest.raises(ConfigError) as exc:
        from bench.load_conditions import validate
        validate(bad)
    assert "B" in str(exc.value)


def test_validate_rejects_C_with_mcp(tmp_path):
    conds, _ = _load_all(tmp_path)
    c = next(c for c in conds if c.id == "C")
    bad = Condition(
        id=c.id, name=c.name, mcp_servers=["jrag"],
        allowed_tools=c.allowed_tools, disallowed_tools=c.disallowed_tools,
        prompt_file=c.prompt_file,
    )
    from bench.load_conditions import validate
    with pytest.raises(ConfigError):
        validate(bad)


def test_load_rejects_missing_condition_id(tmp_path):
    # Only A/B/C present (D missing) -> id set != {A,B,C,D}.
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{k: str(paths[k]) for k in paths})
    body = body.split("  - id: D")[0]  # drop the D block
    yml = _write_conditions(tmp_path, body)
    with pytest.raises(ConfigError):
        load_conditions(yml)
