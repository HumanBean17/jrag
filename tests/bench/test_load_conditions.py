"""Tests for ``bench.load_conditions`` — executable condition isolation (A/B/C/D).

Plan 4: the jrag surface is the CLI (``jrag <verb>`` via Bash), gated per
condition by a PATH shim allow-list (``jrag_allowed_verbs``). The shared
``ESCAPE_TOOLS`` deny-list is auto-appended by ``to_flags``; condition B
additionally gets the granular ``JRAG_LEXICAL_DENY`` Bash deny-list.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bench.load_conditions import (
    ESCAPE_TOOLS,
    JRAG_LEXICAL_DENY,
    JRAG_QUERY_VERBS,
    JRAG_SEARCH_VERBS,
    ConfigError,
    Condition,
    load_conditions,
    prompt_preamble,
    prompt_tools_section,
    to_flags,
    validate,
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
    allowed_tools: [Grep, Glob, Read, Bash]
    disallowed_tools: []
    prompt_file: {A}
  - id: B
    name: Vector-only
    jrag_allowed_verbs: [search]
    allowed_tools: [Read, Bash]
    disallowed_tools: [Grep, Glob]
    prompt_file: {B}
  - id: C
    name: Raw agent
    allowed_tools: [Read, Glob, Bash]
    disallowed_tools: [Grep]
    prompt_file: {C}
  - id: D
    name: jrag full
    jrag_allowed_verbs: all
    allowed_tools: [Read, Grep, Glob, Bash]
    disallowed_tools: []
    prompt_file: {D}
"""


def _load_all(tmp_path, **fmt):
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{**{k: str(paths[k]) for k in paths}, **fmt})
    return load_conditions(_write_conditions(tmp_path, body)), paths


def test_constants():
    assert JRAG_SEARCH_VERBS == ["search"]
    # query surface is the full agent verb set minus daemon/maintenance verbs
    assert "search" in JRAG_QUERY_VERBS
    assert "callers" in JRAG_QUERY_VERBS and "flow" in JRAG_QUERY_VERBS
    assert "watch" not in JRAG_QUERY_VERBS
    assert "vocab-index" not in JRAG_QUERY_VERBS
    assert ESCAPE_TOOLS == [
        "Edit", "Write", "NotebookEdit", "WebSearch", "WebFetch", "Agent", "Task",
    ]
    assert JRAG_LEXICAL_DENY  # non-empty
    assert all(d.startswith("Bash(") and d.endswith(" *)") for d in JRAG_LEXICAL_DENY)
    assert "Bash(grep *)" in JRAG_LEXICAL_DENY


def test_flags_A_no_jrag(tmp_path):
    conds, _ = _load_all(tmp_path)
    a = next(c for c in conds if c.id == "A")
    f = to_flags(a)
    assert f.jrag_allowed_verbs is None
    assert f.allowed_tools == ["Grep", "Glob", "Read", "Bash"]
    # ESCAPE_TOOLS is auto-appended even though conditions.yml A has an empty deny.
    assert set(ESCAPE_TOOLS).issubset(set(f.disallowed_tools))
    # A gets NO lexical deny.
    assert not any(d in JRAG_LEXICAL_DENY for d in f.disallowed_tools)
    assert "preamble A" in f.append_system_prompt


def test_flags_B_search_only_with_lexical_deny(tmp_path):
    conds, _ = _load_all(tmp_path)
    b = next(c for c in conds if c.id == "B")
    f = to_flags(b)
    assert f.jrag_allowed_verbs == ["search"]
    # B denies the lexical tools (explicit) + ESCAPE (auto) + lexical Bash deny (auto).
    assert set(ESCAPE_TOOLS).issubset(set(f.disallowed_tools))
    assert set(JRAG_LEXICAL_DENY).issubset(set(f.disallowed_tools))
    assert "Grep" in f.disallowed_tools and "Glob" in f.disallowed_tools


def test_flags_D_all_verbs(tmp_path):
    conds, _ = _load_all(tmp_path)
    d = next(c for c in conds if c.id == "D")
    # The 'all' sentinel resolves to the full query surface at load time.
    assert d.jrag_allowed_verbs == JRAG_QUERY_VERBS
    f = to_flags(d)
    assert f.jrag_allowed_verbs == JRAG_QUERY_VERBS
    # D gets only the ESCAPE auto-deny — no lexical deny.
    assert set(ESCAPE_TOOLS).issubset(set(f.disallowed_tools))
    assert not any(x in JRAG_LEXICAL_DENY for x in f.disallowed_tools)


def test_validate_rejects_B_with_extra_verb(tmp_path):
    conds, paths = _load_all(tmp_path)
    b = next(c for c in conds if c.id == "B")
    bad = Condition(
        id=b.id, name=b.name,
        allowed_tools=b.allowed_tools,
        disallowed_tools=b.disallowed_tools,
        prompt_file=b.prompt_file,
        jrag_allowed_verbs=["search", "callers"],  # graph verb leaks into vector-only
    )
    with pytest.raises(ConfigError) as exc:
        validate(bad)
    assert "B" in str(exc.value)


def test_validate_rejects_C_with_jrag(tmp_path):
    conds, _ = _load_all(tmp_path)
    c = next(c for c in conds if c.id == "C")
    bad = Condition(
        id=c.id, name=c.name,
        allowed_tools=c.allowed_tools, disallowed_tools=c.disallowed_tools,
        prompt_file=c.prompt_file,
        jrag_allowed_verbs=["search"],
    )
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


# --- locked prompts differ ONLY in the tools section. ---

_REAL_PROMPTS = {
    "A": "bench/prompts/A_lexical.md",
    "B": "bench/prompts/B_vector_only.md",
    "C": "bench/prompts/C_raw_agent.md",
    "D": "bench/prompts/D_jrag_full.md",
}


def test_preambles_identical():
    preambles = {k: prompt_preamble(p) for k, p in _REAL_PROMPTS.items()}
    values = list(preambles.values())
    assert all(v == values[0] for v in values), (
        f"preambles differ: {[ (k, hash(v)) for k, v in preambles.items()]}"
    )
    # preamble must actually state the task/output contract, not be empty.
    assert "## Answer" in values[0]
    assert "Tools used:" in values[0]


def test_tools_sections_differ():
    sections = {k: prompt_tools_section(p) for k, p in _REAL_PROMPTS.items()}
    # pairwise distinct
    keys = list(sections)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert sections[keys[i]] != sections[keys[j]], f"{keys[i]} == {keys[j]}"

    # each section names exactly the tools available to that condition.
    assert "Grep" in sections["A"] and "Bash" in sections["A"]
    assert "search" in sections["B"] and "graph" in sections["B"].lower()  # graph explicitly off
    assert "Read" in sections["C"] and "Glob" in sections["C"]
    # D teaches the jrag CLI surface incl. graph traversal verbs.
    assert "jrag" in sections["D"].lower() and "callers" in sections["D"]


def test_rejects_unknown_condition_key(tmp_path):
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{k: str(paths[k]) for k in paths})
    # Inject a stray key into condition A.
    body = body.replace("  - id: A\n", "  - id: A\n    commentary: stray\n", 1)
    yml = _write_conditions(tmp_path, body)
    with pytest.raises(ConfigError) as exc:
        load_conditions(yml)
    assert "commentary" in str(exc.value)


def test_condition_C_isolation_shape():
    """Assert exact shape of condition C (isolation baseline)."""
    conds = load_conditions("bench/conditions.yml")
    c = next(cond for cond in conds if cond.id == "C")
    assert c.name == "Raw agent + shell (no Grep tool, no jrag)"
    assert c.allowed_tools == ["Read", "Glob", "Bash"]
    assert c.disallowed_tools == ["Grep"]
    assert c.jrag_allowed_verbs is None


def test_all_conditions_deny_escape_tools():
    """Regression guard: every condition's ``to_flags()`` denies every ESCAPE_TOOLS
    entry.

    ESCAPE_TOOLS is auto-appended by ``to_flags`` (not hand-listed in
    conditions.yml), so the guard checks the assembled flag payload. Under
    ``--permission-mode bypassPermissions`` only ``--disallowedTools`` blocks, so
    if auto-append ever silently drops an entry the corresponding escape vector
    re-opens: checkout mutation (Edit/Write/NotebookEdit), external info
    (WebSearch/WebFetch), or subagent dispatch (Agent/Task).
    """
    conds = load_conditions("bench/conditions.yml")
    by_id = {c.id: c for c in conds}
    assert set(by_id) == {"A", "B", "C", "D"}
    for cid in ("A", "B", "C", "D"):
        denied = set(to_flags(by_id[cid]).disallowed_tools)
        missing = set(ESCAPE_TOOLS) - denied
        assert not missing, (
            f"condition {cid} dropped escape-tool denies: {sorted(missing)}"
        )


def test_validate_rejects_condition_allowing_escape_tool(tmp_path):
    """``validate()`` rejects a condition that ALLOWS an escape tool.

    ESCAPE_TOOLS is auto-denied by ``to_flags``; a condition that simultaneously
    allows one (e.g. ``WebFetch``) is a self-contradictory spec. Caught at load
    time, not at analysis time.
    """
    paths = _touch_prompts(tmp_path)
    bad = Condition(
        id="A",
        name="Lexical",
        allowed_tools=["Grep", "Glob", "Read", "Bash", "WebFetch"],
        disallowed_tools=[],
        prompt_file=str(paths["A"]),
    )
    with pytest.raises(ConfigError) as exc:
        validate(bad)
    msg = str(exc.value)
    assert "A" in msg
    assert "WebFetch" in msg
