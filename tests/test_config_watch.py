"""Tests for the ``watch:`` YAML block on :class:`ResolvedOperatorConfig`.

Mirrors the absence-knobs tests (``tests/package/test_config.py``).
Knobs: ``watch.debounce_ms`` (int, floor 100), ``watch.backend`` (str,
one of auto/watchdog/polling), ``watch.poll_interval_ms`` (int, floor 200).
No env vars are introduced for watch, so only CLI > YAML > default is exercised.
"""

from java_codebase_rag.config import YAML_CONFIG_FILENAMES, resolve_operator_config


class TestWatchDefaults:
    """No ``watch:`` block -> built-in defaults + ``default`` source."""

    def test_defaults_when_no_watch_block(self, tmp_path):
        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_debounce_ms == 1500
        assert cfg.watch_backend == "auto"
        assert cfg.watch_poll_interval_ms == 2000

        assert cfg.watch_debounce_ms_source == "default"
        assert cfg.watch_backend_source == "default"
        assert cfg.watch_poll_interval_ms_source == "default"

    def test_empty_watch_block_uses_defaults(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text("watch: {}\n")

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_debounce_ms == 1500
        assert cfg.watch_backend == "auto"
        assert cfg.watch_poll_interval_ms == 2000


class TestWatchFromYaml:
    """A populated ``watch:`` block is parsed with correct types and ``yaml`` source."""

    def test_yaml_values_parsed(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n"
            "  debounce_ms: 750\n"
            "  backend: polling\n"
            "  poll_interval_ms: 500\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_debounce_ms == 750
        assert cfg.watch_backend == "polling"
        assert cfg.watch_poll_interval_ms == 500

        assert cfg.watch_debounce_ms_source == "yaml"
        assert cfg.watch_backend_source == "yaml"
        assert cfg.watch_poll_interval_ms_source == "yaml"

    def test_yaml_watchdog_backend(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n  backend: watchdog\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_backend == "watchdog"
        assert cfg.watch_backend_source == "yaml"


class TestWatchCliOverride:
    """CLI kwargs override YAML; source becomes ``cli``."""

    def test_cli_overrides_yaml(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n"
            "  debounce_ms: 750\n"
            "  backend: polling\n"
            "  poll_interval_ms: 500\n"
        )

        cfg = resolve_operator_config(
            source_root=tmp_path,
            cli_watch_debounce_ms=3000,
            cli_watch_backend="watchdog",
            cli_watch_poll_interval_ms=4000,
        )

        assert cfg.watch_debounce_ms == 3000
        assert cfg.watch_backend == "watchdog"
        assert cfg.watch_poll_interval_ms == 4000

        assert cfg.watch_debounce_ms_source == "cli"
        assert cfg.watch_backend_source == "cli"
        assert cfg.watch_poll_interval_ms_source == "cli"

    def test_cli_overrides_defaults_when_no_yaml(self, tmp_path):
        cfg = resolve_operator_config(
            source_root=tmp_path,
            cli_watch_debounce_ms=2500,
            cli_watch_backend="polling",
            cli_watch_poll_interval_ms=3000,
        )

        assert cfg.watch_debounce_ms == 2500
        assert cfg.watch_backend == "polling"
        assert cfg.watch_poll_interval_ms == 3000
        assert cfg.watch_debounce_ms_source == "cli"
        assert cfg.watch_backend_source == "cli"
        assert cfg.watch_poll_interval_ms_source == "cli"


class TestWatchValidation:
    """Out-of-range / unknown values fall back to the default + stderr warning."""

    def test_debounce_below_floor_falls_back(self, tmp_path, capsys):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n  debounce_ms: 10\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_debounce_ms == 1500
        captured = capsys.readouterr()
        assert "debounce_ms" in captured.err.lower()

    def test_debounce_at_floor_accepted(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n  debounce_ms: 100\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_debounce_ms == 100
        assert cfg.watch_debounce_ms_source == "yaml"

    def test_invalid_backend_falls_back(self, tmp_path, capsys):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n  backend: bogus\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_backend == "auto"
        captured = capsys.readouterr()
        assert "backend" in captured.err.lower()

    def test_poll_interval_below_floor_falls_back(self, tmp_path, capsys):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n  poll_interval_ms: 50\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_poll_interval_ms == 2000
        captured = capsys.readouterr()
        assert "poll_interval_ms" in captured.err.lower()

    def test_poll_interval_at_floor_accepted(self, tmp_path):
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(
            "watch:\n  poll_interval_ms: 200\n"
        )

        cfg = resolve_operator_config(source_root=tmp_path)

        assert cfg.watch_poll_interval_ms == 200
        assert cfg.watch_poll_interval_ms_source == "yaml"
