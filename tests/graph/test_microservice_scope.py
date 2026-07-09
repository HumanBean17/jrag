"""Tests for microservice scope detection and ScopeManager."""

from java_codebase_rag.graph.graph_enrich import detect_microservice_from_path


class TestDetectMicroserviceFromPath:
    """Tests for detect_microservice_from_path() function."""

    def test_detect_microservice_deep_inside(self, tmp_path):
        """Deep inside microservice directory detects that microservice."""
        # Create a microservice structure
        ms_dir = tmp_path / "microservice-a"
        ms_dir.mkdir()
        sub_dir = ms_dir / "src" / "main"
        sub_dir.mkdir(parents=True)

        # Add a build marker to the microservice directory
        (ms_dir / "pom.xml").write_text("<project></project>")

        result = detect_microservice_from_path(sub_dir, tmp_path)
        assert result == "microservice-a"

    def test_detect_microservice_at_microservice_root(self, tmp_path):
        """At microservice root (cwd = the dir with pom.xml) detects that microservice."""
        ms_dir = tmp_path / "microservice-b"
        ms_dir.mkdir()

        # Add a build marker
        (ms_dir / "build.gradle").write_text("plugins { id 'java' }")

        # cwd IS the microservice root — the most common user scenario
        result = detect_microservice_from_path(ms_dir, tmp_path)
        assert result == "microservice-b"

    def test_detect_microservice_nested_modules(self, tmp_path):
        """Nested build markers scope to outermost microservice, not inner module."""
        ms_dir = tmp_path / "my-service"
        ms_dir.mkdir()
        (ms_dir / "pom.xml").write_text("<project></project>")
        module_dir = ms_dir / "my-module"
        module_dir.mkdir()
        (module_dir / "pom.xml").write_text("<project></project>")

        # From inside the module, should scope to the service, not the module
        result = detect_microservice_from_path(module_dir, tmp_path)
        assert result == "my-service"

    def test_detect_microservice_at_system_root(self, tmp_path):
        """At system root returns None (no specific scope)."""
        result = detect_microservice_from_path(tmp_path, tmp_path)
        assert result is None

    def test_detect_microservice_outside_source(self, tmp_path):
        """Outside source_root returns None."""
        outside_dir = tmp_path.parent / "outside"
        outside_dir.mkdir(parents=True, exist_ok=True)

        result = detect_microservice_from_path(outside_dir, tmp_path)
        assert result is None


class TestScopeManager:
    """Tests for ScopeManager class."""

    def test_apply_scope_when_filter_none(self, tmp_path):
        """No filter provided injects auto-detected scope."""
        # Create a microservice structure
        ms_dir = tmp_path / "microservice-a"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "pom.xml").write_text("<project></project>")

        from java_codebase_rag.mcp.server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = "microservice-a"  # Simulate detection

        result = mgr.apply_auto_scope(None)
        assert result is not None
        assert result.microservice == "microservice-a"

    def test_apply_scope_when_filter_exists_no_microservice(self, tmp_path):
        """Filter without microservice gets auto-scope injected."""
        from java_codebase_rag.mcp.server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = "microservice-b"  # Simulate detection

        from java_codebase_rag.mcp.mcp_v2 import NodeFilter
        result = mgr.apply_auto_scope(NodeFilter(role="CONTROLLER"))
        assert result is not None
        assert result.role == "CONTROLLER"
        assert result.microservice == "microservice-b"

    def test_apply_scope_preserves_explicit_microservice(self, tmp_path):
        """Explicit microservice not overridden."""
        from java_codebase_rag.mcp.server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = "microservice-a"  # Simulate detection

        from java_codebase_rag.mcp.mcp_v2 import NodeFilter
        result = mgr.apply_auto_scope(NodeFilter(microservice="microservice-c"))
        assert result is not None
        assert result.microservice == "microservice-c"

    def test_apply_scope_no_default(self, tmp_path):
        """No auto-detected scope leaves filter unchanged."""
        from java_codebase_rag.mcp.server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = None  # No detection

        from java_codebase_rag.mcp.mcp_v2 import NodeFilter
        nf = NodeFilter(role="CONTROLLER")
        result = mgr.apply_auto_scope(nf)
        assert result is nf
        assert result.role == "CONTROLLER"

    def test_detect_scope_with_yaml_overrides(self, tmp_path):
        """Test that detect_microservice_from_path respects YAML microservice_roots."""
        # Create a project structure with a YAML config that specifies microservice_roots
        config_file = tmp_path / ".java-codebase-rag.yml"
        config_file.write_text("microservice_roots:\n  - custom-ms-name\n")

        # Create a directory that matches the override name (but no build marker)
        custom_ms_dir = tmp_path / "custom-ms-name"
        custom_ms_dir.mkdir()

        # Even without a build marker, the YAML override should detect this as a microservice
        from java_codebase_rag.graph.graph_enrich import detect_microservice_from_path
        result = detect_microservice_from_path(custom_ms_dir, tmp_path)

        # Should detect the microservice based on YAML override
        assert result == "custom-ms-name"

    def test_detect_scope_integration(self, tmp_path):
        """Test real detection flow: ScopeManager.__init__ → detect_microservice_from_path → microservice_for_path."""
        # Create a microservice structure
        ms_dir = tmp_path / "microservice-a"
        ms_dir.mkdir(parents=True, exist_ok=True)
        (ms_dir / "pom.xml").write_text("<project></project>")

        # Create a ScopeManager with real detection (no manual override)
        from java_codebase_rag.mcp.server import ScopeManager
        mgr = ScopeManager(tmp_path)

        # The detection should have found the microservice
        # (assuming we're at the project root, not inside the microservice)
        # When at tmp_path (project root), default_scope should be None
        assert mgr.default_scope is None

        # Test that apply_auto_scope doesn't inject when no scope detected
        from java_codebase_rag.mcp.mcp_v2 import NodeFilter
        nf = NodeFilter(role="CONTROLLER")
        result = mgr.apply_auto_scope(nf)
        assert result is nf


class TestScopeManagerAutoScopeValidation:
    """Auto-scope must not fire for a microservice absent from the index.

    Regression: launching the MCP server from the config/context directory (a
    top-level child of source_root with no build marker and no source) made
    ``detect_microservice_from_path`` return that directory's name via its
    "first path segment under root" fallback. ScopeManager then auto-scoped
    every query to a microservice with zero indexed rows, so all tools
    returned empty results. The detected scope is now validated against the
    indexed microservice set; a candidate with no indexed code is suppressed.
    """

    @staticmethod
    def _stub_index(monkeypatch, microservices: set[str]) -> None:
        """Make ScopeManager._indexed_microservices() see a fake graph."""
        from java_codebase_rag.mcp import server

        class _FakeGraph:
            def microservice_counts(self):
                return {name: 1 for name in microservices}

        monkeypatch.setattr(
            server.LadybugGraph, "exists", lambda db_path=None: len(microservices) > 0
        )
        monkeypatch.setattr(
            server.LadybugGraph, "get", lambda db_path=None: _FakeGraph()
        )

    def test_context_dir_not_detected_as_microservice(self, tmp_path, monkeypatch):
        """Launching from a codeless context dir must NOT auto-scope (the bug)."""
        from java_codebase_rag.mcp.server import ScopeManager

        # Reported layout: source_root holds both the context dir and a real
        # microservice; the server is launched from the context dir.
        context_dir = tmp_path / "bank-chat-context"
        context_dir.mkdir()
        ms_dir = tmp_path / "microservice-a"
        (ms_dir / "src").mkdir(parents=True)
        (ms_dir / "pom.xml").write_text("<project/>")

        # The index only knows the real microservice, not the context dir.
        self._stub_index(monkeypatch, {"microservice-a"})
        monkeypatch.chdir(context_dir)

        mgr = ScopeManager(tmp_path)
        assert mgr.default_scope is None

    def test_real_microservice_dir_still_scopes(self, tmp_path, monkeypatch):
        """Launching from inside an indexed microservice keeps auto-scope."""
        from java_codebase_rag.mcp.server import ScopeManager

        ms_dir = tmp_path / "microservice-a"
        (ms_dir / "src").mkdir(parents=True)
        (ms_dir / "pom.xml").write_text("<project/>")

        self._stub_index(monkeypatch, {"microservice-a"})
        monkeypatch.chdir(ms_dir)

        mgr = ScopeManager(tmp_path)
        assert mgr.default_scope == "microservice-a"

    def test_empty_index_keeps_detection(self, tmp_path, monkeypatch):
        """When the index is missing (exists()->False), keep detection."""
        from java_codebase_rag.mcp.server import ScopeManager

        ms_dir = tmp_path / "microservice-a"
        ms_dir.mkdir()
        (ms_dir / "pom.xml").write_text("<project/>")

        # Graph missing -> exists() False -> empty known set.
        self._stub_index(monkeypatch, set())
        monkeypatch.chdir(ms_dir)

        mgr = ScopeManager(tmp_path)
        assert mgr.default_scope == "microservice-a"

    def test_empty_graph_present_keeps_detection(self, tmp_path, monkeypatch):
        """Graph present but reporting no microservices also keeps detection.

        Covers the exists()->True branch with empty microservice_counts() —
        distinct from test_empty_index_keeps_detection (missing graph). Both
        paths must converge to keeping the detected scope rather than silently
        disabling auto-scope.
        """
        from java_codebase_rag.mcp import server
        from java_codebase_rag.mcp.server import ScopeManager

        ms_dir = tmp_path / "microservice-a"
        ms_dir.mkdir()
        (ms_dir / "pom.xml").write_text("<project/>")

        class _EmptyGraph:
            def microservice_counts(self):
                return {}

        monkeypatch.setattr(server.LadybugGraph, "exists", lambda db_path=None: True)
        monkeypatch.setattr(server.LadybugGraph, "get", lambda db_path=None: _EmptyGraph())
        monkeypatch.chdir(ms_dir)

        mgr = ScopeManager(tmp_path)
        assert mgr.default_scope == "microservice-a"

    def test_indexed_microservices_extracts_nonempty_keys(self, tmp_path, monkeypatch):
        """_indexed_microservices drops empty-string buckets, keeps the rest."""
        from java_codebase_rag.mcp import server
        from java_codebase_rag.mcp.server import ScopeManager

        class _FakeGraph:
            def microservice_counts(self):
                return {"chat-core": 140, "chat-assign": 50, "": 3}

        monkeypatch.setattr(server.LadybugGraph, "exists", lambda db_path=None: True)
        monkeypatch.setattr(server.LadybugGraph, "get", lambda db_path=None: _FakeGraph())

        mgr = ScopeManager(tmp_path)
        assert mgr._indexed_microservices() == {"chat-core", "chat-assign"}
