# Codebase requirements & MCP tuning guide

This document explains how to get the best out of the `lancedb-code` MCP
(LanceDB vector index + Kuzu AST graph + role-aware ranking) on a Java
codebase, and — if you cannot or will not change the codebase — exactly
**which files in this bundle to edit** so the MCP adapts to your project.

The MCP's quality on any given repo is a product of three things:

1. **What it parses** — Tree-sitter Java only; no Kotlin/Groovy/Scala.
2. **What it classifies** — role inference, service inference, and DI
   detection are driven by a small list of annotation/marker names.
3. **How it ranks** — role weights, type-name overlap, action-verb bumps,
   and graph-expansion fusion.

If your codebase matches the assumptions below, the MCP will behave well
out of the box. If it doesn't, you have two options: change the codebase
(Section A) or change the MCP (Section B).

---

## Section A — Recommendations for the Java codebase

Treat these as a checklist; each item maps directly to an inference path
inside the MCP.

### A.1 Language & build

- **Java only.** The file walker filters strictly on `*.java`; Kotlin,
  Groovy, Scala, and mixed-language source files are skipped entirely
  (not "partially parsed"). Parsing is done via `tree_sitter_java`.
  - See: `ast_java.py` (the parser), `build_ast_graph.py::_iter_java_files`
    (only `*.java`).
- **Source under `src/main/java/...`.** Test sources under
  `src/test/java/` and `src/test/resources/` are intentionally excluded
  from both the LanceDB vector index and the Kuzu graph build.
  - See: `java_index_v1_common.py::COMMON_EXCLUDED_PATH_PATTERNS`.
- **Two location concepts: `module` and `microservice`.** The MCP
  infers both by walking up from each `.java` file until it finds a
  build marker (`pom.xml`, `build.gradle`, `build.gradle.kts`,
  `build.sbt`).
  - **`module`** — the *innermost* build-marker ancestor's directory
    name. For a single-module project this equals the microservice
    name; for a multi-module Maven/Gradle reactor it's the *child*
    module name (e.g. `chat-app`).
  - **`microservice`** — the *outermost* build-marker ancestor under
    `LANCEDB_MCP_PROJECT_ROOT` (e.g. `chat-core` for a reactor whose
    children all live under `chat-core/`). Resolution falls back to:
    explicit override (env `LANCEDB_MCP_MICROSERVICE_ROOTS=foo,bar` or
    `microservice_roots: [foo, bar]` in `.lancedb-mcp.yml` at the
    project root) → outermost build marker → first path segment under
    `project_root` → empty string.
  - **Recommendation:** name your microservice directories meaningfully
    (`order-service/pom.xml`, not `app/pom.xml` — every microservice
    named `app` would collapse into one bucket).
  - **Monorepo layout without build markers:** add the per-microservice
    directory names to `microservice_roots` in `.lancedb-mcp.yml` (or
    the env var) and the MCP will accept them as the `microservice=...`
    filter values. Anything else returns an empty `microservice` and
    `microservice=` filters become useless.
  - See: `graph_enrich.py::module_for_path`,
    `graph_enrich.py::microservice_for_path`, constant `BUILD_MARKERS`.
- **Build outputs out of the way.** `target/`, `build/`, `node_modules/`,
  `.idea/`, `.venv/` are pruned during the graph walk. Don't keep
  generated `.java` (e.g., MapStruct, Lombok delombok output, OpenAPI
  generated clients) in committed source trees — they balloon the graph
  with phantom edges.

**Important:** `module` and `microservice` inference depends on the
**project root** used during indexing:

- For the CocoIndex flow (`java_index_flow_lancedb.py`), `project_root`
  is the **current working directory** when you launch `cocoindex update`
  (hardcoded as `Path(".").resolve()` in `coco_lifespan`).
- For `build_ast_graph.py` standalone, it's `--source-root` (defaults
  to `cwd`).
- For MCP runtime, `LANCEDB_MCP_PROJECT_ROOT` is used only by
  `refresh_code_index` to resolve the indexer's working directory.

Consistency across builds requires running the indexer from the same
directory (or using an absolute `--source-root`).

### A.2 Annotations the MCP knows about (role inference)

Roles are assigned **first hit wins** from the type's annotations
(see `ast_java.py::ROLE_ANNOTATIONS`):

| Annotation | Role assigned |
|------------|---------------|
| `@RestController`, `@Controller` | `CONTROLLER` |
| `@Service` | `SERVICE` |
| `@Repository` | `REPOSITORY` |
| `@Component` | `COMPONENT` |
| `@Configuration` | `CONFIG` |
| `@Entity`, `@MappedSuperclass`, `@Embeddable` | `ENTITY` |
| `@FeignClient` | `FEIGN_CLIENT` |
| `@Mapper` | `MAPPER` |

**Recommendations:**

- **Use Spring stereotypes consistently.** A "service" class without
  `@Service` will be classified as `OTHER` (or `DTO` if it has a value
  suffix) and will not benefit from the +0.08 SERVICE rank weight.
- **Don't rely on meta-annotations** (e.g., a custom `@DomainService`
  that is itself annotated with `@Service`). The parser sees only the
  annotations *written on the type*. Either keep `@Service` on the class
  itself or add your meta-annotation to the role table (Section B.1).
- **Annotate Feign clients with `@FeignClient`.** This is a
  **class-level** annotation; manually-coded HTTP clients (raw
  `RestTemplate`/`WebClient` wrappers) won't get the `FEIGN_CLIENT`
  boost. Consider switching to Feign or extending the role table.
- **JAX-RS resources** (`@Path`, `@GET`, ...) are not recognised as

### A.3 Capabilities (multi-tag axis)

Capabilities are derived at the **type level**: method-level annotation
evidence is aggregated up to the enclosing type. Per-method capability
storage is intentionally out of scope for the current ontology
(version 3) — see `plans/PLAN-CAPABILITIES-MODEL.md`. The deferred
call-graph layer (`propose/DEFERRED-CALL-GRAPH-PROPOSE.md`) is the
designated place to revisit method-granularity if the need arises.

Capabilities are independent of `role` — a `@Service` can simultaneously
be a `MESSAGE_PRODUCER` and a `MESSAGE_LISTENER`, for example. The
capability set and their triggers are documented in `README.md` under
**Capabilities** and in `ast_java.py::_METHOD_ANN_TO_CAPABILITY` etc.
  controllers. Add them to the role table if your stack is Quarkus /
  Jersey instead of Spring MVC.
- **MapStruct mappers must be annotated `@Mapper`** (this is the
  default; just keep it).

### A.3 Dependency injection patterns the MCP detects

`INJECTS` edges are the backbone of "what calls what" reasoning. The MCP
detects (see `ast_java.py::_INJECT_FIELD_ANNOTATIONS` /
`_LOMBOK_RAC` and `build_ast_graph.py::_emit_injects`):

- **Field injection:** `@Autowired`, `@Inject`, `@Resource`.
- **Constructor injection:**
  - any constructor explicitly annotated `@Autowired`;
  - **otherwise** the single constructor with parameters
    (Spring's "implicit constructor injection" rule).
- **Setter injection:** `setXxx(...)` methods annotated `@Autowired`.
- **Lombok:** `@RequiredArgsConstructor` (every `final` non-static
  field) and `@AllArgsConstructor` (every non-static field).

**Recommendations:**

- **Prefer constructor injection** (idiomatic Spring) — the single
  no-`@Autowired` constructor rule is the most reliable detection path.
- **Don't bypass DI** with `new XxxService()` or `ApplicationContext.
  getBean(...)` — those are invisible to the graph.
- **Avoid `@Qualifier` discrimination by string** as your only mechanism;
  the graph stores the type, not the qualifier, so two beans of the
  same interface look identical here.
- **No CDI / Guice / Dagger.** `@Inject` is detected (it's also a JSR-330
  annotation), but `@Produces`, `@Provides`, modules, and bind-DSLs are
  not modelled. If your app is Guice-heavy, expect a sparse `INJECTS`
  graph.
- **Lombok requires the source-form annotation.** If you delombok before
  indexing, the MCP sees the generated constructor and detects it as
  "single constructor with params" — that still works.

### A.4 Class structure & naming (ranking signals)

Beyond role weights, Java hits get an additive **symbol-match bonus**
(see `search_lancedb.py`, summarised in the README §5 "Ranking"):

- **Type-name overlap** (strongest signal, capped at +0.10): the simple
  name of `primary_type_fqn` is tokenised on CamelCase and compared
  against query tokens. `DistributionChunkService` matches a query about
  "distribution chunk" because its name encodes the domain.
  - **Recommendation:** name classes after the domain concept they own
    (`OrderPlacementService`, not `Helper` or `Util`).
- **Action-verb bonus** (+0.02): methods whose names start with
  `process`, `handle`, `on`, `pick`, `select`, `assign`, `notify`,
  `dispatch`, `publish`, `consume`, `route`, `trigger`, `enqueue`,
  `distribute`, `update`, `create`, `apply`, `resolve`, `reassign`,
  `close`, `open` get a flat bonus on their owning chunk.
  - **Recommendation:** name event-handler / orchestration methods with
    these verbs (`onOrderPlaced`, `processPayment`). Domain-specific
    verbs (`reconcile`, `settle`) are *not* in the list — extend it
    (Section B.2) if your domain uses them heavily.
- **DTO down-ranking.** Records, classes annotated `@Data` / `@Value` /
  `@Builder` / `@Getter` / `@Setter` / `@EqualsAndHashCode` /
  `@ToString`, and classes whose simple name ends in
  `Dto`, `DTO`, `Request`, `Response`, `Payload`, `Model`, `Event`,
  `Message`, `Body`, `Form`, `Command`, `Query`, `Record`, or `View`
  are classified as `DTO` and pushed down with a -0.08 penalty
  (stronger than `ENTITY` at -0.06, but only when annotation-based
  inference yields `OTHER` — e.g. `@Service FooRequest` keeps `SERVICE`).
  - **Recommendation:** keep DTOs as records or with a Lombok value
    annotation, and *don't* mix business logic into them.

### A.5 Files & resources picked up by the vector index

The CocoIndex flow indexes only:

- `**/*.java`
- `**/src/main/resources/db/migration/*.sql` (Flyway naming convention)
- `**/src/main/resources/application*.yml` / `application*.yaml`

**Recommendations:**

- **Use Flyway and put migrations under `db/migration/`.** Liquibase
  XML/YAML changelogs and `schema.sql`/`data.sql` are not indexed. Add
  patterns in B.5 if you use them.
- **Keep Spring config in `application*.yml`.** Profile-specific files
  (`application-prod.yml`) are picked up by the wildcard. Properties
  files (`*.properties`) are *not* indexed — consider migrating to YAML
  or extend the patterns (Section B.5).
- **Don't keep secrets in indexed YAML.** They become embeddings and
  are searchable. Use `${ENV_VAR}` placeholders.

### A.6 Repository hygiene

- **Stable, descriptive package names.** `package` is exposed as a
  filter; `package_prefix=com.acme.orders` is much more useful than
  `package_prefix=com.acme.app`.
- **One top-level type per file** — standard Java practice. The graph
  handles nested and multiple top-level types, but search results
  surface chunk-level hits, so a 5-class file produces noisy ranks.
- **Avoid huge files (>2 000 lines).** Tree-sitter's error-tolerant
  parser handles syntax errors robustly (partial AST is still indexed),
  but very large files with complex nesting may produce noisy chunk
  boundaries.
- **Kuzu graph sidecar location.** The graph defaults to
  `${LANCEDB_URI}/code_graph.kuzu` (or `$KUZU_DB_PATH` if set). If
  your index is at `/data/lancedb_data` but Kuzu ends up elsewhere, the
  MCP will silently operate in vector-only mode (no `find_implementors`,
  `trace_flow`, etc.). Verify both paths match, or set `KUZU_DB_PATH`
  explicitly.

---

## Section B — How to adapt the MCP without changing the codebase

If you can't refactor your repo, change the MCP. Each subsection points
at the **exact file and symbol** to edit.

### B.1 Add or rename role-defining annotations

You'd do this if:

- you use **JAX-RS** (`@Path`) instead of Spring MVC;
- your team has **custom stereotypes** (`@DomainService`, `@UseCase`,
  `@ApplicationService`, `@CommandHandler`, ...);
- you want **Quarkus**, **Micronaut**, or **gRPC service** annotations
  to count.

**File:** `ast_java.py`
**Symbol:** `ROLE_ANNOTATIONS`

```python
ROLE_ANNOTATIONS: dict[str, str] = {
    "RestController": "CONTROLLER",
    "Controller": "CONTROLLER",
    "Path": "CONTROLLER",                 # JAX-RS
    "Service": "SERVICE",
    "DomainService": "SERVICE",           # your custom stereotype
    "UseCase": "SERVICE",
    "GrpcService": "SERVICE",             # net.devh / Micronaut
    "Repository": "REPOSITORY",
    "Component": "COMPONENT",
    "Configuration": "CONFIG",
    "Entity": "ENTITY",
    "MappedSuperclass": "ENTITY",
    "Embeddable": "ENTITY",
    "FeignClient": "FEIGN_CLIENT",
    "RegisterRestClient": "FEIGN_CLIENT", # MicroProfile RestClient
    "Mapper": "MAPPER",
}
```

After editing, **rebuild the graph** (`refresh_code_index` or
`build_ast_graph.py`) and **re-run the LanceDB indexer** so per-chunk
`role` values are recomputed.

If you introduce a brand new role string (e.g. `"USE_CASE"`), also add
its weight in `search_lancedb.py` — search for the constant
`ROLE_WEIGHTS` (it lives near the top of the file). Otherwise the new
role gets weight 0 and won't be boosted.

### B.2 Adjust ranking weights & action-verb list

You'd do this if:

- your domain uses verbs like `reconcile`, `settle`, `redeem`, `quote`;
- you want CONFIG/ENTITY *not* to be downranked (e.g. you're answering
  schema questions);
- you want a stronger or weaker boost for orchestrators.

**File:** `search_lancedb.py`

- **Role weights:** look for the dict literal mapping roles to floats
  (`CONTROLLER: 0.10`, `SERVICE: 0.08`, ...). Set whatever you need;
  zero them all out to disable the boost entirely.
- **Action verbs:** look for the tuple/set literal that contains
  `process`, `handle`, `on`, ... and add your domain verbs.
- **Caps:** the per-bonus caps (`+0.06` / `+0.10`) are also literals
  in the same file — increase them if your domain class names are very
  specific and you trust the signal.

These changes are **runtime-only** (no re-index needed). Restart the
MCP server.

### B.3 Add or change DI mechanisms

You'd do this if:

- your code uses a custom field annotation (`@LazyInject`, `@Wire`);
- you use **Dagger** / **Guice** modules (not auto-detected; you'd need
  custom logic);
- you use **CDI** with `@Produces` (still requires custom logic, but
  field-side `@Inject` already works).

**File:** `ast_java.py`
**Symbols:** `_INJECT_FIELD_ANNOTATIONS`, `_LOMBOK_RAC`

```python
_INJECT_FIELD_ANNOTATIONS = frozenset({
    "Autowired", "Inject", "Resource",
    "LazyInject", "Wire",                # add your own
})
_LOMBOK_RAC = frozenset({
    "RequiredArgsConstructor",
    "AllArgsConstructor",
    "NoArgsConstructor",                 # only if you use it for DI
})
```

If you need a different *mechanism* (e.g. method-level Guice `@Provides`),
you'll need to extend `build_ast_graph.py::_emit_injects` — that is
where field/constructor/setter scanning happens.

Rebuild the Kuzu graph after editing.

### B.4 Change module / microservice inference / pruning

You'd do this if:

- your monorepo doesn't use per-microservice `pom.xml` (or the
  microservice root isn't itself a build module) and the MCP can't
  group symbols correctly;
- you use a build system the MCP doesn't recognise (`package.json`,
  `Cargo.toml`, `BUILD.bazel`, custom marker file);
- you want to exclude additional directories (generated code, vendored
  forks).

**No-code option (recommended first):** drop a `.lancedb-mcp.yml` at
the project root listing the directory names that should be treated as
microservice roots, or set `LANCEDB_MCP_MICROSERVICE_ROOTS=foo,bar` in
the env. The override list wins over structural inference.

```yaml
# .lancedb-mcp.yml
microservice_roots:
  - order-service
  - billing-service
  - notifications
```

**Code-level changes:**

- `graph_enrich.py::BUILD_MARKERS` — add new marker filenames so both
  `module_for_path` and `microservice_for_path` discover them.
- `graph_enrich.py::microservice_for_path` — adjust the fallback rules
  (e.g. promote `services/<name>/...` segments).
- `java_index_v1_common.py::COMMON_EXCLUDED_PATH_PATTERNS` — append
  globs like `**/generated/**`, `**/openapi/**`, `**/legacy/**`.
- `build_ast_graph.py::_iter_java_files` — extra hard-coded directory
  names to prune (`target`, `build`, `node_modules`, ...).

A **chunk-index re-build** is required if you change exclusion patterns;
a **graph re-build** is required if you change module / microservice
inference (and the `ONTOLOGY_VERSION` bump triggers it automatically
when the schema changes).

### B.5 Index more file types (properties, Liquibase, Kotlin DSL configs)

You'd do this if:

- you use `*.properties` instead of YAML;
- you use Liquibase (`db/changelog/*.xml` or `*.yaml`);
- you keep config in `bootstrap.yml`, `*.conf` (HOCON), or `*.toml`.

**File:** `java_index_flow_lancedb.py`
**Symbol:** `app_main()`'s `localfs.walk_dir(... included_patterns=[...])`
calls (one per table — Java / SQL / YAML).

Add patterns to the existing `yaml_files` matcher, or declare a new
`@dataclass` chunk type + new `@coco.fn process_xxx_file` + new table.

For brand-new file types you'll also want to teach the MCP server what
table to expose: see `search_lancedb.py::TABLES` (the dict mapping
`"java"` / `"sql"` / `"yaml"` to LanceDB table names).

A **full re-index** is required.

### B.6 Tune chunk sizes

You'd do this if:

- your methods are unusually long and get split mid-body;
- your files are tiny and the current 1500-char window swallows whole
  classes (good in theory, but means less granularity in results).

**File:** `java_index_v1_common.py`
**Symbols:** `JAVA_CHUNK`, `SQL_CHUNK`, `YAML_CHUNK` — `(chunk_size,
min_chunk_size, overlap)`.

Re-index after changing.

### B.7 Switch the embedding model

You'd do this if:

- your codebase is non-English (variable / comment names) and a
  multilingual model would help;
- you have GPU/MPS budget for a larger model
  (`all-mpnet-base-v2`, `bge-large`).

**Settings:**

- Set env `SBERT_MODEL=<hub-id-or-local-dir>` for both the indexer and
  the MCP (they must match exactly).
- Set env `SBERT_DEVICE=cuda` / `mps` / `cpu`.
- The default (`sentence-transformers/all-MiniLM-L6-v2`) lives in two
  places that must stay in sync:
  - `java_index_v1_common.py::SBERT_MODEL` — used by the indexer.
  - `index_common.py::SBERT_MODEL` — used by the runtime (search / MCP).

A **full re-index** is required.

### B.8 Disable / replace the DTO classifier

You'd do this if:

- your domain has classes named `Order` / `Payment` that are *not*
  records or Lombok values, but the heuristic flags them as DTO via
  some accidental suffix;
- you actively want DTO chunks to rank as `OTHER` (no special handling).

**File:** `ast_java.py`
**Function:** `infer_role_for_type`
**Constants:** `_DTO_NAME_SUFFIXES`, `_DTO_LOMBOK_ANNOTATIONS`.

Trim the suffix tuple, drop the Lombok set, or simply replace the
function body with `return infer_role(ann_names)` to disable DTO
inference entirely.

Rebuild the graph and re-run the indexer.

### B.9 Add a new edge type to the graph

You'd do this if:

- you want to model `@KafkaListener` topic edges, `@Scheduled` triggers,
  Spring Cloud Stream bindings, etc., **before** the deferred CALLS /
  HTTP_CALLS work lands.

This is a larger change; rough map:

1. `ast_java.py` — add the data you need to `MethodDecl` /
   `AnnotationRef` (e.g. parsed annotation arguments).
2. `build_ast_graph.py` — add a new `_emit_xxx` pass and a new
   `EdgeRow` subclass; wire it in `pass2_edges`; add a schema string
   like `_SCHEMA_KAFKA = "CREATE REL TABLE KAFKA_LISTEN(...)"`.
3. `kuzu_queries.py` — add helper queries that traverse the new
   relation.
4. `server.py` — expose a new MCP tool (or extend `graph_neighbors` /
   `trace_flow` to recognise the new edge type).

See `propose/DEFERRED-CALL-GRAPH-PROPOSE.md` for the planned shape of
CALLS / HTTP_CALLS / ASYNC_CALLS — your custom edge should follow the
same conventions so a future merge is painless.

---

## Section C — Quick triage when results look bad

| Symptom | First thing to check |
|---------|---------------------|
| `module` / `microservice` is empty on most chunks | A.1 (build markers + `.lancedb-mcp.yml`) → B.4 |
| `microservice=...` filter returns 0 hits | check `graph_meta.microservice_counts` for canonical names; → A.1 / B.4 |
| Everything ranks as `OTHER` | A.2 (stereotypes) → B.1 |
| Sparse `INJECTS` graph | A.3 (DI patterns) → B.3 |
| Wrong class wins for "what does X do?" | A.4 (naming) → B.2 (verbs / caps) |
| Important `.properties` / `.xml` configs missing | A.5 → B.5 |
| Recently re-indexed but search is stale | Restart the MCP server; re-run `refresh_code_index` |
| `context_before` / `context_after` empty | Set `LANCEDB_MCP_DEBUG_CONTEXT=1` (see README §5) |
| Graph has lots of phantom nodes | Expected for external libs; inspect via `graph_meta` — only worry if domain types are phantoms (means resolution is failing; check imports). Structural queries like `find_implementors` only return resolved (non-phantom) symbols by default. |
| Graph tools unavailable / silent failures | Kuzu DB missing or wrong path — verify `KUZU_DB_PATH` or `${LANCEDB_URI}/code_graph.kuzu` exists (see A.6). |

---

## Section D — Re-indexing reference

| Change you made | Re-run |
|-----------------|--------|
| Role table, DI annotations, DTO heuristics, exclusion patterns, file-type patterns, chunk sizes, embedding model | **Both** the LanceDB indexer (`cocoindex update ... --full-reprocess` or `refresh_code_index`) **and** `build_ast_graph.py` |
| Graph-only logic (new edge type, module/microservice inference, phantom resolution) | `build_ast_graph.py` + `graph_enrich.py` |
| Ranking weights, action-verb list, search-time caps, hybrid/RRF behaviour | Nothing — restart the MCP server |
| Server tool surface (new tools, parameter changes) | Restart the MCP server (and re-register in the client if the tool list changed) |
