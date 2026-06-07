"""Tests for java_codebase_rag.installer module."""

import json
import pytest
from pathlib import Path
from java_codebase_rag.installer import HOSTS


class TestHostConfigPaths:
    """Test HostConfig path resolution for all hosts and scopes."""

    def test_host_config_paths_claude_code_project(self):
        """HostConfig for claude-code + project scope resolves .claude/skills/, .claude/agents/, .mcp.json"""
        host = HOSTS["claude-code"]
        cwd = Path("/test/project")

        assert host.scope_path("project", cwd) == Path("/test/project/.claude")
        assert host.skills_dir("project", cwd) == Path("/test/project/.claude/skills")
        assert host.agents_dir("project", cwd) == Path("/test/project/.claude/agents")
        assert host.mcp_config_path("project", cwd) == Path("/test/project/.mcp.json")

    def test_host_config_paths_claude_code_user(self):
        """HostConfig for claude-code + user scope resolves ~/.claude/skills/, ~/.claude/agents/, ~/.claude.json"""
        host = HOSTS["claude-code"]
        cwd = Path("/test/project")

        assert host.scope_path("user", cwd) == Path.home() / ".claude"
        assert host.skills_dir("user", cwd) == Path.home() / ".claude" / "skills"
        assert host.agents_dir("user", cwd) == Path.home() / ".claude" / "agents"
        assert host.mcp_config_path("user", cwd) == Path.home() / ".claude.json"

    def test_host_config_paths_qwen_project(self):
        """Qwen Code + project: .qwen/skills/, .qwen/agents/, .qwen/settings.json"""
        host = HOSTS["qwen-code"]
        cwd = Path("/test/project")

        assert host.scope_path("project", cwd) == Path("/test/project/.qwen")
        assert host.skills_dir("project", cwd) == Path("/test/project/.qwen/skills")
        assert host.agents_dir("project", cwd) == Path("/test/project/.qwen/agents")
        assert host.mcp_config_path("project", cwd) == Path("/test/project/.qwen/settings.json")

    def test_host_config_paths_qwen_user(self):
        """Qwen Code + user: ~/.qwen/skills/, ~/.qwen/agents/, ~/.qwen/settings.json"""
        host = HOSTS["qwen-code"]
        cwd = Path("/test/project")

        assert host.scope_path("user", cwd) == Path.home() / ".qwen"
        assert host.skills_dir("user", cwd) == Path.home() / ".qwen" / "skills"
        assert host.agents_dir("user", cwd) == Path.home() / ".qwen" / "agents"
        assert host.mcp_config_path("user", cwd) == Path.home() / ".qwen/settings.json"

    def test_host_config_paths_gigacode_project(self):
        """GigaCode + project"""
        host = HOSTS["gigacode"]
        cwd = Path("/test/project")

        assert host.scope_path("project", cwd) == Path("/test/project/.gigacode")
        assert host.skills_dir("project", cwd) == Path("/test/project/.gigacode/skills")
        assert host.agents_dir("project", cwd) == Path("/test/project/.gigacode/agents")
        assert host.mcp_config_path("project", cwd) == Path("/test/project/.gigacode/settings.json")

    def test_host_config_paths_gigacode_user(self):
        """GigaCode + user"""
        host = HOSTS["gigacode"]
        cwd = Path("/test/project")

        assert host.scope_path("user", cwd) == Path.home() / ".gigacode"
        assert host.skills_dir("user", cwd) == Path.home() / ".gigacode" / "skills"
        assert host.agents_dir("user", cwd) == Path.home() / ".gigacode" / "agents"
        assert host.mcp_config_path("user", cwd) == Path.home() / ".gigacode/settings.json"


class TestPromptHelper:
    """Test prompt() helper function."""

    def test_prompt_returns_default_on_non_tty(self, monkeypatch):
        """non-TTY → default returned, questionary not called"""
        import sys
        from java_codebase_rag.installer import prompt

        # Mock isatty to return False
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        result = prompt("checkbox", "Select items", choices=["choice1", "choice2"], default=["default"])
        assert result == ["default"]

    def test_prompt_returns_default_when_none_tty(self, monkeypatch):
        """Test that default is returned for all prompt types in non-TTY mode"""
        import sys
        from java_codebase_rag.installer import prompt

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        # Test different prompt types
        assert prompt("checkbox", "test", default=["a"]) == ["a"]
        assert prompt("select", "test", default="b") == "b"
        assert prompt("text", "test", default="c") == "c"
        assert prompt("confirm", "test", default=True) is True


class TestDetectJavaDirectories:
    """Test detect_java_directories function."""

    def test_detect_java_root_has_maven_pom(self, tmp_path):
        """cwd with pom.xml → returns [Path('.')]"""
        (tmp_path / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import detect_java_directories
        result = detect_java_directories(tmp_path)
        assert result == [Path(".")]

    def test_detect_java_root_has_gradle_build(self, tmp_path):
        """cwd with build.gradle → returns [Path('.')]"""
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }")
        from java_codebase_rag.installer import detect_java_directories
        result = detect_java_directories(tmp_path)
        assert result == [Path(".")]

    def test_detect_java_root_has_gradle_kts(self, tmp_path):
        """cwd with build.gradle.kts → returns [Path('.')]"""
        (tmp_path / "build.gradle.kts").write_text("plugins { java }")
        from java_codebase_rag.installer import detect_java_directories
        result = detect_java_directories(tmp_path)
        assert result == [Path(".")]

    def test_detect_java_no_root_microservice_monorepo(self, tmp_path):
        """cwd has no build file, service-a/pom.xml and service-b/pom.xml exist → returns [Path('service-a'), Path('service-b')]"""
        service_a = tmp_path / "service-a"
        service_b = tmp_path / "service-b"
        service_a.mkdir()
        service_b.mkdir()
        (service_a / "pom.xml").write_text("<project></project>")
        (service_b / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import detect_java_directories
        result = detect_java_directories(tmp_path)
        assert set(result) == {Path("service-a"), Path("service-b")}

    def test_detect_java_no_root_single_service(self, tmp_path):
        """cwd has no build file, only service-a/pom.xml exists → returns [Path('service-a')]"""
        service_a = tmp_path / "service-a"
        service_a.mkdir()
        (service_a / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import detect_java_directories
        result = detect_java_directories(tmp_path)
        assert result == [Path("service-a")]

    def test_detect_java_no_root_no_services_exit_2(self, tmp_path, capsys):
        """cwd has no build file, no children have build files → raises SystemExit(2)"""
        from java_codebase_rag.installer import detect_java_directories
        with pytest.raises(SystemExit) as exc_info:
            detect_java_directories(tmp_path)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "Error:" in captured.out and "No Java build files" in captured.out


class TestConfirmSourceRoot:
    """Test confirm_source_root function."""

    def test_confirm_source_root_interactive_accepts_default(self, monkeypatch):
        """user presses Enter → returns cwd"""
        from java_codebase_rag.installer import confirm_source_root
        cwd = Path("/test/project")
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = confirm_source_root(cwd, non_interactive=False)
        # In non-TTY mode, prompt returns default
        assert result == cwd

    def test_confirm_source_root_non_interactive_returns_cwd(self):
        """non-interactive → returns cwd, no prompt"""
        from java_codebase_rag.installer import confirm_source_root
        cwd = Path("/test/project")
        result = confirm_source_root(cwd, non_interactive=True)
        assert result == cwd

    def test_confirm_source_root_expands_tilde(self, monkeypatch):
        """user types ~/projects/foo → expanded via Path.home()"""
        import sys
        from java_codebase_rag.installer import confirm_source_root

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(Path, "is_dir", lambda self: True)

        # Mock prompt to return a path with ~
        cwd = Path("/test/project")
        test_path = Path.home() / "projects" / "foo"

        def mock_prompt(*args, **kwargs):
            return "~/projects/foo"

        monkeypatch.setattr("java_codebase_rag.installer.prompt", mock_prompt)
        monkeypatch.setattr(Path, "resolve", lambda self: self)

        result = confirm_source_root(cwd, non_interactive=False)
        assert str(result) == str(test_path)


class TestResolveModel:
    """Test resolve_model function."""

    def test_model_path_found_returns_resolved(self, tmp_path):
        """existing path → returned expanded"""
        model_file = tmp_path / "model.bin"
        model_file.write_text("fake model")
        from java_codebase_rag.installer import resolve_model
        result = resolve_model(str(model_file), non_interactive=False)
        assert result == str(model_file)

    def test_model_path_not_found_prompts_confirmation(self, monkeypatch):
        """non-existent path → confirmation prompt"""
        import sys
        from java_codebase_rag.installer import resolve_model

        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        # Mock prompt to return True (confirm using auto)
        def mock_prompt(*args, **kwargs):
            return True
        monkeypatch.setattr("java_codebase_rag.installer.prompt", mock_prompt)

        result = resolve_model("/nonexistent/path", non_interactive=False)
        assert result == "auto"


class TestSelectHostsAndScope:
    """Test select_hosts and select_scope functions."""

    def test_select_hosts_non_interactive_requires_agent(self):
        """no --agent in non-interactive → exit 2"""
        from java_codebase_rag.installer import select_hosts
        with pytest.raises(SystemExit) as exc_info:
            select_hosts(non_interactive=True, cli_agents=None)
        assert exc_info.value.code == 2

    def test_select_hosts_invalid_agent_exit_2(self):
        """unknown agent string → exit 2"""
        from java_codebase_rag.installer import select_hosts
        with pytest.raises(SystemExit) as exc_info:
            select_hosts(non_interactive=True, cli_agents=["unknown-agent"])
        assert exc_info.value.code == 2

    def test_select_hosts_multi_host_non_interactive(self):
        """--agent claude-code --agent qwen-code → both hosts selected"""
        from java_codebase_rag.installer import select_hosts, HOSTS
        result = select_hosts(non_interactive=True, cli_agents=["claude-code", "qwen-code"])
        assert len(result) == 2
        assert result[0] == HOSTS["claude-code"]
        assert result[1] == HOSTS["qwen-code"]

    def test_select_scope_non_interactive_default_project(self):
        """non-interactive → returns 'project'"""
        from java_codebase_rag.installer import select_scope
        result = select_scope(non_interactive=True, cli_scope=None)
        assert result == "project"

    def test_select_scope_invalid_scope_exit_2(self):
        """invalid scope string → exit 2"""
        from java_codebase_rag.installer import select_scope
        with pytest.raises(SystemExit) as exc_info:
            select_scope(non_interactive=True, cli_scope="invalid")
        assert exc_info.value.code == 2


class TestResolveMcpCommand:
    """Test resolve_mcp_command function."""

    def test_resolve_mcp_command_found(self, monkeypatch):
        """shutil.which returns /usr/local/bin/java-codebase-rag-mcp → that path returned"""
        import shutil
        from java_codebase_rag.installer import resolve_mcp_command

        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/java-codebase-rag-mcp")
        result = resolve_mcp_command(non_interactive=True)
        assert result == "/usr/local/bin/java-codebase-rag-mcp"

    def test_resolve_mcp_command_not_found_non_interactive_exit_2(self, monkeypatch, capsys):
        """shutil.which returns None + non-interactive → SystemExit(2)"""
        import shutil
        from java_codebase_rag.installer import resolve_mcp_command

        monkeypatch.setattr(shutil, "which", lambda x: None)
        with pytest.raises(SystemExit) as exc_info:
            resolve_mcp_command(non_interactive=True)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "not found on PATH" in captured.out

    def test_resolve_mcp_command_not_found_interactive_abort(self, monkeypatch):
        """user enters "abort" at prompt → SystemExit(2)"""
        import shutil
        import sys
        from java_codebase_rag.installer import resolve_mcp_command

        monkeypatch.setattr(shutil, "which", lambda x: None)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        # Mock prompt to return "abort"
        def mock_prompt(*args, **kwargs):
            return "abort"
        monkeypatch.setattr("java_codebase_rag.installer.prompt", mock_prompt)

        with pytest.raises(SystemExit) as exc_info:
            resolve_mcp_command(non_interactive=False)
        assert exc_info.value.code == 2


class TestMergeMcpConfig:
    """Test merge_mcp_config function."""

    def test_mcp_merge_adds_to_empty(self, tmp_path):
        """empty {} → {"mcpServers": {"java-codebase-rag": {...}}}"""
        from java_codebase_rag.installer import merge_mcp_config, HOSTS
        config_path = tmp_path / "mcp.json"
        result = merge_mcp_config(config_path, HOSTS["claude-code"], mcp_command="/bin/mcp")
        assert result is True
        with open(config_path) as f:
            config = json.load(f)
        assert "mcpServers" in config
        assert "java-codebase-rag" in config["mcpServers"]
        assert config["mcpServers"]["java-codebase-rag"]["command"] == "/bin/mcp"
        assert config["mcpServers"]["java-codebase-rag"]["type"] == "stdio"

    def test_mcp_merge_adds_to_existing_servers(self, tmp_path):
        """existing {"mcpServers": {"other": {...}}} → both servers present"""
        from java_codebase_rag.installer import merge_mcp_config, HOSTS
        config_path = tmp_path / "mcp.json"
        config_path.write_text(json.dumps({"mcpServers": {"other": {"command": "/other"}}}))
        result = merge_mcp_config(config_path, HOSTS["claude-code"], mcp_command="/bin/mcp")
        assert result is True
        with open(config_path) as f:
            config = json.load(f)
        assert "other" in config["mcpServers"]
        assert "java-codebase-rag" in config["mcpServers"]

    def test_mcp_merge_updates_existing_entry(self, tmp_path):
        """existing java-codebase-rag entry with different command → updated"""
        from java_codebase_rag.installer import merge_mcp_config, HOSTS
        config_path = tmp_path / "mcp.json"
        config_path.write_text(json.dumps({
            "mcpServers": {
                "java-codebase-rag": {"command": "/old/path", "type": "stdio"}
            }
        }))
        result = merge_mcp_config(config_path, HOSTS["claude-code"], mcp_command="/new/path")
        assert result is True
        with open(config_path) as f:
            config = json.load(f)
        assert config["mcpServers"]["java-codebase-rag"]["command"] == "/new/path"

    def test_mcp_merge_preserves_other_keys_claude_json(self, tmp_path):
        """{"numStartups": 42, "userID": "abc", "mcpServers": {...}} → preserved"""
        from java_codebase_rag.installer import merge_mcp_config, HOSTS
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps({
            "numStartups": 42,
            "userID": "abc",
            "mcpServers": {}
        }))
        merge_mcp_config(config_path, HOSTS["claude-code"], mcp_command="/bin/mcp")
        with open(config_path) as f:
            config = json.load(f)
        assert config["numStartups"] == 42
        assert config["userID"] == "abc"

    def test_mcp_merge_preserves_other_keys_settings_json(self, tmp_path):
        """{"security": {...}, "$version": 2, "mcpServers": {...}} → preserved"""
        from java_codebase_rag.installer import merge_mcp_config, HOSTS
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps({
            "security": {"level": "high"},
            "$version": 2,
            "mcpServers": {}
        }))
        merge_mcp_config(config_path, HOSTS["qwen-code"], mcp_command="/bin/mcp")
        with open(config_path) as f:
            config = json.load(f)
        assert config["security"]["level"] == "high"
        assert config["$version"] == 2


class TestDeployArtifacts:
    """Test deploy_artifacts function."""

    def test_permission_error_skips_artifact_continues(self, tmp_path, monkeypatch):
        """unwritable directory → artifact skipped, others continue, exit 1"""
        from java_codebase_rag.installer import deploy_artifacts, HOSTS

        # Mock _is_writable to return False for skills directory
        def mock_is_writable(path):
            return "skills" not in str(path)

        monkeypatch.setattr("java_codebase_rag.installer._is_writable", mock_is_writable)

        results = deploy_artifacts(
            [HOSTS["claude-code"]],
            "project",
            tmp_path,
            non_interactive=True,
            mcp_command="/bin/mcp",
        )

        # Should have 3 results (MCP, skill, agent)
        assert len(results) == 3
        # MCP should succeed
        assert results[0].success is True
        # Skill should fail due to permission
        assert results[1].success is False
        assert "not writable" in results[1].error
        # Agent should succeed
        assert results[2].success is True

    def test_artifact_overwrite_prompt_existing_skill(self, tmp_path, monkeypatch):
        """existing skill file → prompts overwrite/skip/abort"""
        import sys
        from java_codebase_rag.installer import _deploy_file

        # Create existing skill file
        skills_dir = tmp_path / ".claude" / "skills" / "explore-codebase"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("old content")

        # Mock prompt to return "skip"
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        def mock_prompt(*args, **kwargs):
            return "skip"
        monkeypatch.setattr("java_codebase_rag.installer.prompt", mock_prompt)

        result = _deploy_file(
            skill_file,
            "skills/explore-codebase/SKILL.md",
            artifact_type="skill",
            non_interactive=False,
        )

        assert result.success is False
        assert "Skipped by user" in result.error

    def test_deploy_artifacts_multi_host_deploy_all(self, tmp_path, monkeypatch):
        """multiple hosts selected → artifacts deployed to all"""
        from java_codebase_rag.installer import deploy_artifacts, HOSTS

        results = deploy_artifacts(
            [HOSTS["claude-code"], HOSTS["qwen-code"]],
            "project",
            tmp_path,
            non_interactive=True,
            mcp_command="/bin/mcp",
        )

        # Should have 6 results (3 per host: MCP, skill, agent)
        assert len(results) == 6
        # All should succeed
        assert all(r.success for r in results)

        # Verify files exist for both hosts
        assert (tmp_path / ".mcp.json").is_file()
        assert (tmp_path / ".claude" / "skills" / "explore-codebase" / "SKILL.md").is_file()
        assert (tmp_path / ".claude" / "agents" / "explorer-rag-enhanced.md").is_file()
        assert (tmp_path / ".qwen" / "settings.json").is_file()
        assert (tmp_path / ".qwen" / "skills" / "explore-codebase" / "SKILL.md").is_file()
        assert (tmp_path / ".qwen" / "agents" / "explorer-rag-enhanced.md").is_file()


class TestGenerateYamlConfig:
    """Test generate_yaml_config function."""

    def test_yaml_generation_auto_model(self):
        """model=auto → YAML has no embedding.model key and no source_root key"""
        from java_codebase_rag.installer import generate_yaml_config
        import yaml
        result = generate_yaml_config(Path("/test"), "auto", None, None)
        config = yaml.safe_load(result)
        assert "source_root" not in config
        assert "embedding" not in config or "model" not in config.get("embedding", {})

    def test_yaml_generation_custom_model(self):
        """model=/path/to/model → YAML has embedding.model but no source_root"""
        from java_codebase_rag.installer import generate_yaml_config
        import yaml
        result = generate_yaml_config(Path("/test"), "/path/to/model", None, None)
        config = yaml.safe_load(result)
        assert config["embedding"]["model"] == "/path/to/model"
        assert "source_root" not in config

    def test_yaml_generation_with_microservice_roots(self):
        """subset of dirs → YAML has microservice_roots"""
        from java_codebase_rag.installer import generate_yaml_config
        import yaml
        result = generate_yaml_config(
            Path("/test"), "auto", ["service-a", "service-b"], None
        )
        config = yaml.safe_load(result)
        assert config["microservice_roots"] == ["service-a", "service-b"]

    def test_yaml_generation_all_dirs_selected(self):
        """all dirs → no microservice_roots in YAML"""
        from java_codebase_rag.installer import generate_yaml_config
        import yaml
        result = generate_yaml_config(Path("/test"), "auto", None, None)
        config = yaml.safe_load(result)
        assert "microservice_roots" not in config

    def test_yaml_generation_preserves_unmanaged_keys(self):
        """existing YAML with brownfield_overrides and embedding.device → both preserved"""
        from java_codebase_rag.installer import generate_yaml_config
        import yaml
        existing = {
            "brownfield_overrides": {"routes": ["/api"]},
            "embedding": {"device": "cuda"},
        }
        result = generate_yaml_config(Path("/test"), "auto", None, existing)
        config = yaml.safe_load(result)
        assert config["brownfield_overrides"] == {"routes": ["/api"]}
        assert config["embedding"]["device"] == "cuda"


class TestUpdateGitignore:
    """Test update_gitignore function."""

    def test_gitignore_creates_if_missing(self, tmp_path, monkeypatch):
        """no .gitignore → created with .java-codebase-rag/"""
        # Create .git directory to simulate git repo
        (tmp_path / ".git").mkdir()
        from java_codebase_rag.installer import update_gitignore
        update_gitignore(tmp_path)
        gitignore = tmp_path / ".gitignore"
        assert gitignore.is_file()
        content = gitignore.read_text()
        assert ".java-codebase-rag/" in content

    def test_gitignore_appends_if_not_present(self, tmp_path, monkeypatch):
        """existing .gitignore without pattern → appended"""
        (tmp_path / ".git").mkdir()
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("node_modules/\n")
        from java_codebase_rag.installer import update_gitignore
        update_gitignore(tmp_path)
        content = gitignore.read_text()
        assert ".java-codebase-rag/" in content

    def test_gitignore_skips_if_present_with_slash(self, tmp_path, monkeypatch):
        """existing .java-codebase-rag/ → no change"""
        (tmp_path / ".git").mkdir()
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".java-codebase-rag/\n")
        from java_codebase_rag.installer import update_gitignore
        original_content = gitignore.read_text()
        update_gitignore(tmp_path)
        assert gitignore.read_text() == original_content

    def test_gitignore_skips_if_present_without_slash(self, tmp_path, monkeypatch):
        """existing .java-codebase-rag → no change"""
        (tmp_path / ".git").mkdir()
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".java-codebase-rag\n")
        from java_codebase_rag.installer import update_gitignore
        original_content = gitignore.read_text()
        update_gitignore(tmp_path)
        assert gitignore.read_text() == original_content

    def test_gitignore_skips_if_not_git_repo(self, tmp_path):
        """no .git dir → no file created, no error"""
        from java_codebase_rag.installer import update_gitignore
        update_gitignore(tmp_path)
        assert not (tmp_path / ".gitignore").is_file()


class TestHandleRerun:
    """Test handle_rerun function."""

    def test_rerun_detects_existing_config(self, tmp_path):
        """existing .java-codebase-rag.yml → returns parsed data"""
        import yaml
        config_path = tmp_path / ".java-codebase-rag.yml"
        config_path.write_text(yaml.dump({"model": "auto", "source_root": "."}))
        from java_codebase_rag.installer import handle_rerun
        result = handle_rerun(tmp_path, non_interactive=True)
        assert result is not None
        assert result["model"] == "auto"

    def test_rerun_no_config_returns_none(self, tmp_path):
        """no config → returns None"""
        from java_codebase_rag.installer import handle_rerun
        result = handle_rerun(tmp_path, non_interactive=True)
        assert result is None


class TestInstallIntegration:
    """Integration tests for install command."""

    def test_install_non_interactive_claude_code_bank_chat(self, tmp_path, monkeypatch):
        """run install --non-interactive --agent claude-code from tests/bank-chat-system/ fixture"""
        import shutil
        from java_codebase_rag.installer import run_install

        # Copy bank-chat fixture to tmp_path
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Mock shutil.which to return a fake MCP path
        monkeypatch.setattr(shutil, "which", lambda x: "/fake/bin/java-codebase-rag-mcp")

        # Mock pipeline functions to avoid actual indexing
        def mock_run_cocoindex_update(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["cocoindex"], 0)

        def mock_run_build_ast_graph(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["build_ast_graph"], 0)

        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_cocoindex_update",
            mock_run_cocoindex_update,
        )
        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_build_ast_graph",
            mock_run_build_ast_graph,
        )

        # Change to fixture directory
        monkeypatch.setattr(Path, "cwd", lambda: cwd)

        result = run_install(
            non_interactive=True,
            agents=["claude-code"],
            scope="project",
            model="auto",
            source_root=cwd,
            quiet=True,
        )

        # Verify exit code
        assert result == 0

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

    def test_install_non_interactive_multi_host_bank_chat(self, tmp_path, monkeypatch):
        """run install --non-interactive --agent claude-code --agent qwen-code"""
        import shutil
        from java_codebase_rag.installer import run_install

        # Copy bank-chat fixture to tmp_path
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Mock shutil.which to return a fake MCP path
        monkeypatch.setattr(shutil, "which", lambda x: "/fake/bin/java-codebase-rag-mcp")

        # Mock pipeline functions
        def mock_run_cocoindex_update(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["cocoindex"], 0)

        def mock_run_build_ast_graph(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["build_ast_graph"], 0)

        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_cocoindex_update",
            mock_run_cocoindex_update,
        )
        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_build_ast_graph",
            mock_run_build_ast_graph,
        )

        # Change to fixture directory
        monkeypatch.setattr(Path, "cwd", lambda: cwd)

        result = run_install(
            non_interactive=True,
            agents=["claude-code", "qwen-code"],
            scope="project",
            model="auto",
            source_root=cwd,
            quiet=True,
        )

        # Verify exit code
        assert result == 0

        # Verify both hosts configured
        mcp_claude = cwd / ".mcp.json"
        mcp_qwen = cwd / ".qwen" / "settings.json"
        assert mcp_claude.is_file()
        assert mcp_qwen.is_file()

        skill_claude = cwd / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
        skill_qwen = cwd / ".qwen" / "skills" / "explore-codebase" / "SKILL.md"
        assert skill_claude.is_file()
        assert skill_qwen.is_file()
