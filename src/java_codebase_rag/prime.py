"""``jrag prime`` payload template (jrag-prime plan, Task 1).

Pure rendering of the SessionStart priming payload. The canonical template
lives in ``docs/superpowers/specs/active/2026-08-30-jrag-prime-design.md``;
:data:`PRIME_TEMPLATE` is that text verbatim with the computed ``{…}`` slots
turned into ``str.format`` placeholders. Parts 1-3 are static: identity
paragraph, trust rule, and the command surface copied from ``jrag --help``
(with ``routes``/``clients`` normalized to the real ``http-routes``/
``http-clients``). Part 4 — the ``**Index state**`` bullets — is computed by
the caller and passed in as a :class:`PrimeState`.

This module must stay importable with the stdlib alone: it loads on the
SessionStart hook path, where the vector stack (torch,
sentence_transformers, lancedb, pyarrow, cocoindex) must never load.
``render``/``render_hook_json`` are string formatting only; the one I/O here
is :func:`_staleness_since`, a metadata-only directory walk.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PRIME_TEMPLATE", "PrimeState", "render", "render_hook_json"]

PRIME_TEMPLATE = """`jrag` is a CLI over a prebuilt structural index of this Java/Kotlin repo — a map, not an oracle.
It resolves names to files, walks who-calls-whom and dependency edges, and surfaces entry points
and service boundaries — structure you'd otherwise grep for. You are the explorer; jrag is the map.

**Trust rule:** if jrag and the files disagree, trust the files — the index may lag the working tree.

**Index state**

- Index {freshness} (incremented {last_increment_age} ago) · watch daemon {daemon_state}
- {service_count} services ({service_names}) · {symbol_count} symbols
- {route_count} routes · {client_count} clients · {producer_count} producers

**Commands by group** (from `jrag --help`)

    health:      status
    locate:      find, inspect
    listings:    http-routes, http-clients, producers, topics, jobs, listeners, entities
    traversal:   callers, callees, hierarchy, implementations, subclasses,
                 overrides, overridden-by, dependents, impact, decompose,
                 flow, dependencies, connection, outline, imports
    orientation: microservices, map, conventions, overview
    search:      search

**Command reference**

- `status` — Print index freshness, ontology version, and counts.
- `prime` — Print agent priming context (index state + command surface).
- `find` — Find nodes by query or filter.
- `inspect` — Inspect a node by query.
- `http-routes` — List HTTP routes.
- `http-clients` — List HTTP clients.
- `producers` — List async message producers.
- `topics` — List message topics (producer-grouped).
- `jobs` — List scheduled tasks.
- `listeners` — List message listeners.
- `entities` — List JPA entities.
- `callers` — Who calls this symbol or route?
- `callees` — What does this symbol call?
- `hierarchy` — Type hierarchy (parents and children).
- `implementations` — Classes implementing an interface.
- `subclasses` — Classes extending a type.
- `overrides` — Methods this method overrides (dispatch UP to declaration).
- `overridden-by` — Methods overriding this one (dispatch DOWN to overriders).
- `dependents` — Who injects this type?
- `impact` — Fleet-wide blast radius (INJECTS/IMPLEMENTS/EXTENDS reverse closure).
- `decompose` — Role-waterfall flow from an entrypoint.
- `flow` — Request flow through a route (inbound callers + outbound CALLS hops).
- `dependencies` — Types this Symbol injects (INJECTS out).
- `connection` — Cross-service connections for a microservice (inbound/outbound).
- `outline` — List symbols declared in a file.
- `imports` — List imports declared in a file (tree-sitter parse + resolve_v2).
- `microservices` — List microservices with resolved type counts.
- `map` — Symbol counts per kind, grouped by service or module.
- `conventions` — Dominant roles + framework tallies.
- `overview` — Bundle for a microservice, route, or topic.
- `search` — Semantic search over Lance tables.
- `vocab-index` — Rebuild the vocabulary index (absence diagnosis).
- `watch` — keep the index fresh and serve warm queries while running

Run `jrag <command> --help` for flags."""


@dataclass(frozen=True)
class PrimeState:
    """Computed index state for the payload's ``**Index state**`` bullets.

    ``last_increment_age`` is pre-humanized by the caller (e.g. ``"3h"``) and
    ``service_names`` arrives already truncated (e.g.
    ``("orders", "billing", "+2 more")``); this module only formats them.
    """

    freshness: str
    changed_files: int | None
    last_increment_age: str
    service_count: int
    service_names: tuple[str, ...]
    symbol_count: int
    route_count: int
    client_count: int
    producer_count: int
    daemon_running: bool


def render(state: PrimeState) -> str:
    """Fill :data:`PRIME_TEMPLATE`'s slots from ``state``.

    A stale index with a known change count spells it out (``stale — 56 files
    changed since last increment``); every other case renders the bare
    freshness word.
    """
    if state.freshness == "stale" and state.changed_files is not None:
        freshness = f"stale — {state.changed_files} files changed since last increment"
    else:
        freshness = state.freshness
    return PRIME_TEMPLATE.format(
        freshness=freshness,
        last_increment_age=state.last_increment_age,
        daemon_state="running" if state.daemon_running else "not running",
        service_count=state.service_count,
        service_names=", ".join(state.service_names),
        symbol_count=state.symbol_count,
        route_count=state.route_count,
        client_count=state.client_count,
        producer_count=state.producer_count,
    )


def render_hook_json(state: PrimeState) -> str:
    """Wrap :func:`render` in the Claude Code SessionStart hook envelope."""
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": render(state),
            }
        },
        ensure_ascii=False,
    )


# Pruned from the staleness walk. ``.java-codebase-rag`` is the index's own
# directory (its mtimes move on every increment); the rest are build output
# that would swamp the count without ever being indexed.
_STALENESS_SKIP_DIRS = frozenset(
    {".git", "target", "build", "node_modules", ".java-codebase-rag"}
)


def _staleness_since(built_at: float, source_root: Path, *, cap: int = 5000) -> int | None:
    """Count ``.java``/``.kt`` files under ``source_root`` newer than ``built_at``.

    Filesystem metadata only — mtimes, no reads and no parses — so the
    SessionStart hook stays cheap. Two bounds share ``cap``: the changed-count
    saturates (a mass rename or fresh checkout reports ``cap`` and stops), and
    the total files visited is capped so a fresh index over a huge tree cannot
    turn every session start into a full-tree stat storm — once ``cap``
    all-unchanged files have been visited without finding a change, the walk
    gives up and returns ``None`` (unknown), not a verified 0. Every other
    case returns the exact ``int`` count. Returns 0 for a missing
    ``source_root`` (nothing walked, nothing newer).
    """
    changed = 0
    visited = 0
    for dirpath, dirnames, filenames in os.walk(source_root):
        dirnames[:] = [d for d in dirnames if d not in _STALENESS_SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1] not in (".java", ".kt"):
                continue
            visited += 1
            if visited > cap and changed == 0:
                return None  # all-unchanged walk unbounded — unknown, not zero
            try:
                mtime = os.stat(os.path.join(dirpath, name)).st_mtime
            except OSError:
                continue  # raced with a delete; a gone file is not a change
            if mtime > built_at:
                changed += 1
                if changed >= cap:
                    return cap
    return changed
