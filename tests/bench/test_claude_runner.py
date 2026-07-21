"""Tests for bench.claude_runner — stream-json parser."""

from pathlib import Path

import pytest


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
