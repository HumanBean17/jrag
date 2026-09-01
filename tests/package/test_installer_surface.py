"""``--surface mcp|cli`` install branching (PR-JRAG-5 + the prime-hook surfaces).

Validates the surface model end-to-end:
  - ``Surface`` Literal + ``ConfiguredHost`` NamedTuple (3-field)
  - ``ARTIFACT_MANIFEST`` single source iterated by ``deploy_artifacts``,
    ``refresh_artifacts`` and ``_undeploy_surface``: ``cli`` deploys a
    SessionStart prime hook (no files), ``mcp`` deploys the MCP entry only
    (tools only, no skill/agent artifacts)
  - ``.java-codebase-rag.hosts`` marker file round-trip (so a CLI-only install
    is visible to ``update`` — no MCP entry to scan)
  - ``detect_configured_hosts`` returns ``list[ConfiguredHost]`` (reads marker
    first, falls back to the MCP-entry scan with ``surface="mcp"`` for
    pre-marker installs)
  - ``run_update`` unpacks surface and routes the refresh through it, and a
    corrupt host settings.json fails that host's teardown without aborting the
    surface switch
  - ``resolve_mcp_command`` surface-conditional: ``cli`` resolves the ``jrag``
    console script and skips the MCP-binary ``SystemExit(2)``
  - ``select_surface`` wizard + ``--surface`` flag (help text agrees with the
    non-interactive default)
  - ``handle_rerun`` prefill behavior
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from java_codebase_rag.installer import (
    ARTIFACT_MANIFEST,
    ConfiguredHost,
    HOSTS,
    Surface,  # noqa: F401  — assert the Literal is exported
    _marker_path,
    _read_hosts_marker,
    _write_hosts_marker,
    deploy_artifacts,
    detect_configured_hosts,
    refresh_artifacts,
    resolve_mcp_command,
    run_update,
    select_surface,
)


# ---------------------------------------------------------------------------
# Test 1 + 2: deploy behavior per surface (hook / entry only)
# ---------------------------------------------------------------------------


def _forbid_package_artifacts(monkeypatch):
    """Fail any ``_read_package_artifact`` call.

    Neither surface ships files anymore, so a deploy that reaches for a package
    skill/agent is a bug — this makes it a loud failure instead of a silent
    file copy.
    """

    def _boom(relative_path):
        raise AssertionError(f"no surface ships package files (read {relative_path!r})")

    monkeypatch.setattr("java_codebase_rag.installer._read_package_artifact", _boom)


def _session_start_entries(config: dict) -> list[dict]:
    """Flatten a settings.json's SessionStart ""-matcher hook entries."""
    matchers = config.get("hooks", {}).get("SessionStart", [])
    catch_all = [m for m in matchers if isinstance(m, dict) and m.get("matcher") == ""]
    entries: list[dict] = []
    for matcher in catch_all:
        hooks = matcher.get("hooks", [])
        entries.extend(e for e in hooks if isinstance(e, dict))
    return entries


def test_cli_surface_deploys_hook_not_files(tmp_path, monkeypatch):
    """surface="cli" deploys a SessionStart prime hook and NO files at all.

    The CLI manifest is a single ``("hook", "", "")`` row: the only thing
    touched is the host settings.json. ``_read_package_artifact`` is stubbed to
    raise so an accidental skill/agent deployment fails the test loudly.
    """
    _forbid_package_artifacts(monkeypatch)

    for _ in range(2):  # second pass exercises the idempotent merge
        results = deploy_artifacts(
            [HOSTS["claude-code"]],
            "project",
            tmp_path,
            non_interactive=True,
            mcp_command="/fake/bin/jrag",
            surface="cli",
        )
        assert len(results) == 1
        assert all(r.success for r in results), (
            [str((r.path, r.success, r.error)) for r in results]
        )

    settings = tmp_path / ".claude" / "settings.json"
    assert settings.is_file(), f"SessionStart hook not deployed at {settings}"
    config = json.loads(settings.read_text())
    assert _session_start_entries(config) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]

    # No skill/agent files anywhere under the host scope path.
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "agents").exists()
    # And no MCP config registered by the CLI surface.
    assert not (tmp_path / ".mcp.json").exists()


def test_mcp_surface_deploys_entry_only(tmp_path, monkeypatch):
    """surface="mcp" deploys the MCP entry and nothing else (tools only)."""
    monkeypatch.setattr(
        shutil, "which", lambda name: "/fake/bin/java-codebase-rag-mcp"
    )
    _forbid_package_artifacts(monkeypatch)

    results = deploy_artifacts(
        [HOSTS["claude-code"]],
        "project",
        tmp_path,
        non_interactive=True,
        mcp_command="/fake/bin/java-codebase-rag-mcp",
        surface="mcp",
    )

    # One row per host: the MCP entry, no skill/agent artifacts.
    assert len(results) == 1
    assert all(r.success for r in results)
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert cfg["mcpServers"]["java-codebase-rag"] == {
        "command": "/fake/bin/java-codebase-rag-mcp",
        "type": "stdio",
    }
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "agents").exists()


# ---------------------------------------------------------------------------
# Test 3: marker file round-trips host/scope/surface
# ---------------------------------------------------------------------------


def test_marker_file_round_trips_host_scope_surface(tmp_path):
    """_write_hosts_marker → _read_hosts_marker round-trips ConfiguredHost set."""
    configured_in = [
        ConfiguredHost(HOSTS["claude-code"], "project", "mcp"),
        ConfiguredHost(HOSTS["qwen-code"], "user", "cli"),
    ]

    _write_hosts_marker(tmp_path, configured_in)

    # The marker file exists at the project root with the canonical name.
    assert _marker_path(tmp_path).is_file()

    configured_out = _read_hosts_marker(tmp_path)
    assert configured_out is not None, "marker file not parsed"
    assert len(configured_out) == 2

    # Round-trip preserves host/scope/surface in order.
    assert configured_out[0].host.name == "claude-code"
    assert configured_out[0].scope == "project"
    assert configured_out[0].surface == "mcp"
    assert configured_out[1].host.name == "qwen-code"
    assert configured_out[1].scope == "user"
    assert configured_out[1].surface == "cli"


# ---------------------------------------------------------------------------
# Test 4: detect_configured_hosts returns ConfiguredHost (3-field NamedTuple)
# ---------------------------------------------------------------------------


def test_detect_configured_hosts_returns_configured_host_namedtuple(tmp_path):
    """Marker-driven detection returns ConfiguredHost (3-field) instances.

    A CLI-only install writes a marker with surface="cli" and no MCP entry —
    detect_configured_hosts must surface it via the marker (the legacy
    MCP-entry scan would return [] here, leaving the install invisible to
    ``update``).
    """
    configured_in = [
        ConfiguredHost(HOSTS["claude-code"], "project", "cli"),
    ]
    _write_hosts_marker(tmp_path, configured_in)

    detected = detect_configured_hosts(tmp_path)
    assert len(detected) == 1
    ch = detected[0]
    # NamedTuple shape — 3 fields.
    assert isinstance(ch, ConfiguredHost)
    assert ch.host is HOSTS["claude-code"]
    assert ch.scope == "project"
    assert ch.surface == "cli"

    # Direct field access works (not tuple position only).
    assert ch.host.name == "claude-code"


# ---------------------------------------------------------------------------
# Test 5 + 6: run_update routes through surface; CLI install visible
# ---------------------------------------------------------------------------


def test_update_after_cli_only_install_refreshes_hook(tmp_path, monkeypatch):
    """CLI-only install (no MCP entry) is visible to ``update`` via the marker.

    Regression: before PR-JRAG-5, ``detect_configured_hosts`` only scanned MCP
    entries; a CLI-only install left no MCP entry, so ``run_update`` exited
    with the fatal "No configured agent hosts found." (exit 2). With the marker
    file + surface routing, update refreshes the SessionStart hook instead.
    """
    # Stage a CLI-only install state with a stale hook command.
    _write_hosts_marker(
        tmp_path,
        [ConfiguredHost(HOSTS["claude-code"], "project", "cli")],
    )
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path)
    # No fatal exit 2 ("No configured agent hosts found.").
    assert rc != 2, "CLI-only install must NOT be invisible to update (exit 2)"
    # Refresh rewrote the hook command in place.
    config = json.loads(settings.read_text())
    assert _session_start_entries(config) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]


def test_run_update_unpacks_surface_and_passes_to_refresh(tmp_path, monkeypatch):
    """run_update unpacks (host, scope, surface) and passes surface= to refresh.

    Captures the surface kwarg each refresh_artifacts call receives; the marker
    is the source of truth (so a marker carrying surface=cli routes through
    the CLI manifest).
    """
    _write_hosts_marker(
        tmp_path,
        [ConfiguredHost(HOSTS["claude-code"], "project", "cli")],
    )

    seen_surfaces: list[str] = []
    real_refresh = refresh_artifacts

    def spy_refresh(host, scope, cwd, *, force, dry_run, surface="mcp"):
        seen_surfaces.append(surface)
        return real_refresh(
            host, scope, cwd, force=force, dry_run=dry_run, surface=surface
        )

    monkeypatch.setattr("java_codebase_rag.installer.refresh_artifacts", spy_refresh)
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: "CONTENT",
    )
    monkeypatch.setattr(
        "java_codebase_rag.config.discover_project_root",
        lambda cwd: None,
    )

    rc = run_update(force=False, dry_run=True, cwd=tmp_path)
    assert rc in (0, 1)
    assert seen_surfaces == ["cli"], (
        f"run_update must pass surface='cli' to refresh; got {seen_surfaces}"
    )


# ---------------------------------------------------------------------------
# Test 7: resolve_mcp_command surface-conditional
# ---------------------------------------------------------------------------


def test_resolve_mcp_command_resolves_jrag_on_cli_surface(monkeypatch):
    """On surface='cli', resolve_mcp_command targets jrag (not the MCP binary).

    The CLI surface never raises SystemExit(2) for a missing MCP binary — the
    MCP binary is irrelevant when no MCP entry is registered.
    """
    seen_which_targets: list[str] = []

    def fake_which(name):
        seen_which_targets.append(name)
        if name == "jrag":
            return "/fake/bin/jrag"
        return None  # java-codebase-rag-mcp would NOT be found

    monkeypatch.setattr(shutil, "which", fake_which)

    resolved = resolve_mcp_command(non_interactive=True, surface="cli")
    assert resolved == "/fake/bin/jrag"
    assert "jrag" in seen_which_targets, "CLI surface must target jrag via which()"
    # The MCP binary is never queried on the CLI surface.
    assert "java-codebase-rag-mcp" not in seen_which_targets, (
        "CLI surface must not query for the MCP binary"
    )


def test_resolve_mcp_command_cli_surface_missing_jrag_exits_cleanly(monkeypatch, capsys):
    """Missing jrag on CLI surface + non-interactive → SystemExit(2) (clean).

    Surfaces the same exit code as the MCP path, but the message targets
    ``jrag`` and the user-facing hint mentions the console script.
    """
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SystemExit) as exc:
        resolve_mcp_command(non_interactive=True, surface="cli")
    assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "jrag" in out
    assert "java-codebase-rag-mcp" not in out


def test_resolve_mcp_command_mcp_surface_keeps_today_behavior(monkeypatch):
    """On surface='mcp', resolve_mcp_command reproduces today's behavior
    (targets java-codebase-rag-mcp)."""
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/local/bin/java-codebase-rag-mcp"
    )
    resolved = resolve_mcp_command(non_interactive=True, surface="mcp")
    assert resolved == "/usr/local/bin/java-codebase-rag-mcp"


# ---------------------------------------------------------------------------
# Test 8: deploy/refresh surface defaults to mcp for back-comat
# ---------------------------------------------------------------------------


def test_deploy_refresh_surface_defaults_to_mcp_back_compat(tmp_path, monkeypatch):
    """Existing direct-call sites in test_installer.py pass NO surface kwarg.

    Both deploy_artifacts and refresh_artifacts default to surface="mcp"
    (keyword-only) so those callers keep working unchanged. Asserts the default
    produces the MCP surface's artifact set: the entry only, no files.
    """
    monkeypatch.setattr(
        shutil, "which", lambda name: "/fake/bin/java-codebase-rag-mcp"
    )
    _forbid_package_artifacts(monkeypatch)

    # deploy_artifacts with NO surface kwarg.
    deploy_results = deploy_artifacts(
        [HOSTS["claude-code"]],
        "project",
        tmp_path,
        non_interactive=True,
        mcp_command="/fake/bin/java-codebase-rag-mcp",
    )
    # MCP surface = 1 result (the MCP entry).
    assert len(deploy_results) == 1
    assert all(r.success for r in deploy_results)
    assert "java-codebase-rag" in json.loads((tmp_path / ".mcp.json").read_text())[
        "mcpServers"
    ]

    # refresh_artifacts with NO surface kwarg.
    refresh_results = refresh_artifacts(
        HOSTS["claude-code"],
        "project",
        tmp_path,
        force=True,
        dry_run=False,
    )
    # MCP surface = 1 result (the MCP entry).
    assert len(refresh_results) == 1
    assert all(r.success for r in refresh_results)
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "agents").exists()


def test_refresh_updates_hook_command(tmp_path, monkeypatch):
    """Refresh on the cli surface rewrites a stale ``jrag`` path in place.

    The hook command is content-addressed by the resolved binary path, so a
    pip upgrade that moves the venv is repaired by ``update`` — one entry, not
    a growing list.
    """
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/bin/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: "/new/bin/jrag")
    _forbid_package_artifacts(monkeypatch)

    results = refresh_artifacts(
        HOSTS["claude-code"],
        "project",
        tmp_path,
        force=False,
        dry_run=False,
        surface="cli",
    )

    assert len(results) == 1
    assert all(r.success for r in results), (
        [str((r.path, r.success, r.error)) for r in results]
    )
    assert _session_start_entries(json.loads(settings.read_text())) == [
        {"type": "command", "command": "/new/bin/jrag prime --hook-json"}
    ]


# ---------------------------------------------------------------------------
# Test 9: handle_rerun pre-fills surface from marker
# ---------------------------------------------------------------------------


def test_handle_rerun_prefills_surface_from_marker(tmp_path, monkeypatch):
    """select_surface(prefill=...) returns the prior surface on default input.

    The wizard's re-run path reads the marker, extracts the prior surface, and
    passes it as ``prefill``. With non-interactive input (no --surface), the
    prefill is preserved.
    """
    _write_hosts_marker(
        tmp_path,
        [ConfiguredHost(HOSTS["qwen-code"], "user", "cli")],
    )

    # Read the prior surface exactly as run_install does.
    from java_codebase_rag.installer import _prior_surface_from_marker

    prior = _prior_surface_from_marker(tmp_path)
    assert prior == "cli"

    # select_surface with prefill + no CLI flag + non-interactive returns the
    # default behavior — but interactive with default (TTY off) preserves the
    # prefill as the default and returns it.
    selected = select_surface(
        non_interactive=False,
        cli_surface=None,
        prefill=prior,
    )
    # Non-TTY prompt returns the default; select_surface uses prefill as default.
    assert selected == "cli"


# ---------------------------------------------------------------------------
# Test 10: ARTIFACT_MANIFEST single source for deploy and refresh
# ---------------------------------------------------------------------------


def test_artifact_manifest_single_source_for_deploy_and_refresh():
    """ARTIFACT_MANIFEST is iterated by deploy, refresh AND undeploy.

    The invariant: adding/removing an artifact is ONE manifest edit, not three.
    Both surfaces are now file-free: ``cli`` ships a SessionStart prime hook,
    ``mcp`` ships the MCP entry only — no skill/agent rows anywhere.
    """
    # Documented shape.
    assert set(ARTIFACT_MANIFEST.keys()) == {"mcp", "cli"}

    # MCP surface = the MCP entry only (tools only, no skill/agent artifacts).
    assert ARTIFACT_MANIFEST["mcp"] == [("mcp", "", "")]

    # CLI surface = the SessionStart prime hook; no package file, no dest file.
    assert ARTIFACT_MANIFEST["cli"] == [("hook", "", "")]

    kinds = {kind for entries in ARTIFACT_MANIFEST.values() for kind, _, _ in entries}
    assert kinds == {"mcp", "hook"}, (
        f"no surface ships skill/agent files anymore, got kinds {sorted(kinds)}"
    )


# ---------------------------------------------------------------------------
# Bonus: --surface CLI flag registration (lightweight, parser-only)
# ---------------------------------------------------------------------------


def test_install_subparser_registers_surface_flag():
    """``--surface`` is registered on the install subparser.

    Default is ``None`` so the interactive ``select_surface`` wizard prompts
    when the flag is omitted (the proposal's CLI-vs-MCP choice); non-interactive
    installs fall back to ``'cli'`` inside ``select_surface`` (the recommended
    default).
    """
    import argparse

    from java_codebase_rag.cli import build_parser  # operator CLI

    parser = build_parser()
    # Reach into argparse internals to find the install subparser's surface opt.
    install_action = next(
        a
        for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    )
    install_parser = install_action.choices["install"]
    surface_action = next(
        a for a in install_parser._actions if "--surface" in (a.option_strings or [])
    )
    assert surface_action.choices == ["mcp", "cli"]
    assert surface_action.default is None
    assert surface_action.dest == "surface"


# ---------------------------------------------------------------------------
# cli is the recommended surface: choice order/label + default flip
# ---------------------------------------------------------------------------


def test_surface_choices_cli_first_and_recommended():
    """_surface_choices lists cli first and marks it '(Recommended)'."""
    from java_codebase_rag.installer import _surface_choices

    choices = _surface_choices()
    assert [c["value"] for c in choices] == ["cli", "mcp"]
    assert "Recommended" in choices[0]["name"]
    assert choices[1]["value"] == "mcp"


def test_select_surface_non_interactive_defaults_to_cli():
    """Non-interactive install without --surface now defaults to cli."""
    assert select_surface(non_interactive=True, cli_surface=None) == "cli"


def test_select_surface_prefill_is_preserved_non_tty():
    """Re-run prefill is honored (cursor/default = prefill) on non-TTY.

    cli is still the first/recommended choice, but the default returns the
    prior surface so a re-run preserves it.
    """
    assert select_surface(non_interactive=False, cli_surface=None, prefill="mcp") == "mcp"
    assert select_surface(non_interactive=False, cli_surface=None, prefill="cli") == "cli"


def test_prompt_select_forwards_default_and_normalizes_dict_choices(monkeypatch):
    """prompt('select') forwards default and normalizes dict choices to Choice.

    questionary.select validates default only against Choice.value (not dict
    values), so dict choices must be normalized or default raises. Verified on a
    faked TTY (prompt returns default without calling questionary when non-TTY).
    """
    import sys

    import questionary

    from java_codebase_rag.installer import prompt

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    seen: dict = {}

    class _FakeQuestion:
        def __init__(self, message, choices=None, default=None, style=None, **kw):
            seen["choices"] = choices
            seen["default"] = default

        def ask(self):
            return "mcp"

    monkeypatch.setattr(questionary, "select", _FakeQuestion)

    result = prompt(
        "select",
        "pick",
        choices=[{"name": "cli (Recommended)", "value": "cli"}, {"name": "mcp", "value": "mcp"}],
        default="mcp",
    )
    assert result == "mcp"
    assert seen["default"] == "mcp"
    norm = seen["choices"]
    assert all(isinstance(c, questionary.Choice) for c in norm)
    assert [c.value for c in norm] == ["cli", "mcp"]
    assert norm[0].title == "cli (Recommended)"


# ---------------------------------------------------------------------------
# update --surface: mcp <-> cli migration
# ---------------------------------------------------------------------------


def _stub_update_index_skip(monkeypatch):
    """Stub the index-discovery + pipeline so run_update stops before indexing."""
    monkeypatch.setattr(
        "java_codebase_rag.config.discover_project_root", lambda cwd: None
    )


def test_update_migrates_mcp_to_cli(tmp_path, monkeypatch):
    """run_update(surface='cli') on an mcp install migrates: tears down the MCP
    entry, deploys the hook, rewrites the marker. Sibling MCP servers survive."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "mcp")])

    # Existing mcp-surface state: .mcp.json (us + a sibling server).
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "/other"},
                    "java-codebase-rag": {"command": "/fake/bin/java-codebase-rag-mcp", "type": "stdio"},
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="cli")
    assert rc == 0

    # mcp entry removed, sibling preserved.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" not in cfg["mcpServers"]
    assert "other" in cfg["mcpServers"]

    # The cli surface deployed its one artifact: the SessionStart hook.
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert _session_start_entries(settings) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]

    # Marker rewritten to cli.
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "cli"


def test_update_migrates_cli_to_mcp(tmp_path, monkeypatch):
    """run_update(surface='mcp') on a cli install migrates the other way."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")])

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="mcp")
    assert rc == 0

    # Hook torn down (the whole hooks tree was ours alone).
    assert "hooks" not in json.loads(settings.read_text())
    # mcp entry deployed.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert cfg["mcpServers"]["java-codebase-rag"]["command"] == (
        "/fake/bin/java-codebase-rag-mcp"
    )

    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "mcp"


def test_update_surface_missing_target_binary_returns_partial(tmp_path, monkeypatch):
    """Migrating to mcp when java-codebase-rag-mcp is absent -> exit 1, no migration."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")])
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: None)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="mcp")
    assert rc == 1
    # Nothing torn down / deployed.
    assert "hooks" in json.loads(settings.read_text())
    assert not (tmp_path / ".mcp.json").is_file()
    # Marker unchanged.
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "cli"


def test_update_surface_same_as_current_does_not_migrate(tmp_path, monkeypatch):
    """run_update(surface=<current>) takes the refresh path; no teardown/marker write."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "mcp")])
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/stale", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    called = {"undeploy": False}
    real_undeploy = installer._undeploy_surface

    def spy(host, scope, cwd, *, surface, dry_run):
        called["undeploy"] = True
        return real_undeploy(host, scope, cwd, surface=surface, dry_run=dry_run)

    monkeypatch.setattr("java_codebase_rag.installer._undeploy_surface", spy)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="mcp")
    assert rc == 0
    assert called["undeploy"] is False, "same-surface update must not tear down"
    # Refresh did run: the entry's command path was brought up to date.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert cfg["mcpServers"]["java-codebase-rag"]["command"] == (
        "/fake/bin/java-codebase-rag-mcp"
    )
    # Marker still mcp.
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "mcp"


def test_update_surface_dry_run_writes_nothing(tmp_path, monkeypatch):
    """run_update(surface='cli', dry_run=True) prints intent, writes no files/marker."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "mcp")])
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/x", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=True, cwd=tmp_path, surface="cli")
    # Dry run performs no writes, so there can be no partial failures.
    assert rc == 0
    # Nothing changed on disk.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" in cfg["mcpServers"]
    assert not (tmp_path / ".claude" / "settings.json").exists()
    # Marker still mcp (not rewritten on dry-run).
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "mcp"


def test_remove_mcp_entry_preserves_sibling_servers(tmp_path):
    """_remove_mcp_entry pops only our key; other servers + file survive."""
    import json

    from java_codebase_rag.installer import _remove_mcp_entry

    config_path = tmp_path / ".mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "numStartups": 42,
                "mcpServers": {
                    "other": {"command": "/other"},
                    "java-codebase-rag": {"command": "/x", "type": "stdio"},
                },
            }
        ),
        encoding="utf-8",
    )

    result = _remove_mcp_entry(config_path, dry_run=False)
    assert result.success
    cfg = json.loads(config_path.read_text())
    assert "java-codebase-rag" not in cfg["mcpServers"]
    assert "other" in cfg["mcpServers"]
    assert cfg["numStartups"] == 42


def test_undeploy_hook_corrupt_settings_json_is_non_fatal(tmp_path, monkeypatch, capsys):
    """A corrupt host settings.json fails THAT host's hook teardown only.

    ``_remove_session_start_hook`` raises ``ValueError`` on unparseable JSON
    (Task 6 contract: fail loudly, never silently replace). The undeploy path
    must convert that into a failed ``ArtifactResult`` so the surface switch
    keeps going: the healthy host still migrates, the run reports the broken
    file as a warning, and the exit code is partial (1) — not a crash.
    """
    import java_codebase_rag.installer as installer

    _write_hosts_marker(
        tmp_path,
        [
            ConfiguredHost(HOSTS["claude-code"], "project", "cli"),
            ConfiguredHost(HOSTS["qwen-code"], "project", "cli"),
        ],
    )
    # claude-code: corrupt settings.json — teardown must fail soft.
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text("{ not json", encoding="utf-8")
    # qwen-code: healthy hook — its teardown must still run.
    qwen_settings = tmp_path / ".qwen" / "settings.json"
    qwen_settings.parent.mkdir(parents=True)
    qwen_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="mcp")
    assert rc == 1, "a corrupt host settings file must surface as a partial failure"

    # The corrupt file is reported, and left for the operator to fix.
    out = capsys.readouterr().out
    assert "settings.json" in out
    assert (tmp_path / ".claude" / "settings.json").read_text() == "{ not json"

    # The healthy host's teardown ran despite the failure.
    qwen_cfg = json.loads(qwen_settings.read_text())
    assert "hooks" not in qwen_cfg
    # And both hosts still got the MCP entry they were migrating to.
    assert "java-codebase-rag" in json.loads((tmp_path / ".mcp.json").read_text())[
        "mcpServers"
    ]
    assert "java-codebase-rag" in qwen_cfg["mcpServers"]
    # Marker records the switch.
    detected = installer._read_hosts_marker(tmp_path)
    assert [d.surface for d in detected] == ["mcp", "mcp"]


# ---------------------------------------------------------------------------
# mixed-surface markers + user scope (per-host dispatch)
# ---------------------------------------------------------------------------


def test_update_no_flag_non_tty_mixed_marker_does_not_migrate(tmp_path, monkeypatch):
    """Non-TTY update with NO --surface refreshes each host on its OWN surface.

    Regression: an earlier version returned the first host's surface as the
    global target, so a mixed marker like [claude-code/mcp, qwen-code/cli] would
    migrate qwen-code to mcp. The non-TTY no-flag path must migrate nothing.
    """
    import java_codebase_rag.installer as installer

    _write_hosts_marker(
        tmp_path,
        [
            ConfiguredHost(HOSTS["claude-code"], "project", "mcp"),
            ConfiguredHost(HOSTS["qwen-code"], "project", "cli"),
        ],
    )
    # claude-code (mcp) has a stale MCP entry command.
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/stale", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )
    # qwen-code (cli) has a stale hook command.
    qwen_settings = tmp_path / ".qwen" / "settings.json"
    qwen_settings.parent.mkdir(parents=True)
    qwen_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    # _undeploy_surface must NOT be called (no migration on this path).
    called = {"undeploy": False}
    real_undeploy = installer._undeploy_surface

    def spy(host, scope, cwd, *, surface, dry_run):
        called["undeploy"] = True
        return real_undeploy(host, scope, cwd, surface=surface, dry_run=dry_run)

    monkeypatch.setattr("java_codebase_rag.installer._undeploy_surface", spy)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path)  # no surface, non-TTY
    assert rc == 0
    assert called["undeploy"] is False, "non-TTY no-flag update must not migrate"
    # Each host refreshed on its OWN surface: stale commands brought up to date.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert cfg["mcpServers"]["java-codebase-rag"]["command"] == (
        "/fake/bin/java-codebase-rag-mcp"
    )
    assert _session_start_entries(json.loads(qwen_settings.read_text())) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]
    # Marker surfaces unchanged.
    detected = installer._read_hosts_marker(tmp_path)
    assert [d.surface for d in detected] == ["mcp", "cli"]


def test_update_surface_normalizes_mixed_marker(tmp_path, monkeypatch):
    """--surface normalizes a mixed-surface marker: every host migrates to it."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(
        tmp_path,
        [
            ConfiguredHost(HOSTS["claude-code"], "project", "mcp"),
            ConfiguredHost(HOSTS["qwen-code"], "project", "cli"),
        ],
    )
    # claude-code (mcp) has an MCP entry to tear down.
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/x", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )
    # qwen-code (cli) already has the hook (one file holds both surfaces).
    qwen_settings = tmp_path / ".qwen" / "settings.json"
    qwen_settings.parent.mkdir(parents=True)
    qwen_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [
                                {"type": "command", "command": "/old/jrag prime --hook-json"}
                            ],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="cli")
    assert rc == 0

    # claude-code migrated mcp -> cli: entry gone, hook deployed.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" not in cfg.get("mcpServers", {})
    claude_settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert _session_start_entries(claude_settings) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]
    # qwen-code was already cli -> hook refreshed in place.
    assert _session_start_entries(json.loads(qwen_settings.read_text())) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]
    # Marker normalized: both cli.
    detected = installer._read_hosts_marker(tmp_path)
    assert [d.surface for d in detected] == ["cli", "cli"]


def test_update_migrates_user_scope_host(tmp_path, monkeypatch):
    """Migration is scope-agnostic: a user-scope host migrates too.

    User-scope paths resolve under ``Path.home()``; home is redirected to
    ``tmp_path`` to keep the test hermetic.
    """
    import java_codebase_rag.installer as installer

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "user", "mcp")])

    # User-scope MCP config for claude-code lives at ~/.claude.json (== tmp_path).
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/x", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _forbid_package_artifacts(monkeypatch)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="cli")
    assert rc == 0

    # User-scope MCP entry removed; user-scope hook deployed at ~/.claude/settings.json.
    cfg = json.loads((tmp_path / ".claude.json").read_text())
    assert "java-codebase-rag" not in cfg.get("mcpServers", {})
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert _session_start_entries(settings) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]
    detected = installer._read_hosts_marker(tmp_path)
    assert detected[0].scope == "user" and detected[0].surface == "cli"


# ---------------------------------------------------------------------------
# run_install end-to-end on the cli surface + help/behavior agreement
# ---------------------------------------------------------------------------


def _stub_install_pipeline(monkeypatch):
    """Stub the indexing helpers so run_install's init step is a no-op success."""
    import subprocess

    def _ok(*args, **kwargs):
        return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", _ok)
    monkeypatch.setattr("java_codebase_rag.pipeline.run_build_ast_graph", _ok)


def test_install_cli_end_to_end(tmp_path, monkeypatch):
    """run_install --surface cli (non-interactive): marker says cli, hook
    installed, no files, exit 0."""
    from java_codebase_rag.installer import run_install

    # Minimal single-module Java repo so detect_java_layout -> single_module.
    src = tmp_path / "src" / "main" / "java" / "com" / "acme"
    src.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project></project>", encoding="utf-8")
    (src / "App.java").write_text("package com.acme; class App {}", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")  # hermetic user scope
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    _forbid_package_artifacts(monkeypatch)
    _stub_install_pipeline(monkeypatch)
    monkeypatch.chdir(tmp_path)

    rc = run_install(
        non_interactive=True,
        agents=["claude-code"],
        scope="project",
        model="auto",
        surface="cli",
        source_root=tmp_path,
        quiet=True,
    )
    assert rc == 0

    # Marker records the surface so a later `update` routes through cli.
    detected = _read_hosts_marker(tmp_path)
    assert detected is not None
    assert [(d.host.name, d.surface) for d in detected] == [("claude-code", "cli")]

    # The one deployed artifact: the SessionStart prime hook.
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert _session_start_entries(settings) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]

    # No skill/agent files, no MCP entry.
    assert not (tmp_path / ".claude" / "skills").exists()
    assert not (tmp_path / ".claude" / "agents").exists()
    assert not (tmp_path / ".mcp.json").exists()


def test_install_hook_write_failure_is_critical(tmp_path, monkeypatch):
    """A hook deploy failure is a critical (.json) failure: run_install exits 1.

    The severity gate treats ``.json`` writes as critical — a broken host
    settings file means the hook never fires, so the install must not report
    success in CI/automation. Same rule the MCP config already follows.
    """
    from java_codebase_rag.installer import run_install

    src = tmp_path / "src" / "main" / "java" / "com" / "acme"
    src.mkdir(parents=True)
    (tmp_path / "pom.xml").write_text("<project></project>", encoding="utf-8")
    (src / "App.java").write_text("package com.acme; class App {}", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    monkeypatch.setattr(
        "java_codebase_rag.installer._is_writable",
        lambda path: Path(path).name != ".claude",
    )
    _stub_install_pipeline(monkeypatch)
    monkeypatch.chdir(tmp_path)

    rc = run_install(
        non_interactive=True,
        agents=["claude-code"],
        scope="project",
        model="auto",
        surface="cli",
        source_root=tmp_path,
        quiet=True,
    )
    assert rc == 1, "a failed hook write (.json) must be a critical install failure"


def test_surface_help_matches_default():
    """The install --surface help names the surface select_surface defaults to.

    Regression: the help claimed "non-interactive mode defaults to 'mcp'" while
    ``select_surface`` returns ``"cli"`` — help text and behavior must agree.
    """
    import argparse
    import re

    from java_codebase_rag.cli import build_parser

    parser = build_parser()
    subparsers = next(
        a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
    )
    rendered = subparsers.choices["install"].format_help()
    # Undo argparse's wrapping: rejoin words it split on an embedded hyphen
    # ("non-\ninteractive"), then collapse the remaining line breaks.
    help_text = " ".join(re.sub(r"(\w)-\s*\n\s*(\w)", r"\1-\2", rendered).split())

    default = select_surface(non_interactive=True, cli_surface=None)
    assert f"non-interactive mode defaults to '{default}'" in help_text, (
        f"--surface help must name the actual non-interactive default '{default}'"
    )
    # Both surfaces are described as file-free.
    assert "no files deployed" in help_text
    assert "no skill/agent artifacts" in help_text
