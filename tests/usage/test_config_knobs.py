"""usage.enabled / usage.dir config knobs (Task 3) — opt-in by default."""

from __future__ import annotations

from pathlib import Path

from java_codebase_rag.config import (
    YAML_CONFIG_FILENAMES,
    resolve_operator_config,
)


def _resolve(tmp_path: Path, monkeypatch, yaml_text: str | None = None,
             env: dict[str, str] | None = None):
    monkeypatch.delenv("JAVA_CODEBASE_RAG_INDEX_DIR", raising=False)
    monkeypatch.delenv("JAVA_CODEBASE_RAG_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_ENABLED", raising=False)
    monkeypatch.delenv("JAVA_CODEBASE_RAG_USAGE_DIR", raising=False)
    if yaml_text is not None:
        (tmp_path / YAML_CONFIG_FILENAMES[0]).write_text(yaml_text)
    monkeypatch.chdir(tmp_path)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    return resolve_operator_config(source_root=None)


def test_usage_disabled_by_default(tmp_path, monkeypatch) -> None:
    result = _resolve(tmp_path, monkeypatch)
    assert result.usage_enabled is False
    assert result.usage_enabled_source == "default"
    assert result.usage_dir is None
    assert result.usage_dir_source == "default"


def test_usage_enabled_via_env(tmp_path, monkeypatch) -> None:
    result = _resolve(
        tmp_path, monkeypatch, env={"JAVA_CODEBASE_RAG_USAGE_ENABLED": "1"}
    )
    assert result.usage_enabled is True
    assert result.usage_enabled_source == "env"


def test_usage_enabled_via_yaml_and_env_precedence(tmp_path, monkeypatch) -> None:
    result = _resolve(tmp_path, monkeypatch, "usage:\n  enabled: true\n")
    assert result.usage_enabled is True
    assert result.usage_enabled_source == "yaml"
    # Env wins over YAML when both set.
    result = _resolve(
        tmp_path, monkeypatch, "usage:\n  enabled: true\n",
        env={"JAVA_CODEBASE_RAG_USAGE_ENABLED": "0"},
    )
    assert result.usage_enabled is False
    assert result.usage_enabled_source == "env"


def test_usage_dir_resolution(tmp_path, monkeypatch) -> None:
    result = _resolve(
        tmp_path, monkeypatch, env={"JAVA_CODEBASE_RAG_USAGE_DIR": "/tmp/u"}
    )
    assert result.usage_dir == "/tmp/u"
    assert result.usage_dir_source == "env"
    # YAML path also resolves.
    result = _resolve(tmp_path, monkeypatch, "usage:\n  dir: /tmp/y\n")
    assert result.usage_dir == "/tmp/y"
    assert result.usage_dir_source == "yaml"
