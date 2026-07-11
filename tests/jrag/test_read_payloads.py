"""Golden-output equivalence tests for the jrag read-command payload cores.

These pin the cold read path to BYTE-IDENTICAL output after the behavior-
preserving extraction of ``*_payload(args, cfg, graph)`` cores into
``java_codebase_rag.read_payloads`` (Task 5 of the jrag-watch plan).

Two layers of evidence:

1. **End-to-end byte-identity** (``test_golden_*``): runs each read command via
   the installed ``jrag`` CLI with ``--format json`` on the bank-chat fixture
   and compares stdout, byte-for-byte, to a golden file captured from the
   CURRENT (pre-refactor) handlers. The handler after refactor is
   ``payload = <cmd>_payload(...); render(payload, args)``, so this proves the
   whole payload+render pipeline is unchanged. Golden files live in
   ``tests/jrag/golden/``.

2. **Payload boundary** (``test_payload_boundary_*``): calls each
   ``*_payload(args, cfg, graph)`` directly in-process and asserts it returns a
   JSON-serializable structure of the expected shape (and, for the three
   traversal folds, that the fold actually fired). This verifies the extraction
   boundary itself, independent of rendering.

Fold coverage (the whole point of Task 5 — the golden fixtures MUST exercise
each ad-hoc fold):

* ``callers`` EXPOSES-inbound fold  -> ``callers_exposes_fold`` golden
  (controller CLASS root -> DECLARES.EXPOSES -> Route rows). Verified to contain
  ``edge_type: EXPOSES`` edges to ``kind: route`` nodes.
* ``callees`` CLIENT-role HTTP_CALLS fold -> ``callees_client_fold`` golden
  (Feign-client interface root -> declared Client nodes -> HTTP_CALLS -> Route).
  Verified to contain ``edge_type: HTTP_CALLS`` edges to ``kind: route`` nodes.
* ``flow`` inbound/outbound merge -> ``flow_merge`` golden (route root). Verified
  to contain BOTH ``CALLS`` (outbound) and ``HTTP_CALLS`` (inbound) edges.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Golden cases: (name, argv, must_exercise_fold).
# ``argv`` is exactly what the golden was captured with. ``must_exercise_fold``
# is True for the three fold-bearing paths and drives the boundary assertions.
_GOLDEN_CASES = [
    ("callers_exposes_fold", ["callers", "com.bank.chat.app.web.ChatIngressController", "--format", "json"], True),
    ("callers_symbol", ["callers", "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)", "--format", "json"], False),
    ("callers_route", ["callers", "/chat/joinOperator", "--service", "chat-core", "--format", "json"], False),
    ("callees_client_fold", ["callees", "com.bank.chat.assign.integration.ChatCoreFeignClient", "--format", "json"], True),
    ("callees_symbol", ["callees", "com.bank.chat.assign.service.ChatManagementService#assign(AssignmentRequest)", "--format", "json"], False),
    ("flow_merge", ["flow", "/chat/joinOperator", "--service", "chat-core", "--format", "json"], True),
    ("search", ["search", "assign chat", "--format", "json"], False),
    ("find_query", ["find", "ChatManagementService", "--format", "json"], False),
    ("find_filter", ["find", "--role", "SERVICE", "--format", "json"], False),
    ("inspect", ["inspect", "com.bank.chat.assign.service.ChatManagementService", "--format", "json"], False),
]

_GOLDEN_DIR = Path(__file__).parent / "golden"

# Commands whose rendered JSON is byte-stable run-to-run and can be pinned with
# raw byte comparison. ``inspect`` is EXCLUDED: its ``edge_summary`` sub-dict
# key order (DECLARES/INJECTS) is non-deterministic across processes — a
# PRE-EXISTING property of ``describe_v2`` (verified: 6 original-code runs
# produced DECLARES-first 3x and INJECTS-first 3x, identical values). inspect is
# therefore pinned by CANONICALIZED JSON (same values, order-insensitive).
_BYTE_STABLE = {
    "callers_exposes_fold", "callers_symbol", "callers_route",
    "callees_client_fold", "callees_symbol", "flow_merge",
    "search", "find_query", "find_filter",
}


def _canonical_json(s: str) -> str:
    """Parse + re-dump with sorted keys: order-insensitive value identity."""
    return json.dumps(json.loads(s), sort_keys=True, ensure_ascii=False)


def _jrag_exe() -> str:
    """Locate the installed ``jrag`` entry point next to the venv interpreter."""
    candidate = Path(sys.executable).parent / "jrag"
    if candidate.is_file():
        return str(candidate)
    exe = shutil.which("jrag")
    assert exe is not None, "expected installed jrag entrypoint (run: pip install -e .)"
    return exe


def _run_jrag(argv: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_jrag_exe(), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def _env_for(corpus_root: Path, ladybug_db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] = str(corpus_root)
    env["JAVA_CODEBASE_RAG_INDEX_DIR"] = str(ladybug_db_path.parent)
    return env


# ---------------------------------------------------------------------------
# Layer 1: end-to-end byte-identity vs golden files.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,argv,_exercise_fold", _GOLDEN_CASES, ids=[c[0] for c in _GOLDEN_CASES])
def test_golden_output_byte_identical(name, argv, _exercise_fold, corpus_root, ladybug_db_path) -> None:
    """Full ``jrag <cmd> --format json`` stdout matches the pre-refactor golden.

    The handler is ``payload = <cmd>_payload(...); render(payload, args)`` after
    refactor, so this exercises the entire payload+render pipeline and proves
    byte-identity of the cold read path.
    """
    golden_path = _GOLDEN_DIR / f"{name}.json"
    assert golden_path.is_file(), (
        f"golden file missing: {golden_path}. Re-run the capture script (see "
        f"task-5 report) to regenerate."
    )
    meta_path = _GOLDEN_DIR / f"{name}.meta.json"
    expected_rc = json.loads(meta_path.read_text(encoding="utf-8"))["rc"] if meta_path.is_file() else 0

    proc = _run_jrag(argv, env=_env_for(corpus_root, ladybug_db_path))
    golden = golden_path.read_text(encoding="utf-8")
    assert proc.returncode == expected_rc, (
        f"{name}: rc changed: expected {expected_rc}, got {proc.returncode}\n"
        f"stderr={proc.stderr}"
    )
    if name in _BYTE_STABLE:
        assert proc.stdout == golden, (
            f"{name}: stdout differs from golden (byte-identity broken).\n"
            f"--- expected (golden) ---\n{golden}\n"
            f"--- actual ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n"
        )
    else:
        # inspect: edge_summary key order is pre-existing non-deterministic
        # (see _BYTE_STABLE comment); pin canonicalized value identity instead.
        assert _canonical_json(proc.stdout) == _canonical_json(golden), (
            f"{name}: canonicalized JSON differs from golden (values changed).\n"
            f"--- expected (golden) ---\n{_canonical_json(golden)}\n"
            f"--- actual ---\n{_canonical_json(proc.stdout)}\n"
        )


# ---------------------------------------------------------------------------
# Layer 2: payload boundary (in-process).
# ---------------------------------------------------------------------------


def _build_args(argv: list[str]):
    """Parse a jrag command line into the same argparse.Namespace the handler sees."""
    from java_codebase_rag.jrag import build_parser

    # build_parser attaches the subcommand handler; parse_args reproduces the
    # exact Namespace (incl. defaults like auto_scope/detail) the CLI produces.
    return build_parser().parse_args(argv)


def _load_cfg_graph(args, ladybug_db_path, monkeypatch):
    """Resolve cfg + load a fresh graph singleton pointed at the session index."""
    from java_codebase_rag.graph.ladybug_queries import LadybugGraph
    from java_codebase_rag.jrag import _resolve_cfg, _load_graph

    monkeypatch.setenv("JAVA_CODEBASE_RAG_INDEX_DIR", str(ladybug_db_path.parent))
    cfg = _resolve_cfg(args)
    # Reset the singleton so the handler/payload loads THIS index regardless of
    # whatever prior tests left in LadybugGraph._instance.
    LadybugGraph._instance = None
    LadybugGraph._instance_path = None
    graph = _load_graph(cfg)
    return cfg, graph


def _is_json_serializable(obj) -> bool:
    try:
        json.dumps(obj, default=_pydantic_default)
        return True
    except (TypeError, ValueError):
        return False


def _pydantic_default(obj):
    # pydantic models + dataclasses round-trip via model_dump()/__dict__.
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"not serializable: {type(obj)!r}")


@pytest.mark.parametrize(
    "name,argv,exercise_fold",
    [c for c in _GOLDEN_CASES if c[0] in {"callers_exposes_fold", "callees_client_fold", "flow_merge"}],
    ids=["callers_exposes_fold", "callees_client_fold", "flow_merge"],
)
def test_payload_boundary_traversals(name, argv, exercise_fold, corpus_root, ladybug_db_path, monkeypatch) -> None:
    """``callers_payload``/``callees_payload``/``flow_payload`` return a JSON-
    serializable traversal payload, and the fold for this case actually fired.

    Verifies the extraction boundary: the payload function (not the renderer)
    carries the fold's edges/nodes.
    """
    from java_codebase_rag.read_payloads import (
        callers_payload,
        callees_payload,
        flow_payload,
    )

    cmd = argv[0]
    payload_fn = {"callers": callers_payload, "callees": callees_payload, "flow": flow_payload}[cmd]
    args = _build_args(argv)
    cfg, graph = _load_cfg_graph(args, ladybug_db_path, monkeypatch)

    payload = payload_fn(args, cfg, graph)  # type: ignore[arg-type]
    assert isinstance(payload, dict), f"{cmd}_payload must return a dict, got {type(payload)!r}"
    # Core keys the renderer (_emit_traversal) consumes.
    for key in ("root_id", "nodes", "edges", "noun", "warnings", "truncated", "is_external_entrypoint"):
        assert key in payload, f"{cmd}_payload missing key {key!r}: {sorted(payload)}"
    # JSON-serializable (the watch daemon will json.dumps this). Edges/nodes are
    # plain dicts; warnings is a list; truncated/is_external_entrypoint are bool.
    assert _is_json_serializable(payload), f"{cmd}_payload not JSON-serializable"

    # Fold actually fired for this golden case:
    edge_types = {e.get("edge_type") for e in payload["edges"]}
    node_kinds = {n.get("kind") for n in payload["nodes"].values()}
    if cmd == "callers":
        assert "EXPOSES" in edge_types, (
            f"callers EXPOSES-inbound fold did not fire; edge_types={edge_types}"
        )
        assert "route" in node_kinds, f"folded route nodes missing; node_kinds={node_kinds}"
    elif cmd == "callees":
        assert "HTTP_CALLS" in edge_types, (
            f"callees CLIENT-role HTTP_CALLS fold did not fire; edge_types={edge_types}"
        )
        assert "route" in node_kinds, f"folded route nodes missing; node_kinds={node_kinds}"
    elif cmd == "flow":
        assert "CALLS" in edge_types and "HTTP_CALLS" in edge_types, (
            f"flow inbound/outbound merge did not fire; edge_types={edge_types}"
        )


def test_payload_boundary_search_find_inspect(corpus_root, ladybug_db_path, monkeypatch) -> None:
    """search/find/inspect payload cores return their ``*_v2`` (or
    find_by_name_or_fqn) result and are JSON-serializable. These are the
    'clean core' commands; the boundary test is lighter (no fold to exercise)."""
    from java_codebase_rag.read_payloads import (
        search_payload,
        find_payload,
        inspect_payload,
    )

    # find query mode + filter mode + inspect all resolve on the graph-only index.
    cases = [
        (find_payload, ["find", "ChatManagementService", "--format", "json"], "find"),
        (find_payload, ["find", "--role", "SERVICE", "--format", "json"], "find"),
        (inspect_payload, ["inspect", "com.bank.chat.assign.service.ChatManagementService", "--format", "json"], "inspect"),
    ]
    for payload_fn, argv, label in cases:
        args = _build_args(argv)
        cfg, graph = _load_cfg_graph(args, ladybug_db_path, monkeypatch)
        payload = payload_fn(args, cfg, graph)
        assert _is_json_serializable(payload), f"{label}_payload not JSON-serializable"

    # search payload: the bank-chat index has no Lance table, so search_v2
    # returns success=False (table-not-found). The payload must still be a
    # SearchOutput (JSON-serializable) — this is the same shape the handler
    # renders into the error envelope pinned by the search golden.
    args = _build_args(["search", "assign chat", "--format", "json"])
    cfg, graph = _load_cfg_graph(args, ladybug_db_path, monkeypatch)
    out = search_payload(args, cfg, graph)
    assert hasattr(out, "model_dump"), "search_payload must return a SearchOutput (pydantic)"
    assert _is_json_serializable(out.model_dump()), "search_payload result not JSON-serializable"


def test_payload_error_raises_envelope(monkeypatch, corpus_root, ladybug_db_path) -> None:
    """A resolve/guard failure raises ``PayloadError`` carrying the Envelope + rc
    the handler renders — so the handler can render errors byte-identically."""
    from java_codebase_rag.read_payloads import flow_payload, PayloadError

    # flow requires a Route root; a non-existent symbol resolves to not_found
    # (status != "ok") -> PayloadError.
    args = _build_args(["flow", "com.bank.chat.does.NotExist", "--format", "json"])
    cfg, graph = _load_cfg_graph(args, ladybug_db_path, monkeypatch)
    with pytest.raises(PayloadError) as ei:
        flow_payload(args, cfg, graph)
    assert ei.value.env is not None
    assert isinstance(ei.value.rc, int)
