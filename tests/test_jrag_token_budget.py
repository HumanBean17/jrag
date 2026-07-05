"""Token-budget guard for `jrag` default (text) output (PR-JRAG-4, §14).

Test 19: test_no_default_output_exceeds_token_ceiling

Asserts that no jrag command's default text output exceeds a token ceiling on
the bank-chat fixture. This prevents output bloat from blowing the agent's
context window as fields accrete over time.

Token estimation: chars / 4 (a common heuristic for English/code text; the
actual ratio for this CLI is closer to 3.5–4). The ceiling is generous (4000
tokens ≈ 16000 chars) to allow room for legitimately large traversals (e.g.
``decompose`` with multi-stage flows) while still catching runaway growth.

Commands that need a ``<query>`` use seed identifiers verified against the
bank-chat fixture (see test_jrag_traversal_direct.py). ``search`` is excluded
from this guard because it requires the Lance index (heavy); it has its own
truncation via +1-fetch and ``--limit``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _jrag_exe() -> str:
    """Locate the installed ``jrag`` entry point next to the venv interpreter."""
    candidate = Path(sys.executable).parent / "jrag"
    if candidate.is_file():
        return str(candidate)
    exe = shutil.which("jrag")
    assert exe is not None, "expected installed jrag entrypoint (run: pip install -e .)"
    return exe


def _run_jrag_text(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run jrag in text mode (default) and return the completed process."""
    return subprocess.run(
        [_jrag_exe(), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


# Token ceiling: ~4000 tokens (≈16000 chars). Generous enough for multi-stage
# decompose flows, tight enough to catch bloat.
_TOKEN_CEILING = 4000
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: chars / 4."""
    return len(text) // _CHARS_PER_TOKEN


# Commands and their args. Queries use seed identifiers from the bank-chat
# fixture (verified in test_jrag_traversal_direct.py). Each tuple is
# (label, args-list). Commands that take <query> use a known-good seed.
_SEED_METHOD = "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)"
_SEED_TYPE = "com.bank.chat.engine.notification.AbstractNotificationSender"
_SEED_FILE = "chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java"


def test_no_default_output_exceeds_token_ceiling(
    corpus_root: Path, ladybug_db_path: Path
) -> None:
    """No jrag command's default text output exceeds the token ceiling."""
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)

    commands: list[tuple[str, list[str]]] = [
        # Orientation
        ("status", ["status"]),
        ("microservices", ["microservices"]),
        ("map", ["map"]),
        ("conventions", ["conventions"]),
        ("overview-microservice", ["overview", "chat-assign"]),
        ("overview-route", ["overview", "/chat/assign"]),
        ("overview-topic", ["overview", "banking.chat.compliance.review"]),
        # Locate
        ("find-query", ["find", "ChatManagementService"]),
        ("find-filter", ["find", "--role", "CONTROLLER"]),
        ("inspect", ["inspect", "ChatManagementService"]),
        ("outline", ["outline", _SEED_FILE]),
        ("imports", ["imports", _SEED_FILE]),
        # Listings
        ("http-routes", ["http-routes"]),
        ("http-clients", ["http-clients"]),
        ("producers", ["producers"]),
        ("topics", ["topics"]),
        ("jobs", ["jobs"]),
        ("listeners", ["listeners"]),
        ("entities", ["entities"]),
        # Traversals
        ("callers", ["callers", _SEED_METHOD]),
        ("callees", ["callees", _SEED_METHOD]),
        ("hierarchy", ["hierarchy", _SEED_TYPE]),
        ("dependents", ["dependents", _SEED_TYPE]),
        ("dependencies", ["dependencies", _SEED_TYPE]),
        ("impact", ["impact", _SEED_TYPE, "--depth", "1"]),
        ("connection", ["connection", "chat-assign"]),
        ("flow", ["flow", "/chat/assign"]),
    ]

    violations: list[str] = []
    for label, args in commands:
        proc = _run_jrag_text(args, env=env)
        output = proc.stdout
        tokens = _estimate_tokens(output)
        if tokens > _TOKEN_CEILING:
            violations.append(
                f"{label}: {tokens} tokens ({len(output)} chars) > {_TOKEN_CEILING} ceiling"
            )

    assert not violations, (
        f"token-budget violations on {len(violations)} command(s):\n  "
        + "\n  ".join(violations)
    )
