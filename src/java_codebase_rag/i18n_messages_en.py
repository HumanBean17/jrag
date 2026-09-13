"""English runtime catalog (MSG_/ERR_/LBL_/HINT_ keys).

Key namespaces beyond the core MSG_/ERR_/LBL_/HINT_ set: WARN_ (envelope
warnings), INST_ (installer wizard), RS_/ABS_ (shared resolve/absence
producers). Values are ``str`` templates (``{placeholder}`` formatting) or, for plural
keys, dicts of forms (en: ``one``/``other``). EN entries are the byte-exact
pre-i18n strings — golden payloads and render tests pin them.
"""
from __future__ import annotations

from typing import Any

MESSAGES: dict[str, Any] = {
    # Unified dispatcher help section header (byte-exact pre-i18n string).
    "MSG_UNIFIED_OPERATOR_HEADER": (
        "Operator commands (indexing & maintenance; run `jrag <command> --help` "
        "for details):\n"
    ),
    # Absence verdict labels + render prefixes (jrag_render).
    "LBL_ABSENCE_NOT_IN_PROJECT": "not in project",
    "LBL_ABSENCE_EXTERNAL_DEPENDENCY": "external dependency",
    "LBL_ABSENCE_REFINE_QUERY": "refine your query",
    "LBL_ABSENCE_CORRECT_EMPTY": "correct empty",
    "LBL_VERDICT_PREFIX": "Verdict: ",
    "LBL_NEXT_PREFIX": "next: ",
    "LBL_WARNING_PREFIX": "warning: ",
    "LBL_ERROR_PREFIX": "error: ",
    "LBL_ERROR_WORD": "error",
    "LBL_NOT_FOUND_PREFIX": "not found: ",
    "LBL_NOT_FOUND_WORD": "not found",
    # Truncation notices.
    "MSG_TRUNCATED_OFFSET": "truncated: more results — use --offset {offset}",
    "MSG_TRUNCATED_NARROW": "truncated: more results — narrow your query",
    # Did-you-mean (not_found + closest_symbols).
    "MSG_DID_YOU_MEAN_ONE": "Did you mean: {sym}?",
    "MSG_DID_YOU_MEAN_TWO": "Did you mean: {a} or {b}?",
    "MSG_DID_YOU_MEAN_MANY": "Did you mean: {list}?",
    "LBL_OR": ", or {last}",
    # Zero-result lines (listing + traversal).
    "MSG_ZERO_LISTING": "0 {noun}",
    "MSG_EXTERNAL_ENTRYPOINT": "external entrypoint — no in-repo callers",
    # Row labels.
    "LBL_NO_IDENTIFIER": "(no identifier)",
    "LBL_MISSING": "(missing)",
    "LBL_UNRESOLVED": "(unresolved)",
    "LBL_ROOT_PREFIX": "root: ",
    # Grouped-traversal headers.
    "LBL_INBOUND": "inbound:",
    "LBL_OUTBOUND": "outbound:",
    "LBL_SUPERTYPES": "↑ supertypes:",
    "LBL_SUBTYPES": "↓ subtypes:",
    "LBL_STAGE_SEED": "stage 0 (seed):",
    "LBL_STAGE_ROLES": "stage {n} ({roles}):",
    "LBL_STAGE": "stage {n}:",
    # Ambiguous renderer. EN one == other on purpose: the pre-i18n string was
    # "{n} ambiguous matches" for every n; byte-stability outranks grammar.
    "MSG_AMBIGUOUS_HEADER": {
        "one": "{n} ambiguous matches for '{noun}'",
        "other": "{n} ambiguous matches for '{noun}'",
    },
    "MSG_AMBIGUOUS_HEADER_NO_NOUN": {
        "one": "{n} ambiguous matches",
        "other": "{n} ambiguous matches",
    },
    "MSG_NARROW": "Narrow with --kind --java-kind --role --fqn-contains:",
    # Error paths in jrag.main() and authored envelope messages (Task 5).
    "ERR_USAGE_WORD": "usage error",
    "ERR_INTERNAL": "internal error: {exc}",
    "LBL_JRAG_ERROR_STDERR": "jrag: error: ",
    "MSG_INTERRUPTED": "\nInterrupted.\n",
    "MSG_NO_INDEX": "No index at {path}. Run: jrag init --source-root <root>",
    "ERR_INDEX_META_FAILED": "Index meta read failed: {error}",
    "ERR_INVALID_FILTER": "invalid filter: {message}",
    "ERR_DESCRIBE_FAILED": "describe failed",
    "ERR_NEIGHBORS_FAILED": "neighbors_v2 failed",
    "ERR_SEARCH_FAILED": "search failed",
    "ERR_OUTLINE_FAILED": "outline failed: {exc}",
    "ERR_READ_FAILED": "could not read {path}: {exc}",
    "ERR_FILE_NOT_FOUND": (
        "file not found: '{file}' (looked at the literal path and at "
        "<source_root>/{file})"
    ),
    "ERR_NO_BACKEND": (
        "no language backend registered for '{file}' "
        "(suffix '{suffix}' not in registry)"
    ),
    "ERR_INVALID_FRAMEWORK": (
        "invalid framework: '{framework}' (normalized to '{normalized}'); "
        "expected one of: {valid}"
    ),
    "ERR_OVERVIEW_AS_ROUTE": (
        "overview --as route expects a Route; resolved kind is '{kind}'."
    ),
    "MSG_AMBIGUOUS_CANDIDATES": {"one": "{n} candidate", "other": "{n} candidates"},
    # Auto-scope notices (stderr line + envelope warnings[] value).
    "MSG_AUTO_SCOPE_STDERR": "[jrag] auto-scope: --service {svc} (cwd)",
    "WARN_AUTO_SCOPE": (
        "auto-scope: --service {svc} (inferred from cwd; "
        "pass --no-auto-scope to disable)"
    ),
    # Watch lifecycle lines.
    "MSG_WATCH_UP": "jrag watch: up (pid {pid}, socket {sock})",
    "MSG_WATCH_DOWN": "jrag watch: down (no daemon at {sock})",
    "MSG_WATCH_STOPPED": "jrag watch: stopped (pid {pid})",
    "MSG_WATCH_DETACHED": "jrag watch: detached (pid {pid}, socket {sock}, log {log})",
    "MSG_WATCH_CHILD_EXITED": "jrag watch: child exited before serving (see {log})",
    "MSG_WATCH_START_TIMEOUT": "jrag watch: failed to start within {seconds}s (see {log})",
    "MSG_WATCH_LAST_REINDEX": "  last reindex: {kind} at {when} (total {count})",
    "MSG_WATCH_LAST_REINDEX_NONE": "  last reindex: none (total {count})",
    "LBL_WATCH_MODE": "  mode: {label}",
    # vocab-index stderr lines.
    "ERR_VOCAB_STDERR": "[error] {exc}",
    "ERR_VOCAB_BUILD_FAILED": "[error] Vocabulary index build failed: {exc}",
    # Operator CLI: lazy advisory twins of the frozen module constants (the
    # constants stay English forever — MCP consumes them directly; spec D2/D4).
    "MSG_INCREMENT_WARNING": (
        "WARNING: AST graph (LadybugDB) incremental rebuild is not yet implemented.\n"
        "The graph reflects the index state from the last `init` or `reprocess`,\n"
        "which means `find`, `neighbors`, and `describe` may return stale results\n"
        "for files changed since then.\n"
        "\n"
        "Lance vector index has been updated incrementally and is current.\n"
        "\n"
        "For an up-to-date graph, run:\n"
        "    jrag reprocess\n"
        "\n"
        "Track progress on LadybugDB incremental rebuild:\n"
        "    {url}"
    ),
    "MSG_REFRESH_DEPRECATION": (
        "WARN: 'refresh' is deprecated; use 'reprocess'. "
        "This alias will be removed in the next release."
    ),
    "MSG_REPROCESS_DRIFT_VECTORS_ONLY": (
        "jrag reprocess: rebuilt vectors only; graph (code_graph.lbug) was NOT rebuilt "
        "and may now reflect a stale source snapshot."
    ),
    "MSG_REPROCESS_DRIFT_GRAPH_ONLY": (
        "jrag reprocess: rebuilt graph only; vectors (Lance tables under "
        "{index_dir}) were NOT rebuilt and may now reflect a stale source snapshot."
    ),
    "MSG_VECTORS_SKIPPED_GRAPH_ONLY": (
        "jrag: vectors skipped — vector stack not installed on this platform "
        "(graph-only mode). The graph is built/refreshed; semantic search is unavailable."
    ),
    "MSG_VECTORS_SKIPPED_BM25": (
        "jrag: vectors skipped — retrieval mode is bm25; building graph only."
    ),
    "MSG_RETRIEVAL_BM25_HINT": (
        "Tip: can't download the embedding model? Switch to keyword search: "
        "jrag install --retrieval bm25 (or set JAVA_CODEBASE_RAG_RETRIEVAL=bm25) "
        "— indexing and search then work fully offline."
    ),
    "MSG_DEPRECATION_NOTICE": (
        "jrag: 'java-codebase-rag' is now 'jrag'; this alias continues to work. "
        "Set JRAG_NO_DEPRECATION=1 to silence.\n"
    ),
    # Operator erase flow.
    "MSG_ERASE_WILL_DELETE": "Will delete:",
    "MSG_ERASE_NOTHING": "  (nothing to delete under resolved index dir)",
    "MSG_ERASE_CONFIRM": "Delete these paths? [y/N]: ",
    "MSG_ERASE_NON_INTERACTIVE": (
        "jrag erase: non-interactive stdin; pass --yes to confirm."
    ),
    "MSG_ERASE_ABORTED": "Aborted.",
    "MSG_ERASE_COCO_MISSING": (
        "jrag erase: cocoindex CLI not found next to this Python; "
        "skipped `cocoindex drop` — cocoindex.db (if any) was not removed by CocoIndex."
    ),
    "MSG_ERASE_DROPPED": "jrag: erase: dropped Lance tables: {tables}",
    "MSG_WARN_RM_FAILED": "warning: failed to remove {path}: {exc}",
    # Reprocess selective-mode TTY lines.
    "MSG_REBUILT_VECTORS": "Rebuilt: vectors",
    "MSG_SKIPPED_GRAPH": (
        "Skipped: graph (use `jrag reprocess --graph-only` or `reprocess` to refresh)"
    ),
    "MSG_REBUILT_GRAPH": "Rebuilt: graph",
    "MSG_REPROCESS_COMPLETED_VECTORS": "reprocess completed (vectors only; graph not rebuilt)",
    "MSG_REPROCESS_COMPLETED_GRAPH": "reprocess completed (graph only; vectors not rebuilt)",
    "MSG_REPROCESS_COMPLETED": "reprocess completed",
    "MSG_REPROCESS_COMPLETED_BM25": "reprocess completed (graph-only; vectors skipped — retrieval mode is bm25)",
    "MSG_SKIPPED_VECTORS": (
        "Skipped: vectors (use `jrag reprocess --vectors-only` or `reprocess` to refresh)"
    ),
    # Result-kind nouns (EN values are the identity tokens passed at the seam).
    "LBL_NOUN_MATCHES": "matches",
    "LBL_NOUN_CALLERS": "callers",
    "LBL_NOUN_CALLEES": "callees",
    "LBL_NOUN_IMPLEMENTATIONS": "implementations",
    "LBL_NOUN_SUBCLASSES": "subclasses",
    "LBL_NOUN_OVERRIDES": "overrides",
    "LBL_NOUN_OVERRIDDEN_BY": "overridden-by",
    "LBL_NOUN_DEPENDENTS": "dependents",
    "LBL_NOUN_IMPACT": "impact",
    "LBL_NOUN_DECOMPOSE": "decompose",
    "LBL_NOUN_DEPENDENCIES": "dependencies",
    "LBL_NOUN_CONNECTION": "connection",
    "LBL_NOUN_HIERARCHY": "hierarchy",
    "LBL_NOUN_ROUTE": "route",
    "LBL_NOUN_CLIENT": "client",
    "LBL_NOUN_PRODUCER": "producer",
    "LBL_NOUN_TOPIC": "topic",
    "LBL_NOUN_SYMBOL": "symbol",
    "LBL_NOUN_IMPORT": "import",
    "LBL_NOUN_MICROSERVICES": "microservices",
    "LBL_NOUN_MAP": "map",
    "LBL_NOUN_CONVENTIONS": "conventions",
    "LBL_NOUN_OVERVIEW": "overview",
    # Installer wizard (Task 7). YAML keys / choice values stay literal.
    "INST_NOTE_HOSTS": "Note: You can select multiple agent hosts with Space. Navigate with arrow keys.",
    "INST_PROMPT_HOSTS": "Select agent hosts to configure:",
    "INST_RETRY_HOSTS": "At least one agent host is required. Re-select hosts?",
    "INST_WILL_DEPLOY": "Will deploy to: {names}",
    "INST_ERR_UNKNOWN_AGENT": "Error: Unknown agent '{agent}'. Valid agents: {valid}",
    "INST_ERR_AGENT_REQUIRED": "Error: --agent flag is required in non-interactive mode.",
    "INST_VALID_AGENTS": "Valid agents: {valid}",
    "INST_NOTE_MODULES": "Note: Select which modules to index. Toggle with Space, confirm with Enter.",
    "INST_PROMPT_MODULES": "Select microservices to index:",
    "INST_RETRY_MODULES": "At least one module is required. Re-select?",
    "INST_ERR_SCOPE": "Error: Invalid scope '{scope}'. Must be 'project' or 'user'.",
    "INST_NOTE_SCOPE_PROJECT": "Note: 'project' scope stores configs in the project directory.",
    "INST_NOTE_SCOPE_USER": "      'user' scope stores configs in your home directory.",
    "INST_SELECTED_SCOPE": "Selected scope: {scope}",
    "INST_ERR_SURFACE": "Error: Invalid surface '{surface}'. Must be 'mcp' or 'cli'.",
    "INST_ERR_RETRIEVAL": "Error: Invalid retrieval '{retrieval}'. Must be 'vectors' or 'bm25'.",
    "INST_NOTE_RETRIEVAL": (
        "Note: 'vectors' needs an embedding model (auto-downloaded from Hugging "
        "Face, or a local path); 'bm25' is keyword search — no model, no "
        "downloads, works offline. In bm25 mode the Lance source table is not "
        "searched (Java/Kotlin symbols only)."
    ),
    "INST_PROMPT_RETRIEVAL": "Select retrieval mode:",
    "INST_INDEX_EXISTS": "Index already exists. Run `jrag reprocess` to rebuild.",
    "INST_NO_CONFIG": "\nNo project configuration found (.java-codebase-rag.yml).",
    "INST_SKIPPING_UPDATE": "Skipping index update.",
    "INST_WARN_RESOLVE_FAIL": "\nWarning: Failed to resolve configuration: {exc}",
    "INST_NO_INDEX": "\nNo index found.",
    "INST_RUN_INSTALL": "Run `jrag install` to create one.",
    "INST_UPDATE_COMPLETE": "\nUpdate complete.",
    "INST_UPDATED_ARTIFACTS": "Updated {n} artifact(s).",
    "INST_CONFIG_WRITTEN": "Configuration written to {path}",
    "INST_FOUND_CONFIG": "Found existing config at {path}",
    "INST_CURRENT_CONFIG": "Current configuration:",
    "INST_WARN_PARSE_FAIL": "Warning: Failed to parse existing config: {exc}",
    "INST_WARN_SOME_FAILED_DEPLOY": "Warning: Some artifacts failed to deploy:",
    "INST_WARN_SOME_FAILED_UPDATE": "\nWarning: Some artifacts failed to update:",
    "INST_ARTIFACT_ROW": "  {path}: {error}",
    "INST_CONTINUING": "Continuing (MCP configs deployed successfully)...",
    "INST_WOULD_RUN_INCREMENTAL": "\nWould run incremental index update (Lance + graph).",
    "INST_ERR_NOT_A_DIR": "Error: Path {path} does not exist or is not a directory.",
    "INST_WARN_MODEL_FALLBACK": "Warning: Model path {model} not found, falling back to 'auto'.",
    # Shared producers: resolve + absence diagnosis (Task 8, spec D7).
    # EN values are byte-exact; MCP consumes these modules with locale=en.
    "ERR_INVALID_IDENTIFIER": "Invalid identifier: {detail}",
    "RS_DETAIL_EMPTY": "empty string",
    "RS_DETAIL_WS": "whitespace only",
    "RS_NO_MATCHES": "No matches for identifier; use search(query=...) for ranked fuzzy lookup.",
    "RS_WILDCARDS": (
        "Wildcards (* and ?) are not supported in resolve; "
        "use search(query=...) for ranked text search."
    ),
    "ABS_EXTERNAL": (
        "`{fqn}` is referenced by this project but not defined in it "
        "({reason}). It is an external dependency."
    ),
    "ABS_NO_NODE_ID": (
        "No node with id `{query}`. Run `resolve` to map a name/FQN to an id, "
        "or `search` to discover symbols."
    ),
    "ABS_EMPTY_INDEX": (
        "Index appears empty/unindexed — verify the project was indexed "
        "before concluding a symbol is absent."
    ),
    "ABS_NL_MISS": (
        "No symbol matches `{query}`. Refine the query — try an identifier "
        "(class/method/FQN) or browse the project vocabulary below."
    ),
    "ABS_FILTER_MISS_CLOSE": (
        "No results for `{identifier}` under the current filter. "
        "Close matches exist — try relaxing a dimension (see filter_relaxation)."
    ),
    "ABS_FILTER_MISS": (
        "No results under the current filter. Matches exist under other values "
        "(see filter_relaxation)."
    ),
    "ABS_NEIGHBORS_MEANINGFUL": (
        "`{node}` has no neighbors of the requested type here — this is a "
        "genuine leaf / external entrypoint, not an error."
    ),
    "ABS_NEIGHBORS_MISS": (
        "No neighbors for `{node}` with the requested edge type/direction. "
        "Run `describe` and inspect `edge_summary` for the edge types this "
        "node actually participates in."
    ),
    "ABS_NOT_IN_PROJECT": (
        "No symbol matching `{query}` was found in the project vocabulary. "
        "It does not appear to be defined here."
    ),
    "ABS_CLOSEST": (
        "No exact match for `{query}`. Closest symbols: {names}. "
        "Refine the query (typo? scope?) and retry."
    ),
    "ABS_NO_MATCH_PLAIN": "No match for `{query}`. Refine the query and retry.",
    "ABS_CAPABILITY_HEAD": "This index contains 0 {subject_noun} —",
    "ABS_CAPABILITY_MID": (
        " any query on {subject} returns empty regardless of arguments — "
        "don't retry it."
    ),
    "ABS_CAPABILITY_TAIL_REDIRECT": (
        " For what you need, use the edge types this index does have (e.g. {named})."
    ),
    "ABS_CAPABILITY_TAIL_FIND": " For symbol discovery use `find`/`search` instead.",
    "ABS_UNABLE": "Unable to diagnose the empty result; refine the query and retry.",
    # Review follow-up: remaining installer/jrag/cli operator strings.
    "INST_ERR_NO_JAVA": (
        "Error: No Java build files (pom.xml, build.gradle, build.gradle.kts, "
        "build.sbt) found in {root} or its subtree."
    ),
    "INST_PROMPT_SOURCE_ROOT": "Source root:",
    "INST_MODEL_NOT_FOUND_CONFIRM": "Model path {model} not found. Use 'auto' instead?",
    "INST_PROMPT_MODEL": "Enter model path (or 'auto'):",
    "INST_PROMPT_MODEL_FULL": "Embedding model path (or 'auto'):",
    "INST_PROMPT_SCOPE": "Select installation scope:",
    "INST_NOTE_SURFACE_CLI": (
        "Note: 'cli' surface deploys the `jrag` console-script skill+subagent "
        "(one command per intent, no MCP server) — recommended."
    ),
    "INST_NOTE_SURFACE_MCP": (
        "      'mcp' surface registers the java-codebase-rag MCP server "
        "(5 tools: search/find/describe/neighbors/resolve)."
    ),
    "INST_PROMPT_SURFACE": "Select agent surface:",
    "INST_ERR_BINARY_NOT_FOUND": "Error: `{name}` not found on PATH.",
    "INST_ENSURE_JRAG": (
        "Ensure `jrag` is installed, then re-run with `--non-interactive --agent <host>`."
    ),
    "INST_ENSURE_CONSOLE": (
        "Ensure the `jrag` console script is installed, "
        "then re-run with `--non-interactive --agent <host>`."
    ),
    "INST_WARN_BINARY_NOT_FOUND": "Warning: `{name}` not found on PATH.",
    "INST_PROMPT_BINARY_PATH": "Enter the full path to {name} (or 'abort'):",
    "INST_ERR_NOT_A_FILE": "Error: Path {path} does not exist or is not a file.",
    "INST_WARN_NOT_EXECUTABLE": "Warning: {path} is not executable. This may cause issues.",
    "INST_ERR_COCO": "Error: CocoIndex update failed with code {code}",
    "INST_ERR_AST": "Error: AST graph build failed with code {code}",
    "INST_VECTORS_SKIPPED_INSTALL": (
        "jrag: vectors skipped — vector stack not installed on this "
        "platform (graph-only mode). Building graph only; semantic search is unavailable."
    ),
    "INST_VECTORS_SKIPPED_UPDATE": (
        "jrag: vectors skipped — vector stack not installed on this "
        "platform (graph-only mode). Running graph catch-up only."
    ),
    "INST_PROMPT_CHOOSE_ACTION": "Choose an action:",
    "INST_WARN_MARKER_WRITE": "Warning: failed to write {path}: {exc}",
    "INST_WOULD_UPDATE_FILE": "Would update {kind} file at {path}",
    "INST_WOULD_CREATE_FILE": "Would create {kind} file at {path}",
    "INST_UPDATED_FILE": "Updated {kind} file at {path}",
    "INST_WOULD_UPDATE_MCP": "Would update MCP config at {path}",
    "INST_UPDATED_MCP": "Updated MCP config at {path}",
    "INST_WOULD_REMOVE_MCP": "Would remove MCP entry from {path}",
    "INST_REMOVED_MCP": "Removed MCP entry from {path}",
    "INST_WOULD_REMOVE_FILE": "Would remove {path}",
    "INST_REMOVED_FILE": "Removed {path}",
    "INST_NO_HOSTS": "No configured agent hosts found.",
    "INST_RUN_INSTALL_FIRST": "Run `jrag install` first.",
    "INST_FOUND_HOSTS": "Found {n} configured host(s).",
    "INST_NOTE_MULTI_SURFACE_OWN": (
        "Note: configured hosts span multiple surfaces ({surfaces}); "
        "refreshing each on its own recorded surface (pass --surface to normalize)."
    ),
    "INST_NOTE_MULTI_SURFACE_NORMALIZE": (
        "Note: configured hosts span multiple surfaces ({surfaces}); "
        "normalizing to '{surface}'."
    ),
    "INST_ERR_MIGRATE_BINARY": (
        "Error: `{name}` not found on PATH — cannot migrate to the '{surface}' surface."
    ),
    "INST_ENSURE_MIGRATE": (
        "Ensure `jrag` is installed, then re-run `update --surface {surface}`."
    ),
    "INST_MIGRATING": "\nMigrating {name} ({scope} scope): {from_s} → {to_s}...",
    "INST_WOULD_TEAR_DOWN": (
        "  Would tear down {from_s} artifacts and deploy {to_s} artifacts."
    ),
    "INST_REFRESHING": "\nRefreshing {name} ({scope} scope, surface={surface})...",
    "INST_ERR_LANCE": "Error: Lance index update failed with code {code}",
    "INST_WARN_INCREMENTAL_GRAPH": (
        "\nWarning: incremental graph update failed (exit {code}). "
        "Run `jrag reprocess` for a full rebuild."
    ),
    "INST_SKIP_MODEL_GRAPH_ONLY": (
        "Skipping embedding model selection: vector stack not installed on this "
        "platform (graph-only mode)."
    ),
    "INST_SKIP_MODEL_BM25": (
        "Skipping embedding model selection: retrieval mode is bm25 "
        "(keyword search; no model needed)."
    ),
    "INST_ERR_DIR_NOT_WRITABLE": "Directory not writable: {path}",
    "INST_ERR_WRITE_FAILED": "Failed to write {path}: {exc}",
    "INST_ERR_REMOVE_FAILED": "Failed to remove {path}: {exc}",
    # jrag/cli residue.
    "MSG_VOCAB_REBUILT": "Vocabulary index rebuilt successfully:",
    "MSG_VOCAB_SYMBOL_COUNT": "  Symbol count: {n}",
    "MSG_VOCAB_SIDECAR": "  Sidecar path: {path}",
    "ERR_VOCAB_SAVE_FAILED": "[error] Failed to save vocabulary index: {exc}",
    "MSG_WATCH_NOT_RUNNING": "jrag watch: not running",
    "MSG_PRIME_STDERR": "jrag prime: {msg}",
    "MSG_WARN_EXISTING_CONFIG": (
        "Warning: found existing config at {path}. "
        "Creating a new project here will create a separate index."
    ),
    "MSG_WARN_EXISTING_INDEX": (
        "Warning: found existing index at {path}. "
        "Creating a new project here will create a separate index."
    ),
    "LBL_CLI_ARG_ERROR_STDERR": "jrag: ",
    "MSG_INCREMENT_FALLBACK": (
        "[increment] fell back to full graph rebuild — this is normal after "
        "schema changes or first run"
    ),
}
