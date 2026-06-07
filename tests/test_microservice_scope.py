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
        """At microservice root detects that microservice."""
        ms_dir = tmp_path / "microservice-b"
        ms_dir.mkdir()

        # Add a build marker
        (ms_dir / "build.gradle").write_text("plugins { id 'java' }")

        # Use a subdirectory inside the microservice (not the root itself)
        sub_dir = ms_dir / "src"
        sub_dir.mkdir()

        result = detect_microservice_from_path(sub_dir, tmp_path)
        assert result == "microservice-b"

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
