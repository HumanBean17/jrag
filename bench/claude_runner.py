"""Stream-json parser for claude -p transcript analysis + cell executor (CLI surface).

Plan 4: the agent drives jrag through its CLI (``jrag <verb>`` via Bash), not
MCP. Verb-level isolation is enforced by a per-condition PATH shim
(``materialize_cli_env``); lexical escape from the vector-only condition is
closed by the granular ``Bash(<lexical> *)`` deny-list assembled in
``load_conditions.to_flags``.
"""

from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Iterable

from bench.load_conditions import Condition, ConditionFlags, to_flags
from bench.load_corpora import CorpusRecord
from bench.load_questions import Question


class ConfigError(Exception):
    """Raised when CLI/shim setup is invalid (e.g. real jrag binary not found)."""


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


# --- CLI surface setup: per-condition PATH shim (verb-level isolation) ---


def _resolve_real_jrag(venv_python: str) -> str:
    """Locate the real ``jrag`` binary the shim will exec.

    Prefers the console-script sibling of ``venv_python`` (``<venv>/bin/jrag``);
    falls back to ``shutil.which("jrag")``. Raises ``ConfigError`` if neither
    exists.
    """
    candidate = os.path.join(os.path.dirname(venv_python), "jrag")
    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    found = shutil.which("jrag")
    if found:
        return os.path.abspath(found)
    raise ConfigError(
        f"could not locate the real jrag binary "
        f"(looked for {candidate!r} and on PATH)"
    )


def materialize_cli_env(
    cell_dir: str,
    allowed_verbs: list[str],
    real_jrag_bin: str,
    venv_python: str,
) -> str:
    """Write the per-condition ``jrag`` shim; return the shim directory.

    The shim is a small Python script placed at ``<cell_dir>/bin/jrag`` and put
    first on the spawned ``PATH`` by ``run_cell``. It exec's ``real_jrag_bin``
    ONLY for verbs in ``allowed_verbs`` (plus the harmless meta tokens
    ``--help`` / ``-h``); every other verb exits 2 with a message, so a
    vector-only cell (B) literally cannot run a graph verb and a full cell (D)
    gets the whole surface. This is the verb-level isolation layer; lexical
    escape is handled by the ``--disallowedTools`` granular Bash deny-list (see
    ``load_conditions.JRAG_LEXICAL_DENY``).

    Args:
        cell_dir: Absolute per-cell results dir (sibling of the transcript).
        allowed_verbs: Verbs the shim lets through (``["search"]`` for B, all of
            ``JRAG_QUERY_VERBS`` for D).
        real_jrag_bin: Absolute path to the real ``jrag`` binary.
        venv_python: Absolute path to the venv Python (used as the shebang).

    Returns:
        The shim directory (``<cell_dir>/bin``), absolute — prepend to ``PATH``.
    """
    shim_dir = os.path.join(cell_dir, "bin")
    os.makedirs(shim_dir, exist_ok=True)
    shim_path = os.path.join(shim_dir, "jrag")
    # A proper tuple literal in the generated source (note the trailing comma so a
    # single verb is a 1-tuple, not a parenthesized string). Sorted for stable,
    # deterministic output and error messages.
    allowed_tuple = "(" + ", ".join(repr(v) for v in sorted(set(allowed_verbs))) + ",)"
    shim_src = (
        f"#!{venv_python}\n"
        '"""Auto-generated by the bench harness. Gates jrag verbs per condition."""\n'
        "import os, sys\n"
        f"REAL = {real_jrag_bin!r}\n"
        f"ALLOWED = frozenset({allowed_tuple})\n"
        'META = frozenset(("--help", "-h"))\n'
        "args = sys.argv[1:]\n"
        'verb = args[0] if args else ""\n'
        "if verb in ALLOWED or verb in META:\n"
        "    os.execv(REAL, [REAL] + args)\n"
        'sys.stderr.write("jrag: %r is not available in this benchmark condition "\n'
        '                  "(allowed: %s)\\n" % (verb, ", ".join(sorted(ALLOWED))))\n'
        "sys.exit(2)\n"
    )
    # Atomic write (temp + os.replace) so a crash never leaves a half shim.
    tmp = shim_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(shim_src)
    os.replace(tmp, shim_path)
    os.chmod(shim_path, 0o755)
    return shim_dir


# --- CellSpec + argv assembly (pure) ---


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


def build_argv(spec: CellSpec, flags: ConditionFlags) -> list[str]:
    """Assemble the exact ``claude`` argv for one cell.

    Element order:
        claude -p <question>
            --output-format stream-json --verbose
            --permission-mode bypassPermissions
            --model <model>
            --add-dir <absolute checkout>
            --append-system-prompt <prompt CONTENTS string>
            --allowedTools <comma-joined flags.allowed_tools>
            [--disallowedTools <comma-joined>]            # iff non-empty

    The jrag CLI surface is exposed via a PATH shim (materialized by run_cell),
    not via ``--mcp-config`` — there are no MCP flags. ``--max-turns`` /
    ``--temperature`` / ``--seed`` are NEVER emitted — the harness controls
    those out-of-band.
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

    return argv


# --- CellResult + JSONL schema (pure) ---


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

    Returns a dict keyed exactly by the 24 ``CellResult`` field names
    (including ``grade``), with ``grade`` present and set to ``None``.
    The dict round-trips through ``json.dumps``/``json.loads``.
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


def derive_exit_reason(
    summary: StreamSummary, capped: bool, timed_out: bool = False
) -> str:
    """Derive the exit reason from a ``StreamSummary`` and cap/timeout flags.

    Precedence (highest to lowest):
        1. ``capped=True`` → ``"cap"`` (turn budget exceeded; in-loop SIGTERM)
        2. ``timed_out=True`` → ``"timeout"`` (wall budget exceeded; watchdog
           SIGTERM)
        3. ``summary.is_error`` or ``summary.api_error_status`` → ``"error"``
        4. otherwise → ``"done"``
    """
    if capped:
        return "cap"
    if timed_out:
        return "timeout"
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


# --- run_cell — subprocess spawn + driver-side turn cap ---


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


def run_cell(
    spec: CellSpec,
    *,
    claude_bin: str = "claude",
    results_transcript_path: str,
    venv_python: str | None = None,
    jrag_bin: str | None = None,
    wall_timeout_s: float | None = None,
) -> CellResult:
    """Run one cell end-to-end via ``claude -p`` -> ``CellResult``.

    For jrag conditions (B/D) the per-condition ``jrag`` PATH shim is
    materialized into the cell's results dir and prepended to the spawned
    ``PATH``; the jrag index/source env (``JAVA_CODEBASE_RAG_INDEX_DIR`` /
    ``JAVA_CODEBASE_RAG_SOURCE_ROOT``) is set so the CLI reads the right index.
    Non-jrag conditions (A/C) get an unmodified inherited environment.

    Streams stdout line by line, incrementally writes the raw transcript to
    ``results_transcript_path``, and SIGTERMs the process when an ``assistant``
    event would exceed ``spec.max_turns`` (driver-side cap; there is no
    ``--max-turns`` flag on ``claude -p``).

    If ``wall_timeout_s`` is set, a daemon watchdog thread SIGTERMs the process
    once that many seconds elapse (a stalled cell — readline blocked — can't be
    interrupted from the read loop itself). The watchdog is a no-op if the run
    finished first. Sets ``exit_reason="timeout"`` distinct from the turn cap.
    """
    flags = to_flags(spec.condition)

    env = os.environ.copy()
    if flags.jrag_allowed_verbs is not None:
        abs_index_dir = os.path.abspath(spec.corpus.index.index_dir)
        abs_checkout = cell_cwd(spec)
        python_bin = os.path.abspath(venv_python or sys.executable)
        real_jrag = os.path.abspath(jrag_bin or _resolve_real_jrag(python_bin))
        # Absolutize the cell dir: claude is spawned with cwd=cell_cwd (the
        # checkout), but the transcript path is relative to the driver's cwd
        # (repo root). Resolve it absolutely so the shim lands at a stable,
        # cwd-independent path and the REAL/PATH references in it stay valid.
        cell_dir = os.path.dirname(os.path.abspath(results_transcript_path))
        shim_dir = materialize_cli_env(
            cell_dir, flags.jrag_allowed_verbs, real_jrag, python_bin
        )
        env["PATH"] = shim_dir + os.pathsep + env.get("PATH", "")
        env["JAVA_CODEBASE_RAG_INDEX_DIR"] = abs_index_dir
        env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = abs_checkout

    argv = build_argv(spec, flags)
    # ``build_argv`` returns ``["claude", ...]`` — swap argv[0] for ``claude_bin``
    # so tests can substitute a fake binary without rebuilding argv.
    popen_argv = [claude_bin] + argv[1:]

    buffer: list[str] = []
    capped = False
    timed_out = False
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
        env=env,
    )

    # Wall-clock watchdog: a stalled cell blocks the read loop on ``readline``,
    # so the turn-cap check (which runs between lines) can't rescue it. The
    # daemon thread SIGTERMs the process after ``wall_timeout_s``; the blocked
    # ``readline`` then returns EOF and the loop breaks. No-op if the run
    # finished first (``proc.poll()`` is not None → already reaped/exited).
    if wall_timeout_s is not None:
        def _fire():
            nonlocal timed_out
            time.sleep(wall_timeout_s)
            if proc.poll() is None:
                timed_out = True
                proc.terminate()

        threading.Thread(target=_fire, daemon=True).start()

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

    # A capped cell breaks before the `result` event, so summary.final_answer is
    # None. Write a self-documenting sentinel instead: non-null (clean data —
    # report.py and the human kappa-gate never see a null answer) and readable
    # as "no answer produced". grade_cell recognizes the cap via exit_reason.
    if capped:
        final_answer = (
            f"[BENCH_CAP: reached max-turns {spec.max_turns} "
            f"without a final result]"
        )
    else:
        final_answer = summary.final_answer

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
        exit_reason=derive_exit_reason(summary, capped, timed_out),
        final_answer=final_answer,
        transcript_path=results_transcript_path,
        grade=None,
    )
