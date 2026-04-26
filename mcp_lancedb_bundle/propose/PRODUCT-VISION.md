# AST GraphRAG Integration for Java Microservices: Architecture, Call Graphs & Agent Workflow

## Executive Summary

Vector-only RAG, as used in most CocoIndex-based setups, excels at semantic similarity but fails systematically on multi-hop architectural reasoning — `controller → service → repository` chains, interface-driven dependency injection, and inheritance trees. AST-derived GraphRAG (DKB) is the correct addition, not a replacement: it layers a deterministic structural knowledge graph on top of the existing vector index, enabling bidirectional traversal at query time to supply context that similarity search structurally cannot find. A 2026 benchmark on Java codebases (Shopizer, ThingsBoard, OpenMRS Core) confirmed DKB achieves **15/15 (100%)** answer correctness on architecture-tracing queries, compared to 6/15 for pure vector RAG, at only ~2× the query cost and with indexing times under 15 seconds.[^1][^2][^3][^4]

**This repository’s reference implementation** pairs **LanceDB** (embeddings, optional full-text + vector hybrid via RRF) with a **Kuzu** sidecar graph (default `code_graph.kuzu` colocated with the LanceDB data directory). Search and the MCP server do not require a running CocoIndex process—only the built artifacts and Python dependencies (see the bundle `README`).

***

## 1. Why AST GraphRAG Is an Addition, Not a Replacement

### 1.1 The Fundamental Retrieval Gap

Standard vector RAG — including CocoIndex's Tree-sitter-chunked embeddings — retrieves code by semantic proximity: chunks whose embeddings are "close" to the query embedding bubble to the top. This works well for local, lexically contained questions ("how is the order placed?"), but breaks on structural questions because the dependencies needed to answer them live in *different, structurally related files* whose text may not resemble the query at all.[^5][^6][^7][^1]

Classic failure mode: a query "which controllers depend on the PaymentService?" requires traversing *upstream* consumers — but vector search returns the `PaymentService` implementation itself (high similarity) and misses the controllers (low similarity to the query, despite being the answer). This is the context-flattening problem: retrieved chunks share topical overlap with the query but do not preserve structural dependencies such as inheritance, dependency injection, and call relationships.[^2][^4]

### 1.2 What Each Layer Retrieves Best

| Query Type | Best Retrieval Layer | Why |
|---|---|---|
| "How does the payment calculation work?" | Vector RAG | Semantic similarity to implementation body |
| "Which classes implement `PaymentGateway`?" | AST Graph | `implements` edge traversal |
| "Which controllers call the cart service?" | AST Graph | Bidirectional `injects`/`calls` traversal |
| "What Spring annotations does the order service use?" | Vector RAG | Textual/lexical match |
| "What breaks if I change `UserRepository`?" | AST Graph | Transitive forward traversal of `injects` edges |
| "Show me all authentication-related code" | Vector RAG | Semantic similarity clustering |
| "Trace the full call path from REST endpoint to DB" | AST Graph + Vector | Multi-hop + full body context |

The two layers are orthogonal and complementary. A **hybrid retrieval** system that runs both in parallel and fuses results via Reciprocal Rank Fusion (RRF) consistently outperforms either layer alone.[^8][^9][^10][^5]

***

## 2. Integrating AST GraphRAG into the indexing and query path

### 2.1 Architecture overview

The integration adds a **parallel graph index** alongside the **vector index**. Both are built from the same source files; the graph is derived deterministically from AST parsing, not from embeddings.[^2] In this bundle, that split is **LanceDB + Kuzu**.

```
Java Microservices
          │
          ├── CocoIndex flow (index time) — e.g. java_index_flow_lancedb.py
          │     ├── Tree-sitter chunking (.java, SQL, YAML, …)
          │     ├── Embedding generation
          │     └── LanceDB tables ──────────────────► Vector / hybrid retriever
          │
          └── build_ast_graph.py (index time, parallel)
                ├── Tree-sitter Java (tree_sitter_java)
                ├── Two-pass ontology extractor
                │     ├── Pass 1: class/interface/enum nodes, …
                │     └── Pass 2: injects/extends/implements (Phase 1 edges)
                └── Kuzu (code_graph.kuzu) ──────────► Graph retriever (Cypher)
                                                              │
                                                    BFS + bidirectional closure
                                                    (MCP: expand, impact, …)
                                                              │
                                         ┌────────────────────┘
                                         ▼
                    Context merge (RRF: vector, FTS, graph-expanded chunks)
                                         │
                                         ▼
                                     LLM / agent
```

### 2.2 Building the AST Graph: DKB Approach

The DKB (Deterministic Knowledge Base) approach, as validated in the 2026 benchmark, uses **Tree-sitter's `tree_sitter_java` grammar** to deterministically extract a typed ontology:[^1][^2]

**Node Types:**
- `Class`, `Interface`, `Enum`, `Record`, `Annotation` — extracted from AST `class_declaration`, `interface_declaration` nodes
- `Method`, `Constructor` — from `method_declaration` nodes
- `File`, `Package` — from directory structure[^11]

**Edge types (DKB typed set):**
- `EXTENDS` — class inheritance
- `IMPLEMENTS` — interface implementation
- `INJECTS` — field-type DI (Spring `@Autowired`, constructor injection)
- `CALLS` — method-to-method call sites (requires call resolution) — *planned* (not yet in the Kuzu schema)
- `HTTP_CALLS` — cross-service REST calls (Feign clients, `RestTemplate`)[^11] — *planned*
- `ASYNC_CALLS` — Kafka, messaging patterns[^11] — *planned*

**Shipped in the Kuzu sidecar (Phase 1):** `EXTENDS`, `IMPLEMENTS`, `INJECTS`. The bundle documents deferred `CALLS` / `HTTP_CALLS` / `ASYNC_CALLS` in its roadmap (`README` §6).

The two-pass extraction strategy matters: Pass 1 builds all node records (so every class/interface in the codebase is known); Pass 2 resolves edge targets using the completed node registry, eliminating forward-reference gaps.[^2]

**Why deterministic extraction beats LLM-based graph construction:**
In the benchmark, LLM-KB skipped 377 out of 1210 files (31.2% miss rate), reducing chunk coverage to 64.1% and node coverage to 72.7% of DKB's graph. Indexing time for LLM-KB was 200 seconds vs. 2.8 seconds for DKB on the same codebase, and cost was ~20× higher. For a production codebase you maintain incrementally, stochastic extraction failures create silent blind spots.[^1][^2]

### 2.3 How this bundle wires CocoIndex, LanceDB, and Kuzu

1. **Vector / chunk index (LanceDB):** a CocoIndex flow (e.g. `java_index_flow_lancedb.py` in the repo) walks sources, applies Tree-sitter-based chunking, embeds, and writes **LanceDB** tables. At query time the MCP / CLI loads embeddings from `LANCEDB_URI` and runs vector search, optional **FTS + vector RRF** (`auto_hybrid`), and filters on enriched columns (`role`, `microservice`, `module`, …).[^6][^7]

2. **Graph index (Kuzu):** `build_ast_graph.py` runs **in parallel** (same repo root, same `.java` sources). It is **not** required for read-only search if the Kuzu file already exists. Output defaults to `code_graph.kuzu` next to the LanceDB directory. Query-time access is read-only Cypher from Python (`kuzu`).

3. **Cross-service `HTTP_CALLS` / `ASYNC_CALLS` (future):** Feign / `RestTemplate` / Kafka static patterns belong in a later pass once method- and service-level edges are modeled; see §8.[^14][^15][^11]

4. **Incremental updates:** CocoIndex can incrementally refresh LanceDB chunks. The Kuzu build in Phase 1 is a **full rebuild** when the graph is regenerated; incremental graph diffing is a future improvement (bundle `README`).

### 2.4 Why Kuzu (and what LanceDB covers)

- **LanceDB** holds dense retrieval: embeddings, optional FTS, and chunk metadata (package, FQN, role, capabilities, `microservice` / `module`, …) produced with the same Tree-sitter chunks the agent reads.
- **Kuzu** is an **embedded** property graph with **Cypher**, no separate server process, and a small on-disk footprint beside `lancedb_data`. It matches the “structural retriever + parallel to vectors” model without running Neo4j or another cluster alongside the MCP process.

Research stacks often cite pgvector or other vector stores; functionally, **LanceDB plays that role here**, paired with Kuzu for graph traversals.[^12]

***

## 3. Query-Time: Bidirectional Graph Expansion

### 3.1 The DKB Retrieval Algorithm

At query time, the graph augments (does not replace) the vector retrieval:[^2]

```
1. Vector (or hybrid FTS+vector) search in LanceDB → top-k chunks
2. Entity extraction from top-k chunks → identify class/method names
3. Graph node lookup → find matching graph nodes for extracted entities
4. Bidirectional expansion:
     V_expanded = V_0 ∪ N_d(v) for all v in V_0
     where N_d includes both successors AND predecessors to depth d
5. Interface-consumer expansion:
     For each interface node, add all implementing classes AND
     all classes that inject/use those interfaces (upstream consumers)
6. Merge expanded graph context + original vector chunks
7. Deduplicate and rank by relevance (RRF or context budget)
8. Feed assembled context to LLM
```

The bidirectional expansion step is the critical innovation: successor-only traversal (following what a class uses) misses the upstream consumers (what uses the class), causing controller-discovery queries to fail. Interface-consumer expansion additionally resolves Spring's DI pattern where controllers inject interfaces, not concrete implementations.[^1][^2]

### 3.2 Context Budget Management

Graph expansion can produce large context. The DKB paper implements a context assembly budget: prioritize direct neighbors, then 2-hop, then 3-hop, truncating to fit the LLM's context window. In practice for 5 microservices, set `d=2` for most queries and `d=3` only for explicit "trace the full flow" queries.[^2]

***

## 4. Adding a Call Graph Layer

### 4.1 What a Call Graph Adds vs. AST GraphRAG

The DKB graph (extends/implements/injects edges) captures *structural/architectural* relationships — the wiring of your system. A **call graph** captures *dynamic behavioral* relationships — the sequence of method invocations at runtime.[^21]

| Dimension | AST Structural Graph | Call Graph |
|---|---|---|
| Edge meaning | "A depends on / is wired to B" | "A calls method M on B" |
| Primary use | Architecture discovery, impact analysis | Flow tracing, dead code, performance hotspots |
| Query example | "Which services use UserRepository?" | "What's the full execution path for `/checkout`?" |
| Derivation | Static, deterministic, fast | Static (approximate) or dynamic (runtime traces) |
| Java complexity | Easy (DI, inheritance) | Hard (polymorphism, reflection, Spring AOP) |

For your "Ask Me Anything" agent, call graphs are extremely valuable for questions like:
- "Trace the full request flow from the order REST endpoint to the database"
- "What methods are called when a payment fails?"
- "Which code paths handle Kafka message `order.created`?"

### 4.2 Static Call Graph Extraction for Java

**Tree-sitter + call resolution (recommended for your setup)**

The `Codebase-Memory` paper describes a 6-strategy call resolution cascade that achieves ~80% resolution accuracy for well-structured Java codebases. The key strategies in order:[^11]
1. Import-map resolution (confidence 0.95): `pkg.Method` → resolve `pkg` via import statements
2. Same-module resolution (0.90): check enclosing file's package first
3. Unique-name resolution (0.75): single project-wide match
4. Suffix-match fallback (0.55)

In the resulting graph, `CALLS` edges join `Method` nodes, and `HTTP_CALLS` edges join REST endpoint nodes across microservice boundaries.[^11]

**Spring-specific patterns to extract statically:**
- `@FeignClient` interfaces: each method maps to an `HTTP_CALLS` edge to the target service's endpoint[^15][^14]
- `RestTemplate.exchange(url, ...)`: parse URL pattern → match to `@RequestMapping` in target service
- `KafkaTemplate.send(topic, ...)`: creates an `ASYNC_CALLS` edge to the consumer `@KafkaListener` method
- `ApplicationEventPublisher.publishEvent(...)`: creates an event-dispatch edge

**Graph schema for microservice call graph:**

```
(ServiceA:Microservice)-[:EXPOSES]->(endpoint:RestEndpoint {path:"/api/orders"})
(ControllerA:Class)-[:HANDLES]->(endpoint)
(ControllerA)-[:CALLS]->(OrderService:Class)
(OrderService)-[:CALLS]->(PaymentServiceClient:FeignClient)
(PaymentServiceClient)-[:HTTP_CALLS {confidence:0.95}]->(ServiceB:Microservice)
(ServiceB)-[:EXPOSES]->(paymentEndpoint:RestEndpoint {path:"/api/payments"})
(OrderService)-[:ASYNC_CALLS {topic:"order.created"}]->(NotificationService:Microservice)
```

### 4.3 Dynamic Call Graph via Runtime Tracing

Static analysis struggles with polymorphism and Spring AOP proxies. For critical flows, complement static call graph with runtime traces:

- **Spring Cloud Sleuth / Micrometer Tracing**: instrument your services to emit distributed traces (Zipkin/Jaeger format)
- **Codebase-Memory's `ingest_traces` tool**: imports runtime traces directly into the graph database, creating real `CALLS` edges observed during actual execution[^11]
- This gives you a *hybrid call graph*: static edges for code coverage, dynamic edges for actual execution paths

***

## 5. Inter-Service Graph: The Microservices-Specific Layer

Your 5-microservice architecture adds a dimension most single-repo GraphRAG tools ignore: **cross-service dependency topology**. This is where graph representation pays the highest dividends.

### 5.1 Service Dependency Graph

Model each microservice as a top-level node with edges representing runtime dependencies:[^21]

```cypher
// Example Cypher for cross-service topology
(svc:Microservice {name: "order-service"})
  -[:CALLS_HTTP {endpoint: "/api/payments", method: "POST", client: "FeignClient"}]->
(svc2:Microservice {name: "payment-service"})

(svc:Microservice {name: "order-service"})
  -[:PUBLISHES_EVENT {topic: "order.created"}]->
(svc3:Microservice {name: "notification-service"})
```

This can be extracted statically from Feign client annotations and `@KafkaListener`/`KafkaTemplate` usages across all 5 services.[^14][^15]

### 5.2 Impact Analysis Queries

With this graph, your agent can answer impact analysis queries that are impossible with pure vector RAG:

```cypher
// "What breaks if I change the UserService API?"
MATCH (changed:Class {name: "UserService"})<-[:INJECTS|CALLS*1..3]-(affected)
RETURN affected.name, labels(affected)

// "Which other services are affected if payment-service is down?"
MATCH (svc:Microservice {name: "payment-service"})<-[:CALLS_HTTP]-(caller)
RETURN caller.name

// "Trace the full path for /api/checkout"
MATCH path = (ep:RestEndpoint {path: "/api/checkout"})-[:HANDLES|CALLS|HTTP_CALLS*1..5]->(terminal)
RETURN path
```

***

## 6. Complete Agent Workflow After Integration

### 6.1 Routing Architecture

The agent should not always invoke both retrieval layers — that wastes tokens and latency. A lightweight query classifier routes to the appropriate path:[^10][^22]

```
User Query
    │
    ▼
Query Classifier (LLM with schema context)
    │
    ├── "Structural/architectural question" ──► Graph Retriever → [+Vector fallback]
    │    Examples: "who calls X", "what implements Y",
    │    "impact if I change Z", "trace flow from endpoint"
    │
    ├── "Semantic/conceptual question" ──────► Vector Retriever → [+Graph expansion]
    │    Examples: "how does auth work",
    │    "show me error handling patterns"
    │
    └── "Combined question" ─────────────────► Both → RRF Merge
         Examples: "how does the checkout flow
         work end to end", "explain the payment service"
```

### 6.2 Full Agent Workflow (LangGraph / Agentic)

```
┌─────────────────────────────────────────────────────────┐
│                  QUERY INTAKE                           │
│  Parse query → extract entity mentions (class names,   │
│  service names, method names, topics)                   │
└───────────────────────┬─────────────────────────────────┘
                        │
              ┌─────────▼──────────┐
              │  ROUTE QUERY        │
              │  Structural?        │
              │  Semantic?          │
              │  Both?              │
              └──┬──────────┬───────┘
                 │          │
    ┌────────────▼──┐  ┌────▼──────────────┐
    │ VECTOR RAG    │  │  GRAPH RETRIEVAL   │
    │ (LanceDB:     │  │  1. Entity lookup  │
    │  embeddings + │  │  2. Bidir expand   │
    │  optional FTS)│  │  3. Interface-     │
    │  top-k chunks │  │     consumer expand│
    └──────┬────────┘  └────────┬───────────┘
           │                    │
           └──────────┬─────────┘
                      │
              ┌───────▼───────────┐
              │  CONTEXT MERGE    │
              │  RRF fusion       │
              │  Dedup & budget   │
              │  rank by relevance│
              └───────┬───────────┘
                      │
              ┌───────▼───────────┐
              │  RELEVANCE GRADE  │
              │  Are retrieved    │
              │  chunks grounded? │
              └──┬────────────────┘
                 │ No: rewrite query / expand depth
                 │ Yes:
              ┌──▼────────────────┐
              │  LLM GENERATION   │
              │  Assembled context│
              │  + system prompt  │
              │  with schema info │
              └──┬────────────────┘
                 │
              ┌──▼────────────────┐
              │  HALLUCINATION    │
              │  CHECK (optional) │
              │  Ground claims    │
              │  against graph    │
              └───────────────────┘
```

### 6.3 Specialized agent tools (MCP — `mcp_lancedb_bundle`)

Expose the graph and vector index as discrete MCP tools. Implemented tools today (names match the server):[^11]

| Tool | What it does |
|---|---|
| `codebase_search` | Vector / hybrid (RRF) / **graph_expand** (vector top-k + Kuzu BFS + RRF) over LanceDB chunks |
| `list_by_role` / `list_by_annotation` / `list_by_capability` | Filter symbols or search by `role`, annotations, or capability tags |
| `find_implementors` / `find_subclasses` / `find_injectors` | Kuzu: `IMPLEMENTS`, `EXTENDS`/`IMPLEMENTS`, reverse `INJECTS` |
| `graph_neighbors` | Configurable BFS on structural edges (`EXTENDS`, `IMPLEMENTS`, `INJECTS`) |
| `impact_analysis` | Reverse structural closure (what is affected if this type changes) |
| `trace_flow` | Staged **structural** trace (seeds from vector search; walks roles + injection graph — not a full `CALLS` / `HTTP_CALLS` chain until those edges exist) |
| `graph_meta` / `list_code_index_tables` | Kuzu + LanceDB metadata, counts, ontology version |
| `refresh_code_index` | Optional: rebuild LanceDB (CocoIndex) + Kuzu; gated by env |

**Deferred** (per roadmap, until call/inter-service graph lands): `find_callers` / `find_callees` on `CALLS`, `trace_request_flow` over `HTTP_CALLS` → `CALLS`, and a dedicated `get_service_topology` beyond current metadata and filters.

This tool-per-capability model lets the agent (e.g. Claude Code) pick the right retrieval per sub-question, rather than always running a fixed pipeline.[^23][^24]

### 6.4 Self-Correction Loop

For complex multi-hop questions, add a self-correction loop:[^10][^23]

1. **Retrieve** via both layers
2. **Grade** retrieved context: does it contain enough structural grounding to answer?
3. If grading fails: **expand depth** (`d=1` → `d=2`) or **rewrite query** (extract better entity names)
4. Re-retrieve with expanded parameters
5. **Generate** only when grading passes (max 2 retries)

***

## 7. Implementation stack in this repository

- **Vector store:** **LanceDB** (tables produced by the Java/SQL/Yaml CocoIndex flows the repo uses for indexing).
- **Graph store:** **Kuzu** (`code_graph.kuzu`), populated by `build_ast_graph.py` using **tree_sitter_java** in the DKB style (two-pass ontology, phantom nodes for unresolved targets).[^2]
- **Query / agent surface:** `server.py` (MCP) + `search_lancedb.py` (CLI); RRF in hybrid search and in `graph_expand`.[^8][^10]
- For broader **literature and alternatives** (other parsers, third-party graph DBs, and hybrid-retrieval studies), see the DKB paper and the references list below.[^1][^16][^11]

### 7.1 Reference implementation

The DKB benchmark paper has a public GitHub repository (`graph-based-rag-ast-vs-llm`) with working Python scripts for all three retrieval strategies on Java codebases — including the Tree-sitter extraction (`dkb_gemini_v2.py`), bidirectional traversal logic, and interface-consumer expansion. This is the closest existing reference to your exact problem (Java microservices, architectural Q&A).[^1]

***

## 8. Implementation Roadmap

### Phase 1: AST graph index (1–2 weeks) — *delivered in this bundle*

- Parse all 5 microservices (or a monorepo) with `tree_sitter_java`
- Extract Class/Interface/Method nodes + `EXTENDS` / `IMPLEMENTS` / `INJECTS` edges
- Store in **Kuzu** (`code_graph.kuzu`); run **LanceDB** + hybrid / `graph_expand` over the same project
- Verify graph completeness (node count, edge count per service)
- *Status:* implemented via `build_ast_graph.py` + `java_index_flow_lancedb.py` + MCP; graph rebuild is currently full, not incremental.

### Phase 2: Cross-Service Edges (1 week)
- Add Feign client detection → `HTTP_CALLS` edges between service nodes
- Add Kafka `@KafkaListener`/`KafkaTemplate` → `ASYNC_CALLS` edges
- Build the 5-node microservice topology graph

### Phase 3: Call Graph (1–2 weeks)
- Add method-level `CALLS` edges via Tree-sitter call-site extraction + 6-strategy resolution
- Optionally ingest Micrometer/Sleuth traces for dynamic call edges

### Phase 4: Agent integration (1 week) — *partially delivered*

- Graph + vector access **MCP tools** and **RRF** (vector + FTS; vector + graph expand) are implemented.
- **Still open:** LLM **query classifier**, **relevance grading + retry loop**, and deeper wiring once `CALLS` / `HTTP_CALLS` exist.

### Phase 5: Evaluation
- Build a golden question set (15 structural + 15 semantic questions per service)
- Measure correctness: vector-only vs. hybrid vs. graph-only
- Tune expansion depth `d` and context budget per query category

***

## Key Takeaways

- **GraphRAG is an additive layer:** keep **LanceDB** for dense retrieval; add a deterministic AST graph in **Kuzu** alongside it. The two are complementary retrieval primitives, not competitors.[^5][^1]
- **Use AST parsing (DKB), not LLM-based graph extraction:** LLM-KB skips ~30% of files and costs 20–45× more. Tree-sitter completes in seconds and is fully deterministic.[^2]
- **Your 5-service topology is a first-class graph asset (roadmap):** model inter-service Feign/Kafka dependencies as `HTTP_CALLS` and `ASYNC_CALLS` edges when Phase 2 lands.[^21][^11]
- **Bidirectional traversal is non-negotiable:** successor-only graphs miss upstream consumers (controllers that inject services); the interface-consumer expansion fixes Spring DI wiring gaps.[^1][^2]
- **Route queries to the right tool:** structural questions → Kuzu-backed MCP tools; semantic questions → `codebase_search` on LanceDB; combined flows use **hybrid** and **graph_expand** with RRF.[^9][^22][^10]

---

## References

1. [Reliable Graph-RAG for Codebases: AST-Derived Graphs vs LLM ...](https://arxiv.org/abs/2601.08773) - Using 15 architecture and code-tracing queries per repository, we measure indexing time, query laten...

2. [Reliable Graph-RAG for Codebases: AST-Derived Graphs vs LLM ...](https://arxiv.org/html/2601.08773v1) - This paper compares three retrieval paradigms for code analysis: (A) No-Graph Naive RAG (vector-only...

3. [AST-Derived Graphs vs LLM-Extracted Knowledge Graphs](https://www.themoonlight.io/en/review/reliable-graph-rag-for-codebases-ast-derived-graphs-vs-llm-extracted-knowledge-graphs) - This paper presents a comprehensive benchmark comparing three distinct Retrieval-Augmented Generatio...

4. [How AI Knowledge Graphs Turn Legacy Code into Structured ...](https://www.softwareseni.com/how-ai-knowledge-graphs-turn-legacy-code-into-structured-intelligence/) - GraphRAG becomes necessary for dependency analysis, impact assessment, and understanding complete fl...

5. [RAG vs GraphRAG: Shared Goal & Key Differences - Memgraph](https://memgraph.com/blog/rag-vs-graphrag) - Retrieval-augmented generation (RAG) changed how large language models (LLMs) access information by ...

6. [Build Real-Time Codebase Indexing for AI Code Generation](https://cocoindex.io/blogs/index-code-base-for-rag) - In this blog, we will show you how to index a codebase for RAG with CocoIndex. CocoIndex provides bu...

7. [Real-time Codebase Indexing - CocoIndex](https://cocoindex.io/docs/examples/code_index/) - Walk a repo, split by syntax, embed, and query your codebase in English. Real-time RAG for code.

8. [RAG vs. GraphRAG: A Systematic Evaluation and Key Insights - arXiv](https://arxiv.org/html/2502.11371v3)

9. [Efficient Knowledge Graph Construction and Hybrid Retrieval at Scale](https://arxiv.org/html/2507.03226v3) - We further introduce a hybrid retrieval strategy combining vector similarity with efficient graph tr...

10. [Practical Design for GraphRAG, Hybrid Retrieval, and Evaluation](https://hyunjoong.kim/en/blog/post-rag-architecture-graphrag-hybrid-evaluation) - Why vector-only RAG breaks in production, when GraphRAG is worth the complexity, and how to run a re...

11. [Codebase-Memory: Tree-Sitter-Based Knowledge Graphs for LLM ...](https://arxiv.org/html/2603.27277v1) - 3.2 Graph Schema. The knowledge graph uses a property-graph model with typed nodes and edges: Table ...

12. [Build Real-Time Knowledge Graphs from Documents Using ...](https://dev.to/cocoindex/build-real-time-knowledge-graphs-from-documents-using-cocoindex-kuzu-with-llms-live-updates-n1b) - If you are using CocoIndex to build your knowledge graph, you can use Kuzu as a target graph data st...

13. [Build Real-Time Knowledge Graph For Documents with LLM](https://cocoindex.io/blogs/knowledge-graph-for-docs) - CocoIndex now supports knowledge graph with incremental processing. Build live knowledge for agents ...

14. [Implementing OpenFeign for Inter-Service Communication in Spring ...](https://www.linkedin.com/posts/amanraj7337_post-28-microservices-implementing-activity-7392507776033300480-pwb4) - Microservices in Spring Boot can communicate :: with each other using various methods, categorized i...

15. [Interservice Communication using OpenFeign - NashTech Blog](https://blog.nashtechglobal.com/interservice-communication-using-openfeign/) - Learn how to use OpenFeign in a Spring Boot microservices architecture for seamless interservice com...

16. [GitHub - stakwork/stakgraph: A source code parser using treesitter, LSP, and neo4j, powering software knowledge graphs for AI agents.](https://github.com/stakwork/stakgraph) - A source code parser using treesitter, LSP, and neo4j, powering software knowledge graphs for AI age...

17. [Real-time knowledge graph with Kuzu and CocoIndex, high ... - Reddit](https://www.reddit.com/r/Rag/comments/1l392g8/realtime_knowledge_graph_with_kuzu_and_cocoindex/) - CocoIndex is written in Rust to help with real-time data transformation for AI, like knowledge graph...

18. [vitali87/code-graph-rag](https://github.com/vitali87/code-graph-rag) - The ultimate RAG for your monorepo. Query, understand, and edit multi-language codebases with the po...

19. [Code-Graph-RAG - AI-Powered Codebase Analysis](https://code-graph-rag.com) - An AI-powered codebase analysis tool that builds knowledge graphs from multi-language codebases usin...

20. [#opensource #llm #knowledgegraph #neo4j #cocoindex #ai #nlp ...](https://www.linkedin.com/posts/linghua-jin-8209b138_opensource-llm-knowledgegraph-activity-7328135839555706880-CAPe) - Build Real-Time Knowledge Graph with CocoIndex and Neo4j for documents with LLM #OpenSource. CocoInd...

21. [Unveiling Graph Structures in Microservices: Service Dependency ...](https://www.abhishek-tiwari.com/unveiling-graph-structures-in-microservices-service-dependency-graph-call-graph-and-causal-graph/) - Diary of a Tech Savant and Servant Leader - All things technology, product, and engineering leadersh...

22. [Build Smarter RAG with Routing and Hybrid Retrieval - Milvus Blog](https://milvus.io/blog/build-smarter-rag-routing-hybrid-retrieval.md) - Learn how modern RAG systems use query routing, hybrid retrieval, and stage-by-stage evaluation to d...

23. [Building a Comprehensive Agentic RAG Workflow: Query Routing ...](https://sajalsharma.com/posts/comprehensive-agentic-rag/) - A tutorial on building an advanced agentic RAG workflow that combines query routing, document gradin...

24. [LangGraph RAG: Build Agentic Retrieval‑Augmented Generation](https://www.leanware.co/insights/langgraph-rag-agentic) - Most RAG workflows are linear: retrieve documents, feed them to an LLM, and generate an answer. That...

