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
from graph_enrich import collect_annotation_meta_chain, enrich_chunk, load_brownfield_overrides

# Older cocoindex (e.g. 1.0.0a43) uses ``tracked=False``; newer releases renamed
# the flag to ``detect_change`` (default False) and reject ``tracked``.
_ck_params = inspect.signature(coco.ContextKey.__init__).parameters
if "detect_change" in _ck_params:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root")
    LANCE_DB = coco.ContextKey("java_lance_async_conn")
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("java_lance_embedder")
    IGNORE = coco.ContextKey[LayeredIgnore]("java_lance_layered_ignore")
elif "tracked" in _ck_params:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root", tracked=False)
    LANCE_DB = coco.ContextKey("java_lance_async_conn", tracked=False)
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder](
        "java_lance_embedder", tracked=False
    )
    IGNORE = coco.ContextKey[LayeredIgnore](
        "java_lance_layered_ignore", tracked=False
    )
else:
    PROJECT_ROOT = coco.ContextKey[Path]("java_lance_project_root")
    LANCE_DB = coco.ContextKey("java_lance_async_conn")
    EMBEDDER = coco.ContextKey[SentenceTransformerEmbedder]("java_lance_embedder")
    IGNORE = coco.ContextKey[LayeredIgnore]("java_lance_layered_ignore")

splitter = RecursiveSplitter()

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

# Bounded concurrency for the per-file drain in app_main. cocoindex's embedder
# is ``@coco.fn.as_async(batching=True, runner=GPU, max_batch_size=64)`` — but
# its batching layer only coalesces calls that are in flight SIMULTANEOUSLY. A
# serial ``async for … await`` loop keeps just one file's chunks (avg 1–3) in
# flight, so real batches stay tiny and MPS idles between them (measured ~138
# chunks/s vs the ~235 chunks/s ceiling at batch=64 for all-MiniLM-L6-v2).
# Draining many files at once with a semaphore puts their chunks in flight
# together → the embedder coalesces them into full batches → MPS climbs toward
# the ceiling. Measured on Shopizer (1167 files / 3475 chunks): full init drops
# from ~46.7s (serial) to ~36.0s (32) / ~34.3s (64), with identical row output.
#
# This stays inside ONE component, so the earlier mount_each→app_main win is
# preserved: still exactly ONE merge_insert per table at commit. Memoization
# (``@coco.fn(memo=True)``) and the lock-guarded tick counter are safe under
# concurrency; ``parse_java`` uses a per-thread tree-sitter Parser (already
# routed via ``asyncio.to_thread``) and ``splitter.split`` is synchronous so the
# event loop cannot reenter it.
#
# Default 64 is sized to MATCH the embedder's hardcoded max_batch_size=64 (the
# decorator above; not a constructor arg, so not raisable from the flow): ~64
# files in flight reliably fills a 64-chunk batch and saturates MPS. Going higher
# buys nothing — the batch is already capped — and lower underfills it. Memory
# is NOT the limiting factor here: cocoindex buffers ALL staged rows until the
# single final merge_insert regardless of concurrency, so peak RSS is set by
# total chunk count (the commit buffer), not by how many files process at once.
# Set to ``1`` for the old serial behavior; raise/lower only if you have also
# changed the effective batch size or are constraining the commit buffer itself.
_FILE_CONCURRENCY = max(
    1,
    int(os.environ.get("JAVA_CODEBASE_RAG_FILE_CONCURRENCY", "64") or "64"),
)

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

    # Default to Apple Metal (MPS) when available: ~1.7x faster encode on
    # all-MiniLM-L6-v2 (measured), and the win grows with repo size since
    # embedding dominates on large trees. torch is already on the import path
    # here (sentence-transformers pulls it), so the availability check is free
    # in this child process — and it keeps the CLI parent (config.py) from ever
    # paying a torch import. Operators force CPU with SBERT_DEVICE=cpu.
    device = os.environ.get("SBERT_DEVICE") or None
    if device is None:
        try:
            import torch  # noqa: WPS433 (local import: avoid parent-import cost)
            if torch.backends.mps.is_available():
                device = "mps"
        except Exception:
            pass
    embedder = SentenceTransformerEmbedder(
        resolved_sbert_model_for_process_env(SBERT_MODEL),
        device=device,
        trust_remote_code=True,
    )
    builder.provide(EMBEDDER, embedder)
    builder.provide(IGNORE, LayeredIgnore(root))

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

    Thread-safety: ``parse_java`` uses a per-thread tree-sitter ``Parser``
    (see ``ast_java._parser``), so it is safe to call concurrently from these
    worker threads — including the transitive ``parse_java`` that ``enrich_chunk``
    triggers via ``collect_annotation_meta_chain`` → ``_collect_annotation_decl_index``.
    ``enrich_chunk`` is otherwise pure-Python over the now-immutable AST; its
    ``lru_cache`` reads are thread-safe under the GIL.
    """
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
    ignore = coco.use_context(IGNORE)
    if ignore.is_ignored((project_root / file.file_path.path).resolve()):
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
    # parse_java is thread-safe (per-thread tree-sitter Parser in ast_java).
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
    ignore = coco.use_context(IGNORE)
    if ignore.is_ignored((project_root / file.file_path.path).resolve()):
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
    ignore = coco.use_context(IGNORE)
    if ignore.is_ignored((project_root / file.file_path.path).resolve()):
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


async def _drain_files_concurrently(
    files: Any, process_fn: Any, table: Any, sem: asyncio.Semaphore
) -> None:
    """Run ``process_fn(file, table)`` over every file with bounded concurrency.

    Replaces the serial ``async for … await process_*_file`` loop so the
    embedder's batching layer sees many files' chunks in flight at once (see
    ``_FILE_CONCURRENCY``). Materializes the async iterable up front — file
    handles are lightweight and cocoindex already realized the collection when
    the walker mounted, so this is not a second walk. An empty collection is a
    no-op (e.g. SQL/YAML tables on a repo with none).
    """
    items = [f async for _, f in files.items()]
    if not items:
        return

    async def _one(_file: Any) -> None:
        async with sem:
            await process_fn(_file, table)

    await asyncio.gather(*(_one(f) for f in items))


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
    # Warm per-project enrichment caches ONCE on the event-loop thread, BEFORE
    # coco.mount_each fans files into worker threads. collect_annotation_meta_chain
    # and load_brownfield_overrides are lru_cached per (resolved) project root;
    # without warming, the first wave of concurrent process_java_file worker
    # threads each cold-miss and redundantly walk+parse the ENTIRE project (a
    # thundering herd that would offset the embedding-batching win on large
    # repos — perf lever #2 made enrich concurrent). With warming, every worker
    # hits a populated cache (lru_cache reads are thread-safe). Key derivation
    # mirrors enrich_chunk exactly so the warmed entries are the ones workers hit.
    try:
        load_brownfield_overrides(project_root)
        try:
            prs = str(Path(project_root).resolve())
        except OSError:
            prs = str(project_root)
        collect_annotation_meta_chain(prs)
    except Exception:
        # Warm-up must never break indexing — a failure just means workers
        # cold-miss lazily (the pre-warming behavior). Swallow and continue.
        pass
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

    # PERF: declare all rows in ONE component (app_main) instead of one
    # component per file via coco.mount_each. cocoindex flushes target writes
    # once per processing component, and all declare_row calls inside a
    # component batch into a single Lance merge_insert (see _RowHandler.
    # _apply_actions). mount_each created one component PER FILE → ~1167
    # merge_insert transactions (one fragment + manifest commit each) → ~91s
    # of kernel I/O on a 1167-file repo. The single-component loop collapses
    # that to ONE merge_insert per table. cocoindex does not yet batch across
    # mount_each components natively (open issue cocoindex#2219), so the loop
    # is the supported workaround. process_*_file stay @coco.fn(memo=True), so
    # unchanged files still skip re-embedding on incremental; _RowHandler.
    # reconcile skips rows whose fingerprint is unchanged → increment carries
    # only changed rows in its single merge_insert.
    #
    # PERF (concurrency): drain files with a bounded semaphore instead of a
    # serial ``async for … await``. See ``_FILE_CONCURRENCY`` — this is what
    # lets the embedder's batching layer fill real batches (embedding dominates
    # init cost, and serial files starve it). One shared semaphore bounds total
    # in-flight work; tables are drained in order (java dominates, sql/yaml are
    # usually near-empty).
    _sem = asyncio.Semaphore(_FILE_CONCURRENCY)
    await _drain_files_concurrently(java_files, process_java_file, java_table, _sem)
    await _drain_files_concurrently(sql_files, process_sql_file, sql_table, _sem)
    await _drain_files_concurrently(yaml_files, process_yaml_file, yaml_table, _sem)


app = coco.App(
    coco.AppConfig(name="JavaCodeIndexLance"),
    app_main,
)