"""``run_update`` removes legacy 0.12.x skill/agent file deployments.

0.12.x shipped real files — ``skills/explore-codebase/SKILL.md`` +
``agents/explorer-rag-enhanced.md`` (mcp surface) and
``skills/explore-codebase-cli/SKILL.md`` + ``agents/explorer-rag-cli.md``
(cli surface). Today neither surface ships files (``ARTIFACT_MANIFEST`` holds
only the MCP entry / the SessionStart prime hook), so those files are no
longer reachable by manifest-driven teardown: ``update`` must remove them
explicitly, for every configured host/scope, whatever the recorded surface is
(a user may have switched surfaces across versions — the marker records only
the current one).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from java_codebase_rag.installer import (
    ConfiguredHost,
    HOSTS,
    _write_hosts_marker,
    run_update,
)

# Dest-relative paths a 0.12.x install left behind (under host.scope_path).
LEGACY_MCP_SKILL = "skills/explore-codebase/SKILL.md"
LEGACY_MCP_AGENT = "agents/explorer-rag-enhanced.md"
LEGACY_CLI_SKILL = "skills/explore-codebase-cli/SKILL.md"
LEGACY_CLI_AGENT = "agents/explorer-rag-cli.md"

ALL_LEGACY = (LEGACY_MCP_SKILL, LEGACY_MCP_AGENT, LEGACY_CLI_SKILL, LEGACY_CLI_AGENT)


def _stub_update_index_skip(monkeypatch):
    """Stub index discovery so run_update stops before the indexing sub-step."""
    monkeypatch.setattr(
        "java_codebase_rag.config.discover_project_root", lambda cwd: None
    )


def _plant_legacy_files(host_dir: Path) -> None:
    """Plant the four 0.12.x deploy destinations under a host dir (.claude/.qwen)."""
    for rel in ALL_LEGACY:
        dest = host_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("# legacy 0.12.x artifact\n", encoding="utf-8")


def _plant_hook(host_dir: Path, command: str = "/old/jrag prime --hook-json") -> Path:
    """Write a host settings.json carrying our SessionStart prime hook."""
    settings = host_dir / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "",
                            "hooks": [{"type": "command", "command": command}],
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return settings


def _session_start_entries(config: dict) -> list[dict]:
    """Flatten a settings.json's SessionStart ""-matcher hook entries."""
    matchers = config.get("hooks", {}).get("SessionStart", [])
    catch_all = [m for m in matchers if isinstance(m, dict) and m.get("matcher") == ""]
    entries: list[dict] = []
    for matcher in catch_all:
        hooks = matcher.get("hooks", [])
        entries.extend(e for e in hooks if isinstance(e, dict))
    return entries


def test_update_removes_012_artifacts(tmp_path, monkeypatch):
    """A 0.12.x deployment's four files are gone after update; siblings survive.

    Both pairs are removed regardless of the current surface (cli here), the
    user file sharing ``skills/explore-codebase-cli/`` survives with its
    directory, and the current surface's own artifact (the prime hook) is
    refreshed, not torn down.
    """
    _write_hosts_marker(
        tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")]
    )
    host_dir = tmp_path / ".claude"
    settings = _plant_hook(host_dir)
    notes = host_dir / "skills" / "explore-codebase-cli" / "NOTES.txt"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text("user notes\n", encoding="utf-8")
    _plant_legacy_files(host_dir)

    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path)
    assert rc == 0, "legacy cleanup must not fail the update"

    for rel in ALL_LEGACY:
        assert not (host_dir / rel).exists(), f"legacy file survived: {rel}"
    # Sibling user file and its directory survive the cleanup.
    assert notes.is_file()
    assert notes.parent.is_dir()
    # Dirs left empty by the removal are pruned.
    assert not (host_dir / "skills" / "explore-codebase").exists()
    assert not (host_dir / "agents").exists()
    # Current-surface artifact intact (and refreshed to the resolved binary).
    entries = _session_start_entries(json.loads(settings.read_text()))
    assert entries == [{"type": "command", "command": "/fake/bin/jrag prime --hook-json"}]


def test_legacy_cleanup_dry_run(tmp_path, monkeypatch, capsys):
    """--dry-run leaves every legacy file in place but reports all four paths."""
    _write_hosts_marker(
        tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")]
    )
    host_dir = tmp_path / ".claude"
    _plant_hook(host_dir)
    _plant_legacy_files(host_dir)

    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=True, cwd=tmp_path)
    assert rc == 0

    for rel in ALL_LEGACY:
        assert (host_dir / rel).is_file(), f"dry-run must not remove: {rel}"
    out = capsys.readouterr().out
    for rel in ALL_LEGACY:
        assert str(host_dir / rel) in out, f"dry-run report must list: {rel}"


def test_legacy_cleanup_idempotent(tmp_path, monkeypatch, capsys):
    """A second update run is quiet: nothing left to remove, nothing reported."""
    _write_hosts_marker(
        tmp_path, [ConfiguredHost(HOSTS["claude-code"], "project", "cli")]
    )
    host_dir = tmp_path / ".claude"
    _plant_hook(host_dir)
    _plant_legacy_files(host_dir)

    monkeypatch.setattr(shutil, "which", lambda name: "/fake/bin/jrag")
    _stub_update_index_skip(monkeypatch)

    assert run_update(force=False, dry_run=False, cwd=tmp_path) == 0
    first_out = capsys.readouterr().out
    assert "legacy" in first_out.lower(), "first run must report the cleanup"
    assert [ln for ln in first_out.splitlines() if ln.startswith("Removed ")], (
        "first run must print the per-file removals"
    )

    assert run_update(force=False, dry_run=False, cwd=tmp_path) == 0
    out = capsys.readouterr().out
    assert "legacy" not in out.lower(), "second run must not repeat the cleanup noise"
    assert not [ln for ln in out.splitlines() if ln.startswith("Removed ")]


def test_legacy_cleanup_covers_user_scope_and_second_host(tmp_path, monkeypatch):
    """Cleanup spans every configured host/scope, not just the first one.

    A user-scope claude-code host (mcp surface) and a project-scope qwen-code
    host (cli surface) in one marker: all four legacy files disappear from both
    host dirs, and each host's own surface artifact is refreshed in place.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    _write_hosts_marker(
        tmp_path,
        [
            ConfiguredHost(HOSTS["claude-code"], "user", "mcp"),
            ConfiguredHost(HOSTS["qwen-code"], "project", "cli"),
        ],
    )

    user_host_dir = tmp_path / "home" / ".claude"
    _plant_legacy_files(user_host_dir)
    user_mcp_config = tmp_path / "home" / ".claude.json"
    user_mcp_config.parent.mkdir(parents=True, exist_ok=True)
    user_mcp_config.write_text(
        json.dumps(
            {"mcpServers": {"java-codebase-rag": {"command": "/x", "type": "stdio"}}}
        ),
        encoding="utf-8",
    )

    qwen_host_dir = tmp_path / ".qwen"
    _plant_legacy_files(qwen_host_dir)
    qwen_settings = _plant_hook(qwen_host_dir)

    monkeypatch.setattr(shutil, "which", lambda name: f"/fake/bin/{name}")
    _stub_update_index_skip(monkeypatch)

    rc = run_update(force=False, dry_run=False, cwd=tmp_path)
    assert rc == 0

    for host_dir in (user_host_dir, qwen_host_dir):
        for rel in ALL_LEGACY:
            assert not (host_dir / rel).exists(), (
                f"legacy file survived in {host_dir.name}: {rel}"
            )
    # Each host's own surface artifact survived the cleanup (and was refreshed).
    cfg = json.loads(user_mcp_config.read_text())
    assert cfg["mcpServers"]["java-codebase-rag"]["command"] == (
        "/fake/bin/java-codebase-rag-mcp"
    )
    assert _session_start_entries(json.loads(qwen_settings.read_text())) == [
        {"type": "command", "command": "/fake/bin/jrag prime --hook-json"}
    ]
