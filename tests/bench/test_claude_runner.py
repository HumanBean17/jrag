"""Tests for bench.claude_runner — stream-json parser + argv assembly."""

import json
from pathlib import Path
import tempfile

import pytest

from bench.load_conditions import (
    ALL_JRAG_TOOLS,
    JRAG_GRAPH_TOOLS,
    Condition,
    ConditionFlags,
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


def test_materialize_substitutes_and_rewrites_command(tmp_path):
    """Test materialize_mcp_config substitutes placeholders and rewrites command."""
    from bench.claude_runner import materialize_mcp_config

    # Use the real template (path: bench/mcp/jrag.json)
    # __file__ is tests/bench/test_claude_runner.py
    # parent.parent.parent goes from tests/bench/ -> tests/ -> repo root
    repo_root = Path(__file__).parent.parent.parent
    template_path = repo_root / "bench" / "mcp" / "jrag.json"
    dest_path = tmp_path / "mcp_config.json"

    result = materialize_mcp_config(
        template_path=str(template_path),
        index_dir_abs="/x/idx",
        source_root_abs="/y/src",
        venv_python="/z/bin/python",
        dest_path=str(dest_path),
    )

    # Verify return value
    assert result == str(dest_path)

    # Load and verify the written file
    with open(dest_path) as f:
        config = json.load(f)

    assert config["mcpServers"]["jrag"]["env"]["JAVA_CODEBASE_RAG_INDEX_DIR"] == "/x/idx"
    assert config["mcpServers"]["jrag"]["env"]["JAVA_CODEBASE_RAG_SOURCE_ROOT"] == "/y/src"
    assert config["mcpServers"]["jrag"]["command"] == "/z/bin/python"


def test_materialize_rejects_template_without_jrag(tmp_path):
    """Test materialize_mcp_config raises ConfigError when template has no jrag server."""
    from bench.claude_runner import materialize_mcp_config, ConfigError

    # Create a template without jrag server
    template_path = tmp_path / "bad_template.json"
    with open(template_path, "w") as f:
        json.dump({"mcpServers": {}}, f)

    dest_path = tmp_path / "dest.json"

    with pytest.raises(ConfigError, match=".*jrag.*"):
        materialize_mcp_config(
            template_path=str(template_path),
            index_dir_abs="/x/idx",
            source_root_abs="/y/src",
            venv_python="/z/bin/python",
            dest_path=str(dest_path),
        )


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
            id="A", name="Lexical", mcp_servers=[],
            allowed_tools=["Grep", "Glob", "Read", "Bash"],
            disallowed_tools=[],
            prompt_file="bench/prompts/A_lexical.md",
        )
    if letter == "B":
        return Condition(
            id="B", name="Vector-only", mcp_servers=["jrag"],
            allowed_tools=["Read", "mcp__jrag__search"],
            disallowed_tools=list(JRAG_GRAPH_TOOLS),
            prompt_file="bench/prompts/B_vector_only.md",
        )
    if letter == "D":
        return Condition(
            id="D", name="jrag full", mcp_servers=["jrag"],
            allowed_tools=["Read", "Grep", "Glob"] + list(ALL_JRAG_TOOLS),
            disallowed_tools=[],
            prompt_file="bench/prompts/D_jrag_full.md",
        )
    raise ValueError(f"no fixture for condition {letter!r}")


def _flags_for(cond: Condition, prompt_contents: str = "PROMPT") -> ConditionFlags:
    return ConditionFlags(
        mcp_config_arg=("bench/mcp/jrag.json" if "jrag" in cond.mcp_servers else None),
        allowed_tools=list(cond.allowed_tools),
        disallowed_tools=list(cond.disallowed_tools),
        append_system_prompt=prompt_contents,
    )


def test_argv_condition_A_no_mcp():
    """Condition A (no MCP) produces a claude argv with no MCP flags."""
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

    argv = build_argv(spec, flags, mcp_config_path=None)

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
    # No MCP flags because mcp_config_path is None
    assert "--mcp-config" not in argv
    assert "--strict-mcp-config" not in argv
    # No disallowedTools for condition A
    assert "--disallowedTools" not in argv
    # Forbidden flags must NEVER appear
    assert "--max-turns" not in argv
    assert "--temperature" not in argv
    assert "--seed" not in argv


def test_argv_condition_D_with_mcp():
    """Condition D (jrag full) with an mcp_config_path emits MCP flags + all jrag tools."""
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

    argv = build_argv(spec, flags, mcp_config_path="/tmp/x.json")

    # --mcp-config + --strict-mcp-config appear in order when path is provided
    assert "--mcp-config" in argv
    mcp_idx = argv.index("--mcp-config")
    assert argv[mcp_idx + 1] == "/tmp/x.json"
    assert argv[mcp_idx + 2] == "--strict-mcp-config"
    # allowedTools value contains every member of ALL_JRAG_TOOLS
    allowed_idx = argv.index("--allowedTools")
    allowed_value = argv[allowed_idx + 1]
    allowed_members = set(allowed_value.split(","))
    for tool in ALL_JRAG_TOOLS:
        assert tool in allowed_members, f"{tool} missing from {allowed_value!r}"
    # Forbidden flags still must not appear
    assert "--max-turns" not in argv
    assert "--temperature" not in argv
    assert "--seed" not in argv


def test_argv_condition_B_denies_graph():
    """Condition B (vector-only): --disallowedTools is the comma-joined graph tools,
    and --allowedTools still contains mcp__jrag__search."""
    from bench.claude_runner import CellSpec, build_argv

    cond = _condition("B")
    flags = _flags_for(cond, prompt_contents="PROMPT-B")
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

    argv = build_argv(spec, flags, mcp_config_path=None)

    # --disallowedTools value equals the comma-joined JRAG_GRAPH_TOOLS
    assert "--disallowedTools" in argv
    dis_idx = argv.index("--disallowedTools")
    assert argv[dis_idx + 1] == ",".join(JRAG_GRAPH_TOOLS)
    # --allowedTools value contains mcp__jrag__search
    allowed_idx = argv.index("--allowedTools")
    allowed_value = argv[allowed_idx + 1]
    assert "mcp__jrag__search" in allowed_value.split(",")


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
