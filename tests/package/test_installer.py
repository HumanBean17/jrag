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


class TestDetectJavaLayout:
    """Test detect_java_layout (single-module / sibling-modules / multi-system)."""

    def test_detect_layout_root_has_maven_pom(self, tmp_path):
        """source_root with pom.xml → single-module layout"""
        (tmp_path / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import (
            detect_java_layout,
            JavaDetection,
            LAYOUT_SINGLE_MODULE,
        )
        result = detect_java_layout(tmp_path)
        assert result == JavaDetection(
            kind=LAYOUT_SINGLE_MODULE, roots=[Path(".")], system_dirs=[]
        )

    def test_detect_layout_root_has_gradle_build(self, tmp_path):
        """source_root with build.gradle → single-module layout"""
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }")
        from java_codebase_rag.installer import (
            detect_java_layout,
            JavaDetection,
            LAYOUT_SINGLE_MODULE,
        )
        result = detect_java_layout(tmp_path)
        assert result == JavaDetection(
            kind=LAYOUT_SINGLE_MODULE, roots=[Path(".")], system_dirs=[]
        )

    def test_detect_layout_root_has_gradle_kts(self, tmp_path):
        """source_root with build.gradle.kts → single-module layout"""
        (tmp_path / "build.gradle.kts").write_text("plugins { java }")
        from java_codebase_rag.installer import (
            detect_java_layout,
            JavaDetection,
            LAYOUT_SINGLE_MODULE,
        )
        result = detect_java_layout(tmp_path)
        assert result == JavaDetection(
            kind=LAYOUT_SINGLE_MODULE, roots=[Path(".")], system_dirs=[]
        )

    def test_detect_layout_sibling_modules_monorepo(self, tmp_path):
        """no root marker; service-a/pom.xml + service-b/pom.xml → sibling_modules"""
        service_a = tmp_path / "service-a"
        service_b = tmp_path / "service-b"
        service_a.mkdir()
        service_b.mkdir()
        (service_a / "pom.xml").write_text("<project></project>")
        (service_b / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import (
            detect_java_layout,
            LAYOUT_SIBLING_MODULES,
        )
        result = detect_java_layout(tmp_path)
        assert result.kind == LAYOUT_SIBLING_MODULES
        assert set(result.roots) == {Path("service-a"), Path("service-b")}
        assert result.system_dirs == []

    def test_detect_layout_sibling_modules_single_service(self, tmp_path):
        """no root marker; only service-a/pom.xml → sibling_modules, [service-a]"""
        service_a = tmp_path / "service-a"
        service_a.mkdir()
        (service_a / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import (
            detect_java_layout,
            LAYOUT_SIBLING_MODULES,
        )
        result = detect_java_layout(tmp_path)
        assert result.kind == LAYOUT_SIBLING_MODULES
        assert result.roots == [Path("service-a")]
        assert result.system_dirs == []

    def test_detect_layout_no_java_raises_exit_2(self, tmp_path, capsys):
        """no build files anywhere under source_root → SystemExit(2)"""
        from java_codebase_rag.installer import detect_java_layout
        with pytest.raises(SystemExit) as exc_info:
            detect_java_layout(tmp_path)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "Error:" in captured.out
        assert "No Java build files" in captured.out

    # --- Multi-system parent layout ---

    def test_detect_layout_multi_system_happy_path(self, tmp_path):
        """SystemA/microservice-1/pom.xml + SystemB/microservice-2/pom.xml → multi_system"""
        sys_a = tmp_path / "SystemA" / "microservice-1"
        sys_b = tmp_path / "SystemB" / "microservice-2"
        sys_a.mkdir(parents=True)
        sys_b.mkdir(parents=True)
        (sys_a / "pom.xml").write_text("<project></project>")
        (sys_b / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import (
            detect_java_layout,
            LAYOUT_MULTI_SYSTEM,
        )
        result = detect_java_layout(tmp_path)
        assert result.kind == LAYOUT_MULTI_SYSTEM
        assert result.roots == [Path(".")]
        assert set(result.system_dirs) == {"SystemA", "SystemB"}

    def test_detect_layout_multi_system_leaf_rule(self, tmp_path):
        """microservice-1/pom.xml makes microservice-1 a leaf; nested sub-mod/pom.xml not counted"""
        leaf = tmp_path / "SystemA" / "microservice-1"
        leaf.mkdir(parents=True)
        (leaf / "pom.xml").write_text("<project></project>")
        (leaf / "sub-mod").mkdir()
        (leaf / "sub-mod" / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import detect_java_layout
        result = detect_java_layout(tmp_path)
        assert set(result.system_dirs) == {"SystemA"}

    def test_detect_layout_multi_system_prunes_node_modules(self, tmp_path):
        """node_modules subtree pruned via UNCONDITIONAL_PRUNE_DIRS"""
        sys_a = tmp_path / "SystemA" / "microservice-1"
        sys_a.mkdir(parents=True)
        (sys_a / "pom.xml").write_text("<project></project>")
        evil = tmp_path / "node_modules" / "evil"
        evil.mkdir(parents=True)
        (evil / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import detect_java_layout
        result = detect_java_layout(tmp_path)
        assert set(result.system_dirs) == {"SystemA"}

    def test_detect_layout_multi_system_recognizes_build_sbt(self, tmp_path):
        """build.sbt is a recognized marker (BUILD_FILES includes build.sbt)"""
        sys_a = tmp_path / "SystemA" / "microservice-1"
        sys_a.mkdir(parents=True)
        (sys_a / "build.sbt").write_text('name := "microservice-1"')
        from java_codebase_rag.installer import (
            detect_java_layout,
            LAYOUT_MULTI_SYSTEM,
        )
        result = detect_java_layout(tmp_path)
        assert result.kind == LAYOUT_MULTI_SYSTEM
        assert set(result.system_dirs) == {"SystemA"}

    def test_detect_layout_multi_system_prunes_build_output_dir(self, tmp_path):
        """stale target/pom.xml under a real module is pruned via the
        build-output rule (_is_build_output_dir / prune_walk_dirnames).

        The target/ marker is pruned because its parent microservice-1 holds a
        build-tool indicator (pom.xml). Routing through prune_walk_dirnames (Fix 2)
        also exercises the shared helper.
        """
        mod = tmp_path / "SystemA" / "microservice-1"
        mod.mkdir(parents=True)
        (mod / "pom.xml").write_text("<project></project>")
        # Stale build-output marker inside microservice-1/target/.
        (mod / "target").mkdir()
        (mod / "target" / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import (
            detect_java_layout,
            LAYOUT_MULTI_SYSTEM,
        )
        result = detect_java_layout(tmp_path)
        assert result.kind == LAYOUT_MULTI_SYSTEM
        assert set(result.system_dirs) == {"SystemA"}

    def test_detect_layout_sibling_beats_multi_system_precedence(self, tmp_path):
        """immediate-child marker (service-a/pom.xml) wins over a deeper
        marker (SystemB/microservice-2/pom.xml) — sibling-modules precedence."""
        service_a = tmp_path / "service-a"
        service_a.mkdir()
        (service_a / "pom.xml").write_text("<project></project>")
        sys_b = tmp_path / "SystemB" / "microservice-2"
        sys_b.mkdir(parents=True)
        (sys_b / "pom.xml").write_text("<project></project>")
        from java_codebase_rag.installer import (
            detect_java_layout,
            LAYOUT_SIBLING_MODULES,
        )
        result = detect_java_layout(tmp_path)
        assert result.kind == LAYOUT_SIBLING_MODULES
        assert result.roots == [Path("service-a")]
        assert result.system_dirs == []

    def test_detect_layout_no_java_exit_2_via_multi_system_descent(
        self, tmp_path, capsys
    ):
        """only a pruned marker (node_modules/evil/pom.xml) → descent runs,
        prunes node_modules, finds nothing usable, falls through to exit 2.

        Proves the multi-system descent runs (not a false-positive detection),
        prunes correctly, and reaches the no-Java exit-2 path.
        """
        from java_codebase_rag.installer import detect_java_layout

        evil = tmp_path / "node_modules" / "evil"
        evil.mkdir(parents=True)
        (evil / "pom.xml").write_text("<project></project>")
        with pytest.raises(SystemExit) as exc_info:
            detect_java_layout(tmp_path)
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "No Java build files" in captured.out
        assert "subtree" in captured.out


class TestMultiSystemSummary:
    """Test _multi_system_summary helper."""

    def test_multi_system_summary_format(self):
        from java_codebase_rag.installer import _multi_system_summary
        result = _multi_system_summary(["SystemA", "SystemB"])
        assert "Multi-system workspace" in result
        assert "SystemA/" in result
        assert "SystemB/" in result
        assert "Indexing all as one merged index" in result
        # Not a str(list) repr — a single formatted string.
        assert "['SystemA'" not in result


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


class TestSessionStartHook:
    """Test hooks_settings_path / merge_session_start_hook / _remove_session_start_hook."""

    def test_session_start_hook_settings_path_resolves_per_host(self, tmp_path, monkeypatch):
        """settings.json lives in host.scope_path for all three hosts"""
        from java_codebase_rag.installer import HOSTS, hooks_settings_path
        monkeypatch.setenv("HOME", str(tmp_path))
        proj = tmp_path / "proj"
        assert (
            hooks_settings_path(HOSTS["claude-code"], "project", proj)
            == proj / ".claude" / "settings.json"
        )
        assert (
            hooks_settings_path(HOSTS["qwen-code"], "project", proj)
            == proj / ".qwen" / "settings.json"
        )
        # user scope resolves under $HOME, not the project
        assert (
            hooks_settings_path(HOSTS["gigacode"], "user", proj)
            == tmp_path / ".gigacode" / "settings.json"
        )

    def test_session_start_hook_merge_into_empty_settings(self, tmp_path):
        """no file → creates hooks.SessionStart with our command; re-merge is a no-op"""
        from java_codebase_rag.installer import merge_session_start_hook
        config_path = tmp_path / "settings.json"
        command = "/bin/jrag prime --hook-json"
        result = merge_session_start_hook(config_path, hook_command=command)
        assert result is True
        with open(config_path) as f:
            config = json.load(f)
        assert config == {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [{"type": "command", "command": command}]}
                ]
            }
        }
        # identical second call → False, file bytes untouched
        before = config_path.read_bytes()
        assert merge_session_start_hook(config_path, hook_command=command) is False
        assert config_path.read_bytes() == before

    def test_session_start_hook_merge_preserves_siblings_and_other_hooks(self, tmp_path):
        """existing security/$version/other matchers/other events survive the merge"""
        from java_codebase_rag.installer import merge_session_start_hook
        config_path = tmp_path / "settings.json"
        seeded = {
            "security": {"level": "high"},
            "$version": 5,
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [{"type": "command", "command": "echo hi"}],
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "lint"}],
                    }
                ],
            },
        }
        config_path.write_text(json.dumps(seeded))
        command = "/new/jrag prime --hook-json"
        assert merge_session_start_hook(config_path, hook_command=command) is True
        with open(config_path) as f:
            config = json.load(f)
        assert config["security"] == {"level": "high"}
        assert config["$version"] == 5
        assert config["hooks"]["PreToolUse"] == seeded["hooks"]["PreToolUse"]
        session_start = config["hooks"]["SessionStart"]
        # the foreign matcher stays first and untouched
        assert session_start[0] == seeded["hooks"]["SessionStart"][0]
        ours = [m for m in session_start if isinstance(m, dict) and m.get("matcher") == ""]
        assert len(ours) == 1
        assert ours[0]["hooks"] == [{"type": "command", "command": command}]

    def test_session_start_hook_merge_replaces_stale_command(self, tmp_path):
        """an older jrag prime entry is replaced in place, never duplicated"""
        from java_codebase_rag.installer import merge_session_start_hook
        config_path = tmp_path / "settings.json"
        config_path.write_text(
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
            )
        )
        command = "/new/jrag prime --hook-json"
        assert merge_session_start_hook(config_path, hook_command=command) is True
        with open(config_path) as f:
            config = json.load(f)
        commands = [
            e["command"]
            for m in config["hooks"]["SessionStart"]
            for e in m["hooks"]
            if " prime --hook-json" in e["command"]
        ]
        assert commands == [command]

    def test_session_start_hook_merge_replaces_legacy_binary_entry(self, tmp_path):
        """an entry installed under the legacy ``java-codebase-rag`` console
        script is ours too — replaced in place, never duplicated"""
        from java_codebase_rag.installer import merge_session_start_hook
        config_path = tmp_path / "settings.json"
        config_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            "/old/venv/bin/java-codebase-rag"
                                            " prime --hook-json"
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        command = "/new/jrag prime --hook-json"
        assert merge_session_start_hook(config_path, hook_command=command) is True
        with open(config_path) as f:
            config = json.load(f)
        entries = [
            e for m in config["hooks"]["SessionStart"] for e in m["hooks"]
        ]
        assert entries == [{"type": "command", "command": command}]

    def test_session_start_hook_remove_legacy_binary_entry(self, tmp_path):
        """teardown also recognizes the legacy-binary entry — it must not
        survive an uninstalled hook"""
        from java_codebase_rag.installer import _remove_session_start_hook
        config_path = tmp_path / "settings.json"
        config_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": (
                                            "/usr/local/bin/java-codebase-rag"
                                            " prime --hook-json"
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        assert _remove_session_start_hook(config_path) is True
        # ours was the only tenant → matcher, SessionStart and hooks all pruned
        assert json.loads(config_path.read_text()) == {}

    def test_session_start_hook_merge_invalid_json_raises(self, tmp_path):
        """malformed settings.json → ValueError"""
        from java_codebase_rag.installer import merge_session_start_hook
        config_path = tmp_path / "settings.json"
        config_path.write_text("{invalid json!!!")
        with pytest.raises(ValueError, match="Failed to parse"):
            merge_session_start_hook(config_path, hook_command="/bin/jrag prime")

    def test_session_start_hook_remove(self, tmp_path):
        """remove drops only our entry, prunes emptied containers, honors dry_run"""
        from java_codebase_rag.installer import _remove_session_start_hook
        seeded = {
            "security": {"level": "high"},
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [{"type": "command", "command": "echo hi"}],
                    },
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "/bin/jrag prime --hook-json"}
                        ],
                    },
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "lint"}],
                    }
                ],
            },
        }
        config_path = tmp_path / "settings.json"
        config_path.write_text(json.dumps(seeded))
        assert _remove_session_start_hook(config_path) is True
        with open(config_path) as f:
            config = json.load(f)
        assert config["security"] == {"level": "high"}
        assert config["hooks"]["PreToolUse"] == seeded["hooks"]["PreToolUse"]
        assert config["hooks"]["SessionStart"] == seeded["hooks"]["SessionStart"][:1]
        # nothing left to remove → False, file bytes untouched
        before = config_path.read_bytes()
        assert _remove_session_start_hook(config_path) is False
        assert config_path.read_bytes() == before

        # ours was the only tenant → matcher, SessionStart and hooks all pruned
        lone_path = tmp_path / "lone.json"
        lone_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "/bin/jrag prime --hook-json"}
                                ],
                            }
                        ]
                    }
                }
            )
        )
        assert _remove_session_start_hook(lone_path) is True
        assert json.loads(lone_path.read_text()) == {}

        # dry_run reports the removal but writes nothing
        dry_path = tmp_path / "dry.json"
        dry_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "/bin/jrag prime --hook-json"}
                                ],
                            }
                        ]
                    }
                }
            )
        )
        dry_before = dry_path.read_bytes()
        assert _remove_session_start_hook(dry_path, dry_run=True) is True
        assert dry_path.read_bytes() == dry_before

        # missing file → no-op success
        assert _remove_session_start_hook(tmp_path / "absent.json") is False


class TestDeployArtifacts:
    """Test deploy_artifacts function."""

    def test_permission_error_skips_artifact_continues(self, tmp_path, monkeypatch):
        """unwritable host dir → that host's hook fails, the next host continues"""
        from java_codebase_rag.installer import deploy_artifacts, HOSTS

        # claude-code's settings dir unwritable; qwen-code's is fine.
        def mock_is_writable(path):
            return Path(path).name != ".claude"

        monkeypatch.setattr("java_codebase_rag.installer._is_writable", mock_is_writable)

        results = deploy_artifacts(
            [HOSTS["claude-code"], HOSTS["qwen-code"]],
            "project",
            tmp_path,
            non_interactive=True,
            mcp_command="/fake/bin/jrag",
            surface="cli",
        )

        # One row per host (the SessionStart hook).
        assert len(results) == 2
        # claude-code failed on permissions
        assert results[0].success is False
        assert "not writable" in results[0].error
        # qwen-code still deployed — one bad host doesn't stop the loop
        assert results[1].success is True

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

        # No surface ships package files, so the package artifact this used to
        # read from install_data is supplied directly.
        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "new content",
        )

        result = _deploy_file(
            skill_file,
            "skills/explore-codebase/SKILL.md",
            artifact_type="skill",
            non_interactive=False,
        )

        assert result.success is False
        assert "Skipped by user" in result.error

    def test_deploy_artifacts_multi_host_deploy_all(self, tmp_path, monkeypatch):
        """multiple hosts selected → the MCP entry is deployed to all"""
        from java_codebase_rag.installer import deploy_artifacts, HOSTS

        results = deploy_artifacts(
            [HOSTS["claude-code"], HOSTS["qwen-code"]],
            "project",
            tmp_path,
            non_interactive=True,
            mcp_command="/bin/mcp",
        )

        # One result per host (the MCP entry — the surface ships nothing else).
        assert len(results) == 2
        # All should succeed
        assert all(r.success for r in results)

        # Both hosts carry our MCP entry.
        assert "java-codebase-rag" in json.loads((tmp_path / ".mcp.json").read_text())[
            "mcpServers"
        ]
        qwen_cfg = json.loads((tmp_path / ".qwen" / "settings.json").read_text())
        assert "java-codebase-rag" in qwen_cfg["mcpServers"]
        # And no skill/agent files anywhere.
        assert not (tmp_path / ".claude" / "skills").exists()
        assert not (tmp_path / ".claude" / "agents").exists()
        assert not (tmp_path / ".qwen" / "skills").exists()
        assert not (tmp_path / ".qwen" / "agents").exists()


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

    @staticmethod
    def _skill_agent_files(host_dir: Path) -> dict:
        """Map of ``skills/`` + ``agents/`` files under a host dir -> bytes.

        Neither surface deploys files anymore, so this snapshot must be
        identical before and after an install/refresh. The bank-chat fixture
        ships legacy deployed artifacts, hence the byte comparison rather than
        "directory does not exist".
        """
        files: dict = {}
        for sub in ("skills", "agents"):
            subtree = host_dir / sub
            if not subtree.is_dir():
                continue
            for path in subtree.rglob("*"):
                if path.is_file():
                    files[str(path.relative_to(host_dir))] = path.read_bytes()
        return files

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
        legacy_claude_artifacts = self._skill_agent_files(cwd / ".claude")

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
            surface="mcp",
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

        # Tools only: the mcp surface ships no skill/agent artifacts. The
        # fixture carries legacy deployed files, so assert they were left
        # byte-identical (nothing written under skills/ or agents/).
        assert self._skill_agent_files(cwd / ".claude") == legacy_claude_artifacts

        # Verify .gitignore
        gitignore = cwd / ".gitignore"
        assert gitignore.is_file()
        gitignore_content = gitignore.read_text()
        assert ".java-codebase-rag/" in gitignore_content

    def test_install_non_interactive_multi_system_workspace(self, tmp_path, monkeypatch, capsys):
        """run install over a multi-system parent source-root (no microservice_roots).

        Layout (built directly in tmp_path, no committed fixture):
            SystemA/microservice-1/pom.xml + src/main/java/com/acme/Foo.java
            SystemB/microservice-2/pom.xml + src/main/java/com/acme/Bar.java
        detect_java_layout classifies this as multi_system; run_install prints the
        multi-system summary and indexes everything as one merged index (no
        microservice_roots key in the generated YAML).
        """
        import shutil
        import subprocess
        from java_codebase_rag.installer import run_install

        # Build the multi-system layout directly in tmp_path.
        for system, micro, java_cls in [
            ("SystemA", "microservice-1", "Foo"),
            ("SystemB", "microservice-2", "Bar"),
        ]:
            mod = tmp_path / system / micro
            (mod / "src" / "main" / "java" / "com" / "acme").mkdir(parents=True)
            (mod / "pom.xml").write_text("<project></project>")
            (mod / "src" / "main" / "java" / "com" / "acme" / f"{java_cls}.java").write_text(
                f"package com.acme; class {java_cls} {{}}"
            )

        # .git so update_gitignore runs.
        (tmp_path / ".git").mkdir()

        # Mock MCP binary discovery.
        monkeypatch.setattr(shutil, "which", lambda x: "/fake/bin/java-codebase-rag-mcp")

        # Mock the fresh-init indexing helpers (no pre-existing index → init path).
        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_cocoindex_update",
            lambda *a, **k: subprocess.CompletedProcess(args=["cocoindex"], returncode=0),
        )
        monkeypatch.setattr(
            "java_codebase_rag.pipeline.run_build_ast_graph",
            lambda *a, **k: subprocess.CompletedProcess(args=["build_ast_graph"], returncode=0),
        )

        # run_install resolves source_root from Path.cwd() when None; align cwd.
        monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

        result = run_install(
            non_interactive=True,
            agents=["claude-code"],
            scope="project",
            model="auto",
            surface="mcp",
            source_root=tmp_path,
            quiet=True,
        )

        # Exit success.
        assert result == 0

        # YAML parses and carries NO microservice_roots (multi-system = index all).
        import yaml
        yaml_path = tmp_path / ".java-codebase-rag.yml"
        assert yaml_path.is_file()
        config = yaml.safe_load(yaml_path.read_text())
        assert "microservice_roots" not in config

        # MCP config has the java-codebase-rag stdio entry.
        mcp_path = tmp_path / ".mcp.json"
        assert mcp_path.is_file()
        mcp_config = json.loads(mcp_path.read_text())
        assert "java-codebase-rag" in mcp_config.get("mcpServers", {})
        assert mcp_config["mcpServers"]["java-codebase-rag"]["type"] == "stdio"

        # Tools only: no skill/agent artifacts under tmp_path/.claude/.
        assert not (tmp_path / ".claude" / "skills").exists()
        assert not (tmp_path / ".claude" / "agents").exists()

        # Multi-system summary printed to stdout (quiet=True suppresses only stderr).
        captured = capsys.readouterr()
        assert "Multi-system workspace" in captured.out
        assert "SystemA/" in captured.out
        assert "SystemB/" in captured.out

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

        legacy_claude = self._skill_agent_files(cwd / ".claude")
        legacy_qwen = self._skill_agent_files(cwd / ".qwen")

        result = run_install(
            non_interactive=True,
            agents=["claude-code", "qwen-code"],
            scope="project",
            model="auto",
            surface="mcp",
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
        for path in (mcp_claude, mcp_qwen):
            assert "java-codebase-rag" in json.loads(path.read_text())["mcpServers"]

        # Tools only: no skill/agent artifacts written for either host.
        assert self._skill_agent_files(cwd / ".claude") == legacy_claude
        assert self._skill_agent_files(cwd / ".qwen") == legacy_qwen


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
        # PR-JRAG-5: detect_configured_hosts returns ConfiguredHost (3-field).
        # The legacy MCP-entry fallback path always carries surface="mcp".
        configured = detected[0]
        assert configured.host.name == "claude-code"
        assert configured.scope == "project"
        assert configured.surface == "mcp"

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
        # PR-JRAG-5: 3-field NamedTuple (legacy MCP-entry scan → surface="mcp").
        configured = detected[0]
        assert configured.host.name == "claude-code"
        assert configured.scope == "user"
        assert configured.surface == "mcp"

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

        # Sort by scope for consistent ordering (PR-JRAG-5: NamedTuple fields).
        detected_sorted = sorted(detected, key=lambda ch: ch.scope)

        # First should be project scope claude-code
        assert detected_sorted[0].host.name == "claude-code"
        assert detected_sorted[0].scope == "project"
        assert detected_sorted[0].surface == "mcp"

        # Second should be user scope qwen-code
        assert detected_sorted[1].host.name == "qwen-code"
        assert detected_sorted[1].scope == "user"
        assert detected_sorted[1].surface == "mcp"

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
    """Test refresh_artifacts function for PR-I2 (entry/hook refresh)."""

    def _write_hook(self, tmp_path, command):
        """Stage a claude-code settings.json holding our prime hook."""
        settings = tmp_path / ".claude" / "settings.json"
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

    def _session_start_commands(self, settings_path):
        config = json.loads(settings_path.read_text())
        matchers = config.get("hooks", {}).get("SessionStart", [])
        return [
            e["command"]
            for m in matchers
            if isinstance(m, dict) and m.get("matcher") == ""
            for e in m.get("hooks", [])
            if isinstance(e, dict)
        ]

    def test_refresh_hook_overwrites_stale(self, tmp_path, monkeypatch):
        """hook command differs from the resolved jrag path → rewritten"""
        import shutil
        from java_codebase_rag.installer import refresh_artifacts, HOSTS

        settings = self._write_hook(tmp_path, "/old/bin/jrag prime --hook-json")
        monkeypatch.setattr(shutil, "which", lambda x: "/new/bin/jrag")

        host = HOSTS["claude-code"]
        results = refresh_artifacts(
            host, "project", tmp_path, force=False, dry_run=False, surface="cli"
        )

        assert len(results) == 1 and results[0].success is True
        assert self._session_start_commands(settings) == ["/new/bin/jrag prime --hook-json"]

    def test_refresh_hook_skips_if_matching(self, tmp_path, monkeypatch):
        """hook command already current → left untouched"""
        import shutil
        from java_codebase_rag.installer import refresh_artifacts, HOSTS

        settings = self._write_hook(tmp_path, "/usr/local/bin/jrag prime --hook-json")
        before = settings.read_text()
        monkeypatch.setattr(shutil, "which", lambda x: "/usr/local/bin/jrag")

        host = HOSTS["claude-code"]
        results = refresh_artifacts(
            host, "project", tmp_path, force=False, dry_run=False, surface="cli"
        )

        assert len(results) == 1 and results[0].success is True
        # No change needed — the file is byte-identical.
        assert settings.read_text() == before

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
        from java_codebase_rag.installer import _refresh_file

        # _refresh_file is no longer reached through the manifest (no surface
        # ships files), so its dry-run contract is exercised directly.
        skills_dir = tmp_path / ".claude" / "skills" / "explore-codebase"
        skills_dir.mkdir(parents=True)
        skill_file = skills_dir / "SKILL.md"
        skill_file.write_text("STALE CONTENT")

        monkeypatch.setattr(
            "java_codebase_rag.installer._read_package_artifact",
            lambda path: "NEW CONTENT",
        )

        _refresh_file(
            skill_file,
            "skills/explore-codebase/SKILL.md",
            artifact_type="skill",
            force=False,
            dry_run=True,
        )

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
            surface="mcp",
            source_root=cwd,
            quiet=True,
        )
        assert install_result == 0

        # Verify the MCP entry was created
        mcp_path = cwd / ".mcp.json"
        assert "java-codebase-rag" in json.loads(mcp_path.read_text())["mcpServers"]

        # Make the entry "stale" (a moved/renamed install)
        config = json.loads(mcp_path.read_text())
        config["mcpServers"]["java-codebase-rag"]["command"] = "/old/bin/java-codebase-rag-mcp"
        mcp_path.write_text(json.dumps(config), encoding="utf-8")

        # Run update
        update_result = run_update(force=False, dry_run=False, cwd=cwd)
        assert update_result == 0

        # Entry command refreshed back to the resolved binary path
        refreshed = json.loads(mcp_path.read_text())
        assert (
            refreshed["mcpServers"]["java-codebase-rag"]["command"]
            == "/usr/local/bin/java-codebase-rag-mcp"
        )

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

    def test_install_emits_indexing_progress_on_stderr(self, tmp_path, monkeypatch, capfd):
        """install drives the renderer from the patched pipeline helpers; the
        JCIRAG_PROGRESS event is consumed by the parser and surfaces as a
        rendered progress line on stderr. Wizard stdout prompts remain on
        stdout.

        Captured at the fd level (``capfd``) because the progress renderer
        writes through a ``rich.Console(stderr=True)`` — fd 2 — which pytest's
        default fd capture redirects underneath ``contextlib.redirect_stderr``
        (the latter only patches the ``sys.stderr`` Python object, so the
        renderer's bytes bypass an ``io.StringIO`` under default capture)."""
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)
        _patch_pipeline_for_progress(monkeypatch, emit=True)

        capfd.readouterr()  # discard setup chatter
        rc = run_install(
            non_interactive=True,
            agents=["claude-code"],
            scope="project",
            model="auto",
            source_root=cwd,
            quiet=False,
        )
        assert rc == 0
        captured = capfd.readouterr()
        err_text = captured.err
        out_text = captured.out
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

    def test_install_update_stdout_contract_preserved(self, tmp_path, monkeypatch, capfd):
        """The wizard's human-readable stdout shape is unchanged: NO
        JCIRAG_PROGRESS line leaks to stdout, and the indexing chatter that
        used to live on stdout ("Creating index..." / "Updating index...")
        no longer appears there. Captured at fd level (see
        test_install_emits_indexing_progress_on_stderr for why)."""
        from java_codebase_rag.installer import run_install, run_update

        cwd = self._setup_repo(tmp_path, monkeypatch)
        _patch_pipeline_for_progress(monkeypatch, emit=True)

        # --- install ---
        capfd.readouterr()
        run_install(
            non_interactive=True, agents=["claude-code"], scope="project",
            model="auto", source_root=cwd, quiet=False,
        )
        install_out = capfd.readouterr().out
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

        capfd.readouterr()
        run_update(force=False, dry_run=False, cwd=cwd)
        update_out = capfd.readouterr().out
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

    def test_install_indexing_exception_renders_failed_footer(self, tmp_path, monkeypatch, capfd):
        """If run_cocoindex_update raises during install's indexing sub-step,
        the renderer bracket must render a failed (red cross) footer before the
        exception propagates — not a green check right before the traceback.
        Mirrors cli._run_with_pipeline_progress's BaseException handler. fd
        capture (capfd) — see test_install_emits_indexing_progress_on_stderr."""
        from java_codebase_rag import cli_format
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)

        def boom(env, *, full_reprocess, quiet, verbose=True,
                 lance_project_root=None, on_progress=None, on_progress_console=None):
            raise RuntimeError("boom from cocoindex")

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", boom)

        capfd.readouterr()
        with pytest.raises(RuntimeError, match="boom from cocoindex"):
            run_install(
                non_interactive=True,
                agents=["claude-code"],
                scope="project",
                model="auto",
                source_root=cwd,
                quiet=False,
            )

        err_text = capfd.readouterr().err
        # The footer rendered the failure marker (red cross), not the green check.
        assert cli_format.styled_cross() in err_text
        assert cli_format.styled_check() not in err_text

    def test_install_indexing_failure_returns_nonzero(self, tmp_path, monkeypatch, capfd):
        """A non-exception indexing failure (cocoindex exits non-zero) must NOT
        report install success. Regression for issue #351: run_install discarded
        run_init_if_needed's return value and unconditionally returned 0, so a
        broken or empty index reported exit 0 in CI/automation while the most
        important install step failed silently. (The exception path was already
        covered; this covers the returncode != 0 path.) fd capture (capfd) — see
        test_install_emits_indexing_progress_on_stderr."""
        import subprocess
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)

        def failing_coco(env, *, full_reprocess, quiet, verbose=True,
                         lance_project_root=None, on_progress=None, on_progress_console=None):
            return subprocess.CompletedProcess(args=["stub"], returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", failing_coco)

        capfd.readouterr()
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
        assert "CocoIndex update failed" in capfd.readouterr().err

    def test_install_over_existing_index_skips_init_and_exits_zero(self, tmp_path, monkeypatch, capfd):
        """A re-run of install over an existing index skips init (the index build)
        and still exits 0. Regression guard for the None branch of
        run_init_if_needed (issue #351): run_install uses ``if init_outcome is
        False: return 1`` precisely so a SKIP (None) stays exit 0 -- a future
        ``if not init_outcome`` simplification would collapse None into the
        failure branch and break idempotent re-runs in CI/automation. fd capture
        (capfd) — see test_install_emits_indexing_progress_on_stderr."""
        import subprocess
        from java_codebase_rag.installer import run_install

        cwd = self._setup_repo(tmp_path, monkeypatch)

        # Pre-create an existing index so index_dir_has_existing_artifacts is True
        # -> run_init_if_needed returns None (skip), not True/False.
        index_dir = cwd / ".java-codebase-rag"
        index_dir.mkdir()
        (index_dir / "code_graph.lbug").write_bytes(b"\x00" * 16)

        coco_called = []

        def coco_should_not_run(env, *, full_reprocess, quiet, verbose=True,
                                lance_project_root=None, on_progress=None, on_progress_console=None):
            coco_called.append(True)
            return subprocess.CompletedProcess(args=["stub"], returncode=0, stdout="", stderr="")

        monkeypatch.setattr("java_codebase_rag.pipeline.run_cocoindex_update", coco_should_not_run)

        capfd.readouterr()
        rc = run_install(
            non_interactive=True,
            agents=["claude-code"],
            scope="project",
            model="auto",
            source_root=cwd,
            quiet=False,
        )
        assert rc == 0, (
            f"install over an existing index should skip init and exit 0, got exit {rc} "
            "(#351 None branch: a skip must not be treated as failure)"
        )
        # init was genuinely skipped, not silently succeeded.
        assert not coco_called, "init should have been SKIPPED (index exists) but cocoindex ran"
        # run_init_if_needed prints the skip notice to stdout (no file=sys.stderr).
        assert "Index already exists" in capfd.readouterr().out
