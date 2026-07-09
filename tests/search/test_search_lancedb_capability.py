"""`run_search` with `capability=` — exercises Lance `array_has` + vector path (no CocoIndex)."""
from __future__ import annotations

import uuid

import pytest

# Skip the whole file on graph-only installs (macOS Intel) where the vector stack is absent.
pytest.importorskip("lancedb")
pytest.importorskip("sentence_transformers")

from sentence_transformers import SentenceTransformer

from java_codebase_rag.ast.ast_java import ONTOLOGY_VERSION
from java_codebase_rag.search.index_common import SBERT_MODEL
from java_codebase_rag.search.search_lancedb import TABLES, _query_vector, run_search


def _one_java_row_built_for_capability_filter(
    *,
    text: str,
    primary_fqn: str,
    model: SentenceTransformer,
) -> dict:
    """Build a single row dict compatible with cocoindex java table shape."""
    emb = _query_vector(model, text)
    return {
        "id": str(uuid.uuid4()),
        "filename": "smoke/p/Listener.java",
        "text": text,
        "language": "java",
        "range_start": 0,
        "range_end": 500,
        "start": {"line": 1, "byte_offset": 0},
        "end": {"line": 20, "byte_offset": 400},
        "embedding": emb,
        "package": "p",
        "module": "smoke",
        "microservice": "smoke",
        "primary_type_fqn": primary_fqn,
        "primary_type_kind": "class",
        "role": "OTHER",
        "annotations_on_type": ["KafkaListener"],
        "symbols": ["onMessage"],
        "ontology_version": ONTOLOGY_VERSION,
        "capabilities": ["MESSAGE_LISTENER", "SCHEDULED_TASK"],
    }


def test_run_search_capability_filter_finds_row(tmp_path) -> None:
    """Predicate `array_has(capabilities, 'MESSAGE_LISTENER')` in vector search prefilter."""
    import lancedb  # local import: heavy dep, only in this test module

    uri = str(tmp_path / "ldb")
    model = SentenceTransformer(SBERT_MODEL, device="cpu", trust_remote_code=True)
    text = "Kafka consumer handles incoming record batch for listener endpoint"
    row = _one_java_row_built_for_capability_filter(
        text=text, primary_fqn="p.FooListener", model=model
    )
    db = lancedb.connect(uri)
    db.create_table(TABLES["java"], [row], mode="create")

    hits = run_search(
        text,
        uri=uri,
        table_keys=["java"],
        limit=5,
        path_substring=None,
        model_name=SBERT_MODEL,
        device="cpu",
        model=model,
        capability="MESSAGE_LISTENER",
    )
    assert len(hits) >= 1
    assert any(
        h.get("primary_type_fqn") == "p.FooListener" or "MESSAGE_LISTENER" in (h.get("capabilities") or [])
        for h in hits
    )


def test_run_search_nonmatching_capability_returns_empty(tmp_path) -> None:
    import lancedb

    uri = str(tmp_path / "ldb2")
    model = SentenceTransformer(SBERT_MODEL, device="cpu", trust_remote_code=True)
    text = "unrelated class body"
    row = _one_java_row_built_for_capability_filter(
        text=text, primary_fqn="p.X", model=model
    )
    db = lancedb.connect(uri)
    db.create_table(TABLES["java"], [row], mode="create")

    hits = run_search(
        text,
        uri=uri,
        table_keys=["java"],
        limit=5,
        path_substring=None,
        model_name=SBERT_MODEL,
        device="cpu",
        model=model,
        capability="MESSAGE_PRODUCER",
    )
    assert hits == []


def test_array_has_uses_lance_list_column_type(tmp_path) -> None:
    """Sanity: capabilities column is list / array type Lance accepts (regression for write path)."""
    import lancedb

    uri = str(tmp_path / "t3")
    import pyarrow as pa  # noqa: TCH002

    db = lancedb.connect(uri)
    tbl = db.create_table(
        "probe",
        pa.table(
            {
                "id": [1],
                "capabilities": pa.array(
                    [["MESSAGE_LISTENER"]], type=pa.list_(pa.string())
                ),
            }
        ),
    )
    assert "capabilities" in {f.name for f in tbl.schema}
    assert tbl.to_arrow()["capabilities"].to_pylist() == [["MESSAGE_LISTENER"]]
