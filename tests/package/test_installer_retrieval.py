"""Retrieval-mode selection (``vectors`` | ``bm25``) in the install wizard.

Validates the installer side of the retrieval knob:
  - ``select_retrieval`` wizard question (flag validation, non-interactive
    default, re-run prefill honored as the prompt default)
  - ``generate_yaml_config`` persistence: ``bm25`` writes ``retrieval: bm25``;
    ``vectors`` writes nothing and drops a stale key when a re-run switches
    back
  - Stage-2 wiring in ``run_install``: ``bm25`` skips ``resolve_model``
    entirely; a graph-only platform (macOS Intel) forces ``bm25``; the
    ``vectors`` default is not persisted
  - ``--retrieval`` flag registration + ``_cmd_install`` pass-through
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

import java_codebase_rag.installer as installer
from java_codebase_rag.installer import (
    HOSTS,
    LAYOUT_SINGLE_MODULE,
    JavaDetection,
    generate_yaml_config,
    run_install,
    select_retrieval,
)


def _forbid_prompt():
    """Prompt stub that fails the test if the wizard prompts unexpectedly."""

    def _raise(*args, **kwargs):
        raise AssertionError("select_retrieval must not prompt on this path")

    return _raise


# ---------------------------------------------------------------------------
# select_retrieval: flag validation + non-interactive default
# ---------------------------------------------------------------------------


def test_cli_flag_bm25_wins_without_prompt(monkeypatch):
    """--retrieval bm25 is returned as-is; the wizard never prompts."""
    monkeypatch.setattr(installer, "prompt", _forbid_prompt())
    assert select_retrieval(non_interactive=True, cli_retrieval="bm25") == "bm25"


def test_cli_flag_invalid_value_exits_2(monkeypatch, capsys):
    """An invalid --retrieval value prints the error line and exits 2."""
    monkeypatch.setattr(installer, "prompt", _forbid_prompt())
    with pytest.raises(SystemExit) as exc:
        select_retrieval(non_interactive=True, cli_retrieval="hybrid")
    assert exc.value.code == 2
    assert "Must be 'vectors' or 'bm25'" in capsys.readouterr().out


def test_non_interactive_defaults_to_vectors(monkeypatch):
    """Non-interactive install without --retrieval defaults to vectors."""
    monkeypatch.setattr(installer, "prompt", _forbid_prompt())
    assert select_retrieval(non_interactive=True, cli_retrieval=None) == "vectors"


def test_interactive_prompt_defaults_to_prefill(monkeypatch):
    """Interactive: Enter keeps the cursor default — 'vectors' fresh, prefill on re-run.

    ``prompt`` is stubbed to return the ``default`` it was handed, so the test
    asserts both the returned mode and that the prefill reached ``prompt`` as
    the cursor default.
    """
    seen: dict = {}

    def fake_prompt(prompt_type, message, *, choices=None, default=None):
        seen["default"] = default
        return default

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installer, "prompt", fake_prompt)

    # Fresh install: no prefill -> vectors is the cursor default.
    assert select_retrieval(non_interactive=False, cli_retrieval=None) == "vectors"
    assert seen["default"] == "vectors"

    # Re-run: the prior mode from the existing YAML is the cursor default.
    selected = select_retrieval(non_interactive=False, cli_retrieval=None, prefill="bm25")
    assert selected == "bm25"
    assert seen["default"] == "bm25"


def test_interactive_note_discloses_sql_yaml_limit(monkeypatch, capsys):
    """The wizard Note pins the D8 disclosure verbatim.

    Spec D8 requires the sql/yaml limitation on all three surfaces (wizard
    note, docs, advisory), so the exact Note line is asserted here to keep
    them from drifting apart again.
    """
    monkeypatch.setattr(installer, "prompt", lambda *args, **kwargs: "vectors")

    select_retrieval(non_interactive=False, cli_retrieval=None)

    assert capsys.readouterr().out.splitlines() == [
        (
            "Note: 'vectors' needs an embedding model (auto-downloaded from Hugging "
            "Face, or a local path); 'bm25' is keyword search — no model, no "
            "downloads, works offline. In bm25 mode the sql/yaml tables are not "
            "searched (Java/Kotlin symbols only)."
        )
    ]


def test_interactive_prefill_invalid_value_falls_back_to_vectors(monkeypatch):
    """A hand-edited YAML value (``retrieval: BM25``) must not crash the wizard.

    questionary validates ``default`` against the choice values and raises
    ``ValueError`` on anything else, killing an interactive re-run with a raw
    traceback — so an invalid prefill is clamped to the recommended default.
    """
    seen: dict = {}

    def fake_prompt(prompt_type, message, *, choices=None, default=None):
        # Mirror questionary's select(): an unknown default raises before the
        # prompt is ever shown.
        if default not in [c["value"] for c in (choices or [])]:
            raise ValueError("Invalid 'default' value")
        seen["default"] = default
        return default

    monkeypatch.setattr(installer, "prompt", fake_prompt)

    selected = select_retrieval(non_interactive=False, cli_retrieval=None, prefill="BM25")

    assert selected == "vectors"
    assert seen["default"] == "vectors"


def test_retrieval_choices_vectors_first_and_recommended():
    """_retrieval_choices lists vectors first and marks it '(Recommended)'."""
    choices = installer._retrieval_choices()
    assert [c["value"] for c in choices] == ["vectors", "bm25"]
    assert choices[0]["name"] == "vectors (Recommended)"
    assert choices[1] == {"name": "bm25", "value": "bm25"}


# ---------------------------------------------------------------------------
# generate_yaml_config: retrieval persistence
# ---------------------------------------------------------------------------


def test_generate_yaml_config_bm25_writes_retrieval_key(tmp_path):
    """retrieval='bm25' persists an explicit `retrieval: bm25` line."""
    content = generate_yaml_config(tmp_path, "auto", None, None, retrieval="bm25")
    assert "retrieval: bm25" in content.splitlines()


def test_generate_yaml_config_vectors_writes_no_retrieval_key(tmp_path):
    """retrieval='vectors' (the default) writes no retrieval key."""
    content = generate_yaml_config(tmp_path, "auto", None, None, retrieval="vectors")
    assert not any(line.startswith("retrieval:") for line in content.splitlines())
    # And the default kwarg keeps the legacy call sites byte-identical.
    assert content == generate_yaml_config(tmp_path, "auto", None, None)


def test_generate_yaml_config_vectors_drops_stale_bm25_key(tmp_path):
    """Switching back to vectors on a re-run drops the stale key, keeps the rest.

    Update-mode re-runs pass the parsed existing YAML in, so mode switching
    must not leave a `retrieval: bm25` fossil behind.
    """
    existing = {"retrieval": "bm25", "cross_service_resolution": "brownfield_only"}
    content = generate_yaml_config(tmp_path, "auto", None, existing, retrieval="vectors")
    assert not any(line.startswith("retrieval:") for line in content.splitlines())
    assert "cross_service_resolution: brownfield_only" in content.splitlines()


# ---------------------------------------------------------------------------
# run_install Stage 2: retrieval resolution + model-stage skip
# ---------------------------------------------------------------------------


def _stub_install_stages(monkeypatch, *, vector_stack: bool) -> None:
    """Stub every heavy/interactive install stage; keep YAML generation real.

    run_install then exercises only the Stage-2 retrieval/model resolution and
    the ``generate_yaml_config`` call site — the written
    ``.java-codebase-rag.yml`` is the assertion target.
    """

    def _select_hosts(*, non_interactive, cli_agents):
        return [HOSTS["claude-code"]]

    def _select_surface(*, non_interactive, cli_surface, prefill=None):
        return "cli"

    monkeypatch.setattr(installer, "confirm_source_root", lambda cwd, *, non_interactive: cwd)
    monkeypatch.setattr(
        installer,
        "detect_java_layout",
        lambda root: JavaDetection(LAYOUT_SINGLE_MODULE, [Path(".")], []),
    )
    monkeypatch.setattr(installer, "select_hosts", _select_hosts)
    monkeypatch.setattr(installer, "select_scope", lambda *, non_interactive, cli_scope: "project")
    monkeypatch.setattr(installer, "select_surface", _select_surface)
    monkeypatch.setattr(
        installer,
        "resolve_mcp_command",
        lambda *, non_interactive, surface="mcp": "/fake/bin/jrag",
    )
    monkeypatch.setattr(installer, "deploy_artifacts", lambda *args, **kwargs: [])
    monkeypatch.setattr(installer, "_write_hosts_marker", lambda *args, **kwargs: None)
    monkeypatch.setattr(installer, "update_gitignore", lambda cwd: None)
    monkeypatch.setattr(installer, "run_init_if_needed", lambda *args, **kwargs: None)
    # run_install imports this lazily from the pipeline module at call time.
    monkeypatch.setattr("java_codebase_rag.pipeline.vector_stack_installed", lambda: vector_stack)


def _run_stubbed_install(tmp_path, **kwargs) -> int:
    defaults = {
        "non_interactive": True,
        "agents": ["claude-code"],
        "scope": "project",
        "model": None,
        "surface": "cli",
        "source_root": tmp_path,
    }
    defaults.update(kwargs)
    return run_install(**defaults)


def _written_yaml(tmp_path) -> list[str]:
    config_path = tmp_path / ".java-codebase-rag.yml"
    assert config_path.is_file(), f"install wrote no config at {config_path}"
    return config_path.read_text(encoding="utf-8").splitlines()


def _forbid_resolve_model():
    def _raise(*args, **kwargs):
        raise AssertionError("this path must not call resolve_model")

    return _raise


def test_run_install_bm25_skips_model_selection(tmp_path, monkeypatch):
    """retrieval='bm25' never reaches resolve_model; the YAML records bm25.

    Keyword search needs no embedding model, so the model question is inert —
    prompting for it (or resolving a path) would be wizard noise.
    """
    _stub_install_stages(monkeypatch, vector_stack=True)
    monkeypatch.setattr(installer, "resolve_model", _forbid_resolve_model())

    rc = _run_stubbed_install(tmp_path, retrieval="bm25")
    assert rc == 0
    assert "retrieval: bm25" in _written_yaml(tmp_path)


def test_run_install_graph_only_platform_forces_bm25(tmp_path, monkeypatch, capsys):
    """No vector stack (macOS Intel) forces bm25 even with no --retrieval flag.

    The existing graph-only skip message still prints (unchanged wording), and
    the forced bm25 lands in the YAML so later runs see the same mode.
    """
    _stub_install_stages(monkeypatch, vector_stack=False)
    monkeypatch.setattr(installer, "resolve_model", _forbid_resolve_model())

    rc = _run_stubbed_install(tmp_path, retrieval=None)
    assert rc == 0
    assert "retrieval: bm25" in _written_yaml(tmp_path)
    out = capsys.readouterr().out
    assert (
        "Skipping embedding model selection: vector stack not installed on this "
        "platform (graph-only mode)." in out
    ), "the existing graph-only skip message must keep printing verbatim"


def _capture_init_retrieval(monkeypatch) -> dict:
    """Stub ``run_init_if_needed`` so the test sees exactly what run_install
    threaded into the init sub-step's CLI tier."""
    seen: dict = {}

    def fake_init(source_root, index_dir, model, *, retrieval=None, **_kwargs):
        seen["retrieval"] = retrieval
        return None

    monkeypatch.setattr(installer, "run_init_if_needed", fake_init)
    return seen


def test_run_install_threads_effective_retrieval_into_init(tmp_path, monkeypatch):
    """The post-Stage-2 choice (not just the YAML write) reaches the init
    sub-step: ``jrag install --retrieval bm25`` must outrank an ambient
    ``JAVA_CODEBASE_RAG_RETRIEVAL=vectors`` inside init too, so the sub-step
    resolves bm25 (no cocoindex spawn) instead of following the env tier."""
    _stub_install_stages(monkeypatch, vector_stack=True)
    seen = _capture_init_retrieval(monkeypatch)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_RETRIEVAL", "vectors")

    rc = _run_stubbed_install(tmp_path, retrieval="bm25")
    assert rc == 0
    assert seen["retrieval"] == "bm25"


def test_run_install_threads_forced_graph_only_bm25_into_init(tmp_path, monkeypatch):
    """The graph-only force (no vector stack → bm25) also reaches init: the
    sub-step must resolve bm25 even though no ``--retrieval`` flag was given."""
    _stub_install_stages(monkeypatch, vector_stack=False)
    seen = _capture_init_retrieval(monkeypatch)
    monkeypatch.setenv("JAVA_CODEBASE_RAG_RETRIEVAL", "vectors")

    rc = _run_stubbed_install(tmp_path, retrieval=None)
    assert rc == 0
    assert seen["retrieval"] == "bm25"


def test_run_install_default_vectors_not_persisted(tmp_path, monkeypatch):
    """Non-interactive vectors default writes no retrieval key.

    ``vectors`` is the config-side default, so persisting it would only pin the
    YAML to today's default; the key appears only on an explicit bm25 choice.
    """
    _stub_install_stages(monkeypatch, vector_stack=True)
    monkeypatch.setattr(installer, "resolve_model", lambda model, *, non_interactive: "auto")

    rc = _run_stubbed_install(tmp_path, retrieval=None)
    assert rc == 0
    assert not any(line.startswith("retrieval:") for line in _written_yaml(tmp_path))


# ---------------------------------------------------------------------------
# --retrieval flag: registration + _cmd_install pass-through
# ---------------------------------------------------------------------------


def test_install_subparser_registers_retrieval_flag():
    """``--retrieval`` is registered on the install subparser.

    Default is ``None`` so the interactive wizard prompts when the flag is
    omitted; non-interactive installs fall back to ``'vectors'`` inside
    ``select_retrieval``.
    """
    from java_codebase_rag.cli import build_parser

    parser = build_parser()
    install_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    install_parser = install_action.choices["install"]
    retrieval_action = next(
        a for a in install_parser._actions if "--retrieval" in (a.option_strings or [])
    )
    assert retrieval_action.choices == ["vectors", "bm25"]
    assert retrieval_action.default is None
    assert retrieval_action.dest == "retrieval"


def test_cmd_install_passes_retrieval_through(monkeypatch):
    """_cmd_install forwards args.retrieval to run_install verbatim."""
    from java_codebase_rag.cli import _cmd_install

    seen: dict = {}

    def fake_run_install(**kwargs):
        seen.update(kwargs)
        return 0

    monkeypatch.setattr("java_codebase_rag.installer.run_install", fake_run_install)
    args = argparse.Namespace(
        non_interactive=True,
        agent=[],
        scope=None,
        model=None,
        surface=None,
        retrieval="bm25",
        quiet=False,
        verbose=False,
    )
    assert _cmd_install(args) == 0
    assert seen["retrieval"] == "bm25"
