"""Regression tests for LanceDB hybrid / FTS error handling."""

from search_lancedb import _is_duplicate_fts_index_error


def test_is_duplicate_fts_does_not_match_does_not_exist() -> None:
    assert not _is_duplicate_fts_index_error(
        Exception("table or column 'text' does not exist")
    )
    assert not _is_duplicate_fts_index_error(Exception("resource not found on path"))


def test_is_duplicate_fts_matches_likely_duplicates() -> None:
    assert _is_duplicate_fts_index_error(Exception("inverted index on text already exists"))
    assert _is_duplicate_fts_index_error(Exception("Duplicate index name"))
    assert _is_duplicate_fts_index_error(Exception("index same name as existing"))
