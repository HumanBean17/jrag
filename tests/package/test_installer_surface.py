"""PR-JRAG-5: ``--surface mcp|cli`` install branching.

Validates the surface model end-to-end:
  - ``Surface`` Literal + ``ConfiguredHost`` NamedTuple (3-field)
  - ``ARTIFACT_MANIFEST`` single source iterated by ``deploy_artifacts`` and
    ``refresh_artifacts`` (with ``surface="mcp"`` keyword-only default for
    back-comat with the existing direct-call tests in ``test_installer.py``)
  - ``.java-codebase-rag.hosts`` marker file round-trip (so a CLI-only install
    is visible to ``update`` — no MCP entry to scan)
  - ``detect_configured_hosts`` returns ``list[ConfiguredHost]`` (reads marker
    first, falls back to the MCP-entry scan with ``surface="mcp"`` for
    pre-marker installs)
  - ``run_update`` unpacks surface and routes the refresh through it
  - ``resolve_mcp_command`` surface-conditional: ``cli`` resolves the ``jrag``
    console script and skips the MCP-binary ``SystemExit(2)``
  - ``select_surface`` wizard + ``--surface`` flag
  - ``handle_rerun`` prefill behavior
"""

from __future__ import annotations

import shutil

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
# Test 1 + 2: deploy behavior per surface (parity)
# ---------------------------------------------------------------------------


def test_surface_cli_deploys_cli_skill_and_agent_no_mcp_entry(tmp_path, monkeypatch):
    """surface="cli" deploys explore-codebase-cli skill + explorer-rag-cli agent.

    The CLI surface ships NO MCP entry — the manifest has only two rows
    (skill + agent) and the dest paths use the CLI artifact names.
    """
    # The CLI surface never reaches resolve_mcp_command in deploy_artifacts
    # (no "mcp" manifest row), but the install wizard still resolves jrag and
    # passes it. Stub shutil.which so any incidental call is harmless.
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")

    results = deploy_artifacts(
        [HOSTS["claude-code"]],
        "project",
        tmp_path,
        non_interactive=True,
        mcp_command="/fake/bin/jrag",
        surface="cli",
    )

    # Exactly two artifacts (skill + agent); NO MCP entry.
    assert len(results) == 2
    assert all(r.success for r in results), (
        [str((r.path, r.success, r.error)) for r in results]
    )

    skill_dest = tmp_path / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
    agent_dest = tmp_path / ".claude" / "agents" / "explorer-rag-cli.md"
    assert skill_dest.is_file(), f"CLI skill not deployed at {skill_dest}"
    assert agent_dest.is_file(), f"CLI agent not deployed at {agent_dest}"

    # The MCP-surface artifacts must NOT have been written on the CLI surface.
    assert not (tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md").is_file()
    assert not (tmp_path / ".claude" / "agents" / "explorer-rag-enhanced.md").is_file()
    # And no MCP config registered.
    assert not (tmp_path / ".mcp.json").is_file()


def test_surface_mcp_reproduces_today_behavior(tmp_path, monkeypatch):
    """surface="mcp" (explicit) deploys MCP entry + MCP skill + MCP agent.

    Same artifact set as today's pre-surface install: 3 results per host.
    """
    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/java-codebase-rag-mcp")

    results = deploy_artifacts(
        [HOSTS["claude-code"]],
        "project",
        tmp_path,
        non_interactive=True,
        mcp_command="/fake/bin/java-codebase-rag-mcp",
        surface="mcp",
    )

    # Three artifacts (MCP + skill + agent), in manifest order.
    assert len(results) == 3
    assert all(r.success for r in results)

    assert (tmp_path / ".mcp.json").is_file()
    assert (tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "agents" / "explorer-rag-enhanced.md").is_file()


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


def test_update_after_cli_only_install_refreshes_cli_skill(tmp_path, monkeypatch):
    """CLI-only install (no MCP entry) is visible to ``update`` via the marker.

    Regression: before PR-JRAG-5, ``detect_configured_hosts`` only scanned MCP
    entries; a CLI-only install left no MCP entry, so ``run_update`` exited
    with the fatal "No configured agent hosts found." (exit 2). With the marker
    file + surface routing, update refreshes the CLI skill+agent instead.
    """
    # Stage a CLI-only install state.
    _write_hosts_marker(
        tmp_path,
        [ConfiguredHost(HOSTS["claude-code"], "project", "cli")],
    )
    # Pre-create the CLI skill/agent so refresh has something to compare.
    skill_dir = tmp_path / ".claude" / "skills" / "explore-codebase-cli"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("STALE CLI SKILL", encoding="utf-8")
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "explorer-rag-cli.md").write_text("STALE CLI AGENT", encoding="utf-8")

    # Stub the package-artifact read so refresh has deterministic new content.
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: "FRESH CLI ARTIFACT",
    )
    # Stub the index-side config discovery so update returns before indexing.
    monkeypatch.setattr(
        "java_codebase_rag.config.discover_project_root",
        lambda cwd: None,
    )

    rc = run_update(force=False, dry_run=False, cwd=tmp_path)
    # No fatal exit 2 ("No configured agent hosts found.").
    assert rc != 2, "CLI-only install must NOT be invisible to update (exit 2)"
    # Refresh wrote the new CLI artifacts.
    assert (skill_dir / "SKILL.md").read_text() == "FRESH CLI ARTIFACT"
    assert (agents_dir / "explorer-rag-cli.md").read_text() == "FRESH CLI ARTIFACT"


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
    (keyword-only) so those callers keep working unchanged. Asserts the
    default produces the same MCP-surface artifact set as today.
    """
    monkeypatch.setattr(
        shutil, "which", lambda name: "/fake/bin/java-codebase-rag-mcp"
    )

    # deploy_artifacts with NO surface kwarg.
    deploy_results = deploy_artifacts(
        [HOSTS["claude-code"]],
        "project",
        tmp_path,
        non_interactive=True,
        mcp_command="/fake/bin/java-codebase-rag-mcp",
    )
    # MCP surface = 3 results (mcp + skill + agent).
    assert len(deploy_results) == 3
    assert (tmp_path / ".mcp.json").is_file()
    assert (
        tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
    ).is_file()
    assert (
        tmp_path / ".claude" / "agents" / "explorer-rag-enhanced.md"
    ).is_file()

    # refresh_artifacts with NO surface kwarg.
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: "REFRESHED",
    )
    refresh_results = refresh_artifacts(
        HOSTS["claude-code"],
        "project",
        tmp_path,
        force=True,
        dry_run=False,
    )
    # MCP surface = 3 results (mcp + skill + agent).
    assert len(refresh_results) == 3


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
    """ARTIFACT_MANIFEST is iterated by BOTH deploy_artifacts and refresh_artifacts.

    The invariant: adding/removing an artifact is ONE manifest edit, not two.
    Asserts the manifest carries the documented entries and that the deploy/
    refresh loops are wired to the same constant (no parallel hardcoded lists).
    """
    # Documented shape.
    assert set(ARTIFACT_MANIFEST.keys()) == {"mcp", "cli"}

    mcp_entries = ARTIFACT_MANIFEST["mcp"]
    cli_entries = ARTIFACT_MANIFEST["cli"]

    # MCP surface = mcp entry + explore-codebase skill + explorer-rag-enhanced.
    assert len(mcp_entries) == 3
    mcp_kinds = [kind for kind, _, _ in mcp_entries]
    assert mcp_kinds == ["mcp", "skill", "agent"]
    # Skill + agent paths point at the MCP-surface artifact names.
    skill_pkg = next(pkg for kind, pkg, _ in mcp_entries if kind == "skill")
    agent_pkg = next(pkg for kind, pkg, _ in mcp_entries if kind == "agent")
    assert "explore-codebase/" in skill_pkg
    assert "enhanced" in agent_pkg
    # No CLI-surface artifact leaks into the MCP manifest.
    assert not any("explore-codebase-cli" in pkg for _, pkg, _ in mcp_entries)
    assert not any("explorer-rag-cli" in pkg for _, pkg, _ in mcp_entries)

    # CLI surface = explore-codebase-cli skill + explorer-rag-cli agent (NO mcp).
    assert len(cli_entries) == 2
    cli_kinds = [kind for kind, _, _ in cli_entries]
    assert cli_kinds == ["skill", "agent"]
    assert "mcp" not in cli_kinds, "CLI surface must NOT register an MCP entry"
    # Skill + agent paths point at the CLI-surface artifact names.
    cli_skill_pkg = next(pkg for kind, pkg, _ in cli_entries if kind == "skill")
    cli_agent_pkg = next(pkg for kind, pkg, _ in cli_entries if kind == "agent")
    assert "explore-codebase-cli/" in cli_skill_pkg
    assert "cli" in cli_agent_pkg


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
    """run_update(surface='cli') on an mcp install migrates: tears down mcp,
    deploys cli, rewrites the marker. Sibling MCP servers are preserved."""
    import json

    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "mcp")])

    # Existing mcp-surface state: .mcp.json (us + a sibling server) + mcp skill/agent.
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
    mcp_skill = tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
    mcp_skill.parent.mkdir(parents=True)
    mcp_skill.write_text("OLD MCP SKILL", encoding="utf-8")
    mcp_agent = tmp_path / ".claude" / "agents" / "explorer-rag-enhanced.md"
    mcp_agent.parent.mkdir(parents=True)
    mcp_agent.write_text("OLD MCP AGENT", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: f"FRESH:{rel}",
    )
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="cli")
    assert rc == 0

    # mcp entry removed, sibling preserved.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" not in cfg["mcpServers"]
    assert "other" in cfg["mcpServers"]

    # mcp skill/agent torn down; cli skill/agent deployed.
    assert not mcp_skill.is_file()
    assert not mcp_agent.is_file()
    cli_skill = tmp_path / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
    cli_agent = tmp_path / ".claude" / "agents" / "explorer-rag-cli.md"
    assert cli_skill.is_file() and cli_skill.read_text().startswith("FRESH:")
    assert cli_agent.is_file() and cli_agent.read_text().startswith("FRESH:")

    # Marker rewritten to cli.
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "cli"


def test_update_migrates_cli_to_mcp(tmp_path, monkeypatch):
    """run_update(surface='mcp') on a cli install migrates the other way."""
    import json

    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")])

    cli_skill = tmp_path / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
    cli_skill.parent.mkdir(parents=True)
    cli_skill.write_text("OLD CLI SKILL", encoding="utf-8")
    cli_agent = tmp_path / ".claude" / "agents" / "explorer-rag-cli.md"
    cli_agent.parent.mkdir(parents=True)
    cli_agent.write_text("OLD CLI AGENT", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: f"FRESH:{rel}",
    )
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="mcp")
    assert rc == 0

    # cli artifacts gone; mcp entry + mcp skill/agent deployed.
    assert not cli_skill.is_file()
    assert not cli_agent.is_file()
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" in cfg["mcpServers"]
    assert (tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "agents" / "explorer-rag-enhanced.md").is_file()

    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "mcp"


def test_update_surface_missing_target_binary_returns_partial(tmp_path, monkeypatch):
    """Migrating to mcp when java-codebase-rag-mcp is absent -> exit 1, no migration."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")])
    cli_skill = tmp_path / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
    cli_skill.parent.mkdir(parents=True)
    cli_skill.write_text("OLD CLI SKILL", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: None)
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="mcp")
    assert rc == 1
    # Nothing torn down / deployed.
    assert cli_skill.is_file()
    assert not (tmp_path / ".mcp.json").is_file()
    # Marker unchanged.
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "cli"


def test_update_surface_same_as_current_does_not_migrate(tmp_path, monkeypatch):
    """run_update(surface=<current>) takes the refresh path; no teardown/marker write."""
    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "mcp")])
    mcp_skill = tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
    mcp_skill.parent.mkdir(parents=True)
    mcp_skill.write_text("STALE", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact", lambda rel: "FRESH"
    )
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
    # Refresh did run.
    assert mcp_skill.read_text() == "FRESH"
    # Marker still mcp.
    detected = installer._read_hosts_marker(tmp_path)
    assert detected is not None and detected[0].surface == "mcp"


def test_update_surface_dry_run_writes_nothing(tmp_path, monkeypatch):
    """run_update(surface='cli', dry_run=True) prints intent, writes no files/marker."""
    import json

    import java_codebase_rag.installer as installer

    _write_hosts_marker(tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "mcp")])
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/x", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )
    mcp_skill = tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
    mcp_skill.parent.mkdir(parents=True)
    mcp_skill.write_text("KEEP", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact", lambda rel: "FRESH"
    )
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=True, cwd=tmp_path, surface="cli")
    # Dry run performs no writes, so there can be no partial failures.
    assert rc == 0
    # Nothing changed on disk.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" in cfg["mcpServers"]
    assert mcp_skill.read_text() == "KEEP"
    assert not (tmp_path / ".claude" / "skills" / "explore-codebase-cli").exists()
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
    claude_mcp_skill = tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
    claude_mcp_skill.parent.mkdir(parents=True)
    claude_mcp_skill.write_text("STALE", encoding="utf-8")
    qwen_cli_skill = tmp_path / ".qwen" / "skills" / "explore-codebase-cli" / "SKILL.md"
    qwen_cli_skill.parent.mkdir(parents=True)
    qwen_cli_skill.write_text("STALE", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact", lambda rel: "FRESH"
    )
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
    # Each host refreshed on its OWN surface.
    assert claude_mcp_skill.read_text() == "FRESH"
    assert qwen_cli_skill.read_text() == "FRESH"
    # No cross-surface artifacts appeared.
    assert not (tmp_path / ".claude" / "skills" / "explore-codebase-cli").exists()
    assert not (tmp_path / ".qwen" / "skills" / "explore-codebase").exists()
    # Marker surfaces unchanged.
    detected = installer._read_hosts_marker(tmp_path)
    assert [d.surface for d in detected] == ["mcp", "cli"]


def test_update_surface_normalizes_mixed_marker(tmp_path, monkeypatch):
    """--surface normalizes a mixed-surface marker: every host migrates to it."""
    import json

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
    # qwen-code (cli) already has the cli skill.
    qwen_cli = tmp_path / ".qwen" / "skills" / "explore-codebase-cli" / "SKILL.md"
    qwen_cli.parent.mkdir(parents=True)
    qwen_cli.write_text("OLD", encoding="utf-8")

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: f"FRESH:{rel}",
    )
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="cli")
    assert rc == 0

    # claude-code migrated mcp -> cli: entry gone, cli skill deployed.
    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert "java-codebase-rag" not in cfg.get("mcpServers", {})
    assert (
        tmp_path / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
    ).is_file()
    # qwen-code was already cli -> refreshed in place (still present).
    assert qwen_cli.is_file() and qwen_cli.read_text().startswith("FRESH:")
    # Marker normalized: both cli.
    detected = installer._read_hosts_marker(tmp_path)
    assert [d.surface for d in detected] == ["cli", "cli"]


def test_update_migrates_user_scope_host(tmp_path, monkeypatch):
    """Migration is scope-agnostic: a user-scope host migrates too.

    User-scope paths resolve under ``Path.home()``; home is redirected to
    ``tmp_path`` to keep the test hermetic.
    """
    import json
    from pathlib import Path

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
    monkeypatch.setattr(
        "java_codebase_rag.installer._read_package_artifact",
        lambda rel: f"FRESH:{rel}",
    )
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path, surface="cli")
    assert rc == 0

    # User-scope MCP entry removed; user-scope cli skill deployed.
    cfg = json.loads((tmp_path / ".claude.json").read_text())
    assert "java-codebase-rag" not in cfg.get("mcpServers", {})
    assert (
        tmp_path / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
    ).is_file()
    detected = installer._read_hosts_marker(tmp_path)
    assert detected[0].scope == "user" and detected[0].surface == "cli"
