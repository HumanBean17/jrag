"""Tests for java_codebase_rag.prime (Task 1, jrag-prime plan).

Pure unit tests for the SessionStart priming payload: canonical template
shape, freshness variants, hook-JSON envelope, stdlib-only import purity,
the staleness-walk cost bounds, and a drift guard pinning the embedded
command surface to the real ``jrag --help`` output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from java_codebase_rag.prime import PrimeState, _staleness_since, render, render_hook_json

# The drift guard runs the agent CLI through the interpreter already running
# the tests (conftest pins the editable install, so this is this source tree).
# Never hardcode `.venv/bin/jrag` — CI installs via setup-python, no repo venv.
# Module invocation, not the console script: `cli` is the operator lifecycle
# CLI; `jrag` here is the agent CLI the payload must track.
_JRAG_CMD = [sys.executable, "-m", "java_codebase_rag.jrag"]

_HEAVY_DEPS = ("torch", "sentence_transformers", "lancedb", "pyarrow", "cocoindex")


def _fresh_state() -> PrimeState:
    return PrimeState(
        freshness="fresh",
        changed_files=0,
        last_increment_age="3h",
        service_count=4,
        service_names=("orders", "billing", "shipping", "+1 more"),
        symbol_count=12845,
        route_count=210,
        client_count=45,
        producer_count=12,
        daemon_running=True,
    )


def _stale_state(changed: int | None) -> PrimeState:
    return PrimeState(
        freshness="stale",
        changed_files=changed,
        last_increment_age="5m",
        service_count=1,
        service_names=("orders",),
        symbol_count=10,
        route_count=2,
        client_count=3,
        producer_count=4,
        daemon_running=False,
    )


def _index_state_lines(out: str) -> list[str]:
    """The bullet block under the `**Index state**` heading."""
    bullets: list[str] = []
    for line in out.splitlines()[out.splitlines().index("**Index state**") + 1 :]:
        if line.startswith("- "):
            bullets.append(line)
        elif bullets or line.strip():
            break
    return bullets


# ----- Test 1: canonical shape -----


def test_render_matches_canonical_shape() -> None:
    out = render(_fresh_state())

    # Landmark lines appear, in canonical order.
    landmarks = [
        "You are the explorer; jrag is the map.",
        "**Trust rule:**",
        "**Index state**",
        "**Commands by group**",
        "**Command reference**",
        "Run `jrag <command> --help` for flags.",
    ]
    pos = -1
    for landmark in landmarks:
        pos = out.index(landmark, pos + 1)
    assert out.index("**Commands by group** (from `jrag --help`)") < out.index("- `status` —")

    # State block renders exactly the three spec bullets with the state's numbers.
    assert _index_state_lines(out) == [
        "- Index fresh (incremented 3h ago) · watch daemon running",
        "- 4 services (orders, billing, shipping, +1 more) · 12845 symbols",
        "- 210 routes · 45 clients · 12 producers",
    ]


# ----- Test 2: freshness variants -----


def test_render_stale_variants() -> None:
    with_count = render(_stale_state(56))
    line = _index_state_lines(with_count)[0]
    assert "stale — 56 files changed since last increment" in line
    assert line.startswith("- Index stale — 56 files changed since last increment (incremented")

    without_count = render(_stale_state(None))
    line = _index_state_lines(without_count)[0]
    assert "stale" in line
    assert "files changed" not in line


# ----- Test 3: hook JSON envelope -----


def test_hook_json_envelope() -> None:
    state = _stale_state(56)
    out = render_hook_json(state)

    doc = json.loads(out)
    assert list(doc) == ["hookSpecificOutput"]
    inner = doc["hookSpecificOutput"]
    assert inner["hookEventName"] == "SessionStart"
    assert inner["additionalContext"] == render(state)

    # Key order is part of the contract, and non-ASCII renders literally
    # (ensure_ascii=False) rather than as \uXXXX escapes.
    top_pairs = json.loads(out, object_pairs_hook=lambda pairs: pairs)
    assert [key for key, _ in top_pairs] == ["hookSpecificOutput"]
    assert [key for key, _ in top_pairs[0][1]] == ["hookEventName", "additionalContext"]
    assert "·" in out


# ----- Test 4: stdlib purity -----


def test_template_is_stdlib_pure() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", "import java_codebase_rag.prime"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr

    listing = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, java_codebase_rag.prime; "
            "print('\\n'.join(sorted(m.split('.')[0] for m in sys.modules)))",
        ],
        capture_output=True,
        text=True,
    )
    assert listing.returncode == 0, listing.stderr
    roots = set(listing.stdout.split())
    pulled = sorted(roots.intersection(_HEAVY_DEPS))
    assert not pulled, f"prime import pulled heavy deps: {pulled}"


# ----- Test 5: staleness walk cost bounds -----


def _seed_sources(root: Path, names: list[str], *, built_at: float, newer: bool) -> None:
    """Write source files stamped an hour newer (changed) or older (unchanged)
    than ``built_at``. Subdirectories in ``names`` are created as needed."""
    root.mkdir(parents=True, exist_ok=True)
    stamp = built_at + 3600.0 if newer else built_at - 3600.0
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("class C {}\n")
        os.utime(p, (stamp, stamp))


def test_staleness_walk_counts_changes_exactly(tmp_path: Path) -> None:
    """Changed ``.java``/``.kt`` files are counted; other extensions and skip
    dirs are not; an all-unchanged tree under the cap returns a verified 0."""
    built_at = time.time()
    _seed_sources(
        tmp_path,
        ["a.java", "b.kt", "target/Gen.java"],
        built_at=built_at,
        newer=False,
    )
    _seed_sources(
        tmp_path, ["c.java", "d.kt", "e.txt"], built_at=built_at, newer=True
    )

    assert _staleness_since(built_at, tmp_path) == 2  # c.java + d.kt only
    assert _staleness_since(built_at, tmp_path / "does-not-exist") == 0


def test_staleness_walk_visited_cap_returns_none(tmp_path: Path) -> None:
    """More than ``cap`` all-unchanged files → give up as unknown (``None``),
    not a verified 0 — the walk must stay bounded on every session start."""
    built_at = time.time()
    _seed_sources(
        tmp_path, [f"u{i}.java" for i in range(6)], built_at=built_at, newer=False
    )

    assert _staleness_since(built_at, tmp_path, cap=5) is None
    # Under the cap the same tree is walked to completion: a verified 0.
    assert _staleness_since(built_at, tmp_path, cap=6) == 0


def test_staleness_walk_cap_saturates_changed_count(tmp_path: Path) -> None:
    """The changed-count still saturates at ``cap`` once changes are found."""
    built_at = time.time()
    _seed_sources(
        tmp_path, ["c0.java", "c1.java", "c2.java"], built_at=built_at, newer=True
    )

    assert _staleness_since(built_at, tmp_path, cap=2) == 2


def test_staleness_walk_counts_past_visited_cap_once_changed(tmp_path: Path) -> None:
    """A change already found keeps the walk going past the visited cap to an
    exact count — the visited bound protects the fresh-tree case; it must not
    truncate a count already worth reporting. The changed file sits in the
    walk root (os.walk visits the root's files before any subdirectory), so
    the change is always found before the cap trips."""
    built_at = time.time()
    _seed_sources(tmp_path, ["changed.java"], built_at=built_at, newer=True)
    _seed_sources(
        tmp_path, [f"pkg/u{i}.java" for i in range(5)], built_at=built_at, newer=False
    )

    assert _staleness_since(built_at, tmp_path, cap=4) == 1


# ----- Test 6: drift guard against real `jrag --help` -----


def _collapse(text: str) -> str:
    """Argparse wraps long descriptions; compare with whitespace collapsed."""
    return " ".join(text.split())


def _help_command_entries(help_text: str) -> list[str]:
    """`name description` strings from the positional-arguments block."""
    entries: list[list[str]] = []
    in_block = False
    for line in help_text.splitlines():
        if line.startswith("positional arguments:"):
            in_block = True
            continue
        if in_block and not line.startswith(" "):
            break  # block ends at the blank line before `options:`
        match = re.match(r"^    ([\w][\w-]*)\s\s+(\S.*)$", line)
        if match:
            entries.append([match.group(1), match.group(2)])
        elif entries and line.strip():
            entries[-1][1] += " " + line.strip()
    return [_collapse(f"{name} {description}") for name, description in entries]


def test_template_tracks_real_help() -> None:
    proc = subprocess.run([*_JRAG_CMD, "--help"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr

    help_entries = set(_help_command_entries(proc.stdout))
    assert help_entries, "no command descriptions parsed from `jrag --help`"

    payload_entries = set()
    for line in render(_fresh_state()).splitlines():
        match = re.match(r"^- `([\w-]+)` — (.+)$", line)
        if match:
            payload_entries.add(_collapse(f"{match.group(1)} {match.group(2)}"))
    assert payload_entries, "payload has no command-reference lines"

    stale = payload_entries - help_entries
    assert not stale, f"payload descriptions not in `jrag --help`: {sorted(stale)}"
    drifted = help_entries - payload_entries
    assert not drifted, f"`jrag --help` commands missing from payload: {sorted(drifted)}"
