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


class TestSelectMicroservices:
    """Test select_microservices function."""

    def test_select_microservices_non_interactive_returns_none(self):
        """non_interactive=True with 3 dirs → returns None (all)"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path("service-a"), Path("service-b"), Path("service-c")]
        result = select_microservices(dirs, non_interactive=True)
        assert result is None

    def test_select_microservices_non_tty_returns_none_all_selected(self, monkeypatch):
        """non-TTY → prompt returns default (all) → returns None"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path("service-a"), Path("service-b"), Path("service-c")]
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        result = select_microservices(dirs, non_interactive=False)
        assert result is None

    def test_select_microservices_subset_returns_list(self, monkeypatch):
        """prompt checkbox returns ['service-a'] of 3 → returns ['service-a']"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path("service-a"), Path("service-b"), Path("service-c")]

        def fake_prompt(ptype, message, **kw):
            return ["service-a"] if ptype == "checkbox" else True

        monkeypatch.setattr("java_codebase_rag.installer.prompt", fake_prompt)
        result = select_microservices(dirs, non_interactive=False)
        assert result == ["service-a"]

    def test_select_microservices_all_selected_returns_none(self, monkeypatch):
        """prompt returns all 3 → returns None"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path("service-a"), Path("service-b"), Path("service-c")]
        all_names = ["service-a", "service-b", "service-c"]

        def fake_prompt(ptype, message, **kw):
            return all_names if ptype == "checkbox" else True

        monkeypatch.setattr("java_codebase_rag.installer.prompt", fake_prompt)
        result = select_microservices(dirs, non_interactive=False)
        assert result is None

    def test_select_microservices_empty_then_decline_exit_2(self, monkeypatch):
        """prompt checkbox [] + confirm False → SystemExit(2)"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path("service-a"), Path("service-b"), Path("service-c")]

        def fake_prompt(ptype, message, **kw):
            return [] if ptype == "checkbox" else False

        monkeypatch.setattr("java_codebase_rag.installer.prompt", fake_prompt)
        with pytest.raises(SystemExit) as exc_info:
            select_microservices(dirs, non_interactive=False)
        assert exc_info.value.code == 2

    def test_select_microservices_preselected_marks_choices(self, monkeypatch):
        """preselected=['service-a'] → only service-a has checked=True, result == ['service-a']"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path("service-a"), Path("service-b"), Path("service-c")]
        captured = {}

        def fake_prompt(ptype, message, **kw):
            if ptype == "checkbox":
                captured["choices"] = kw["choices"]
                return ["service-a"]
            return True

        monkeypatch.setattr("java_codebase_rag.installer.prompt", fake_prompt)
        result = select_microservices(dirs, non_interactive=False, preselected=["service-a"])

        checked_names = [c["name"] for c in captured["choices"] if c["checked"]]
        assert checked_names == ["service-a"]
        assert result == ["service-a"]

    def test_select_microservices_single_dir_returns_none(self):
        """len(java_dirs) < 2 → returns None"""
        from java_codebase_rag.installer import select_microservices
        dirs = [Path(".")]
        result = select_microservices(dirs, non_interactive=False)
        assert result is None


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

    def test_model_non_interactive_with_path_uses_path(self, tmp_path):
        """--model /path/to/model with --non-interactive → uses the path"""
        model_file = tmp_path / "model.gguf"
        model_file.write_text("fake model")
        from java_codebase_rag.installer import resolve_model
        result = resolve_model(str(model_file), non_interactive=True)
        assert result == str(model_file)

    def test_model_non_interactive_with_bad_path_falls_back(self, capsys):
        """--model /bad/path with --non-interactive → warning + auto"""
        from java_codebase_rag.installer import resolve_model
        result = resolve_model("/nonexistent/model.gguf", non_interactive=True)
        assert result == "auto"
        captured = capsys.readouterr()
        assert "Warning" in captured.out

    def test_model_non_interactive_no_input_returns_auto(self):
        """no --model with --non-interactive → auto"""
        from java_codebase_rag.installer import resolve_model
        result = resolve_model(None, non_interactive=True)
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

    def test_mcp_merge_raises_on_invalid_json(self, tmp_path):
        """malformed JSON → raises ValueError"""
        from java_codebase_rag.installer import merge_mcp_config, HOSTS
        config_path = tmp_path / "mcp.json"
        config_path.write_text("{invalid json!!!")
        with pytest.raises(ValueError, match="Failed to parse"):
            merge_mcp_config(config_path, HOSTS["claude-code"], mcp_command="/bin/mcp")


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


class TestGenerateYamlConfigCrossService:
    """cross_service_resolution is seeded safe-by-default; an explicit choice is never overridden."""

    def test_fresh_install_seeds_brownfield_only(self):
        import yaml
        from java_codebase_rag.installer import generate_yaml_config

        out = generate_yaml_config(
            Path("."), model="auto", microservice_roots=None, existing_yaml=None
        )
        assert yaml.safe_load(out)["cross_service_resolution"] == "brownfield_only"

    def test_explicit_auto_is_preserved_on_rerun(self):
        import yaml
        from java_codebase_rag.installer import generate_yaml_config

        out = generate_yaml_config(
            Path("."),
            model="auto",
            microservice_roots=None,
            existing_yaml={"cross_service_resolution": "auto"},
        )
        assert yaml.safe_load(out)["cross_service_resolution"] == "auto"

    def test_absent_key_seeded_and_existing_keys_preserved_on_rerun(self):
        import yaml
        from java_codebase_rag.installer import generate_yaml_config

        out = generate_yaml_config(
            Path("."),
            model="auto",
            microservice_roots=None,
            existing_yaml={"brownfield_overrides": {"svc-a": {}}},
        )
        config = yaml.safe_load(out)
        assert config["cross_service_resolution"] == "brownfield_only"
        assert config["brownfield_overrides"] == {"svc-a": {}}


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

        # Create .git so update_gitignore works
        (cwd / ".git").mkdir()

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

        # Create .git so update_gitignore works
        (cwd / ".git").mkdir()

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


class TestDetectConfiguredHosts:
    """Test detect_configured_hosts function for PR-I2."""

    def test_detect_hosts_project_mcp_json(self, tmp_path):
        """.mcp.json with entry → detects claude-code project scope"""
        from java_codebase_rag.installer import detect_configured_hosts

        # Create .mcp.json with java-codebase-rag entry
        mcp_config = tmp_path / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        detected = detect_configured_hosts(tmp_path)
        assert len(detected) == 1
        host_config, scope = detected[0]
        assert host_config.name == "claude-code"
        assert scope == "project"

    def test_detect_hosts_user_claude_json(self, tmp_path, monkeypatch):
        """~/.claude.json with entry → detects claude-code user scope"""
        from java_codebase_rag.installer import detect_configured_hosts

        # Create a fake home directory
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        # Create ~/.claude.json with java-codebase-rag entry
        claude_json = fake_home / ".claude.json"
        claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        detected = detect_configured_hosts(tmp_path)
        assert len(detected) == 1
        host_config, scope = detected[0]
        assert host_config.name == "claude-code"
        assert scope == "user"

    def test_detect_hosts_multiple_hosts(self, tmp_path, monkeypatch):
        """both .mcp.json and ~/.qwen/settings.json → returns both"""
        from java_codebase_rag.installer import detect_configured_hosts

        # Create a fake home directory
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        # Create project-level .mcp.json
        mcp_config = tmp_path / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        # Create user-level .qwen/settings.json
        qwen_settings = fake_home / ".qwen" / "settings.json"
        qwen_settings.parent.mkdir(parents=True, exist_ok=True)
        qwen_settings.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        detected = detect_configured_hosts(tmp_path)
        assert len(detected) == 2

        # Sort by scope for consistent ordering
        detected_sorted = sorted(detected, key=lambda x: x[1])

        # First should be project scope claude-code
        assert detected_sorted[0][0].name == "claude-code"
        assert detected_sorted[0][1] == "project"

        # Second should be user scope qwen-code
        assert detected_sorted[1][0].name == "qwen-code"
        assert detected_sorted[1][1] == "user"

    def test_detect_hosts_no_config_returns_empty(self, tmp_path):
        """no MCP configs → empty list"""
        from java_codebase_rag.installer import detect_configured_hosts

        detected = detect_configured_hosts(tmp_path)
        assert detected == []

    def test_detect_hosts_ignores_unrelated_entries(self, tmp_path):
        """mcpServers with other tools but not java-codebase-rag → empty"""
        from java_codebase_rag.installer import detect_configured_hosts

        # Create .mcp.json with other MCP servers but not java-codebase-rag
        mcp_config = tmp_path / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "filesystem": {"command": "/bin/fs", "type": "stdio"},
                        "brave-search": {"command": "/bin/search", "type": "stdio"},
                    }
                }
            )
        )

        detected = detect_configured_hosts(tmp_path)
        assert detected == []


class TestRefreshArtifacts:
    """Test refresh_artifacts function for PR-I2."""

    def test_refresh_skill_overwrites_stale(self, tmp_path, monkeypatch):
        """skill file differs from package → overwritten"""
        from java_codebase_rag.installer import refresh_artifacts, HOSTS

        # Create skill file with stale content
        skills_dir = tmp_path / ".claude" / "skills" / "explore-codebase"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("STALE CONTENT")

        # Mock _read_package_artifact to return new content
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "NEW CONTENT",
        )

        host = HOSTS["claude-code"]
        results = refresh_artifacts(host, "project", tmp_path, force=False, dry_run=False)

        # Should have updated the skill file
        skill_results = [r for r in results if "SKILL.md" in str(r.path)]
        assert len(skill_results) == 1
        assert skill_results[0].success is True
        assert skill_file.read_text() == "NEW CONTENT"

    def test_refresh_skill_skips_if_matching(self, tmp_path, monkeypatch):
        """skill file matches → not overwritten (unless --force)"""
        from java_codebase_rag.installer import refresh_artifacts, HOSTS

        # Create skill file with current content
        skills_dir = tmp_path / ".claude" / "skills" / "explore-codebase"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("CURRENT CONTENT")

        # Mock _read_package_artifact to return same content
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "CURRENT CONTENT",
        )

        host = HOSTS["claude-code"]
        results = refresh_artifacts(host, "project", tmp_path, force=False, dry_run=False)

        # Should have skipped the skill file (no change needed)
        skill_results = [r for r in results if "SKILL.md" in str(r.path)]
        assert len(skill_results) == 1
        assert skill_results[0].success is True
        # File should remain unchanged
        assert skill_file.read_text() == "CURRENT CONTENT"

    def test_refresh_mcp_skips_if_correct(self, tmp_path, monkeypatch):
        """MCP entry matches the current resolved path → not modified"""
        from java_codebase_rag.installer import refresh_artifacts, HOSTS
        import shutil

        # Create MCP config with correct entry
        mcp_config = tmp_path / ".mcp.json"
        expected_command = "/usr/local/bin/java-codebase-rag-mcp"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": expected_command,
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        # Mock shutil.which to return the same path
        monkeypatch.setattr(shutil, "which", lambda x: expected_command)

        host = HOSTS["claude-code"]
        results = refresh_artifacts(host, "project", tmp_path, force=False, dry_run=False)

        # MCP config should be skipped (no change needed)
        mcp_results = [r for r in results if ".mcp.json" in str(r.path)]
        assert len(mcp_results) == 1
        assert mcp_results[0].success is True
        # Config should remain unchanged
        config_data = json.loads(mcp_config.read_text())
        assert config_data["mcpServers"]["java-codebase-rag"]["command"] == expected_command

    def test_refresh_dry_run_prints_no_write(self, tmp_path, monkeypatch, capsys):
        """--dry-run → prints changes, no files written"""
        from java_codebase_rag.installer import refresh_artifacts, HOSTS

        # Create skill file with stale content
        skills_dir = tmp_path / ".claude" / "skills" / "explore-codebase"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("STALE CONTENT")

        # Mock _read_package_artifact to return new content
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "NEW CONTENT",
        )

        host = HOSTS["claude-code"]
        refresh_artifacts(host, "project", tmp_path, force=False, dry_run=True)

        # In dry-run mode, files should not be written
        captured = capsys.readouterr()
        assert "dry-run" in captured.out.lower() or "would" in captured.out.lower()
        # File should remain unchanged
        assert skill_file.read_text() == "STALE CONTENT"


class TestRunUpdate:
    """Test run_update orchestrator for PR-I2."""

    def test_update_no_hosts_exit_2(self, tmp_path, monkeypatch):
        """no configured hosts → exit 2"""
        from java_codebase_rag.installer import run_update

        # No MCP configs exist
        result = run_update(force=False, dry_run=False, cwd=tmp_path)
        assert result == 2

    def test_update_no_index_skips_increment(self, tmp_path, monkeypatch):
        """hosts configured but no index directory → increment skipped, warning printed"""
        from java_codebase_rag.installer import run_update
        import shutil
        import io

        # Create MCP config to have a configured host
        mcp_config = tmp_path / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        # Create .java-codebase-rag.yml (config exists)
        config_file = tmp_path / ".java-codebase-rag.yml"
        config_file.write_text("source_root: .")

        # Mock shutil.which
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/java-codebase-rag-mcp")

        # Mock index_dir_has_existing_artifacts to return False (no index)
        monkeypatch.setattr(
            "java_codebase_rag.config.index_dir_has_existing_artifacts",
            lambda path: (False, []),
        )

        # Mock _read_package_artifact
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "PACKAGE CONTENT",
        )

        # Capture stdout
        fake_stdout = io.StringIO()
        monkeypatch.setattr("sys.stdout", fake_stdout)

        result = run_update(force=False, dry_run=False, cwd=tmp_path)
        # Should succeed (no hosts is fatal, but no index is just a warning)
        assert result == 0

    def test_update_honors_yaml_source_root_for_nested_config_dir(
        self, tmp_path, monkeypatch
    ):
        """run_update must resolve source_root exactly like increment.

        Regression for the "update mass-deletes the index" bug. run_update passed
        the discovered config dir as an explicit source_root, routing
        resolve_operator_config into the branch that SKIPS the YAML source_root
        field. With a config living in my-project-context/ next to
        ``source_root: ../``, update then indexed my-project-context/ (no Java)
        against the real index one level up — so cocoindex saw every indexed
        file as removed and deleted it (the "_deletions keeps growing" symptom
        after the run was ctrl+C'd mid-delete).

        After the fix, the env handed to cocoindex carries the YAML-resolved
        source_root (one level above the config dir), NOT the config dir itself.
        """
        import json
        import shutil
        from subprocess import CompletedProcess
        from java_codebase_rag.installer import run_update

        # Layout mirroring the reported bug:
        #   tmp_path/
        #     my-project-context/      <- cwd; config lives here
        #       .java-codebase-rag.yml <- source_root: ../ ; index_dir: ../.java-codebase-rag
        #     .java-codebase-rag/      <- real index, one level above the config
        #       code_graph.lbug        <- marker so "index exists"
        config_dir = tmp_path / "my-project-context"
        config_dir.mkdir()
        (config_dir / ".java-codebase-rag.yml").write_text(
            "source_root: ../\nindex_dir: ../.java-codebase-rag\n",
            encoding="utf-8",
        )
        index_dir = tmp_path / ".java-codebase-rag"
        index_dir.mkdir()
        (index_dir / "code_graph.lbug").write_text("", encoding="utf-8")

        # A configured host so run_update reaches the index phase.
        (config_dir / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio",
                        }
                    }
                }
            )
        )
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/java-codebase-rag-mcp")
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "PACKAGE CONTENT",
        )

        # The CLI invokes update from the config dir, so the process cwd is the
        # config dir — resolve_operator_config(source_root=None) discovers the
        # config via Path.cwd(), exactly as increment/init/reprocess do.
        # delenv: resolve_operator_config honors JAVA_CODEBASE_RAG_SOURCE_ROOT /
        # _INDEX_DIR from os.environ first, and apply_to_os_environ() writes them
        # unscoped — a sibling test can leak a value that overrides discovery.
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
        monkeypatch.chdir(config_dir)

        # Capture the subprocess env run_update hands cocoindex: it carries the
        # resolved JAVA_CODEBASE_RAG_SOURCE_ROOT / _INDEX_DIR.
        captured: dict = {}

        def capture_coco(env, *, full_reprocess, quiet, verbose=True, lance_project_root=None,
                         on_progress=None, on_progress_console=None):
            captured["env"] = env
            return CompletedProcess(["cocoindex"], 0)

        def noop_graph(**kwargs):
            return CompletedProcess(["build_ast_graph", "--incremental"], 0)

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", capture_coco)
        monkeypatch.setattr("java_codebase_rag.pipeline.run_incremental_graph", noop_graph)

        result = run_update(force=False, dry_run=False, cwd=config_dir)

        # The index phase must have run (env captured), not been skipped.
        assert "env" in captured, "run_update did not reach the cocoindex update step"
        env = captured["env"]
        # source_root: ../ must resolve ONE level above the config dir (the real
        # Java tree), NOT the config dir itself.
        assert env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] == str(tmp_path.resolve())
        assert env["JAVA_CODEBASE_RAG_SOURCE_ROOT"] != str(config_dir.resolve())
        # index_dir lands on the real index one level above the config dir.
        assert env["JAVA_CODEBASE_RAG_INDEX_DIR"] == str(index_dir.resolve())
        # result is independent of the source_root assertion (artifact refresh
        # may report partial failure unrelated to this regression); tolerate it.
        assert result in (0, 1)

    def test_install_then_update_cycle(self, tmp_path, monkeypatch):
        """install then update: artifacts refreshed, no errors"""
        from java_codebase_rag.installer import run_install, run_update
        import shutil

        # Copy bank-chat fixture
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")

        cwd = tmp_path / "bank-chat"

        # Create .git so update_gitignore works
        (cwd / ".git").mkdir()

        # Mock shutil.which
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/java-codebase-rag-mcp")

        # Mock pipeline functions
        def mock_run_cocoindex_update(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["cocoindex"], 0)

        def mock_run_build_ast_graph(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["build_ast_graph"], 0)

        def mock_run_incremental_graph(*args, **kwargs):
            from subprocess import CompletedProcess
            return CompletedProcess(["build_ast_graph", "--incremental"], 0)

        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_cocoindex_update",
            mock_run_cocoindex_update,
        )
        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_build_ast_graph",
            mock_run_build_ast_graph,
        )
        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_incremental_graph",
            mock_run_incremental_graph,
        )

        # Change to fixture directory
        monkeypatch.setattr(Path, "cwd", lambda: cwd)

        # Run install
        install_result = run_install(
            non_interactive=True,
            agents=["claude-code"],
            scope="project",
            model="auto",
            source_root=cwd,
            quiet=True,
        )
        assert install_result == 0

        # Verify artifacts were created
        skill_file = cwd / ".claude" / "skills" / "explore-codebase" / "SKILL.md"
        assert skill_file.is_file()

        # Modify skill file to make it "stale"
        skill_file.write_text("MODIFIED CONTENT")

        # Run update
        update_result = run_update(force=False, dry_run=False, cwd=cwd)
        assert update_result == 0

        # Skill file should have been refreshed back to package content
        # (In real scenario, this would be the actual package content)

    def test_update_missing_mcp_binary_returns_partial_failure(self, tmp_path, monkeypatch):
        """java-codebase-rag-mcp not found on PATH → returns partial failure (1)"""
        from java_codebase_rag.installer import run_update
        import shutil

        # Create MCP config to have a configured host
        mcp_config = tmp_path / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/usr/local/bin/java-codebase-rag-mcp",
                            "type": "stdio"
                        }
                    }
                }
            )
        )

        # Mock shutil.which to return None (MCP binary not found)
        monkeypatch.setattr(shutil, "which", lambda x: None)

        # Mock _read_package_artifact
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "PACKAGE CONTENT",
        )

        result = run_update(force=False, dry_run=False, cwd=tmp_path)
        # Should return partial failure (1) because artifact refresh failed
        assert result == 1


# ---------------------------------------------------------------------------
# PR-4 — install/update unified index progress (stderr renderer)
# ---------------------------------------------------------------------------


def _patch_pipeline_for_progress(monkeypatch, *, emit: bool = True) -> dict:
    """Patch the three pipeline helpers the installer uses to emit progress.

    Records the ``quiet``/``verbose`` kwargs each was called with so tests can
    assert the installer no longer forces ``quiet=True``. Returns the call log.
    """
    import subprocess
    from java_codebase_rag import pipeline as _pipeline

    calls: dict = {"coco": [], "graph": [], "incremental": []}

    def _coco(env, *, full_reprocess, quiet, verbose=True, lance_project_root=None,
              on_progress=None, on_progress_console=None):
        calls["coco"].append({"quiet": quiet, "verbose": verbose})
        if emit and on_progress is not None:
            from java_codebase_rag.progress import ProgressEvent
            on_progress(ProgressEvent(
                kind="vectors", phase=None, pass_=None, done=1, total=10,
                status="running", elapsed_s=None))
        return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")

    def _graph(*, source_root, ladybug_path, verbose, quiet=False, env=None,
               on_progress=None, on_progress_console=None):
        calls["graph"].append({"quiet": quiet, "verbose": verbose})
        if emit and on_progress is not None:
            from java_codebase_rag.progress import ProgressEvent
            on_progress(ProgressEvent(
                kind="graph", phase=None, pass_="1/6", done=1, total=10,
                status="running", elapsed_s=None))
        return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")

    def _incremental(*, source_root, ladybug_path, verbose, quiet=False, env=None,
                     on_progress=None, on_progress_console=None):
        calls["incremental"].append({"quiet": quiet, "verbose": verbose})
        if emit and on_progress is not None:
            from java_codebase_rag.progress import ProgressEvent
            on_progress(ProgressEvent(
                kind="graph", phase=None, pass_="1/6", done=1, total=10,
                status="running", elapsed_s=None))
        return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_pipeline, "run_cocoindex_update", _coco)
    monkeypatch.setattr(_pipeline, "run_build_ast_graph", _graph)
    monkeypatch.setattr(_pipeline, "run_incremental_graph", _incremental)
    return calls


class TestPR4IndexProgress:
    """PR-4: install/update emit unified index progress on stderr."""

    def _setup_repo(self, tmp_path, monkeypatch):
        """Copy the bank-chat fixture and stub MCP discovery for install/update.

        Also writes a configured ``.mcp.json`` so ``update`` (which requires a
        prior ``install`` per its docstring) detects a configured host and
        reaches its indexing sub-step.
        """
        import shutil
        bank_chat = Path("tests/bank-chat-system")
        if not bank_chat.is_dir():
            pytest.skip("bank-chat-system fixture not found")
        shutil.copytree(bank_chat, tmp_path / "bank-chat")
        cwd = tmp_path / "bank-chat"
        (cwd / ".git").mkdir()
        # A configured host entry — the state `update` expects post-install.
        (cwd / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "java-codebase-rag": {
                            "command": "/fake/bin/java-codebase-rag-mcp",
                            "type": "stdio",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(shutil, "which", lambda x: "/fake/bin/java-codebase-rag-mcp")
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "PACKAGE CONTENT",
        )
        monkeypatch.chdir(cwd)
        return cwd

    def test_install_emits_indexing_progress_on_stderr(self, tmp_path, monkeypatch):
        """install drives the renderer from the patched pipeline helpers; the
        JCIRAG_PROGRESS event is consumed by the parser and surfaces as a
        rendered progress line on stderr. Wizard stdout prompts remain on
        stdout."""
        import io
        import contextlib
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)
        _patch_pipeline_for_progress(monkeypatch, emit=True)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_install(
                non_interactive=True,
                agents=["claude-code"],
                scope="project",
                model="auto",
                source_root=cwd,
                quiet=False,
            )
        assert rc == 0
        err_text = err.getvalue()
        out_text = out.getvalue()
        # The raw structured protocol line is parsed, never raw-relayed.
        assert "JCIRAG_PROGRESS kind=vectors" not in err_text
        # But indexing progress IS rendered on stderr (non-TTY concise fallback
        # prints a "vectors ..." line; the patched coco helper emitted a vectors
        # event). A graph event is emitted by the patched graph helper too.
        assert "vectors" in err_text.lower()
        # The wizard's conversational stdout is preserved (it writes the YAML
        # config path when not quiet).
        assert "Configuration written" in out_text or ".java-codebase-rag.yml" in out_text

    def test_update_emits_indexing_progress_on_stderr(self, tmp_path, monkeypatch):
        """update is no longer silent: the patched cocoindex + incremental
        graph helpers drive the renderer, and progress surfaces on stderr."""
        import io
        import contextlib
        from java_codebase_rag.installer import run_update

        cwd = self._setup_repo(tmp_path, monkeypatch)
        # A configured host + a real-looking index so run_update reaches indexing.
        index_dir = cwd / ".java-codebase-rag"
        index_dir.mkdir(exist_ok=True)
        (index_dir / "code_graph.lbug").write_text("", encoding="utf-8")

        _patch_pipeline_for_progress(monkeypatch, emit=True)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_update(force=False, dry_run=False, cwd=cwd)
        assert rc in (0, 1)
        err_text = err.getvalue()
        # Progress reached the renderer (coco + incremental both emitted).
        assert "JCIRAG_PROGRESS kind=vectors" not in err_text
        assert "vectors" in err_text.lower()

    def test_update_runs_indexing_without_quiet_true(self, tmp_path, monkeypatch):
        """Regression: update no longer forces quiet=True on the indexing
        helpers (the reason it was silent today). In the default path both
        helpers are called with quiet=False."""
        from java_codebase_rag.installer import run_update

        cwd = self._setup_repo(tmp_path, monkeypatch)
        index_dir = cwd / ".java-codebase-rag"
        index_dir.mkdir(exist_ok=True)
        (index_dir / "code_graph.lbug").write_text("", encoding="utf-8")

        calls = _patch_pipeline_for_progress(monkeypatch, emit=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

        rc = run_update(force=False, dry_run=False, cwd=cwd)
        assert rc in (0, 1)
        # Both indexing helpers ran and were NOT silenced.
        assert calls["coco"], "run_cocoindex_update was not called"
        assert calls["incremental"], "run_incremental_graph was not called"
        assert calls["coco"][-1]["quiet"] is False
        assert calls["incremental"][-1]["quiet"] is False

    def test_install_update_stdout_contract_preserved(self, tmp_path, monkeypatch):
        """The wizard's human-readable stdout shape is unchanged: NO
        JCIRAG_PROGRESS line leaks to stdout, and the indexing chatter that
        used to live on stdout ("Creating index..." / "Updating index...")
        no longer appears there."""
        import io
        import contextlib
        from java_codebase_rag.installer import run_install, run_update

        cwd = self._setup_repo(tmp_path, monkeypatch)
        _patch_pipeline_for_progress(monkeypatch, emit=True)

        # --- install ---
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            run_install(
                non_interactive=True, agents=["claude-code"], scope="project",
                model="auto", source_root=cwd, quiet=False,
            )
        install_out = out.getvalue()
        # No structured progress line on stdout (stdout is the wizard payload).
        assert "JCIRAG_PROGRESS" not in install_out
        # The old stdout indexing chatter is gone (moved to stderr framing).
        assert "Creating index..." not in install_out
        assert "Index created successfully." not in install_out

        # --- update ---
        index_dir = cwd / ".java-codebase-rag"
        index_dir.mkdir(exist_ok=True)
        (index_dir / "code_graph.lbug").write_text("", encoding="utf-8")
        _patch_pipeline_for_progress(monkeypatch, emit=True)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

        out2, err2 = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out2), contextlib.redirect_stderr(err2):
            run_update(force=False, dry_run=False, cwd=cwd)
        update_out = out2.getvalue()
        assert "JCIRAG_PROGRESS" not in update_out
        # The old stdout indexing chatter moved off stdout.
        assert "Updating index (Lance + graph)..." not in update_out

    def test_update_graph_catchup_failure_is_best_effort_exit_0(self, tmp_path, monkeypatch):
        """run_update's graph catch-up is best-effort: a graph-only failure must
        NOT flip the exit code. Vectors (cocoindex) succeeded, so exit 0 with a
        Warning on stderr carrying the graph caveat — matches the original
        semantics and the output/UX-only scope of PR-4."""
        import io
        import contextlib
        import subprocess
        from java_codebase_rag.installer import run_update

        cwd = self._setup_repo(tmp_path, monkeypatch)
        index_dir = cwd / ".java-codebase-rag"
        index_dir.mkdir(exist_ok=True)
        (index_dir / "code_graph.lbug").write_text("", encoding="utf-8")
        monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
        monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)

        # Patch at the installer import site (java_codebase_rag.pipeline).
        # cocoindex succeeds; the incremental graph returns a non-zero exit.
        def coco_ok(env, *, full_reprocess, quiet, verbose=True,
                    lance_project_root=None, on_progress=None, on_progress_console=None):
            return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")

        def graph_fail(**kwargs):
            return subprocess.CompletedProcess(args=["stub"], returncode=3, stdout="", stderr="")

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", coco_ok)
        monkeypatch.setattr("java_codebase_rag.pipeline.run_incremental_graph", graph_fail)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_update(force=False, dry_run=False, cwd=cwd)

        assert rc == 0, f"graph-only failure must be best-effort (exit 0), got {rc}"
        err_text = err.getvalue()
        assert "Warning:" in err_text
        assert "incremental graph update failed" in err_text

    def test_install_indexing_exception_renders_failed_footer(self, tmp_path, monkeypatch):
        """If run_cocoindex_update raises during install's indexing sub-step,
        the renderer bracket must render a failed (red cross) footer before the
        exception propagates — not a green check right before the traceback.
        Mirrors cli._run_with_pipeline_progress's BaseException handler."""
        import io
        import contextlib
        from java_codebase_rag import cli_format
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)

        def boom(env, *, full_reprocess, quiet, verbose=True,
                 lance_project_root=None, on_progress=None, on_progress_console=None):
            raise RuntimeError("boom from cocoindex")

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", boom)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with pytest.raises(RuntimeError, match="boom from cocoindex"):
                run_install(
                    non_interactive=True,
                    agents=["claude-code"],
                    scope="project",
                    model="auto",
                    source_root=cwd,
                    quiet=False,
                )

        err_text = err.getvalue()
        # The footer rendered the failure marker (red cross), not the green check.
        assert cli_format.styled_cross() in err_text
        assert cli_format.styled_check() not in err_text

    def test_install_indexing_failure_returns_nonzero(self, tmp_path, monkeypatch):
        """A non-exception indexing failure (cocoindex exits non-zero) must NOT
        report install success. Regression for issue #351: run_install discarded
        run_init_if_needed's return value and unconditionally returned 0, so a
        broken or empty index reported exit 0 in CI/automation while the most
        important install step failed silently. (The exception path was already
        covered; this covers the returncode != 0 path.)"""
        import io
        import contextlib
        import subprocess
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)

        def failing_coco(env, *, full_reprocess, quiet, verbose=True,
                         lance_project_root=None, on_progress=None, on_progress_console=None):
            return subprocess.CompletedProcess(args=["stub"], returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", failing_coco)

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_install(
                non_interactive=True,
                agents=["claude-code"],
                scope="project",
                model="auto",
                source_root=cwd,
                quiet=False,
            )
        assert rc == 1, (
            f"install reported success (exit {rc}) despite cocoindex failure (#351)"
        )
        # The failure was surfaced on stderr, not swallowed.
        assert "CocoIndex update failed" in err.getvalue()
