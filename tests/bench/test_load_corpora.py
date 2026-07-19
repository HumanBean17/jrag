"""Tests for ``bench.load_corpora`` — the corpus registry loader/validator."""
from __future__ import annotations

import textwrap

import pytest

from bench.load_corpora import ConfigError, CorpusRecord, load_corpora


def _write_yaml(tmp_path, body: str) -> str:
    path = tmp_path / "corpora.yml"
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return str(path)


VALID = """
corpora:
  - name: shopizer
    source_kind: git
    git_url: https://github.com/shopizer-ecommerce/shopizer
    commit_sha: abc123def456
    index:
      ontology_version: 18
  - name: bank-chat-system
    source_kind: local
    local_path: tests/bank-chat-system
    pinned_repo_sha: feedface
    index:
      ontology_version: 18
      build_id: null
      build_time_s: null
      on_disk_bytes: null
"""


def test_loads_valid_corpora(tmp_path):
    path = _write_yaml(tmp_path, VALID)
    recs = load_corpora(path)

    assert len(recs) == 2
    assert all(isinstance(r, CorpusRecord) for r in recs)

    by_name = {r.name: r for r in recs}

    shop = by_name["shopizer"]
    assert shop.source_kind == "git"
    assert shop.git_url == "https://github.com/shopizer-ecommerce/shopizer"
    assert shop.commit_sha == "abc123def456"
    assert shop.git_url and shop.commit_sha
    assert shop.local_path is None and shop.pinned_repo_sha is None
    assert shop.index.ontology_version == 18
    assert shop.checkout_path.startswith("bench/checkouts/")
    assert shop.index.index_dir.startswith("bench/indexes/")

    bc = by_name["bank-chat-system"]
    assert bc.source_kind == "local"
    assert bc.local_path == "tests/bank-chat-system"
    assert bc.pinned_repo_sha == "feedface"
    assert bc.git_url is None and bc.commit_sha is None
    assert bc.index.ontology_version == 18


def test_rejects_duplicate_name(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: shopizer
            source_kind: git
            git_url: https://x
            commit_sha: aaa
          - name: shopizer
            source_kind: git
            git_url: https://y
            commit_sha: bbb
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_corpora(path)
    assert "duplicate" in str(exc.value).lower()


def test_rejects_git_missing_sha(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: shopizer
            source_kind: git
            git_url: https://x
            commit_sha: ""
        """,
    )
    with pytest.raises(ConfigError):
        load_corpora(path)


def test_rejects_local_missing_pinned_sha(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: bank-chat-system
            source_kind: local
            local_path: tests/bank-chat-system
            pinned_repo_sha: ""
        """,
    )
    with pytest.raises(ConfigError):
        load_corpora(path)


def test_rejects_bad_name(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: Bank_Chat
            source_kind: local
            local_path: tests/bank-chat-system
            pinned_repo_sha: feedface
        """,
    )
    with pytest.raises(ConfigError):
        load_corpora(path)


def test_rejects_non_positive_ontology_version(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: shopizer
            source_kind: git
            git_url: https://x
            commit_sha: aaa
            index:
              ontology_version: 0
        """,
    )
    with pytest.raises(ConfigError):
        load_corpora(path)


def test_rejects_unknown_top_level_key(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: shopizer
            source_kind: git
            git_url: https://x
            commit_sha: aaa
            commentary: a stray field
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_corpora(path)
    assert "commentary" in str(exc.value)


def test_rejects_unknown_index_key(tmp_path):
    path = _write_yaml(
        tmp_path,
        """
        corpora:
          - name: shopizer
            source_kind: git
            git_url: https://x
            commit_sha: aaa
            index:
              ontology_version: 18
              cost_usd: 1.50
        """,
    )
    with pytest.raises(ConfigError) as exc:
        load_corpora(path)
    assert "cost_usd" in str(exc.value)
