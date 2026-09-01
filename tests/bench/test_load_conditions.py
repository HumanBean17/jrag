"""Tests for ``bench.load_conditions`` — executable condition isolation (A/B/C/D).

Plan 4: the jrag surface is the CLI (``jrag <verb>`` via Bash), gated per
condition by a PATH shim allow-list (``jrag_allowed_verbs``). The shared
``ESCAPE_TOOLS`` deny-list is auto-appended by ``to_flags``; condition B
additionally gets the granular ``JRAG_LEXICAL_DENY`` Bash deny-list.

jrag-prime Task 3: a condition carrying ``tools: prime`` (D in the shipped
spec) has its ``## Your tools`` section replaced at load time by real ``jrag
prime`` output, generated once per index from the cell's context.
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


def _load_all_with_prime(tmp_path):
    """``_load_all`` with ``tools: prime`` on condition D (the shipped spec shape)."""
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{k: str(paths[k]) for k in paths})
    body = body.replace(
        "  - id: D\n    name: jrag full\n",
        "  - id: D\n    name: jrag full\n    tools: prime\n",
        1,
    )
    return load_conditions(_write_conditions(tmp_path, body)), paths


# Snippet of the real ``jrag prime`` payload (``prime.PRIME_TEMPLATE``): realistic
# enough that the "names the jrag CLI + graph verbs" assertions exercise the real
# shape, without forking the whole template into the tests.
PRIME_SENTINEL = (
    "`jrag` is a CLI over a prebuilt structural index of this Java/Kotlin repo — "
    "a map, not an oracle.\n"
    "\n"
    "**Trust rule:** if jrag and the files disagree, trust the files — the index "
    "may lag the working tree.\n"
    "\n"
    "**Index state**\n"
    "\n"
    "- Index fresh (incremented 3h ago) · watch daemon off\n"
    "- 4 services (orders, billing, shipping, payments) · 5231 symbols\n"
    "- 87 routes · 34 clients · 12 producers\n"
    "\n"
    "**Command reference**\n"
    "\n"
    "- `callers` — Who calls this symbol or route?\n"
    "- `callees` — What does this symbol call?\n"
    "- `impact` — Fleet-wide blast radius (INJECTS/IMPLEMENTS/EXTENDS reverse closure).\n"
)

# Dummy but well-typed prime context: the generator is stubbed in every test that
# uses it, so these paths are never executed.
_PRIME_CTX = {
    "jrag_bin": Path("/x/jrag"),
    "source_root": Path("/sr"),
    "index_dir": Path("/idx"),
}


def _stub_prime(monkeypatch, payload: str = PRIME_SENTINEL) -> str:
    """Point the prime generator at a fixed payload and reset the memo cache.

    ``monkeypatch`` restores both, so the process-wide cache never leaks between
    tests.
    """
    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    monkeypatch.setattr(
        "bench.load_conditions._generate_prime_tools_section",
        lambda _bin, _sr, _idx: payload,
    )
    return payload


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


# --- condition D: tools section generated from real `jrag prime` output ---

_TOOLS_MARKER = "## Your tools"


def test_d_tools_section_generated_from_prime(tmp_path, monkeypatch):
    """``tools: prime`` replaces everything after the marker with the generated
    payload; the preamble is carried over byte-identically."""
    sentinel = _stub_prime(monkeypatch)
    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")
    assert d.tools == "prime"

    f = to_flags(d, **_PRIME_CTX)
    assert f.append_system_prompt == (
        prompt_preamble(d.prompt_file) + _TOOLS_MARKER + "\n\n" + sentinel
    )
    # The file's own (stub) tools body is gone.
    assert "stub" not in f.append_system_prompt
    # Flag payload is otherwise untouched by generation.
    assert f.jrag_allowed_verbs == JRAG_QUERY_VERBS
    assert not any(x in JRAG_LEXICAL_DENY for x in f.disallowed_tools)


def test_prime_generation_memoized(tmp_path, monkeypatch):
    """One subprocess per context: the second ``to_flags`` call reuses the cached
    payload, so every D cell of a run composes (and hashes) the same prompt."""
    calls: list[tuple] = []

    def fake_cli(jrag_bin, source_root, index_dir):
        calls.append((jrag_bin, source_root, index_dir))
        return PRIME_SENTINEL

    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    monkeypatch.setattr("bench.load_conditions._run_prime_cli", fake_cli)

    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")
    f1 = to_flags(d, **_PRIME_CTX)
    f2 = to_flags(d, **_PRIME_CTX)

    assert calls == [tuple(_PRIME_CTX.values())]  # exactly one subprocess
    assert f1.append_system_prompt == f2.append_system_prompt


def test_prime_generation_keyed_by_context(tmp_path, monkeypatch):
    """The memo is keyed by the generation context: one bench process runs
    several corpora, each with its own index, and a payload naming corpus A's
    services/counts must never prime corpus B's condition D.
    """
    calls: list[str] = []

    def fake_cli(jrag_bin, source_root, index_dir):
        calls.append(str(index_dir))
        return f"PAYLOAD-FOR-{index_dir}"

    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    monkeypatch.setattr("bench.load_conditions._run_prime_cli", fake_cli)

    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")
    ctx_a = {"jrag_bin": Path("/x/jrag"), "source_root": Path("/sr/a"),
             "index_dir": Path("/idx/a")}
    ctx_b = {"jrag_bin": Path("/x/jrag"), "source_root": Path("/sr/b"),
             "index_dir": Path("/idx/b")}

    fa1 = to_flags(d, **ctx_a).append_system_prompt
    fb = to_flags(d, **ctx_b).append_system_prompt
    fa2 = to_flags(d, **ctx_a).append_system_prompt

    # One subprocess per distinct context, each cached on repeat.
    assert calls == [str(Path("/idx/a")), str(Path("/idx/b"))]
    # Each prompt carries ITS index's payload.
    assert fa1.endswith(_TOOLS_MARKER + "\n\nPAYLOAD-FOR-/idx/a")
    assert fb.endswith(_TOOLS_MARKER + "\n\nPAYLOAD-FOR-/idx/b")
    assert fa1 == fa2


def _fake_jrag(tmp_path: Path, body: str) -> Path:
    """Write an executable fake ``jrag`` binary whose script is ``body``."""
    bin_path = tmp_path / "jrag"
    bin_path.write_text(f"#!/bin/sh\n{body}\n")
    bin_path.chmod(0o755)
    return bin_path


def test_prime_generation_subprocess_failure_raises(tmp_path, monkeypatch):
    """A failing ``jrag prime`` subprocess is a ``ConfigError`` carrying the
    stderr excerpt — the bench never silently falls back to the prompt file's
    stale tools section."""
    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    bad = _fake_jrag(tmp_path, 'echo "jrag prime: RuntimeError: boom" >&2; exit 2')
    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")

    with pytest.raises(ConfigError) as exc:
        to_flags(d, jrag_bin=bad, source_root=tmp_path, index_dir=tmp_path)
    msg = str(exc.value)
    assert "prime" in msg and "boom" in msg


def test_prime_generation_empty_stdout_raises(tmp_path, monkeypatch):
    """``jrag prime`` is hook-safe by design: rc 0 with empty stdout when there
    is no index to describe. For the bench that is a missing tools section, not
    a valid empty payload."""
    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    silent = _fake_jrag(tmp_path, "exit 0")
    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")

    with pytest.raises(ConfigError) as exc:
        to_flags(d, jrag_bin=silent, source_root=tmp_path, index_dir=tmp_path)
    assert "prime" in str(exc.value)


def test_prime_generation_failure_not_cached(tmp_path, monkeypatch):
    """A failed generation is not memoized — repairing the binary and retrying
    succeeds, so one broken cell doesn't poison the whole run."""
    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    bad = _fake_jrag(tmp_path, "echo boom >&2; exit 2")
    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")
    with pytest.raises(ConfigError):
        to_flags(d, jrag_bin=bad, source_root=tmp_path, index_dir=tmp_path)

    # Same path, now working: the failure must not have been pinned in the cache.
    ok = _fake_jrag(tmp_path, "printf 'PRIME-OK\\n'")
    f = to_flags(d, jrag_bin=ok, source_root=tmp_path, index_dir=tmp_path)
    assert f.append_system_prompt.endswith(_TOOLS_MARKER + "\n\nPRIME-OK\n")


def test_prime_generation_runs_cli_with_cell_env(tmp_path, monkeypatch):
    """The subprocess sees JAVA_CODEBASE_RAG_SOURCE_ROOT/INDEX_DIR and argv
    ``<jrag_bin> prime`` — the same context the cell env will carry."""
    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    seen = tmp_path / "seen.txt"
    jrag = _fake_jrag(
        tmp_path,
        f'printf "%s\\n" "$0" "$@" '
        f'"SOURCE=$JAVA_CODEBASE_RAG_SOURCE_ROOT" '
        f'"INDEX=$JAVA_CODEBASE_RAG_INDEX_DIR" > {seen}\n'
        'printf "payload\\n"',
    )
    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")

    f = to_flags(d, jrag_bin=jrag, source_root=tmp_path / "sr", index_dir=tmp_path / "idx")
    recorded = seen.read_text().splitlines()
    assert recorded[0] == str(jrag)
    assert recorded[1:] == ["prime", f"SOURCE={tmp_path / 'sr'}", f"INDEX={tmp_path / 'idx'}"]
    assert f.append_system_prompt.endswith(_TOOLS_MARKER + "\n\npayload\n")


def test_prime_generation_missing_context_raises(tmp_path):
    """``tools: prime`` without jrag_bin/source_root/index_dir is a config error,
    never a silent fall-back to the file's stale tools section."""
    conds, _ = _load_all_with_prime(tmp_path)
    d = next(c for c in conds if c.id == "D")
    with pytest.raises(ConfigError) as exc:
        to_flags(d)
    assert "prime" in str(exc.value)


def test_tools_prime_only_on_d(tmp_path):
    """``tools: prime`` on any condition other than D is rejected at load time."""
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{k: str(paths[k]) for k in paths})
    body = body.replace("  - id: A\n", "  - id: A\n    tools: prime\n", 1)
    with pytest.raises(ConfigError) as exc:
        load_conditions(_write_conditions(tmp_path, body))
    assert "A" in str(exc.value) and "prime" in str(exc.value)


def test_tools_prime_rejects_unknown_value(tmp_path):
    """``tools`` accepts exactly one value: ``prime``."""
    paths = _touch_prompts(tmp_path)
    body = CONDITIONS_BODY.format(**{k: str(paths[k]) for k in paths})
    body = body.replace("  - id: D\n", "  - id: D\n    tools: generated\n", 1)
    with pytest.raises(ConfigError) as exc:
        load_conditions(_write_conditions(tmp_path, body))
    assert "D" in str(exc.value) and "generated" in str(exc.value)


def test_legacy_conditions_keep_file_text_verbatim(tmp_path):
    """Conditions without ``tools`` (all of A/B/C, and a D that omits it) compose
    the prompt file verbatim — the pre-prime behavior, unchanged."""
    conds, paths = _load_all(tmp_path)
    for cond in conds:
        assert cond.tools is None
        assert to_flags(cond).append_system_prompt == paths[cond.id].read_text(
            encoding="utf-8"
        )


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


def test_composed_prompts_differ_only_in_tools_section(monkeypatch):
    """The four COMPOSED prompts (as the agent receives them) share a
    byte-identical preamble and differ only in the tools section.

    A/B/C carry their file text verbatim; D (``tools: prime``) carries the
    generated payload. This is the invariant the whole design rests on: the
    conditions differ ONLY in the tool set, never in the task statement.
    """
    sentinel = _stub_prime(monkeypatch)
    by_id = {c.id: c for c in load_conditions("bench/conditions.yml")}

    preambles: dict[str, str] = {}
    sections: dict[str, str] = {}
    for cid, cond in by_id.items():
        prompt = to_flags(cond, **_PRIME_CTX).append_system_prompt
        preamble, _, section = prompt.partition(_TOOLS_MARKER)
        preambles[cid] = preamble
        sections[cid] = section

    # Preamble byte-identical across A-D — generation must not touch it.
    assert len(set(preambles.values())) == 1, "composed preambles differ"
    assert "## Answer" in next(iter(preambles.values()))

    # Tools sections pairwise distinct.
    keys = list(sections)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert sections[keys[i]] != sections[keys[j]], f"{keys[i]} == {keys[j]}"

    # A/B/C are unchanged: verbatim file text.
    for cid in ("A", "B", "C"):
        assert sections[cid].strip() == prompt_tools_section(_REAL_PROMPTS[cid])

    # each section names exactly the tools available to that condition.
    assert "Grep" in sections["A"] and "Bash" in sections["A"]
    assert "search" in sections["B"] and "graph" in sections["B"].lower()  # graph explicitly off
    assert "Read" in sections["C"] and "Glob" in sections["C"]
    # D's section IS the generated payload, and it teaches the jrag CLI surface
    # incl. graph traversal verbs.
    assert sections["D"] == "\n\n" + sentinel
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


def test_all_conditions_deny_escape_tools(monkeypatch):
    """Regression guard: every condition's ``to_flags()`` denies every ESCAPE_TOOLS
    entry.

    ESCAPE_TOOLS is auto-appended by ``to_flags`` (not hand-listed in
    conditions.yml), so the guard checks the assembled flag payload. Under
    ``--permission-mode bypassPermissions`` only ``--disallowedTools`` blocks, so
    if auto-append ever silently drops an entry the corresponding escape vector
    re-opens: checkout mutation (Edit/Write/NotebookEdit), external info
    (WebSearch/WebFetch), or subagent dispatch (Agent/Task).
    """
    _stub_prime(monkeypatch)  # D composes a generated prompt; stub it out
    conds = load_conditions("bench/conditions.yml")
    by_id = {c.id: c for c in conds}
    assert set(by_id) == {"A", "B", "C", "D"}
    for cid in ("A", "B", "C", "D"):
        denied = set(to_flags(by_id[cid], **_PRIME_CTX).disallowed_tools)
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


def test_jrag_query_verbs_match_cli_agent_verbs():
    """Drift guard: JRAG_QUERY_VERBS is exactly the CLI agent verbs minus the
    non-query ones (watch/vocab-index/prime).

    Ties the bench's hand-list to the canonical ``cli_dispatch.AGENT_VERBS`` so a
    new agent verb is caught here (condition D would under-test it; B can't leak
    it, but the list should stay complete). ``prime`` is excluded with the other
    non-query verbs: the bench harness generates D's prime payload itself
    (jrag-prime Task 3) — the agent under test never invokes ``jrag prime``.
    """
    from java_codebase_rag.cli_dispatch import AGENT_VERBS

    assert set(JRAG_QUERY_VERBS) == set(AGENT_VERBS) - {"watch", "vocab-index", "prime"}


def test_only_condition_B_gets_lexical_deny(monkeypatch):
    """to_flags appends JRAG_LEXICAL_DENY to B only — never A/C/D."""
    _stub_prime(monkeypatch)  # D composes a generated prompt; stub it out
    by_id = {c.id: c for c in load_conditions("bench/conditions.yml")}
    for cid in ("A", "C", "D"):
        denied = set(to_flags(by_id[cid], **_PRIME_CTX).disallowed_tools)
        assert not (set(JRAG_LEXICAL_DENY) & denied), (
            f"condition {cid} should not receive JRAG_LEXICAL_DENY"
        )
    assert set(JRAG_LEXICAL_DENY).issubset(set(to_flags(by_id["B"]).disallowed_tools))


def test_run_cell_passes_prime_context(tmp_path, monkeypatch):
    """``run_cell`` feeds ``to_flags`` the jrag_bin/source_root/index_dir it is
    about to set as cell env, so a condition D cell actually runs with the
    generated tools section (and hashes it).

    Lives here rather than in ``test_claude_runner`` because the wiring under
    test is the prime contract: same context in the payload as in the spawn env.
    """
    import dataclasses
    import os
    import sys

    from bench.claude_runner import CellSpec, run_cell
    from bench.load_corpora import CorpusRecord, IndexManifest
    from bench.load_questions import Question

    # Record the context ``run_cell`` hands to the generator instead of a
    # blind stub, so the test can pin it to the context the spawn env gets.
    prime_calls: list[tuple[str, str, str]] = []

    def recording_prime(jrag_bin, source_root, index_dir):
        prime_calls.append((jrag_bin, source_root, index_dir))
        return PRIME_SENTINEL

    monkeypatch.setattr("bench.load_conditions._PRIME_TOOLS_CACHE", {})
    monkeypatch.setattr(
        "bench.load_conditions._generate_prime_tools_section", recording_prime
    )

    d = next(c for c in load_conditions("bench/conditions.yml") if c.id == "D")
    repo_root = Path(__file__).resolve().parents[2]
    # prompt_file is repo-root-relative; absolutize so to_flags reads it
    # regardless of the driver cwd.
    d = dataclasses.replace(d, prompt_file=str(repo_root / d.prompt_file))
    assert d.tools == "prime"

    checkout = tmp_path / "bench/checkouts/spring-boot-baseline"
    checkout.mkdir(parents=True, exist_ok=True)
    spec = CellSpec(
        question=Question(
            id="bc-impl-01", corpus="spring-boot-baseline",
            category="interface-impls", difficulty="medium",
            question="Find impls of Foo", oracle_source="oracle/foo.py",
            claim_refs=["C1"], grading="programmatic_set_match",
        ),
        condition=d,
        corpus=CorpusRecord(
            name="spring-boot-baseline", source_kind="local", git_url=None,
            commit_sha=None, local_path="/tmp/something", pinned_repo_sha="deadbeef",
            checkout_path="bench/checkouts/spring-boot-baseline",
            index=IndexManifest(
                index_dir="bench/indexes/spring-boot-baseline", ontology_version=1,
            ),
        ),
        model="glm-4.7", seed=0, temperature=0.0, max_turns=10,
        repo_root=str(tmp_path),
    )

    fake_bin = repo_root / "tests/bench/fixtures/fake_claude/emit_short.sh"
    fake_jrag = tmp_path / "fake_jrag.sh"
    fake_jrag.write_text("#!/bin/sh\necho REAL\n")
    fake_jrag.chmod(0o755)

    argv_sidecar = tmp_path / "argv_d.txt"
    monkeypatch.setenv("JRAG_ARGV_SIDECAR", str(argv_sidecar))
    # ``claude_bin --version`` would overwrite the sidecar after the main run.
    monkeypatch.setattr("bench.claude_runner._claude_code_version", lambda _b: None)

    transcript = tmp_path / "d_transcript.jsonl"
    result = run_cell(
        spec,
        claude_bin=str(fake_bin),
        jrag_bin=str(fake_jrag),
        venv_python=sys.executable,
        results_transcript_path=str(transcript),
    )

    assert result.exit_reason == "done"

    # The generator ran once, on the exact context the cell runs under: the
    # resolved jrag binary, the absolutized checkout, and the absolutized
    # index dir — the same three values ``run_cell`` puts on the spawn env
    # (``JAVA_CODEBASE_RAG_INDEX_DIR`` / ``JAVA_CODEBASE_RAG_SOURCE_ROOT``).
    # The relative index_dir absolutizes against the driver's cwd, exactly as
    # the env value does.
    assert prime_calls == [
        (
            os.path.abspath(str(fake_jrag)),
            os.path.join(str(tmp_path), "bench/checkouts/spring-boot-baseline"),
            os.path.abspath("bench/indexes/spring-boot-baseline"),
        )
    ]

    recorded = argv_sidecar.read_text()
    # The spawned --append-system-prompt carries the generated payload...
    assert PRIME_SENTINEL in recorded
    # ...and NOT the prompt file's hand-written tools section.
    assert "You investigate the codebase with the **`jrag` CLI**" not in recorded
    # prompt_hash stays a pure function of the composed prompt.
    assert result.prompt_hash.startswith("sha256:")
