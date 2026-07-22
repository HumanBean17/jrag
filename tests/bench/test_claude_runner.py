"""Tests for bench.claude_runner — stream-json parser + argv assembly."""

import json
from pathlib import Path

import pytest

from bench.load_conditions import (
    ESCAPE_TOOLS,
    JRAG_LEXICAL_DENY,
    JRAG_QUERY_VERBS,
    Condition,
    ConditionFlags,
    to_flags,
)
from bench.load_corpora import CorpusRecord, IndexManifest
from bench.load_questions import Question


def test_parse_minimal_done(tmp_path):
    """Parse minimal complete stream with result event."""
    # We'll import after module creation
    from bench.claude_runner import parse_stream, StreamSummary

    fixture_path = (
        Path(__file__).parent / "fixtures" / "streams" / "minimal_done.jsonl"
    )

    with fixture_path.open() as f:
        summary = parse_stream(f)

    assert summary.n_turns == 1
    assert summary.tool_call_breakdown == {"Read": 1}
    assert summary.context_bytes_retrieved == 6  # "hello\n" is 6 chars
    assert summary.tokens == {"input": 100, "output": 5, "total": 105}
    assert summary.stop_reason == "end_turn"
    assert summary.terminal_reason == "completed"
    assert summary.is_error is False
    assert summary.num_turns_reported == 1


def test_parse_real_run4(tmp_path):
    """Parse real run4 transcript from actual claude -p run."""
    from bench.claude_runner import parse_stream, StreamSummary

    fixture_path = Path(__file__).parent / "fixtures" / "streams" / "run4.jsonl"

    with fixture_path.open() as f:
        summary = parse_stream(f)

    assert summary.tool_call_breakdown == {
        "mcp__jrag__resolve": 1,
        "mcp__jrag__neighbors": 1,
    }
    assert summary.n_turns >= 2
    assert summary.num_turns_reported == 3
    assert summary.terminal_reason == "completed"
    assert summary.is_error is False
    assert summary.final_answer is not None
    assert "AckProcessor" in summary.final_answer


def test_parse_truncated_no_result(tmp_path):
    """Parse stream with no result event (truncated/capped case)."""
    from bench.claude_runner import parse_stream, StreamSummary

    lines = [
        '{"type":"assistant","message":{"id":"msg_1","type":"message","role":"assistant","content":[{"type":"tool_use","id":"call_1","name":"Read","input":{"file_path":"/tmp/test.txt"}}]}}\n',
    ]

    summary = parse_stream(iter(lines))

    assert summary.n_turns == 1
    assert summary.tool_call_breakdown == {"Read": 1}
    assert summary.terminal_reason is None
    assert summary.num_turns_reported is None


def test_materialize_cli_env_writes_gated_shim(tmp_path):
    """materialize_cli_env writes an executable shim that embeds the verbs + real path."""
    import os
    import sys

    from bench.claude_runner import materialize_cli_env

    real = tmp_path / "real_jrag.sh"
    real.write_text("#!/bin/sh\necho REAL: \"$@\"\n")
    real.chmod(0o755)

    shim_dir = materialize_cli_env(
        cell_dir=str(tmp_path),
        allowed_verbs=["search"],
        real_jrag_bin=str(real),
        venv_python=sys.executable,
    )
    shim = Path(shim_dir) / "jrag"
    assert shim.exists()
    assert os.access(shim, os.X_OK)
    src = shim.read_text()
    # shebang is the venv python; the real binary path and the verb are embedded
    assert src.startswith(f"#!{sys.executable}\n")
    assert str(real) in src
    assert "search" in src


def test_shim_gates_verbs(tmp_path):
    """The shim exec's the real binary for allowed verbs (+meta) and exits 2 otherwise."""
    import subprocess
    import sys

    from bench.claude_runner import materialize_cli_env

    real = tmp_path / "real_jrag.sh"
    real.write_text("#!/bin/sh\necho REAL: \"$@\"\n")
    real.chmod(0o755)

    shim_dir = materialize_cli_env(
        cell_dir=str(tmp_path),
        allowed_verbs=["search"],
        real_jrag_bin=str(real),
        venv_python=sys.executable,
    )
    shim = str(Path(shim_dir) / "jrag")

    # allowed verb -> real runs with the args
    r_ok = subprocess.run([shim, "search", "foo"], capture_output=True, text=True)
    assert r_ok.returncode == 0
    assert "REAL: search foo" in r_ok.stdout

    # blocked verb -> exit 2, real NOT executed
    r_blocked = subprocess.run([shim, "callers", "Foo"], capture_output=True, text=True)
    assert r_blocked.returncode == 2
    assert "not available" in r_blocked.stderr
    assert "REAL" not in r_blocked.stdout

    # meta --help passes through to the real binary
    r_help = subprocess.run([shim, "--help"], capture_output=True, text=True)
    assert r_help.returncode == 0
    assert "REAL: --help" in r_help.stdout


def test_resolve_real_jrag_finds_sibling(tmp_path, monkeypatch):
    """_resolve_real_jrag prefers <venv>/bin/jrag, falls back to PATH, else errors."""
    from bench.claude_runner import _resolve_real_jrag, ConfigError

    # Fake a venv layout: <dir>/python + <dir>/jrag
    venv_bin = tmp_path / "bin"
    venv_bin.mkdir()
    py = venv_bin / "python"
    py.write_text("#!/bin/sh\n")
    jrag = venv_bin / "jrag"
    jrag.write_text("#!/bin/sh\n")
    assert _resolve_real_jrag(str(py)) == str(jrag)

    # No sibling, nothing on PATH -> ConfigError (monkeypatch which so the real
    # venv jrag on PATH during pytest doesn't satisfy the fallback).
    monkeypatch.setattr("bench.claude_runner.shutil.which", lambda _cmd: None)
    with pytest.raises(ConfigError):
        _resolve_real_jrag(str(tmp_path / "nopython"))


# --- Task 3: CellSpec + argv assembly ---

def _corpus(name: str = "spring-boot-baseline") -> CorpusRecord:
    """Minimal CorpusRecord for argv tests."""
    return CorpusRecord(
        name=name,
        source_kind="local",
        git_url=None,
        commit_sha=None,
        local_path="/tmp/something",
        pinned_repo_sha="deadbeef",
        checkout_path=f"bench/checkouts/{name}",
        index=IndexManifest(
            index_dir=f"bench/indexes/{name}",
            ontology_version=1,
        ),
    )


def _question(qid: str = "bc-impl-01", text: str = "Find the impls") -> Question:
    return Question(
        id=qid,
        corpus="spring-boot-baseline",
        category="interface-impls",
        difficulty="medium",
        question=text,
        oracle_source="oracle/foo.py",
        claim_refs=["C1"],
        grading="programmatic_set_match",
    )


def _condition(letter: str) -> Condition:
    """Build a real-shaped Condition matching conditions.yml for the given id."""
    if letter == "A":
        return Condition(
            id="A", name="Lexical",
            allowed_tools=["Grep", "Glob", "Read", "Bash"],
            disallowed_tools=[],
            prompt_file="bench/prompts/A_lexical.md",
        )
    if letter == "B":
        return Condition(
            id="B", name="Vector-only",
            jrag_allowed_verbs=["search"],
            allowed_tools=["Read", "Bash"],
            disallowed_tools=["Grep", "Glob"],
            prompt_file="bench/prompts/B_vector_only.md",
        )
    if letter == "D":
        return Condition(
            id="D", name="jrag full",
            jrag_allowed_verbs=list(JRAG_QUERY_VERBS),
            allowed_tools=["Read", "Grep", "Glob", "Bash"],
            disallowed_tools=[],
            prompt_file="bench/prompts/D_jrag_full.md",
        )
    raise ValueError(f"no fixture for condition {letter!r}")


def _flags_for(cond: Condition, prompt_contents: str = "PROMPT") -> ConditionFlags:
    return ConditionFlags(
        allowed_tools=list(cond.allowed_tools),
        disallowed_tools=list(cond.disallowed_tools),
        append_system_prompt=prompt_contents,
        jrag_allowed_verbs=cond.jrag_allowed_verbs,
    )


def test_argv_condition_A():
    """Condition A produces a claude argv with no jrag and no --disallowedTools."""
    from bench.claude_runner import CellSpec, build_argv

    cond = _condition("A")
    flags = _flags_for(cond, prompt_contents="PROMPT-A")
    spec = CellSpec(
        question=_question(text="Find impls of Foo"),
        condition=cond,
        corpus=_corpus(),
        model="glm-4.7",
        seed=0,
        temperature=0.0,
        max_turns=10,
        repo_root="/repo/root",
    )

    argv = build_argv(spec, flags)

    assert argv[0] == "claude"
    assert "-p" in argv
    assert "Find impls of Foo" in argv
    assert "--output-format" in argv
    assert "stream-json" in argv
    assert "--verbose" in argv
    assert "--permission-mode" in argv
    assert "bypassPermissions" in argv
    assert "--model" in argv
    assert "glm-4.7" in argv
    # absolute checkout
    assert "--add-dir" in argv
    abs_checkout = "/repo/root/bench/checkouts/spring-boot-baseline"
    assert abs_checkout in argv
    # append-system-prompt carries the prompt CONTENTS string
    assert "--append-system-prompt" in argv
    assert "PROMPT-A" in argv
    # allowedTools is comma-joined
    allowed_idx = argv.index("--allowedTools")
    assert argv[allowed_idx + 1] == "Grep,Glob,Read,Bash"
    # No MCP flags (the jrag surface is the CLI via a PATH shim, never --mcp-config)
    assert "--mcp-config" not in argv
    assert "--strict-mcp-config" not in argv
    # No disallowedTools for condition A (via _flags_for; ESCAPE auto-appends in to_flags)
    assert "--disallowedTools" not in argv
    # Forbidden flags must NEVER appear
    assert "--max-turns" not in argv
    assert "--temperature" not in argv
    assert "--seed" not in argv


def test_argv_condition_D():
    """Condition D (jrag full) exposes jrag via Bash; never --mcp-config."""
    from bench.claude_runner import CellSpec, build_argv

    cond = _condition("D")
    flags = _flags_for(cond, prompt_contents="PROMPT-D")
    spec = CellSpec(
        question=_question(text="Find impls of Bar"),
        condition=cond,
        corpus=_corpus(),
        model="glm-4.7",
        seed=1,
        temperature=0.0,
        max_turns=10,
        repo_root="/repo/root",
    )

    argv = build_argv(spec, flags)

    # No MCP flags — jrag is the CLI, driven via a PATH shim.
    assert "--mcp-config" not in argv
    assert "--strict-mcp-config" not in argv
    # allowedTools has Read/Grep/Glob/Bash (the agent drives `jrag` via Bash).
    allowed_idx = argv.index("--allowedTools")
    allowed_members = set(argv[allowed_idx + 1].split(","))
    assert {"Read", "Grep", "Glob", "Bash"}.issubset(allowed_members)
    # Forbidden flags must not appear
    assert "--max-turns" not in argv
    assert "--temperature" not in argv
    assert "--seed" not in argv


def test_argv_condition_B_denies_lexical():
    """Condition B (vector-only): --disallowedTools includes Grep/Glob + the granular
    Bash lexical deny; --allowedTools has Read+Bash (jrag search via Bash)."""
    from bench.claude_runner import CellSpec, build_argv

    cond = _condition("B")
    # Real payload: ESCAPE + Grep/Glob + JRAG_LEXICAL_DENY auto-appended by to_flags.
    flags = to_flags(cond)
    spec = CellSpec(
        question=_question(text="Find impls of Baz"),
        condition=cond,
        corpus=_corpus(),
        model="glm-4.7",
        seed=2,
        temperature=0.0,
        max_turns=10,
        repo_root="/repo/root",
    )

    argv = build_argv(spec, flags)

    assert "--disallowedTools" in argv
    dis_idx = argv.index("--disallowedTools")
    denied = set(argv[dis_idx + 1].split(","))
    assert "Grep" in denied and "Glob" in denied
    assert set(ESCAPE_TOOLS).issubset(denied)
    assert "Bash(grep *)" in denied  # lexical escape closed at the Bash-prefix level
    # allowedTools has Read + Bash (the agent runs `jrag search` via Bash)
    allowed_idx = argv.index("--allowedTools")
    allowed = set(argv[allowed_idx + 1].split(","))
    assert "Read" in allowed and "Bash" in allowed
    # No MCP flags
    assert "--mcp-config" not in argv


def test_run_id_format():
    """run_id is f"{q.id}_{c.id}_{model}_s{seed}"."""
    from bench.claude_runner import CellSpec, run_id

    cond = _condition("D")
    spec = CellSpec(
        question=_question(qid="bc-impl-01", text="irrelevant"),
        condition=cond,
        corpus=_corpus(),
        model="glm-4.7",
        seed=0,
        temperature=0.0,
        max_turns=10,
        repo_root="/repo/root",
    )

    assert run_id(spec) == "bc-impl-01_D_glm-4.7_s0"


# --- Task 4: CellResult + JSONL schema (pure) ---


def test_to_cell_jsonl_has_schema_keys():
    """to_cell_jsonl returns a dict with exactly the 23 schema field names."""
    from bench.claude_runner import CellResult, to_cell_jsonl

    # Construct a CellResult with representative values
    result = CellResult(
        run_id="bc-impl-01_A_glm-4.7_s0",
        question_id="bc-impl-01",
        corpus="spring-boot-baseline",
        corpus_commit="deadbeef1234567890",
        condition="A",
        model="glm-4.7",
        seed=0,
        temperature=0.0,
        claude_code_version="1.0.0",
        ontology_version=1,
        index_build_id="build-001",
        prompt_hash="abc123",
        started_at="2026-07-21T12:00:00Z",
        finished_at="2026-07-21T12:01:00Z",
        wall_s=60.0,
        n_turns=5,
        n_tool_calls=10,
        tool_call_breakdown={"Read": 3, "Grep": 7},
        tokens={"input": 1000, "output": 500, "total": 1500},
        context_bytes_retrieved=2000,
        exit_reason="done",
        final_answer="The answer is Foo",
        transcript_path="/tmp/transcript.jsonl",
        grade=None,
    )

    # Call to_cell_jsonl
    jsonl_dict = to_cell_jsonl(result)

    # Verify it's a dict
    assert isinstance(jsonl_dict, dict)

    # Verify key set equals exactly the 23 schema field names
    expected_keys = {
        "run_id",
        "question_id",
        "corpus",
        "corpus_commit",
        "condition",
        "model",
        "seed",
        "temperature",
        "claude_code_version",
        "ontology_version",
        "index_build_id",
        "prompt_hash",
        "started_at",
        "finished_at",
        "wall_s",
        "n_turns",
        "n_tool_calls",
        "tool_call_breakdown",
        "tokens",
        "context_bytes_retrieved",
        "exit_reason",
        "final_answer",
        "transcript_path",
        "grade",
    }
    assert set(jsonl_dict.keys()) == expected_keys

    # Verify grade is None
    assert jsonl_dict["grade"] is None

    # Verify it round-trips through JSON
    json_str = json.dumps(jsonl_dict)
    restored = json.loads(json_str)
    assert restored == jsonl_dict


def test_exit_reason_done():
    """derive_exit_reason returns 'done' when not capped, not error, no api_error_status."""
    from bench.claude_runner import StreamSummary, derive_exit_reason

    summary = StreamSummary(is_error=False, api_error_status=None)
    capped = False

    assert derive_exit_reason(summary, capped) == "done"


def test_exit_reason_cap_overrides():
    """derive_exit_reason returns 'cap' when capped=True, regardless of summary."""
    from bench.claude_runner import StreamSummary, derive_exit_reason

    summary = StreamSummary(is_error=True, api_error_status="500")
    capped = True

    assert derive_exit_reason(summary, capped) == "cap"


def test_exit_reason_error():
    """derive_exit_reason returns 'error' when is_error=True or api_error_status set."""
    from bench.claude_runner import StreamSummary, derive_exit_reason

    # Test is_error=True
    summary1 = StreamSummary(is_error=True, api_error_status=None)
    assert derive_exit_reason(summary1, capped=False) == "error"

    # Test api_error_status set
    summary2 = StreamSummary(is_error=False, api_error_status="500")
    assert derive_exit_reason(summary2, capped=False) == "error"


def test_n_turns_prefers_reported():
    """choose_n_turns returns num_turns_reported when not None, else n_turns."""
    from bench.claude_runner import StreamSummary, choose_n_turns

    # Prefer num_turns_reported
    summary1 = StreamSummary(num_turns_reported=3, n_turns=2)
    assert choose_n_turns(summary1) == 3

    # Fall back to n_turns when num_turns_reported is None
    summary2 = StreamSummary(num_turns_reported=None, n_turns=5)
    assert choose_n_turns(summary2) == 5


# --- Task 5: run_cell — subprocess spawn + driver-side turn cap ---

import os  # noqa: E402

_FAKE_CLAUDE_DIR = Path(__file__).parent / "fixtures" / "fake_claude"


def _spec_for(
    cond_letter: str,
    tmp_path: Path,
    *,
    max_turns: int = 10,
    question_text: str = "Find impls of Foo",
) -> "CellSpec":
    """Build a CellSpec whose cwd exists under tmp_path and uses real prompts."""
    from bench.claude_runner import CellSpec

    cond = _condition(cond_letter)
    corpus = _corpus()
    # Create the checkout dir so subprocess.Popen(cwd=...) succeeds.
    checkout_abs = tmp_path / corpus.checkout_path
    checkout_abs.mkdir(parents=True, exist_ok=True)
    return CellSpec(
        question=_question(text=question_text),
        condition=cond,
        corpus=corpus,
        model="glm-4.7",
        seed=0,
        temperature=0.0,
        max_turns=max_turns,
        repo_root=str(tmp_path),
    )


def test_run_cell_caps_at_max_turns(tmp_path, monkeypatch):
    """run_cell SIGTERMs on the assistant event exceeding max_turns.

    With max_turns=2 and emit_long.sh printing 4 assistant events then a result,
    the 3rd assistant event triggers the cap; the transcript lacks a result line
    and exit_reason is "cap".
    """
    from bench.claude_runner import run_cell

    spec = _spec_for("A", tmp_path, max_turns=2)
    transcript = tmp_path / "cap_transcript.jsonl"
    fake_bin = str(_FAKE_CLAUDE_DIR / "emit_long.sh")

    result = run_cell(
        spec,
        claude_bin=fake_bin,
        results_transcript_path=str(transcript),
    )

    assert result.exit_reason == "cap"
    transcript_text = transcript.read_text()
    assert '"type":"result"' not in transcript_text
    # The 3rd assistant line IS streamed, THEN SIGTERM fires (cap fires AFTER
    # the count exceeds max_turns; see brief: "the 3rd assistant line triggers
    # SIGTERM"). The result line that follows is never read.
    assert transcript_text.count('"type":"assistant"') == 3


def test_run_cell_cap_sentinel_in_final_answer(tmp_path):
    """run_cell writes a self-documenting sentinel into final_answer when capped.

    A capped cell breaks before the `result` event, so summary.final_answer is
    None. Plan 3: write a non-null sentinel instead (clean data; report.py and
    the human kappa-gate never see a null answer; grade_cell recognizes the cap
    via exit_reason, not the sentinel text).
    """
    from bench.claude_runner import run_cell

    spec = _spec_for("A", tmp_path, max_turns=2)
    transcript = tmp_path / "cap_sentinel_transcript.jsonl"
    fake_bin = str(_FAKE_CLAUDE_DIR / "emit_long.sh")

    result = run_cell(
        spec,
        claude_bin=fake_bin,
        results_transcript_path=str(transcript),
    )

    assert result.exit_reason == "cap"
    assert result.final_answer is not None
    assert result.final_answer.startswith("[BENCH_CAP:")
    assert str(spec.max_turns) in result.final_answer


def test_exit_reason_timeout():
    """derive_exit_reason: timed_out -> 'timeout'; cap still wins over timeout."""
    from bench.claude_runner import StreamSummary, derive_exit_reason

    ok = StreamSummary()  # is_error=False, api_error_status=None
    assert derive_exit_reason(ok, capped=False, timed_out=True) == "timeout"
    # cap takes precedence over timeout (turn cap detected in-loop first).
    assert derive_exit_reason(ok, capped=True, timed_out=True) == "cap"
    # default (no timeout) unchanged.
    assert derive_exit_reason(ok, capped=False, timed_out=False) == "done"


def test_run_cell_wall_timeout_terminates(tmp_path):
    """run_cell SIGTERMs via a watchdog when wall_timeout_s elapses.

    emit_slow.sh stalls 30s before emitting; with wall_timeout_s=0.2 the
    watchdog fires while the read loop is blocked on readline, terminates the
    process, readline returns EOF, and exit_reason is 'timeout'. The whole cell
    must return in ~0.2s, not 30s.
    """
    import time as _time

    from bench.claude_runner import run_cell

    spec = _spec_for("A", tmp_path, max_turns=10)
    transcript = tmp_path / "timeout_transcript.jsonl"
    fake_bin = str(_FAKE_CLAUDE_DIR / "emit_slow.sh")

    t0 = _time.monotonic()
    result = run_cell(
        spec,
        claude_bin=fake_bin,
        results_transcript_path=str(transcript),
        wall_timeout_s=0.2,
    )
    elapsed = _time.monotonic() - t0

    assert result.exit_reason == "timeout"
    # Safety: must return well under the 30s sleep (allow slack for reaping).
    assert elapsed < 10.0


def test_run_cell_completes_no_cap(tmp_path):
    """run_cell with emit_short.sh returns exit_reason "done" and the schema fields."""
    from bench.claude_runner import run_cell

    spec = _spec_for("A", tmp_path, max_turns=10)
    transcript = tmp_path / "done_transcript.jsonl"
    fake_bin = str(_FAKE_CLAUDE_DIR / "emit_short.sh")

    result = run_cell(
        spec,
        claude_bin=fake_bin,
        results_transcript_path=str(transcript),
    )

    assert result.exit_reason == "done"
    assert result.n_turns == 1
    assert result.tool_call_breakdown == {"Read": 1}
    assert result.grade is None
    assert result.final_answer is None or isinstance(result.final_answer, str)
    # Transcript contains the raw lines.
    transcript_text = transcript.read_text()
    assert '"type":"assistant"' in transcript_text
    assert '"type":"result"' in transcript_text
    # Identity / metadata fields come from spec.
    assert result.run_id == "bc-impl-01_A_glm-4.7_s0"
    assert result.condition == "A"
    assert result.model == "glm-4.7"
    assert result.seed == 0
    assert result.corpus == "spring-boot-baseline"
    # corpus_commit comes from pinned_repo_sha when commit_sha is None.
    assert result.corpus_commit == "deadbeef"
    # prompt_hash is sha256-prefixed hex of the prompt CONTENTS.
    assert result.prompt_hash.startswith("sha256:")
    hex_part = result.prompt_hash[len("sha256:"):]
    assert len(hex_part) == 64 and all(c in "0123456789abcdef" for c in hex_part)


def test_run_cell_no_shim_for_condition_A(tmp_path, monkeypatch):
    """Condition A (no jrag): run_cell does NOT materialize the shim, no shim dir
    is created, and the spawned argv has no --mcp-config."""
    from bench.claude_runner import run_cell

    spec = _spec_for("A", tmp_path, max_turns=10)
    transcript = tmp_path / "a_transcript.jsonl"
    fake_bin = str(_FAKE_CLAUDE_DIR / "emit_short.sh")

    sidecar = tmp_path / "argv_recorded.txt"
    monkeypatch.setenv("JRAG_ARGV_SIDECAR", str(sidecar))
    # _claude_code_version would overwrite the sidecar with "--version".
    monkeypatch.setattr("bench.claude_runner._claude_code_version", lambda _b: None)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("materialize_cli_env must not be called for condition A")

    monkeypatch.setattr("bench.claude_runner.materialize_cli_env", _fail_if_called)

    result = run_cell(
        spec,
        claude_bin=fake_bin,
        results_transcript_path=str(transcript),
    )

    assert result.exit_reason == "done"
    recorded = sidecar.read_text()
    assert "--mcp-config" not in recorded
    assert "--strict-mcp-config" not in recorded
    # No shim dir is created for condition A.
    assert not (Path(transcript).parent / "bin").exists()


def test_run_cell_writes_shim_and_env_for_condition_B(tmp_path, monkeypatch):
    """Condition B: run_cell writes the per-condition jrag shim, sets the PATH +
    JRAG index env on the spawn, and the argv has no --mcp-config."""
    import dataclasses
    import sys

    from bench.claude_runner import run_cell

    spec = _spec_for("B", tmp_path, max_turns=10)
    repo_root = Path(__file__).parent.parent.parent
    # Condition.prompt_file is relative; absolutize so to_flags can read it.
    abs_cond = dataclasses.replace(
        spec.condition, prompt_file=str(repo_root / spec.condition.prompt_file)
    )
    spec = dataclasses.replace(spec, condition=abs_cond)

    fake_bin = str(_FAKE_CLAUDE_DIR / "emit_short.sh")
    # A fake real-jrag (never actually run by the fake claude, but the shim embeds its path).
    fake_jrag = tmp_path / "fake_jrag.sh"
    fake_jrag.write_text("#!/bin/sh\necho REAL\n")
    fake_jrag.chmod(0o755)

    argv_sidecar = tmp_path / "argv_b.txt"
    env_sidecar = tmp_path / "env_b.txt"
    monkeypatch.setenv("JRAG_ARGV_SIDECAR", str(argv_sidecar))
    monkeypatch.setenv("JRAG_ENV_SIDECAR", str(env_sidecar))
    # _claude_code_version spawns `claude_bin --version` AFTER the main run,
    # which would overwrite the sidecars. Stub it out.
    monkeypatch.setattr("bench.claude_runner._claude_code_version", lambda _b: None)

    transcript = tmp_path / "b_transcript.jsonl"
    result = run_cell(
        spec,
        claude_bin=fake_bin,
        jrag_bin=str(fake_jrag),
        venv_python=sys.executable,
        results_transcript_path=str(transcript),
    )

    assert result.exit_reason == "done"
    # Shim materialized and executable; embeds the real path + B's allow-list.
    shim = Path(transcript).parent / "bin" / "jrag"
    assert shim.exists()
    assert os.access(shim, os.X_OK)
    shim_text = shim.read_text()
    assert str(fake_jrag) in shim_text
    assert "search" in shim_text
    # argv has no MCP flags.
    assert "--mcp-config" not in argv_sidecar.read_text()
    # Spawn env carries the shim dir on PATH and the jrag index vars.
    env_recorded = env_sidecar.read_text()
    assert "JAVA_CODEBASE_RAG_INDEX_DIR=" in env_recorded
    assert "JAVA_CODEBASE_RAG_SOURCE_ROOT=" in env_recorded
    assert str(Path(transcript).parent / "bin") in env_recorded
