"""
CocoIndex 1.0 app: index Java, Flyway SQL, and YAML into LanceDB.

LanceDB requires a single primary key per table; each chunk gets a UUID `id`.

Environment:
  JAVA_CODEBASE_RAG_INDEX_DIR — Lance tables + LadybugDB + cocoindex state (default: ./.java-codebase-rag)
  JAVA_CODEBASE_RAG_SOURCE_ROOT — Java repo root for indexing (optional; else cocoindex cwd)
  SBERT_MODEL / SBERT_DEVICE — embedding (optional; YAML also supported via java-codebase-rag CLI)

Dependencies:
  pip install "cocoindex[lancedb]" sentence-transformers

Usage:
  cocoindex update java_index_flow_lancedb.py:JavaCodeIndexLance --full-reprocess
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Any

import cocoindex as coco
import numpy as np
import numpy.typing as npt
import pyarrow as pa
from cocoindex.connectors import lancedb, localfs
from cocoindex.connectors.lancedb import LanceType
from cocoindex.ops.sentence_transformers import SentenceTransformerEmbedder
from cocoindex.ops.text import RecursiveSplitter, detect_code_language
from cocoindex.resources.file import PatternFilePathMatcher

from java_codebase_rag.config import resolved_sbert_model_for_process_env
from java_codebase_rag.lance_optimize import LANCE_TABLE_NAMES
from java_index_v1_common import (
    JAVA_CHUNK,
    SBERT_MODEL,
    SQL_CHUNK,
    YAML_CHUNK,
    chunk_key_range,
    position_to_json,
)
from path_filtering import LayeredIgnore
from ast_java import ONTOLOGY_VERSION, parse_java
from graph_enrich import enrich_chunk

# Older cocoindex (e.g. 1.0.0a43) uses ``tracked=False``; newer releases renamed
# the flag to ``detect_change`` (default False) and reject ``tracked``.
_ck_params = inspect.signature(coco.ContextKey.__init__).parameters
if "detect_change" in _ck_params:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root")
    LANCE_DB = coco.ContextKey("java_lance_async_conn")
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("java_lance_embedder")
elif "tracked" in _ck_params:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root", tracked=False)
    LANCE_DB = coco.ContextKey("java_lance_async_conn", tracked=False)
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder](
        "java_lance_embedder", tracked=False
    )
else:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root")
    LANCE_DB = coco.ContextKey("java_lance_async_conn")
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("java_lance_embedder")

splitter = RecursiveSplitter()

# ``parse_java`` (ast_java.parse_java) reuses a process-wide, lru-cached
# tree-sitter ``Parser`` (ast_java._parser) whose ``parse()`` mutates internal
# parser state and is NOT safe to call concurrently from multiple threads.
# ``process_java_file`` runs parsing + per-chunk enrichment off the event loop
# (vectors perf lever #2) so the loop stays free to feed the embedder's batching
# queue while a file is being parsed; this lock serializes the non-reentrant
# Parser across those worker threads. Parsing is cheap (ms-scale) so the cost
# of serializing it is negligible — the win is event-loop responsiveness.
_PARSE_LOCK = threading.Lock()

# cocoindex 1.0.7 schedules ``table.optimize()`` (a LanceDB Rewrite/compaction
# transaction) as a *background* asyncio task after every
# ``num_transactions_before_optimize`` mutation batches (default 50). That
# background Rewrite races the concurrent ``table.delete()`` (Delete)
# transactions emitted by later batches, and LanceDB does not allow a Rewrite
# to commit concurrently with a Delete (upstream lancedb#1504), which floods
# stderr with "Retryable commit conflict ... preempted by concurrent
# transaction Delete". Setting this effectively to infinity disables the
# in-flight background optimize; the serialized post-flow optimize in
# ``lance_optimize.optimize_lance_tables`` then compacts the table with no
# concurrent writers. ``optimize()`` is pure maintenance (compact/prune/index);
# upsert/delete correctness via merge_insert does not depend on it.
_NUM_TXN_BEFORE_OPTIMIZE = 10**12


# --- Vectors-phase progress emission (JCIRAG_PROGRESS kind=vectors) -----------
#
# The flow runs in a CHILD cocoindex process; it prints structured progress to
# its stderr and the parent (pipeline._popen_capturing_stderr /
# cli_progress.accumulate_and_relay_subprocess_streams) parses it via
# ProgressRelay and feeds the renderer. The flow CANNOT know when all files are
# done (cocoindex offers no "all files done" hook in the flow), so it emits:
#   - ONE ``total=N status=running`` line from ``app_main`` (approximate
#     pre-walk: matcher includes + LayeredIgnore), and
#   - per-file ``done=k status=running`` ticks (throttled every ~25 files) from
#     ``process_*_file`` (shared atomic counter).
# The PARENT emits the terminal ``status=done``/``failed`` vectors event on
# cocoindex exit (drives clamp-on-completion + phase transition to Optimize).

# Per-file tick cadence: bound stderr volume on huge trees without making the
# bar feel stale. Every 25th file (and the modulo boundary is enough — the
# parent clamps to total on the terminal event anyway).
_VECTORS_TICK_EVERY = 25

# Thread-safe counter: cocoindex may call process_*_file concurrently
# (mount_each parallelism is implementation-defined). A module-level lock guards
# both the counter and the emission so two threads never interleave a tick.
_vectors_done_lock = threading.Lock()
_vectors_done_count = 0


def _emit_vectors_progress(
    *,
    done: int | None = None,
    total: int | None = None,
    status: str = "running",
    elapsed_s: float | None = None,
) -> None:
    """Emit one ``JCIRAG_PROGRESS kind=vectors …`` line to stderr (flushed).

    Field order is fixed (kind, done, total, status, elapsed_s) so the parser
    and tests can pin substrings. Omitted fields are simply absent.
    """
    fields = ["kind=vectors"]
    if done is not None:
        fields.append(f"done={done}")
    if total is not None:
        fields.append(f"total={total}")
    fields.append(f"status={status}")
    if elapsed_s is not None:
        fields.append(f"elapsed_s={elapsed_s:.2f}")
    print("JCIRAG_PROGRESS " + " ".join(fields), file=sys.stderr, flush=True)


def _tick_vectors_done() -> None:
    """Increment the shared per-file counter and emit a throttled ``done=k`` tick.

    Called once per successfully-processed file (after the ignore / empty
    early-returns). The tick is emitted every ``_VECTORS_TICK_EVERY`` files so
    stderr volume stays bounded on huge trees; the parent clamps to total on
    the terminal event, so the exact tick cadence is not load-bearing.
    """
    global _vectors_done_count
    with _vectors_done_lock:
        _vectors_done_count += 1
        n = _vectors_done_count
        if n % _VECTORS_TICK_EVERY != 0:
            return
        # Emit under the lock: the docstring above promises the lock guards both
        # the counter AND the emission, so two concurrent ticks can't emit their
        # ``done=N`` lines out of order. Contention is negligible (fires every
        # ~25 files).
        _emit_vectors_progress(done=n, status="running")


def _approximate_vectors_total(project_root: Path) -> int:
    """Reproduce the matchers' include globs + LayeredIgnore for an approximate total.

    The flow applies two filtering layers: (1) ``PatternFilePathMatcher``
    excludes at walk time via ``LayeredIgnore.cocoindex_excluded_patterns()``,
    then (2) ``LayeredIgnore.is_ignored()`` plus an early-return for empty /
    undecodable files inside each ``process_*_file``. Files that early-return
    never tick, so this pre-walk OVERSTATES the total by the ignored / empty
    count. The parent clamps the bar to 100% on the terminal ``status=done``
    event, so the over-count cannot stall the bar.

    Mirrors the three ``localfs.walk_dir`` matchers in ``app_main``:
      - ``**/*.java``
      - ``**/src/main/resources/db/migration/*.sql``
      - ``**/src/main/resources/application*.yml`` and ``.yaml``
    """
    ignore = LayeredIgnore(project_root)
    excluded = ignore.cocoindex_excluded_patterns()

    def _excluded(rel_posix: str) -> bool:
        return any(fnmatch(rel_posix, pat) for pat in excluded)

    total = 0
    for dirpath, dirnames, filenames in os.walk(project_root):
        # Prune the same universal nuisance dirs as iter_java_source_files /
        # cocoindex walk. (build-output pruning is matcher-dependent in the
        # real walk; for an APPROXIMATE total this cheap prune is sufficient
        # — the clamp absorbs any residual divergence.)
        dirnames[:] = [
            d for d in dirnames if d not in (".git", ".hg", ".svn", "node_modules", ".venv", "venv")
        ]
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                rel = full.resolve().relative_to(project_root).as_posix()
            except ValueError:
                continue
            if _excluded(rel):
                continue
            # Java: **/*.java
            if fn.endswith(".java"):
                if not ignore.is_ignored(full):
                    total += 1
                continue
            # SQL: **/src/main/resources/db/migration/*.sql
            if fn.endswith(".sql") and "/db/migration/" in rel:
                if not ignore.is_ignored(full):
                    total += 1
                continue
            # YAML: **/src/main/resources/application*.yml / .yaml
            # NOTE: ``fn`` is the bare filename (e.g. ``application-cloud.yml``), so
            # the prefix predicate must be ``fn.startswith("application")`` —
            # ``"/application" in fn`` was always False (no leading slash in a bare
            # name) and under-counted every application YAML, driving the pre-walk
            # total below the actual done count. The ``rel``-based
            # ``"/src/main/resources/"`` gate stays (full path component).
            if fn.endswith((".yml", ".yaml")) and fn.startswith("application") and "/src/main/resources/" in rel:
                if not ignore.is_ignored(full):
                    total += 1
    return total


@dataclass
class JavaLanceChunk:
    id: str
    filename: str
    language: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]
    package: str
    module: str
    microservice: str
    primary_type_fqn: str
    primary_type_kind: str
    role: str
    # Native PyArrow lists: without the LanceType override CocoIndex would JSON-encode
    # `list[str]` into a STRING column, which caller code then iterates character-by-character.
    capabilities: Annotated[list[str], LanceType(pa.list_(pa.string()))]
    annotations_on_type: Annotated[list[str], LanceType(pa.list_(pa.string()))]
    symbols: Annotated[list[str], LanceType(pa.list_(pa.string()))]
    ontology_version: int


@dataclass
class SqlLanceChunk:
    id: str
    filename: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]


@dataclass
class YamlLanceChunk:
    id: str
    filename: str
    text: str
    range_start: int
    range_end: int
    start: dict[str, Any]
    end: dict[str, Any]
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]


@coco.lifespan
async def coco_lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    idx_raw = os.environ.get("JAVA_CODEBASE_RAG_INDEX_DIR", "").strip()
    if idx_raw and not idx_raw.startswith(("s3://", "gs://", "az://")):
        index_dir = Path(idx_raw).expanduser().resolve()
    else:
        index_dir = (Path(".").resolve() / ".java-codebase-rag").resolve()
    index_dir.mkdir(parents=True, exist_ok=True)
    builder.settings.db_path = index_dir / "cocoindex.db"

    env_root = os.environ.get("JAVA_CODEBASE_RAG_SOURCE_ROOT", "").strip()
    if env_root:
        root = Path(env_root).expanduser().resolve()
    else:
        root = Path(".").resolve()
    builder.provide(PROJECT_ROOT, root)

    embedder = SentenceTransformerEmbedder(
        resolved_sbert_model_for_process_env(SBERT_MODEL),
        device=os.environ.get("SBERT_DEVICE") or None,
        trust_remote_code=True,
    )
    builder.provide(EMBEDDER, embedder)

    uri = str(index_dir)

    @asynccontextmanager
    async def _lance_cm() -> AsyncIterator[Any]:
        conn = await lancedb.connect_async(uri)
        try:
            yield conn
        finally:
            conn.close()

    await builder.provide_async_with(LANCE_DB, _lance_cm())
    yield


def _parse_and_enrich_java(
    content_bytes: bytes,
    chunks: list[Any],
    rel: str,
    project_root: Path,
) -> list[Any]:
    """Parse one Java file and enrich every chunk, off the event loop.

    Returns a list of :class:`graph_enrich.ChunkEnrichment` aligned 1:1 with
    ``chunks``. Intended to run via ``asyncio.to_thread`` from
    ``process_java_file`` (vectors perf lever #2): while the worker thread
    parses + enriches, the event loop is free to drive other files and keep the
    embedder's batching queue fed.

    ``parse_java`` is serialized by ``_PARSE_LOCK`` (shared non-thread-safe
    tree-sitter ``Parser``). ``enrich_chunk`` is pure-Python over the now
    immutable AST — its ``lru_cache`` reads are thread-safe under the GIL — so
    it runs outside the lock and can overlap across files.
    """
    with _PARSE_LOCK:
        ast = parse_java(content_bytes)
    return [
        enrich_chunk(
            ast,
            chunk_start_byte=ch.start.byte_offset,
            chunk_end_byte=ch.end.byte_offset,
            file_path=rel,
            project_root=project_root,
        )
        for ch in chunks
    ]


@coco.fn(memo=True)
async def process_java_file(
    file: localfs.File,
    table: lancedb.TableTarget[JavaLanceChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    project_root = coco.use_context(PROJECT_ROOT)
    if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve()):
        return
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return
    if not content.strip():
        return

    _tick_vectors_done()

    language = detect_code_language(filename=file.file_path.path.name) or "text"
    cs, mn, ov = JAVA_CHUNK
    # ``splitter.split`` stays inline: the module-level ``RecursiveSplitter``
    # shares one Rust object, so keeping split on the event loop preserves its
    # existing single-threaded access (no new cross-file concurrency hazard).
    chunks = splitter.split(
        content,
        cs,
        min_chunk_size=mn,
        chunk_overlap=ov,
        language=language,
    )
    rel = file.file_path.path.as_posix()
    content_bytes = content.encode("utf-8", errors="replace")

    # (vectors perf lever #2) parse + enrich off the event loop so the loop can
    # keep the embedder's batching queue fed while this file is being parsed.
    # ``parse_java`` is lock-serialized internally (shared tree-sitter Parser).
    enrichments = await asyncio.to_thread(
        _parse_and_enrich_java, content_bytes, chunks, rel, project_root
    )
    # (vectors perf lever #1) embed all chunks concurrently so the batched
    # embedder groups them into one ``model.encode(...)`` (max_batch_size=64)
    # instead of N serial batch-of-1 calls. Dominant win for ``increment``
    # (few changed files → little cross-file concurrency → otherwise no batching).
    embeddings = await asyncio.gather(*(embedder.embed(ch.text) for ch in chunks))

    for ch, enrich, emb in zip(chunks, enrichments, embeddings):
        rs, re = chunk_key_range(ch)
        table.declare_row(
            row=JavaLanceChunk(
                id=str(uuid.uuid4()),
                filename=rel,
                language=language,
                text=ch.text,
                range_start=rs,
                range_end=re,
                start=position_to_json(ch.start),
                end=position_to_json(ch.end),
                embedding=emb,
                package=enrich.package,
                module=enrich.module,
                microservice=enrich.microservice,
                primary_type_fqn=enrich.primary_type_fqn,
                primary_type_kind=enrich.primary_type_kind,
                role=enrich.role,
                capabilities=list(enrich.capabilities),
                annotations_on_type=enrich.annotations_on_type,
                symbols=enrich.symbols,
                ontology_version=ONTOLOGY_VERSION,
            )
        )


@coco.fn(memo=True)
async def process_sql_file(
    file: localfs.File,
    table: lancedb.TableTarget[SqlLanceChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    project_root = coco.use_context(PROJECT_ROOT)
    if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve()):
        return
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return
    if not content.strip():
        return

    _tick_vectors_done()

    language = "sql"
    cs, mn, ov = SQL_CHUNK
    chunks = splitter.split(
        content,
        cs,
        min_chunk_size=mn,
        chunk_overlap=ov,
        language=language,
    )
    rel = file.file_path.path.as_posix()

    # (vectors perf lever #1) embed chunks concurrently → batched encode.
    embeddings = await asyncio.gather(*(embedder.embed(ch.text) for ch in chunks))

    for ch, emb in zip(chunks, embeddings):
        rs, re = chunk_key_range(ch)
        table.declare_row(
            row=SqlLanceChunk(
                id=str(uuid.uuid4()),
                filename=rel,
                text=ch.text,
                range_start=rs,
                range_end=re,
                start=position_to_json(ch.start),
                end=position_to_json(ch.end),
                embedding=emb,
            )
        )


@coco.fn(memo=True)
async def process_yaml_file(
    file: localfs.File,
    table: lancedb.TableTarget[YamlLanceChunk],
) -> None:
    embedder = coco.use_context(EMBEDDER)
    project_root = coco.use_context(PROJECT_ROOT)
    if LayeredIgnore(project_root).is_ignored((project_root / file.file_path.path).resolve()):
        return
    try:
        content = await file.read_text()
    except UnicodeDecodeError:
        return
    if not content.strip():
        return

    _tick_vectors_done()

    ext = file.file_path.path.suffix.lower()
    language = "yaml" if ext in (".yml", ".yaml") else "text"
    cs, mn, ov = YAML_CHUNK
    chunks = splitter.split(
        content,
        cs,
        min_chunk_size=mn,
        chunk_overlap=ov,
        language=language,
    )
    rel = file.file_path.path.as_posix()

    # (vectors perf lever #1) embed chunks concurrently → batched encode.
    embeddings = await asyncio.gather(*(embedder.embed(ch.text) for ch in chunks))

    for ch, emb in zip(chunks, embeddings):
        rs, re = chunk_key_range(ch)
        table.declare_row(
            row=YamlLanceChunk(
                id=str(uuid.uuid4()),
                filename=rel,
                text=ch.text,
                range_start=rs,
                range_end=re,
                start=position_to_json(ch.start),
                end=position_to_json(ch.end),
                embedding=emb,
            )
        )


@coco.fn
async def app_main() -> None:
    java_schema = await lancedb.TableSchema.from_class(
        JavaLanceChunk,
        primary_key=["id"],
    )
    java_table = await lancedb.mount_table_target(
        LANCE_DB,
        LANCE_TABLE_NAMES[0],
        java_schema,
        num_transactions_before_optimize=_NUM_TXN_BEFORE_OPTIMIZE,
    )

    sql_schema = await lancedb.TableSchema.from_class(
        SqlLanceChunk,
        primary_key=["id"],
    )
    sql_table = await lancedb.mount_table_target(
        LANCE_DB,
        LANCE_TABLE_NAMES[1],
        sql_schema,
        num_transactions_before_optimize=_NUM_TXN_BEFORE_OPTIMIZE,
    )

    yaml_schema = await lancedb.TableSchema.from_class(
        YamlLanceChunk,
        primary_key=["id"],
    )
    yaml_table = await lancedb.mount_table_target(
        LANCE_DB,
        LANCE_TABLE_NAMES[2],
        yaml_schema,
        num_transactions_before_optimize=_NUM_TXN_BEFORE_OPTIMIZE,
    )

    project_root = coco.use_context(PROJECT_ROOT)
    _ignore = LayeredIgnore(project_root)
    _walk_excludes = _ignore.cocoindex_excluded_patterns()
    # Emit ONE approximate total so the parent's renderer can show a determinate
    # bar (clamps to 100% on the terminal vectors event the parent emits on
    # cocoindex exit). Approximate — ignored / empty files over-state it; see
    # ``_approximate_vectors_total``. ``--full-reprocess`` only: on incremental
    # catch-up the @coco.fn(memo=True) cache skips unchanged files, so no total
    # is knowable up front → the parent renders indeterminate from the absence.
    try:
        total = _approximate_vectors_total(project_root)
        if total > 0:
            _emit_vectors_progress(total=total, status="running")
    except Exception:
        # The pre-walk must never break indexing — a failure here just means
        # the parent falls back to indeterminate. Swallow and continue.
        pass
    java_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=["**/*.java"],
            excluded_patterns=_walk_excludes,
        ),
    )
    sql_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=["**/src/main/resources/db/migration/*.sql"],
            excluded_patterns=_walk_excludes,
        ),
    )
    yaml_files = localfs.walk_dir(
        PROJECT_ROOT,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=[
                "**/src/main/resources/application*.yml",
                "**/src/main/resources/application*.yaml",
            ],
            excluded_patterns=_walk_excludes,
        ),
    )

    await coco.mount_each(
        coco.component_subpath(coco.Symbol("java_files")),
        process_java_file,
        java_files.items(),
        java_table,
    )
    await coco.mount_each(
        coco.component_subpath(coco.Symbol("sql_files")),
        process_sql_file,
        sql_files.items(),
        sql_table,
    )
    await coco.mount_each(
        coco.component_subpath(coco.Symbol("yaml_files")),
        process_yaml_file,
        yaml_files.items(),
        yaml_table,
    )


app = coco.App(
    coco.AppConfig(name="JavaCodeIndexLance"),
    app_main,
)