"""Integration tests for java_codebase_rag.installer module.

These tests are gated behind JAVA_CODEBASE_RAG_RUN_HEAVY=1.
"""

import json
import os
import pytest
import shutil
import subprocess
import sys
from pathlib import Path


@pytest.mark.skipif(
    "JAVA_CODEBASE_RAG_RUN_HEAVY" not in os.environ,
    reason="Integration tests require JAVA_CODEBASE_RAG_RUN_HEAVY=1",
)
class TestInstallIntegration:
    """Integration tests for install command."""

    def test_install_non_interactive_claude_code_bank_chat(self, tmp_path):
        """run install --non-interactive --agent claude-code --surface cli on bank-chat fixture

        cli surface contract: a SessionStart ``jrag prime --hook-json`` entry in
        the host settings.json, no MCP entry, no skill/agent files.
        """
        # Copy bank-chat fixture to tmp_path
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Create .git so update_gitignore works
        (cwd / ".git").mkdir()

        # Seed a committed 0.12.x-era artifact the way an upgrading repo
        # carries one. tests/*/.claude/ is gitignored, so this must be created
        # here — never read from the fixture tree. Install must leave it in
        # place; removal is `jrag update`'s job (pinned in
        # test_installer_legacy_cleanup.py).
        fixture_skill = cwd / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
        fixture_skill.parent.mkdir(parents=True, exist_ok=True)
        fixture_skill.write_text("# legacy 0.12.x skill, committed by the user\n")

        # Run install via subprocess to test the CLI integration
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "java_codebase_rag.cli",
                "install",
                "--non-interactive",
                "--agent",
                "claude-code",
                "--surface",
                "cli",
                "--quiet",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

        # Verify exit code
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        # Verify artifacts
        yaml_path = cwd / ".java-codebase-rag.yml"
        assert yaml_path.is_file()
        yaml_content = yaml_path.read_text()
        import yaml
        config = yaml.safe_load(yaml_content)
        # Should not have source_root key
        assert "source_root" not in config
        # Should not have embedding.model (auto is default)
        assert "embedding" not in config or "model" not in config.get("embedding", {})

        # Verify SessionStart prime hook (the cli surface's only artifact)
        settings_path = cwd / ".claude" / "settings.json"
        assert settings_path.is_file(), f"SessionStart hook not deployed at {settings_path}"
        settings = json.loads(settings_path.read_text())
        commands = [
            entry["command"]
            for matcher in settings.get("hooks", {}).get("SessionStart", [])
            if matcher.get("matcher") == ""
            for entry in matcher.get("hooks", [])
        ]
        assert len(commands) == 1, f"expected one hook entry, got {commands}"
        binary, _, args = commands[0].partition(" ")
        assert Path(binary).name == "jrag"
        assert args == "prime --hook-json"

        # The cli surface registers no MCP entry
        assert not (cwd / ".mcp.json").exists()

        # No deployed skill/agent artifacts; the fixture's own files survive
        skill_path = cwd / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
        assert not skill_path.is_file()

        agent_path = cwd / ".claude" / "agents" / "explorer-rag-enhanced.md"
        assert not agent_path.is_file()

        fixture_skill = cwd / ".claude" / "skills" / "explore-codebase-cli" / "SKILL.md"
        assert fixture_skill.is_file()

        # Verify .gitignore
        gitignore = cwd / ".gitignore"
        assert gitignore.is_file()
        gitignore_content = gitignore.read_text()
        assert ".java-codebase-rag/" in gitignore_content

    def test_install_non_interactive_multi_host_bank_chat(self, tmp_path):
        """run install --non-interactive --agent claude-code --agent qwen-code

        mcp surface contract: the MCP entry per host and nothing else — no
        SessionStart hook, no skill/agent files.
        """
        # Copy bank-chat fixture to tmp_path
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Create .git so update_gitignore works
        (cwd / ".git").mkdir()

        # Run install via subprocess to test the CLI integration
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "java_codebase_rag.cli",
                "install",
                "--non-interactive",
                "--agent",
                "claude-code",
                "--agent",
                "qwen-code",
                "--surface",
                "mcp",
                "--quiet",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

        # Verify exit code
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        # Verify both hosts configured (the mcp entry is the only artifact)
        mcp_claude = cwd / ".mcp.json"
        mcp_qwen = cwd / ".qwen" / "settings.json"
        assert mcp_claude.is_file()
        assert mcp_qwen.is_file()
        for config_path in (mcp_claude, mcp_qwen):
            config = json.loads(config_path.read_text())
            entry = config.get("mcpServers", {}).get("java-codebase-rag")
            assert entry is not None, f"no MCP entry in {config_path}"
            assert entry["type"] == "stdio"
            # qwen's settings.json carries the MCP entry — and no SessionStart hook
            assert "SessionStart" not in config.get("hooks", {})

        # The mcp surface deploys no SessionStart hook for claude-code
        assert not (cwd / ".claude" / "settings.json").exists()

        # And no skill/agent files for either host
        for host_dir in (".claude", ".qwen"):
            assert not (cwd / host_dir / "skills" / "explore-codebase" / "SKILL.md").is_file()
            assert not (cwd / host_dir / "agents" / "explorer-rag-enhanced.md").is_file()
