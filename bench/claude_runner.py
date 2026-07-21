"""Stream-json parser for claude -p transcript analysis.

Pure parser: reads stream-json lines, extracts summary statistics.
No subprocess, no I/O beyond the passed iterator.
"""

from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import sys
import time
from typing import Iterable

from bench.load_conditions import Condition, ConditionFlags, to_flags
from bench.load_corpora import CorpusRecord
from bench.load_questions import Question


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


# --- Task 3: CellSpec + argv assembly (pure) ---


@dataclass(frozen=True)
class CellSpec:
    """One benchmark cell: (question, condition, corpus, model, seed, ...).

    Carries everything ``build_argv``/``cell_cwd``/``run_id`` need to assemble
    the claude invocation. Pure data; no I/O.
    """

    question: Question
    condition: Condition
    corpus: CorpusRecord
    model: str
    seed: int
    temperature: float
    max_turns: int
    repo_root: str


def cell_cwd(spec: CellSpec) -> str:
    """Absolute checkout path — the cwd the claude subprocess will run in."""
    return os.path.join(spec.repo_root, spec.corpus.checkout_path)


def run_id(spec: CellSpec) -> str:
    """Stable identifier for one cell: ``{q.id}_{c.id}_{model}_s{seed}``."""
    return f"{spec.question.id}_{spec.condition.id}_{spec.model}_s{spec.seed}"


def build_argv(
    spec: CellSpec, flags: ConditionFlags, mcp_config_path: str | None
) -> list[str]:
    """Assemble the exact ``claude`` argv for one cell.

    Element order (per Plan 2 spec):
        claude -p <question>
            --output-format stream-json --verbose
            --permission-mode bypassPermissions
            --model <model>
            --add-dir <absolute checkout>
            --append-system-prompt <prompt CONTENTS string>
            --allowedTools <comma-joined flags.allowed_tools>
            [--disallowedTools <comma-joined>]            # iff non-empty
            [--mcp-config <path> --strict-mcp-config]     # iff path is not None

    Note: ``--max-turns`` / ``--temperature`` / ``--seed`` are NEVER emitted —
    the harness controls those out-of-band.
    """
    argv: list[str] = [
        "claude",
        "-p",
        spec.question.question,
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", "bypassPermissions",
        "--model", spec.model,
        "--add-dir", cell_cwd(spec),
        "--append-system-prompt", flags.append_system_prompt,
        "--allowedTools", ",".join(flags.allowed_tools),
    ]

    if flags.disallowed_tools:
        argv.append("--disallowedTools")
        argv.append(",".join(flags.disallowed_tools))

    if mcp_config_path is not None:
        argv.append("--mcp-config")
        argv.append(mcp_config_path)
        argv.append("--strict-mcp-config")

    return argv


# --- Task 4: CellResult + JSONL schema (pure) ---


@dataclass(frozen=True)
class CellResult:
    """One benchmark cell result — the JSONL schema.

    All fields are populated by the driver; ``grade`` is always ``None``
    (filled in later by ``grade.py``).
    """

    run_id: str
    question_id: str
    corpus: str
    corpus_commit: str
    condition: str
    model: str
    seed: int
    temperature: float
    claude_code_version: str | None
    ontology_version: int
    index_build_id: str | None
    prompt_hash: str
    started_at: str
    finished_at: str
    wall_s: float
    n_turns: int
    n_tool_calls: int
    tool_call_breakdown: dict[str, int]
    tokens: dict
    context_bytes_retrieved: int
    exit_reason: str
    final_answer: str | None
    transcript_path: str
    grade: None


def to_cell_jsonl(result: CellResult) -> dict:
    """Convert a ``CellResult`` to a JSONL-serializable dict.

    Returns a dict keyed exactly by the 23 ``CellResult`` field names,
    with ``grade`` present and set to ``None``. The dict round-trips
    through ``json.dumps``/``json.loads``.
    """
    return {
        "run_id": result.run_id,
        "question_id": result.question_id,
        "corpus": result.corpus,
        "corpus_commit": result.corpus_commit,
        "condition": result.condition,
        "model": result.model,
        "seed": result.seed,
        "temperature": result.temperature,
        "claude_code_version": result.claude_code_version,
        "ontology_version": result.ontology_version,
        "index_build_id": result.index_build_id,
        "prompt_hash": result.prompt_hash,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "wall_s": result.wall_s,
        "n_turns": result.n_turns,
        "n_tool_calls": result.n_tool_calls,
        "tool_call_breakdown": result.tool_call_breakdown,
        "tokens": result.tokens,
        "context_bytes_retrieved": result.context_bytes_retrieved,
        "exit_reason": result.exit_reason,
        "final_answer": result.final_answer,
        "transcript_path": result.transcript_path,
        "grade": result.grade,
    }


def derive_exit_reason(summary: StreamSummary, capped: bool) -> str:
    """Derive the exit reason from a ``StreamSummary`` and cap flag.

    Precedence (highest to lowest):
        1. ``capped=True`` → ``"cap"``
        2. ``summary.is_error`` or ``summary.api_error_status`` → ``"error"``
        3. otherwise → ``"done"``
    """
    if capped:
        return "cap"
    if summary.is_error or summary.api_error_status is not None:
        return "error"
    return "done"


def choose_n_turns(summary: StreamSummary) -> int:
    """Choose the turn count from a ``StreamSummary``.

    Prefers ``summary.num_turns_reported`` when not ``None``;
    otherwise falls back to ``summary.n_turns``.
    """
    if summary.num_turns_reported is not None:
        return summary.num_turns_reported
    return summary.n_turns


# --- Task 5: run_cell — subprocess spawn + driver-side turn cap ---


def _claude_code_version(claude_bin: str) -> str | None:
    """Best-effort ``claude_bin --version`` capture; ``None`` on any failure."""
    try:
        result = subprocess.run(
            [claude_bin, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip() or None


def _mcp_config_dest(results_transcript_path: str) -> str:
    """Derive a sibling path for the materialized MCP config from the transcript path."""
    p = str(results_transcript_path)
    if p.endswith(".jsonl"):
        return p[: -len(".jsonl")] + ".mcp.json"
    return p + ".mcp.json"


def run_cell(
    spec: CellSpec,
    *,
    claude_bin: str = "claude",
    jrag_mcp_template: str = "bench/mcp/jrag.json",
    results_transcript_path: str,
    venv_python: str | None = None,
) -> CellResult:
    """Run one cell end-to-end via ``claude -p`` -> ``CellResult``.

    Streams stdout line by line, incrementally writes the raw transcript to
    ``results_transcript_path``, and SIGTERMs the process when an ``assistant``
    event would exceed ``spec.max_turns`` (driver-side cap; there is no
    ``--max-turns`` flag on ``claude -p``).
    """
    flags = to_flags(spec.condition)

    mcp_config_path: str | None = None
    if flags.mcp_config_arg is not None:
        abs_index_dir = os.path.abspath(spec.corpus.index.index_dir)
        abs_checkout = cell_cwd(spec)
        python_bin = venv_python or sys.executable
        mcp_config_path = materialize_mcp_config(
            jrag_mcp_template,
            abs_index_dir,
            abs_checkout,
            python_bin,
            _mcp_config_dest(results_transcript_path),
        )
        # Absolutize: claude is spawned with cwd=cell_cwd (the checkout dir),
        # but the dest above derives from a relative results path (repo-root cwd).
        # Passing a relative path here makes claude resolve it against the
        # checkout → file-not-found → exit 1. Violates "Always pass jrag paths
        # as ABSOLUTE."
        mcp_config_path = os.path.abspath(mcp_config_path)

    argv = build_argv(spec, flags, mcp_config_path)
    # ``build_argv`` returns ``["claude", ...]`` — swap argv[0] for ``claude_bin``
    # so tests can substitute a fake binary without rebuilding argv.
    popen_argv = [claude_bin] + argv[1:]

    buffer: list[str] = []
    capped = False
    assistant_count = 0

    started_dt = datetime.now(timezone.utc)
    t0 = time.time()

    # ``stdin=DEVNULL`` is required: ``claude -p`` with stream-json + --verbose
    # hangs if stdin stays open.
    proc = subprocess.Popen(
        popen_argv,
        cwd=cell_cwd(spec),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    try:
        with open(results_transcript_path, "w") as transcript_f:
            assert proc.stdout is not None
            while True:
                raw = proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                buffer.append(line)
                transcript_f.write(line)
                transcript_f.flush()

                # Cheap assistant-event probe: a parse + type check.
                if not line.lstrip().startswith("{"):
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not (isinstance(event, dict) and event.get("type") == "assistant"):
                    continue
                assistant_count += 1
                # If this event would EXCEED spec.max_turns, SIGTERM and stop.
                if assistant_count > spec.max_turns:
                    capped = True
                    proc.terminate()
                    break
    finally:
        # ``wait()`` reaps the SIGTERM'd (or naturally-exited) child.
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    finished_dt = datetime.now(timezone.utc)
    wall_s = time.time() - t0

    summary = parse_stream(iter(buffer))

    claude_code_version = _claude_code_version(claude_bin)
    prompt_hash = (
        "sha256:"
        + hashlib.sha256(
            flags.append_system_prompt.encode("utf-8")
        ).hexdigest()
    )
    corpus_commit = spec.corpus.commit_sha or spec.corpus.pinned_repo_sha

    return CellResult(
        run_id=run_id(spec),
        question_id=spec.question.id,
        corpus=spec.corpus.name,
        corpus_commit=corpus_commit,
        condition=spec.condition.id,
        model=spec.model,
        seed=spec.seed,
        temperature=spec.temperature,
        claude_code_version=claude_code_version,
        ontology_version=spec.corpus.index.ontology_version,
        index_build_id=spec.corpus.index.build_id,
        prompt_hash=prompt_hash,
        started_at=started_dt.isoformat(),
        finished_at=finished_dt.isoformat(),
        wall_s=wall_s,
        n_turns=choose_n_turns(summary),
        n_tool_calls=sum(summary.tool_call_breakdown.values()),
        tool_call_breakdown=summary.tool_call_breakdown,
        tokens=summary.tokens,
        context_bytes_retrieved=summary.context_bytes_retrieved,
        exit_reason=derive_exit_reason(summary, capped),
        final_answer=summary.final_answer,
        transcript_path=results_transcript_path,
        grade=None,
    )
