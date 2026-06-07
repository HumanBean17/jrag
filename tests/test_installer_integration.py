"""Integration tests for java_codebase_rag.installer module.

These tests are gated behind JAVA_CODEBASE_RAG_RUN_HEAVY=1.
"""

import json
import os
import pytest
import shutil
import subprocess
from pathlib import Path


@pytest.mark.skipif(
    "JAVA_CODEBASE_RAG_RUN_HEAVY" not in os.environ,
    reason="Integration tests require JAVA_CODEBASE_RAG_RUN_HEAVY=1",
)
class TestInstallIntegration:
    """Integration tests for install command."""

    def test_install_non_interactive_claude_code_bank_chat(self, tmp_path):
        """run install --non-interactive --agent claude-code from tests/bank-chat-system/ fixture"""
        # Copy bank-chat fixture to tmp_path
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Run install via subprocess to test the CLI integration
        result = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "java_codebase_rag.cli",
                "install",
                "--non-interactive",
                "--agent",
                "claude-code",
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

        # Verify MCP config
        mcp_path = cwd / ".mcp.json"
        assert mcp_path.is_file()
        mcp_content = mcp_path.read_text()
        mcp_config = json.loads(mcp_content)
        assert "java-codebase-rag" in mcp_config.get("mcpServers", {})
        assert mcp_config["mcpServers"]["java-codebase-rag"]["type"] == "stdio"

        # Verify skill and agent
        skill_path = cwd / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
        assert skill_path.is_file()

        agent_path = cwd / ".claude" / "agents" / "explorer-rag-enhanced.md"
        assert agent_path.is_file()

        # Verify .gitignore
        gitignore = cwd / ".gitignore"
        assert gitignore.is_file()
        gitignore_content = gitignore.read_text()
        assert ".java-codebase-rag/" in gitignore_content

    def test_install_non_interactive_multi_host_bank_chat(self, tmp_path):
        """run install --non-interactive --agent claude-code --agent qwen-code"""
        # Copy bank-chat fixture to tmp_path
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Run install via subprocess to test the CLI integration
        result = subprocess.run(
            [
                ".venv/bin/python",
                "-m",
                "java_codebase_rag.cli",
                "install",
                "--non-interactive",
                "--agent",
                "claude-code",
                "--agent",
                "qwen-code",
                "--quiet",
            ],
            cwd=cwd,
            capture_output=True,
            text=True,
        )

        # Verify exit code
        assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

        # Verify both hosts configured
        mcp_claude = cwd / ".mcp.json"
        mcp_qwen = cwd / ".qwen" / "settings.json"
        assert mcp_claude.is_file()
        assert mcp_qwen.is_file()

        skill_claude = cwd / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
        skill_qwen = cwd / ".qwen" / "skills" / "explore-codebase" / "SKILL.md"
        assert skill_claude.is_file()
        assert skill_qwen.is_file()
