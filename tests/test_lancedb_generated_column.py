"""Test that Lance chunks are tagged with generated/generated_by columns.

This test verifies that Task 2's implementation correctly tags chunks:
- Generated files (e.g., OpenAPI) have generated=True and generated_by set
- Hand-written files have generated=False and generated_by=None
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import lancedb


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "generated_samples"


def _require_cocoindex_runtime_deps() -> None:
    """cocoindex loads java_index_flow_lancedb.py with the same Python as the CLI."""
    try:
        import tree_sitter_java  # noqa: F401
    except ImportError as exc:
        pytest.skip(
            "Test needs project deps in the current env (e.g. ``pip install -r requirements*``"
            f" in the venv you use to run pytest): {exc}"
        )


def _cocoindex_flow_specifier(bundle_dir: Path, index_cwd: Path) -> str:
    """Return the coco index flow specifier for the java_index_flow_lancedb app."""
    import os
    flow_file = (bundle_dir / "java_index_flow_lancedb.py").resolve()
    if not flow_file.is_file():
        raise FileNotFoundError(f"missing index flow: {flow_file}")
    start = index_cwd.resolve()
    relp = os.path.relpath(str(flow_file), start=str(start))
    relp = Path(relp).as_posix()
    return f"{relp}:JavaCodeIndexLance"


@pytest.mark.parametrize(
    "fixture_file,expected_generated,expected_generated_by",
    [
        ("OpenAPIModel.java", True, "openapi"),
        ("HandWritten.java", False, None),
    ],
)
def test_lance_chunk_generated_columns(
    tmp_path: Path,
    fixture_file: str,
    expected_generated: bool,
    expected_generated_by: str | None,
) -> None:
    """Test that Lance chunks are tagged with generated/generated_by columns.

    This test indexes the generated_samples fixture and verifies:
    1. The columns exist in the LanceDB schema
    2. Generated files have generated=True and correct generated_by
    3. Hand-written files have generated=False and generated_by=None
    """
    _require_cocoindex_runtime_deps()

    # Locate the bundle dir (repo root)
    bundle_dir = Path(__file__).resolve().parent.parent

    # Get the flow specifier
    app_spec = _cocoindex_flow_specifier(bundle_dir, FIXTURE_ROOT)

    # Locate cocoindex binary
    import sys
    import os
    cocoindex_bin = Path(sys.executable).parent / "cocoindex"
    if not cocoindex_bin.is_file():
        pytest.skip(
            f"cocoindex CLI not found next to the pytest interpreter; install cocoindex in this "
            f"venv and run: `.venv/bin/python -m pytest ...` ({cocoindex_bin})"
        )

    # Set up the index directory in tmp_path
    index_dir = tmp_path / ".java-codebase-rag"
    index_dir.mkdir(parents=True)

    # Set up environment
    env = {
        **os.environ,
        "JAVA_CODEBASE_RAG_INDEX_DIR": str(index_dir.resolve()),
        "JAVA_CODEBASE_RAG_SOURCE_ROOT": str(FIXTURE_ROOT.resolve()),
    }

    # Run cocoindex update from the fixture directory
    result = subprocess.run(
        [
            str(cocoindex_bin),
            "update",
            app_spec,
            "-f",
        ],
        cwd=str(FIXTURE_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"STDOUT: {result.stdout}")
        print(f"STDERR: {result.stderr}")
        raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
    print(f"Cocoindex STDOUT: {result.stdout}")
    print(f"Cocoindex STDERR: {result.stderr}")

    # Open the database and check the java table
    db = lancedb.connect(str(index_dir))
    existing_tables = db.table_names()
    print(f"Available tables: {existing_tables}")
    if not existing_tables:
        raise ValueError(f"No tables found in database at {index_dir}. Available tables: {existing_tables}")
    table = db.open_table("javacodeindex_java_code")

    # Check that the columns exist in the schema
    schema = table.schema
    schema_field_names = schema.names
    assert "generated" in schema_field_names, "Column 'generated' must exist in schema"
    assert "generated_by" in schema_field_names, "Column 'generated_by' must exist in schema"

    # Query chunks for the specific file
    chunk_results = table.search().where(f"filename LIKE '%{fixture_file}'").to_arrow()

    # Assert we found chunks for this file
    assert chunk_results.num_rows > 0, f"Expected to find chunks for {fixture_file}"

    # Check that all chunks have the correct generated and generated_by values
    for i in range(chunk_results.num_rows):
        chunk_generated = chunk_results["generated"][i].as_py()
        chunk_generated_by = chunk_results["generated_by"][i].as_py()
        chunk_id = chunk_results["id"][i].as_py()
        assert (
            chunk_generated == expected_generated
        ), f"Chunk {chunk_id} has generated={chunk_generated}, expected {expected_generated}"
        assert (
            chunk_generated_by == expected_generated_by
        ), f"Chunk {chunk_id} has generated_by={chunk_generated_by}, expected {expected_generated_by}"
