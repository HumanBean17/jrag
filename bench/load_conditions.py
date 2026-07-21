"""Executable condition-isolation spec loader (Plan 1, Task 5).

Conditions differ ONLY in the tool set exposed to Claude Code — enforced by
harness flags (``--mcp-config`` / ``--allowedTools`` / ``--disallowedTools``),
never by prompt pleas. This module validates the methodological invariants
(graph tools denied in B; jrag never denied in D; A/C have no MCP) and emits the
exact flag arguments via ``to_flags``.

Pure validation — no I/O beyond reading the YAML and the prompt file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# jrag MCP tool names (must match the server's registered tool surface).
JRAG_GRAPH_TOOLS = [
    "mcp__jrag__find",
    "mcp__jrag__describe",
    "mcp__jrag__neighbors",
    "mcp__jrag__resolve",
]
JRAG_VECTOR_TOOLS = ["mcp__jrag__search"]
ALL_JRAG_TOOLS = JRAG_GRAPH_TOOLS + JRAG_VECTOR_TOOLS

# Common escape/integrity deny-list applied to EVERY condition.
# Under `--permission-mode bypassPermissions`, `--allowedTools` is additive
# (a permission grant, not an exclusive allowlist), so isolation must be
# enforced via `--disallowedTools`. This list blocks: checkout mutation
# (Edit/Write/NotebookEdit — reproducibility), external info (WebSearch/
# WebFetch — all reasoning must come from the local codebase), and subagent
# dispatch (Agent/Task — closes the unmonitorable subagent-escape vector).
# Per-condition variation is ONLY jrag/lexical access (see conditions.yml).
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
    """One experimental condition (A/B/C/D)."""

    id: str
    name: str
    mcp_servers: list[str]
    allowed_tools: list[str]
    disallowed_tools: list[str]
    prompt_file: str


@dataclass(frozen=True)
class ConditionFlags:
    """The exact ``claude -p`` flag payload assembled from a Condition."""

    mcp_config_arg: str | None  # path for --mcp-config, or None when no MCP
    allowed_tools: list[str]
    disallowed_tools: list[str]
    append_system_prompt: str  # contents read from prompt_file


def validate(cond: Condition) -> None:
    """Raise ``ConfigError`` on any per-condition isolation violation."""
    if cond.id not in VALID_IDS:
        raise ConfigError(f"condition id {cond.id!r} must be one of {sorted(VALID_IDS)}")
    if not cond.prompt_file or not Path(cond.prompt_file).is_file():
        raise ConfigError(f"condition {cond.id}: prompt_file {cond.prompt_file!r} not found")

    if cond.id in ("A", "C") and cond.mcp_servers:
        raise ConfigError(
            f"condition {cond.id} must have empty mcp_servers (no MCP); got {cond.mcp_servers!r}"
        )

    if cond.id == "B":
        # B = vector-only: graph tools MUST be denied; the vector tool MUST survive.
        missing = set(JRAG_GRAPH_TOOLS) - set(cond.disallowed_tools)
        if missing:
            raise ConfigError(
                f"condition B disallowed_tools must include all graph tools; missing {sorted(missing)}"
            )
        leaked_vector = set(cond.disallowed_tools) & set(JRAG_VECTOR_TOOLS)
        if leaked_vector:
            raise ConfigError(
                f"condition B must NOT deny vector tools {JRAG_VECTOR_TOOLS}; "
                f"found {sorted(leaked_vector)}"
            )

    if cond.id == "D":
        # D = jrag full: no jrag tool may be denied.
        denied_jrag = set(cond.disallowed_tools) & set(ALL_JRAG_TOOLS)
        if denied_jrag:
            raise ConfigError(
                f"condition D must NOT deny any jrag tool; found {sorted(denied_jrag)}"
            )


def to_flags(
    cond: Condition, jrag_mcp_config_path: str = "bench/mcp/jrag.json"
) -> ConditionFlags:
    """Assemble the ``claude -p`` flag payload for a condition."""
    prompt = Path(cond.prompt_file).read_text(encoding="utf-8")
    mcp_arg = jrag_mcp_config_path if "jrag" in cond.mcp_servers else None
    return ConditionFlags(
        mcp_config_arg=mcp_arg,
        allowed_tools=list(cond.allowed_tools),
        disallowed_tools=list(cond.disallowed_tools),
        append_system_prompt=prompt,
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


_CONDITION_KEYS = {"id", "name", "mcp_servers", "allowed_tools",
                   "disallowed_tools", "prompt_file"}


def _record_from_entry(entry: dict) -> Condition:
    unknown = set(entry.keys()) - _CONDITION_KEYS
    if unknown:
        raise ConfigError(f"condition entry has unknown keys {sorted(unknown)}: {entry!r}")
    return Condition(
        id=str(entry.get("id", "")).strip(),
        name=str(entry.get("name", "")).strip(),
        mcp_servers=list(entry.get("mcp_servers") or []),
        allowed_tools=list(entry.get("allowed_tools") or []),
        disallowed_tools=list(entry.get("disallowed_tools") or []),
        prompt_file=str(entry.get("prompt_file", "")).strip(),
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
