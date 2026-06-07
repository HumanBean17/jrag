"""Tests for microservice scope detection and ScopeManager."""

from graph_enrich import detect_microservice_from_path


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

        from server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = "microservice-a"  # Simulate detection

        result = mgr.apply_auto_scope(None)
        assert result == {"microservice": "microservice-a"}

    def test_apply_scope_when_filter_exists_no_microservice(self, tmp_path):
        """Filter without microservice gets auto-scope injected."""
        from server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = "microservice-b"  # Simulate detection

        filter_dict = {"role": "Controller"}
        result = mgr.apply_auto_scope(filter_dict)
        assert result == {"role": "Controller", "microservice": "microservice-b"}

    def test_apply_scope_preserves_explicit_microservice(self, tmp_path):
        """Explicit microservice not overridden."""
        from server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = "microservice-a"  # Simulate detection

        filter_dict = {"microservice": "microservice-c"}
        result = mgr.apply_auto_scope(filter_dict)
        assert result == {"microservice": "microservice-c"}

    def test_apply_scope_no_default(self, tmp_path):
        """No auto-detected scope leaves filter unchanged."""
        from server import ScopeManager
        mgr = ScopeManager(tmp_path)
        mgr.default_scope = None  # No detection

        filter_dict = {"role": "Controller"}
        result = mgr.apply_auto_scope(filter_dict)
        assert result == {"role": "Controller"}

    def test_detect_scope_with_yaml_overrides(self, tmp_path):
        """Test that detect_microservice_from_path respects YAML microservice_roots."""
        # Create a project structure with a YAML config that specifies microservice_roots
        config_file = tmp_path / ".java-codebase-rag.yml"
        config_file.write_text("microservice_roots:\n  - custom-ms-name\n")

        # Create a directory that matches the override name (but no build marker)
        custom_ms_dir = tmp_path / "custom-ms-name"
        custom_ms_dir.mkdir()

        # Even without a build marker, the YAML override should detect this as a microservice
        from graph_enrich import detect_microservice_from_path
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
        from server import ScopeManager
        mgr = ScopeManager(tmp_path)

        # The detection should have found the microservice
        # (assuming we're at the project root, not inside the microservice)
        # When at tmp_path (project root), default_scope should be None
        assert mgr.default_scope is None

        # Test that apply_auto_scope doesn't inject when no scope detected
        filter_dict = {"role": "Controller"}
        result = mgr.apply_auto_scope(filter_dict)
        assert result == {"role": "Controller"}
