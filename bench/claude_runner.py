"""Stream-json parser for claude -p transcript analysis.

Pure parser: reads stream-json lines, extracts summary statistics.
No subprocess, no I/O beyond the passed iterator.
"""

from dataclasses import dataclass, field
from collections import defaultdict
import json
from typing import Iterable


class ConfigError(Exception):
    """Raised when MCP config template is invalid."""


@dataclass(frozen=True)
class StreamSummary:
    """Summary of a claude -p stream-json transcript."""

    tool_call_breakdown: dict[str, int] = field(default_factory=dict)
    context_bytes_retrieved: int = 0
    n_turns: int = 0
    tokens: dict = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0})
    stop_reason: str | None = None
    terminal_reason: str | None = None
    is_error: bool = False
    api_error_status: str | None = None
    final_answer: str | None = None
    num_turns_reported: int | None = None


def parse_stream(lines: Iterable[str]) -> StreamSummary:
    """Parse stream-json lines into a StreamSummary.

    Single-pass over the iterator. Skips non-JSON/blank lines silently.

    Args:
        lines: Iterable of JSONL strings (one event per line)

    Returns:
        StreamSummary with extracted statistics
    """
    tool_call_breakdown = defaultdict(int)
    context_bytes_retrieved = 0
    n_turns = 0
    tokens = {"input": 0, "output": 0, "total": 0}
    stop_reason = None
    terminal_reason = None
    is_error = False
    api_error_status = None
    final_answer = None
    num_turns_reported = None

    for line in lines:
        # Skip blank lines
        if not line.strip():
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            # Skip non-JSON lines silently
            continue

        event_type = event.get("type")

        if event_type == "system":
            # Ignore system events
            continue

        elif event_type == "assistant":
            # Increment turn count
            n_turns += 1

            # Count tool uses
            message = event.get("message", {})
            content = message.get("content", [])

            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        name = item.get("name")
                        if name:
                            tool_call_breakdown[name] += 1

        elif event_type == "user":
            # Sum tool_result content lengths
            message = event.get("message", {})
            content = message.get("content", [])

            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        item_content = item.get("content", "")
                        # Stringify content if it's not already a string
                        content_str = json.dumps(item_content) if not isinstance(item_content, str) else item_content
                        context_bytes_retrieved += len(content_str)

        elif event_type == "result":
            # Extract result fields
            stop_reason = event.get("stop_reason")
            terminal_reason = event.get("terminal_reason")
            is_error = event.get("is_error", False)
            api_error_status = event.get("api_error_status")
            final_answer = event.get("result")
            num_turns_reported = event.get("num_turns")

            # Extract token counts
            usage = event.get("usage", {})
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                tokens = {
                    "input": input_tokens,
                    "output": output_tokens,
                    "total": input_tokens + output_tokens,
                }

    # Build frozen summary
    return StreamSummary(
        tool_call_breakdown=dict(tool_call_breakdown),
        context_bytes_retrieved=context_bytes_retrieved,
        n_turns=n_turns,
        tokens=tokens,
        stop_reason=stop_reason,
        terminal_reason=terminal_reason,
        is_error=is_error,
        api_error_status=api_error_status,
        final_answer=final_answer,
        num_turns_reported=num_turns_reported,
    )


def materialize_mcp_config(
    template_path: str,
    index_dir_abs: str,
    source_root_abs: str,
    venv_python: str,
    dest_path: str,
) -> str:
    """Materialize MCP config by substituting placeholders and rewriting command.

    Reads the template JSON, substitutes the literal substrings
    ${JRAG_INDEX_DIR} → index_dir_abs and ${JRAG_SOURCE_ROOT} → source_root_abs
    everywhere in the serialized JSON, rewrites the mcpServers.jrag.command value
    to venv_python, writes the result to dest_path, returns dest_path.

    Args:
        template_path: Path to the template JSON file.
        index_dir_abs: Absolute path to substitute for ${JRAG_INDEX_DIR}.
        source_root_abs: Absolute path to substitute for ${JRAG_SOURCE_ROOT}.
        venv_python: Absolute path to venv Python binary (rewrites command).
        dest_path: Path where the materialized config should be written.

    Returns:
        The dest_path (for convenience).

    Raises:
        ConfigError: If template has no mcpServers.jrag key or no
            JAVA_CODEBASE_RAG_INDEX_DIR placeholder.
    """
    with open(template_path) as f:
        template_str = f.read()

    # Check that template has the jrag server
    template_json = json.loads(template_str)
    if "mcpServers" not in template_json or "jrag" not in template_json["mcpServers"]:
        raise ConfigError("Template must have mcpServers.jrag")

    # Check that template has the placeholder
    if "${JRAG_INDEX_DIR}" not in template_str:
        raise ConfigError("Template must contain ${JRAG_INDEX_DIR} placeholder")

    # Substitute placeholders on the serialized string
    materialized_str = template_str.replace("${JRAG_INDEX_DIR}", index_dir_abs)
    materialized_str = materialized_str.replace("${JRAG_SOURCE_ROOT}", source_root_abs)

    # Parse and rewrite command
    config = json.loads(materialized_str)
    config["mcpServers"]["jrag"]["command"] = venv_python

    # Write to dest
    with open(dest_path, "w") as f:
        json.dump(config, f, indent=2)

    return dest_path
