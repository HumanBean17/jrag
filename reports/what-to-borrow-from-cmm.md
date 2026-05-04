# What to Borrow from Codebase-Memory MCP

A focused, prioritized guide for evolving `java-enterprise-codebase-rag` (AMA agent) by adopting proven patterns from [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) (paper: [arXiv:2603.27277](https://arxiv.org/abs/2603.27277)) — without giving up your Spring-aware, hybrid (vector + graph) edge.

> **Guiding principle.** CMM optimizes for *token efficiency at acceptable quality* across 66 languages. Your AMA agent optimizes for *answer quality on Spring/Java microservices* via hybrid retrieval. Borrow CMM's structural mechanics; keep your semantic / role-aware layer as the differentiator.

---

## Snapshot — where each tool wins

| Layer | Your AMA agent | Codebase-Memory MCP | Action |
|---|---|---|---|
| Java/Spring DI semantics | Strong (`@Autowired`, `@Inject`, Lombok, `@FeignClient`) | None | Keep yours |
| Vector / hybrid retrieval (LanceDB + RRF + `graph_expand`) | Yes | None | Keep yours |
| Role / capability ontology (`CONTROLLER`, `MESSAGE_LISTENER`, ...) | Yes | None | Keep yours |
| Microservice topology + brownfield overrides | Yes | Generic `Project` only | Keep yours |
| `CALLS` / `HTTP_CALLS` / `ASYNC_CALLS` resolution | Roadmap (Phase 3) | Shipped, mature | **Borrow** |
| `Route` as first-class node | Roadmap | Shipped | **Borrow** |
| Cross-repo / cross-service edges | Roadmap | Shipped (`pass_cross_repo`) | **Borrow** |
| Runtime trace ingestion | None | Shipped (`ingest_traces`) | **Borrow** |
| Git-diff impact + risk classification | Partial (`impact_analysis`) | Shipped (`detect_changes`) | **Borrow** |
| Layered ignore (`.gitignore` + project ignore) | Constant list | Layered (`.cbmignore`) | **Borrow** |
| Louvain community detection | None | Shipped | **Borrow (Phase 4)** |
| Dead-code detection | None | Shipped | **Borrow (Phase 4)** |
| 66-language tree-sitter grammars | Java only | Yes | Skip (off-strategy) |
| Single static binary distribution | Python venv | Yes | Skip until Phase 5+ |
| 3D graph UI | None | Yes | Skip |
| `get_architecture` mega-tool | Split into small tools | One bundled tool | Skip — keep yours |

---

## Tier 1 — Borrow now (cheap, high impact)

### B1. Confidence-scored CALLS resolution cascade

CMM's [`pass_calls.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_calls.c) and [`extract_calls.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/internal/cbm/extract_calls.c) resolve calls via a deterministic cascade. Adopt the **shape**, not the C code.

**What to lift:**

- A 4-strategy cascade with explicit confidence values:
  1. Import-map resolved (`0.95`)
  2. Same-module / same-package (`0.90`)
  3. Globally unique simple name (`0.75`)
  4. Suffix / fuzzy match (`0.55`)
- A `confidence` property on every `CALLS` edge so downstream tools (and the MCP agent) can filter (`WHERE c.confidence >= 0.8`).
- A `source` property: `"static"` vs `"trace"` vs `"di_proxy"`.

**Why now:** Add the property when you create the Kuzu schema for Phase 3 — retrofitting columns later is painful.

**Suggested Kuzu DDL:**

```sql
CREATE REL TABLE CALLS (
    FROM Method TO Method,
    confidence DOUBLE,         -- 0.55 .. 1.0
    source     STRING,         -- 'static' | 'trace' | 'di_proxy'
    strategy   STRING,         -- 'import_map' | 'same_module' | 'unique_name' | 'suffix'
    call_site  STRING          -- file:line
);
```

---

### B2. `Route` as a first-class node

CMM models REST endpoints and message channels as a single `Route` label so that *any* call site can attach to *any* endpoint via `HTTP_CALLS` / `ASYNC_CALLS`. See [`pass_route_nodes.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_route_nodes.c).

**What to lift:**

- Adopt the **`Route`** label (instead of `RestEndpoint` from your current PRODUCT-VISION) — keeps you semantically interoperable if anyone runs both MCPs in parallel.
- Properties: `path`, `method`, `framework` (`spring_mvc`, `webflux`, `feign`, `kafka`, `rabbitmq`), `broker` (for async), `service` (microservice name).
- Edges:
  - `(Method)-[:EXPOSES]->(Route)` for `@RequestMapping`/`@KafkaListener`
  - `(Method)-[:HTTP_CALLS]->(Route)` for `RestTemplate`/`WebClient`/`@FeignClient`
  - `(Method)-[:ASYNC_CALLS]->(Route)` for `KafkaTemplate.send`/`StreamBridge.send`
- A normalization rule: `/api/users/{id}` and `/api/users/123` collapse to the same `Route` (path-template canonicalization).

---

### B3. Runtime trace ingestion (`ingest_traces`)

This is the single biggest quality lever you don't have yet. Static analysis misses Spring AOP proxies, polymorphic dispatch, reflection, and event-driven flows — runtime traces capture all of them.

**What to lift:**

- A new MCP tool `ingest_traces(spans: List[Span], source: str)`.
- Accept OpenTelemetry / Sleuth / Micrometer JSON natively.
- For each `(parent_span, child_span)` pair, emit `(caller:Method)-[:CALLS {source:"trace", confidence:1.0}]->(callee:Method)`.
- For HTTP client spans, emit `(caller)-[:HTTP_CALLS]->(Route)` using `http.url` + `http.method` to match an existing `Route` node.
- Deduplicate via `(source_id, target_id, source)` so re-ingesting traces is idempotent.

**Why this matters:** Lifts Phase 3 from "static approximation" to "ground-truth where traces exist, static elsewhere" — and the agent can prefer `confidence:1.0` edges automatically.

---

### B4. Git-diff impact mapping with risk score

CMM's [`detect_changes`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_gitdiff.c) maps a diff to affected symbols and a blast radius. You already have `impact_analysis` — make it diff-driven and add risk classification.

**What to lift:**

- New MCP tool `analyze_pr(diff: str | git_ref: str)`:
  1. Parse `git diff` line ranges per file
  2. Map line ranges → chunks → graph nodes (functions/methods)
  3. Run your existing reverse closure
  4. Return `{ changed_nodes, blast_radius, risk_score, risk_level }`
- Risk formula (start simple, tune later):

```
risk = log10(1 + downstream_consumers) * role_weight * cross_service_factor

role_weight        = { CONTROLLER:1.5, SERVICE:1.2, REPOSITORY:1.0, CONFIG:1.8, ENTITY:1.3, ... }
cross_service_factor = 1.0 if changes only touch one microservice, 2.0 otherwise
risk_level         = "low" (<1.0), "medium" (1.0..2.5), "high" (>2.5)
```

- Output usable directly in PR review or CI gating.

---

### B5. Layered ignore patterns

CMM uses **hardcoded patterns → `.gitignore` hierarchy → `.cbmignore`** ([`discover/`](https://github.com/DeusData/codebase-memory-mcp/tree/master/src/discover)). Cleaner than your current `COMMON_EXCLUDED_PATH_PATTERNS` constant.

**What to lift:**

- Layer order:
  1. Hardcoded must-skip (`.git`, `node_modules`, `target`, `build`, `out`, `.idea`, `.gradle`, `bin`)
  2. Walk up `.gitignore` files from each indexed directory
  3. Project-level `.lancedb-mcp.yml`'s `ignore:` list
  4. NEW: optional `.lancedb-mcp-ignore` file with gitignore syntax
- Always skip symlinks (cycle protection).
- Reuse `pathspec` (Python) — it's the gitignore-spec-compliant matcher.

---

## Tier 2 — Borrow during Phase 2 / 3

### B6. Cross-repo / cross-service edges

CMM's [`pass_cross_repo.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_cross_repo.c) matches an `HTTP_CALLS` edge in service A to a `Route` node in service B and creates a `CROSS_HTTP_CALLS` edge. This is the killer feature for a multi-microservice AMA.

**What to lift:**

- After per-service indexing, run a global pass:
  - For each `HTTP_CALLS` edge with `path` + `method`, find the matching `Route` node in any other indexed service.
  - Emit `(callerMethod)-[:CALLS_HTTP]->(Route)<-[:EXPOSES]-(calleeMethod)` so traversal in either direction works.
- Same for async: match `topic`/`queue` strings in `KafkaTemplate.send` calls to `@KafkaListener` `Route` nodes.
- Path template matching: `GET /api/orders/{id}` matches a call to `GET /api/orders/123` — use a `path_pattern` regex stored on the `Route`.

**Killer query unlocked:** *"What breaks if I rename `POST /api/orders` in `order-service`?"* → traverse `Route` → cross-service `HTTP_CALLS` → caller methods → reverse closure → affected controllers in `checkout-service`.

---

### B7. Louvain community detection

CMM runs Louvain over `CALLS` to discover functional modules. Useful for onboarding and architecture pitches.

**What to lift:**

- After Phase 3 `CALLS` lands, run Louvain on the call subgraph (use `python-igraph` or `networkx-community`).
- Store `cluster_id` and `cluster_size` as `Method` properties.
- New MCP tool `find_module_clusters(min_size: int)` returning ranked clusters with their dominant role mix and entry methods.
- Bonus: weight edges by call frequency from traces (B3) for higher-quality partitions.

---

### B8. Dead-code detection

Trivial once `CALLS` exists, but valuable for cleanup and consulting deliverables.

**What to lift:**

- New MCP tool `find_dead_code(exclude_entry_points: bool = true)`.
- Definition: `Method` with zero incoming `CALLS` and zero incoming `EXPOSES`.
- Entry-point predicates to exclude:
  - Spring stereotypes that auto-invoke: `@Scheduled`, `@PostConstruct`, `@EventListener`, `@KafkaListener`, `@RabbitListener`, `@JmsListener`
  - HTTP entry points: any method with an `EXPOSES` edge
  - Test methods: `@Test`, `@ParameterizedTest`, lifecycle annotations
  - `public static void main(String[])`
- Cypher (one query):

```cypher
MATCH (m:Method)
WHERE NOT (m)<-[:CALLS]-()
  AND NOT (m)-[:EXPOSES]->()
  AND NOT m.is_entry_point
RETURN m.qualified_name, m.role, m.file, m.line
ORDER BY m.role, m.qualified_name
```

---

## Tier 3 — Borrow later or skip

### Borrow only if you go poly-language (Phase 5+)

- **B9. Multi-grammar indexing.** CMM ships 66 grammars vendored. Adopt only if you sell to non-Java SMBs.
- **B10. Static binary distribution.** Compelling for SMB clients ("download → run"). Not relevant while you're a Python venv.

### Skip (don't fit your strategy)

- **`get_architecture` mega-tool.** Your split tools (`graph_meta`, `list_by_role`, `list_by_capability`) are more agent-friendly because each is named and small. The agent picks better when tool intent is narrow.
- **3D graph UI.** Not the differentiator. If you need visualization, render Kuzu subgraphs to Mermaid or Graphviz on demand from a tool — far less code, embeds in chat.
- **Their ADR module.** Markdown folder + your existing search is enough. Adding ADR CRUD is scope creep.
- **CMM's mini-Cypher executor.** You already have Kuzu — strictly more capable.

---

## Suggested roadmap reorder

A revised ordering that front-loads borrowed pieces with the highest ROI:

| Phase | Goal | Borrowed items |
|---|---|---|
| **2** (now) | `Route` nodes + `HTTP_CALLS` / `ASYNC_CALLS` from Spring/Feign/Kafka, with `confidence` columns | B2 |
| **2.5** | `ingest_traces` MCP tool (cheap, huge quality lift) | B3 |
| **3** | Static `CALLS` with 4-strategy cascade; `find_callers` / `find_callees`; dead code | B1, B8 |
| **3.5** | `pass_cross_repo`-style cross-service edges | B6 |
| **4** | `analyze_pr` (diff → impact + risk); Louvain clusters | B4, B7 |
| **5** | Eval harness; head-to-head benchmark vs. CMM on Java repos | — |
| **5+** | Optional poly-language grammars; static-binary packaging | B9, B10 |

Layered ignores (B5) can land anywhere — drop it in alongside the next indexer change.

---

## Strategic notes

- **Run both MCPs in parallel as a zero-integration option.** `.mcp.json` supports many servers. Let your tool answer Java/architectural queries; CMM handles non-Java or generic structural queries when you eventually touch poly-glot codebases. Zero integration cost, maximum optionality.
- **Use the comparison itself as a portfolio asset.** When you start pitching SMB clients on AI automation, "I built a Spring-aware hybrid retrieval system that beats the published Codebase-Memory baseline on Java microservice questions" — with numbers from your Phase 5 eval harness — is a credible artifact. Few consultants can show that.
- **Don't fork CMM.** It's MIT-licensed C with vendored grammars; maintenance cost is high and the code style diverges from your Python stack. Read it as documentation, port the patterns.

---

## References

- Codebase-Memory MCP source — [github.com/DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp)
- Paper — [Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM Code Exploration via MCP (arXiv:2603.27277)](https://arxiv.org/abs/2603.27277)
- Your repo — [HumanBean17/java-enterprise-codebase-rag](https://github.com/HumanBean17/java-enterprise-codebase-rag)
- Key CMM files referenced above:
  - [`pass_calls.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_calls.c) — call resolution
  - [`pass_route_nodes.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_route_nodes.c) — route nodes
  - [`pass_cross_repo.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_cross_repo.c) — cross-service edges
  - [`pass_gitdiff.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/src/pipeline/pass_gitdiff.c) — git diff impact
  - [`extract_channels.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/internal/cbm/extract_channels.c) — async patterns
  - [`service_patterns.c`](https://github.com/DeusData/codebase-memory-mcp/blob/master/internal/cbm/service_patterns.c) — framework markers
