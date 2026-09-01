"""Executable condition-isolation spec loader (Plan 4: CLI surface).

Conditions differ ONLY in the tool set exposed to Claude Code — enforced by
harness flags (``--allowedTools`` / ``--disallowedTools`` and, for the jrag
surface, a per-condition PATH shim), never by prompt pleas. This module
validates the methodological invariants (B allows only ``jrag search``; D
allows the full graph; A/C expose no jrag) and emits the flag payload via
``to_flags``.

The agent drives jrag through its **CLI** (``jrag <verb>`` shell commands via
Bash), not MCP. Verb-level isolation (B = search-only; D = all verbs) is
enforced by a PATH shim written per cell by ``claude_runner.materialize_cli_env``;
the shim allow-list each condition exposes is ``Condition.jrag_allowed_verbs``.
Lexical escape from the vector-only condition (B) is closed as tightly as
``bypassPermissions`` allows: B denies ``Grep``/``Glob`` plus a granular
``Bash(<lexical> *)`` deny-list (``JRAG_LEXICAL_DENY``). See the Plan 4 design
and PREREGISTRATION Amendment 2026-07-22 (Plan 4).

Pure validation — no I/O beyond reading the YAML and the prompt file. The one
exception is ``tools: prime`` (condition D): composing that condition's prompt
runs ``jrag prime`` once per index (see ``_generate_prime_tools_section``) so
the agent is taught the REAL index state and command surface instead of a
hand-written snapshot that drifts.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

# jrag CLI agent verbs the benchmark exposes (Plan 4). This is the full agent
# verb surface from ``cli_dispatch.AGENT_VERBS`` MINUS the non-query verbs:
# ``watch`` (long-lived daemon — would hang a cell), ``vocab-index`` (index
# mutation), and ``prime`` (not a query — the harness itself generates
# condition D's priming payload from ``jrag prime`` at compose time, so the
# agent under test never invokes it; jrag-prime plan, Task 3). Keep in sync
# with ``src/java_codebase_rag/jrag.py``; a stale entry over-restricts
# condition D (graceful — the agent just can't use a new verb) and never leaks
# a verb into B (B's allow-list is the literal ``["search"]``).
JRAG_QUERY_VERBS = [
    # orientation
    "status", "microservices", "map", "conventions", "overview",
    # locate
    "find", "inspect", "outline", "imports",
    # listings
    "http-routes", "http-clients", "producers", "topics", "jobs",
    "listeners", "entities",
    # traversal
    "callers", "callees", "hierarchy", "implementations", "subclasses",
    "overrides", "overridden-by", "dependents", "dependencies", "impact",
    "decompose", "flow", "connection",
    # semantic
    "search",
]
JRAG_SEARCH_VERBS = ["search"]

# Granular Bash deny-list auto-applied to condition B (vector-only). Under
# ``--permission-mode bypassPermissions``, ``--disallowedTools`` granular rules
# like ``Bash(grep *)`` ARE enforced (only *allow* rules are additive there),
# and Claude Code splits compound commands on ``&&`` / ``||`` / ``;`` / ``|`` so
# ``jrag search x && grep y`` is independently denied on the grep half. This
# closes the lexical-escape vector as tightly as the permission model allows;
# the residual (an un-enumerated binary) is reported as a leakage metric by
# ``report.py``. Maintained here in one place; appended by ``to_flags`` for B.
JRAG_LEXICAL_DENY = [
    "Bash(cat *)",
    "Bash(grep *)",
    "Bash(egrep *)",
    "Bash(fgrep *)",
    "Bash(rg *)",
    "Bash(find *)",
    "Bash(head *)",
    "Bash(tail *)",
    "Bash(less *)",
    "Bash(more *)",
    "Bash(awk *)",
    "Bash(sed *)",
    "Bash(xxd *)",
    "Bash(od *)",
    "Bash(perl *)",
    "Bash(python *)",
    "Bash(node *)",
]

# Common escape/integrity deny-list applied to EVERY condition (auto-appended by
# ``to_flags``). Under ``--permission-mode bypassPermissions``,
# ``--allowedTools`` is additive (a permission grant, not an exclusive
# allowlist), so isolation is enforced via ``--disallowedTools``. This list
# blocks: checkout mutation (Edit/Write/NotebookEdit — reproducibility),
# external info (WebSearch/WebFetch — all reasoning must come from the local
# codebase), and subagent dispatch (Agent/Task — closes the unmonitorable
# subagent-escape vector). Per-condition variation is ONLY jrag/lexical access.
ESCAPE_TOOLS = [
    "Edit",
    "Write",
    "NotebookEdit",
    "WebSearch",
    "WebFetch",
    "Agent",
    "Task",
]

VALID_IDS = {"A", "B", "C", "D"}


class ConfigError(ValueError):
    """Raised when ``conditions.yml`` violates an isolation invariant."""


@dataclass(frozen=True)
class Condition:
    """One experimental condition (A/B/C/D).

    ``jrag_allowed_verbs``: ``None`` = no jrag surface (A/C); a verb list = the
    exact verbs the per-cell PATH shim will let through. For D this is the full
    ``JRAG_QUERY_VERBS`` (the YAML sentinel ``all`` resolves to it at load).
    Defaults to ``None`` so stub conditions in tests can omit it.

    ``tools``: ``"prime"`` (D only) = replace the prompt file's ``## Your tools``
    section with real ``jrag prime`` output at compose time; ``None`` = use the
    file text verbatim (legacy / Task 5's file-based baseline).
    """

    id: str
    name: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    prompt_file: str
    jrag_allowed_verbs: list[str] | None = None
    tools: str | None = None


@dataclass(frozen=True)
class ConditionFlags:
    """The exact ``claude -p`` flag payload assembled from a Condition."""

    allowed_tools: list[str]
    disallowed_tools: list[str]
    append_system_prompt: str  # contents read from prompt_file
    jrag_allowed_verbs: list[str] | None = None  # None = no jrag; drives the PATH shim


def validate(cond: Condition) -> None:
    """Raise ``ConfigError`` on any per-condition isolation violation."""
    if cond.id not in VALID_IDS:
        raise ConfigError(f"condition id {cond.id!r} must be one of {sorted(VALID_IDS)}")
    if not cond.prompt_file or not Path(cond.prompt_file).is_file():
        raise ConfigError(f"condition {cond.id}: prompt_file {cond.prompt_file!r} not found")

    # No condition may ALLOW an escape tool. ESCAPE_TOOLS is auto-denied by
    # ``to_flags``; this guard catches a condition that simultaneously allows
    # one (which would be a confusing, self-contradictory spec).
    leaked_allow = set(cond.allowed_tools) & set(ESCAPE_TOOLS)
    if leaked_allow:
        raise ConfigError(
            f"condition {cond.id} allowed_tools must not include any ESCAPE_TOOLS "
            f"({ESCAPE_TOOLS}); found {sorted(leaked_allow)}"
        )

    if cond.id in ("A", "C"):
        # A/C = no jrag surface at all.
        if cond.jrag_allowed_verbs is not None:
            raise ConfigError(
                f"condition {cond.id} must expose no jrag surface "
                f"(jrag_allowed_verbs must be absent); got {cond.jrag_allowed_verbs!r}"
            )

    if cond.id == "B":
        # B = vector-only: the shim must allow ONLY `search`; the lexical tools
        # Grep/Glob must be denied (the Bash lexical deny-list is auto-appended
        # by to_flags, so it is not checked here).
        if cond.jrag_allowed_verbs != JRAG_SEARCH_VERBS:
            raise ConfigError(
                f"condition B jrag_allowed_verbs must be exactly {JRAG_SEARCH_VERBS}; "
                f"got {cond.jrag_allowed_verbs!r}"
            )
        missing_lexical = {"Grep", "Glob"} - set(cond.disallowed_tools)
        if missing_lexical:
            raise ConfigError(
                f"condition B disallowed_tools must include Grep and Glob; "
                f"missing {sorted(missing_lexical)}"
            )

    if cond.id == "D":
        # D = jrag full: the shim must allow the full query surface.
        if cond.jrag_allowed_verbs is None:
            raise ConfigError("condition D must expose the jrag surface (jrag_allowed_verbs absent)")
        missing_verbs = set(JRAG_QUERY_VERBS) - set(cond.jrag_allowed_verbs)
        if missing_verbs:
            raise ConfigError(
                f"condition D jrag_allowed_verbs must include all JRAG_QUERY_VERBS; "
                f"missing {sorted(missing_verbs)}"
            )


def to_flags(
    cond: Condition,
    *,
    jrag_bin: Path | None = None,
    source_root: Path | None = None,
    index_dir: Path | None = None,
) -> ConditionFlags:
    """Assemble the ``claude -p`` flag payload for a condition.

    The shared ``ESCAPE_TOOLS`` deny-list is auto-appended to every condition
    (it is the always-on integrity baseline, not per-condition variation).
    Condition B additionally gets ``JRAG_LEXICAL_DENY`` (the granular Bash
    lexical deny-list). The verb allow-list (``jrag_allowed_verbs``) is carried
    through for ``claude_runner`` to bake into the per-cell PATH shim.

    A condition with ``tools="prime"`` (D) has its prompt composed as
    ``preamble + "## Your tools\\n\\n" + <jrag prime output>`` — the file's own
    tools section is discarded. That requires the caller to supply the same
    ``jrag_bin`` / ``source_root`` / ``index_dir`` the cell will run under (the
    payload embeds index state, so a mismatch would prime the agent with a
    different repo than it can query); omitting them is a ``ConfigError`` rather
    than a silent fall-back to the stale file text. Every other condition reads
    the prompt file verbatim and ignores the context arguments.
    """
    if cond.tools == "prime":
        if jrag_bin is None or source_root is None or index_dir is None:
            raise ConfigError(
                "condition D requires jrag_bin/source_root/index_dir "
                "for prime generation"
            )
        prompt = (
            prompt_preamble(cond.prompt_file)
            + _TOOLS_MARKER + "\n\n"
            + _generate_prime_tools_section(jrag_bin, source_root, index_dir)
        )
    else:
        prompt = Path(cond.prompt_file).read_text(encoding="utf-8")

    disallowed = list(cond.disallowed_tools) + list(ESCAPE_TOOLS)
    if cond.id == "B":
        disallowed += JRAG_LEXICAL_DENY
    return ConditionFlags(
        allowed_tools=list(cond.allowed_tools),
        disallowed_tools=disallowed,
        append_system_prompt=prompt,
        jrag_allowed_verbs=cond.jrag_allowed_verbs,
    )


_TOOLS_MARKER = "## Your tools"


def prompt_preamble(path: str) -> str:
    """Return everything before the ``## Your tools`` marker of a prompt file.

    Byte-identical across the four condition prompts (asserted in tests) — the
    conditions differ ONLY in the tools section.
    """
    content = Path(path).read_text(encoding="utf-8")
    return content.split(_TOOLS_MARKER, 1)[0]


def prompt_tools_section(path: str) -> str:
    """Return the body of the ``## Your tools`` section of a prompt file."""
    content = Path(path).read_text(encoding="utf-8")
    parts = content.split(_TOOLS_MARKER, 1)
    return parts[1].strip() if len(parts) > 1 else ""


# Memoized ``jrag prime`` payloads, keyed by the generation context
# ``(jrag_bin, source_root, index_dir)``. ``to_flags`` is re-invoked for every
# cell of a run, and the payload embeds live index state (freshness, counts) —
# regenerating it mid-run could race an increment and change ``prompt_hash``
# between cells of the same condition, so the first successful generation per
# context wins. The KEY matters because one bench process runs several corpora
# (bench/questions/ spans three, each with its own index): a single cache entry
# would prime shopizer's condition D with bank-chat-system's services and
# counts, breaking the byte-identity contract below.
_PRIME_TOOLS_CACHE: dict[tuple[str, str, str], str] = {}


def _generate_prime_tools_section(
    jrag_bin: Path, source_root: Path, index_dir: Path
) -> str:
    """Return the ``jrag prime`` payload, running the CLI once per context.

    Memoized on first SUCCESSFUL call per ``(jrag_bin, source_root, index_dir)``
    (a failure must not pin a broken payload): one subprocess per distinct
    index, one payload shared by every cell that targets it.
    """
    key = (str(jrag_bin), str(source_root), str(index_dir))
    if key not in _PRIME_TOOLS_CACHE:
        _PRIME_TOOLS_CACHE[key] = _run_prime_cli(jrag_bin, source_root, index_dir)
    return _PRIME_TOOLS_CACHE[key]


def _run_prime_cli(jrag_bin: Path, source_root: Path, index_dir: Path) -> str:
    """Run ``jrag prime`` against the cell's index and capture stdout.

    The env mirrors what ``run_cell`` sets on the claude spawn, so the payload is
    byte-identical to what the agent's own ``jrag`` would print in-cell. ``prime``
    is hook-safe by design (rc 0, empty stdout when there is nothing to say), so
    a non-zero exit AND an empty success are both fatal here: the bench must
    never silently fall back to the prompt file's stale tools section.
    """
    env = dict(os.environ)
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(source_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(index_dir)
    try:
        proc = subprocess.run(
            [str(jrag_bin), "prime"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except FileNotFoundError as exc:
        raise ConfigError(f"jrag prime failed: {jrag_bin}: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise ConfigError(
            f"jrag prime exited {exc.returncode}: {stderr[:400] or '(no stderr)'}"
        ) from exc
    payload = proc.stdout.decode("utf-8")
    if not payload.strip():
        raise ConfigError(
            "jrag prime produced no payload (empty stdout, rc 0) — the index at "
            f"{index_dir} is missing or unreadable; refusing to compose condition "
            f"D's prompt without a tools section"
        )
    return payload


_CONDITION_KEYS = {"id", "name", "jrag_allowed_verbs", "allowed_tools",
                   "disallowed_tools", "prompt_file", "tools"}


def _resolve_verbs(raw) -> list[str] | None:
    """Resolve the YAML ``jrag_allowed_verbs`` value to a concrete verb list.

    ``None``/missing → ``None`` (no jrag surface). The string ``"all"`` → the
    full ``JRAG_QUERY_VERBS``. A list → the stripped list. Anything else is a
    config error.
    """
    if raw is None:
        return None
    if raw == "all":
        return list(JRAG_QUERY_VERBS)
    if isinstance(raw, list):
        return [str(v).strip() for v in raw]
    raise ConfigError(
        f"jrag_allowed_verbs must be absent, 'all', or a list; got {raw!r}"
    )


def _record_from_entry(entry: dict) -> Condition:
    unknown = set(entry.keys()) - _CONDITION_KEYS
    if unknown:
        raise ConfigError(f"condition entry has unknown keys {sorted(unknown)}: {entry!r}")
    cond_id = str(entry.get("id", "")).strip()
    # ``tools`` has exactly one legal shape: ``prime`` on condition D. Anywhere
    # else (or any other value) the entry is misconfigured, not a variant.
    raw_tools = entry.get("tools")
    if raw_tools is not None and raw_tools != "prime":
        raise ConfigError(
            f"condition {cond_id}: tools must be 'prime' or absent; got {raw_tools!r}"
        )
    if raw_tools == "prime" and cond_id != "D":
        raise ConfigError(
            f"condition {cond_id}: tools 'prime' is only valid on condition D"
        )
    return Condition(
        id=cond_id,
        name=str(entry.get("name", "")).strip(),
        jrag_allowed_verbs=_resolve_verbs(entry.get("jrag_allowed_verbs")),
        allowed_tools=list(entry.get("allowed_tools") or []),
        disallowed_tools=list(entry.get("disallowed_tools") or []),
        prompt_file=str(entry.get("prompt_file", "")).strip(),
        tools=raw_tools,
    )


def load_conditions(path: str = "bench/conditions.yml") -> list[Condition]:
    """Read ``conditions.yml`` -> validated ``Condition`` list (exactly A/B/C/D)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("conditions"), list):
        raise ConfigError(f"{path}: expected top-level mapping with a 'conditions:' list")
    entries = raw["conditions"]

    conds: list[Condition] = []
    seen: set[str] = set()
    for entry in entries:
        cond = _record_from_entry(entry)
        validate(cond)
        if cond.id in seen:
            raise ConfigError(f"duplicate condition id {cond.id!r} in {path}")
        seen.add(cond.id)
        conds.append(cond)

    missing_extra = VALID_IDS ^ seen
    if missing_extra:
        raise ConfigError(
            f"{path}: condition ids must be exactly {sorted(VALID_IDS)}; "
            f"differs by {sorted(missing_extra)}"
        )
    return conds
