# AST GraphRAG Integration for Java Microservices: Architecture, Call Graphs & Agent Workflow

## Executive Summary

Vector-only RAG, as used in most CocoIndex-based setups, excels at semantic similarity but fails systematically on multi-hop architectural reasoning — `controller → service → repository` chains, interface-driven dependency injection, and inheritance trees. AST-derived GraphRAG (DKB) is the correct addition, not a replacement: it layers a deterministic structural knowledge graph on top of the existing vector index, enabling bidirectional traversal at query time to supply context that similarity search structurally cannot find. A 2026 benchmark on Java codebases (Shopizer, ThingsBoard, OpenMRS Core) confirmed DKB achieves **15/15 (100%)** answer correctness on architecture-tracing queries, compared to 6/15 for pure vector RAG, at only ~2× the query cost and with indexing times under 15 seconds.[^1][^2][^3][^4]

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

## 2. Integrating AST GraphRAG into Your CocoIndex Pipeline

### 2.1 Architecture Overview

The integration adds a parallel graph index alongside your existing vector store. Both indexes are built from the same source files; the graph is derived deterministically from AST parsing, not from embeddings.[^2]

```
Java Microservices (5 repos)
          │
          ├── CocoIndex (existing)
          │     ├── Tree-sitter chunking (.java files)
          │     ├── Embedding generation
          │     └── pgvector store ──────────────────► Vector Retriever
          │
          └── AST Graph Builder (new)
                ├── Tree-sitter Java parser (tree_sitter_java)
                ├── Two-pass ontology extractor
                │     ├── Pass 1: class/interface/enum nodes
                │     └── Pass 2: injects/extends/implements edges
                └── Graph DB (Neo4j / Kuzu) ──────────► Graph Retriever
                                                              │
                                                    Bidirectional traversal
                                                    + Interface-consumer expansion
                                                              │
                                         ┌────────────────────┘
                                         ▼
                              Context Merger (RRF fusion)
                                         │
                                         ▼
                                     LLM Answer
```

### 2.2 Building the AST Graph: DKB Approach

The DKB (Deterministic Knowledge Base) approach, as validated in the 2026 benchmark, uses **Tree-sitter's `tree_sitter_java` grammar** to deterministically extract a typed ontology:[^1][^2]

**Node Types:**
- `Class`, `Interface`, `Enum`, `Record`, `Annotation` — extracted from AST `class_declaration`, `interface_declaration` nodes
- `Method`, `Constructor` — from `method_declaration` nodes
- `File`, `Package` — from directory structure[^11]

**Edge Types (DKB typed set):**
- `EXTENDS` — class inheritance
- `IMPLEMENTS` — interface implementation
- `INJECTS` — field-type DI (Spring `@Autowired`, constructor injection)
- `CALLS` — method-to-method call sites (requires call resolution)
- `HTTP_CALLS` — cross-service REST calls (Feign clients, `RestTemplate`)[^11]
- `ASYNC_CALLS` — Kafka, messaging patterns[^11]

The two-pass extraction strategy matters: Pass 1 builds all node records (so every class/interface in the codebase is known); Pass 2 resolves edge targets using the completed node registry, eliminating forward-reference gaps.[^2]

**Why deterministic extraction beats LLM-based graph construction:**
In the benchmark, LLM-KB skipped 377 out of 1210 files (31.2% miss rate), reducing chunk coverage to 64.1% and node coverage to 72.7% of DKB's graph. Indexing time for LLM-KB was 200 seconds vs. 2.8 seconds for DKB on the same codebase, and cost was ~20× higher. For a production codebase you maintain incrementally, stochastic extraction failures create silent blind spots.[^1][^2]

### 2.3 CocoIndex Integration Points

CocoIndex already uses Tree-sitter internally for semantic chunking, and natively supports Neo4j and Kuzu as graph export targets. The practical integration path:[^12][^13][^6][^7]

1. **Keep your existing CocoIndex vector flow unchanged** — Tree-sitter chunked embeddings in pgvector remain your vector retriever.

2. **Add a parallel CocoIndex flow for graph construction:**
   ```python
   @cocoindex.flow_def(name="CodeGraph")
   def code_graph_flow(flow_builder, data_scope):
       data_scope["files"] = flow_builder.add_source(
           cocoindex.sources.LocalFile(path="./services",
               included_patterns=["*.java"])
       )
       # Custom AST extractor: extract nodes and edges
       graph_data = data_scope["files"].transform(
           ASTGraphExtractor(language="java")
       )
       graph_data.export("code_graph",
           cocoindex.storages.Neo4jGraph(uri=NEO4J_URI))
   ```

3. **For cross-service HTTP_CALLS edges**, detect Feign client interfaces (`@FeignClient`) and `RestTemplate` call sites in Pass 2 — these become `HTTP_CALLS` edges between *microservice nodes* (one node per service repo). This is where your 5-microservice setup gains a significant advantage over single-repo tools: you can model the full inter-service topology as graph edges.[^14][^15][^11]

4. **Incremental sync**: CocoIndex's incremental processing only re-indexes changed files. For the graph, this means re-parsing only modified `.java` files and updating affected nodes/edges — critical for a live development workflow.[^6]

### 2.4 Graph Database Choice

| Database | Best For | Notes |
|---|---|---|
| **Neo4j** | Rich Cypher queries, visualization, production | CocoIndex native support[^13]; great for complex multi-hop Cypher[^16] |
| **Kuzu** | Lightweight, local dev, high performance | CocoIndex native support[^17][^12]; switch from Neo4j with one config change |
| **Memgraph** | In-memory, real-time updates | Used by code-graph-rag[^18][^19] |
| **SQLite (via Codebase-Memory)** | Zero-infrastructure, MCP-native | Single binary, ~0.3ms query latency[^11] |

For your setup (5 microservices, banking context, OpenShift), **Neo4j** is the pragmatic choice: it integrates with CocoIndex directly, has a robust Cypher query language for complex traversals, and handles the scale of 5 Java microservices comfortably.[^20]

***

## 3. Query-Time: Bidirectional Graph Expansion

### 3.1 The DKB Retrieval Algorithm

At query time, the graph augments (does not replace) the vector retrieval:[^2]

```
1. Vector search → top-k chunks (existing CocoIndex query)
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

## 4. Complete Agent Workflow After Integration

### 4.1 Routing Architecture

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

### 4.2 Full Agent Workflow 

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
    │ (CocoIndex    │  │  1. Entity lookup  │
    │  pgvector)    │  │  2. Bidir expand   │
    │  top-k chunks │  │  3. Interface-     │
    │               │  │     consumer expand│
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

### 4.3 Specialized Agent Tools (MCP-Compatible)

Expose the graph as discrete MCP tools your AI agent can invoke:[^19][^11]

| Tool | Input | What It Does |
|---|---|---|
| `search_code_semantic` | query string | Vector RAG on CocoIndex chunks |
| `find_implementors` | interface name | Graph: `IMPLEMENTS` edge traversal |
| `find_callers` | class/method name | Graph: upstream `INJECTS`/`CALLS` traversal |
| `find_callees` | class/method name | Graph: downstream `CALLS` traversal |
| `trace_request_flow` | REST endpoint path | Combined: HTTP_CALLS → CALLS chain |
| `impact_analysis` | class/method name | Graph: transitive forward traversal |
| `get_service_topology` | none | Graph: full microservice dependency map |
| `get_architecture_summary` | service name | Community detection → module summary |

This tool-per-capability model lets your agentic workflow (Claude Code, or whatever you use) pick the right retrieval method per sub-question, rather than running a fixed pipeline.[^23][^24]

### 4.4 Self-Correction Loop

For complex multi-hop questions, add a self-correction loop:[^10][^23]

1. **Retrieve** via both layers
2. **Grade** retrieved context: does it contain enough structural grounding to answer?
3. If grading fails: **expand depth** (`d=1` → `d=2`) or **rewrite query** (extract better entity names)
4. Re-retrieve with expanded parameters
5. **Generate** only when grading passes (max 2 retries)

***

## 5. Tool & Library Recommendations

### 5.1 AST Graph Construction

| Tool | Language | Notes |
|---|---|---|
| **tree_sitter_java** | Python/Rust | Core Java parser; used in DKB benchmark[^2] |
| **stakgraph** | Rust | Tree-sitter + LSP + Neo4j; Java support, agent-focused[^16] |
| **code-graph-rag** | Python | Tree-sitter + Memgraph; supports Java[^18] |
| **Codebase-Memory MCP** | C (binary) | 14 MCP tools, ~0.3ms queries, SQLite[^11]; MIT license |
| **CocoIndex custom extractor** | Python | Extend existing flow; Neo4j/Kuzu native output[^12][^13] |

### 5.2 Graph Databases

- **Neo4j Community**: Best for complex Cypher traversals, visual exploration; CocoIndex native[^16]
- **Kuzu**: Embedded, zero-infrastructure alternative to Neo4j, same CocoIndex config[^17][^12]

### 5.3 Reference Implementation

The DKB benchmark paper has a public GitHub repository (`graph-based-rag-ast-vs-llm`) with working Python scripts for all three retrieval strategies on Java codebases — including the Tree-sitter extraction (`dkb_gemini_v2.py`), bidirectional traversal logic, and interface-consumer expansion. This is the closest existing reference to your exact problem (Java microservices, architectural Q&A).[^1]

***

## 6. Implementation Roadmap

### Phase 1: AST Graph Index
- Parse all 5 microservices with `tree_sitter_java`
- Extract Class/Interface/Method nodes + EXTENDS/IMPLEMENTS/INJECTS edges
- Store in Neo4j or Kuzu
- Verify graph completeness (node count, edge count per service)
- Add graph query alongside existing CocoIndex vector query

### Phase 2: Agent Integration
- Wrap graph queries as MCP tools
- Add query classifier (LLM-based, ~5 categories)
- Implement RRF fusion for hybrid context assembly
- Add relevance grading + retry loop

### Phase 3: Evaluation
- Build a golden question set (15 structural + 15 semantic questions per service)
- Measure correctness: vector-only vs. hybrid vs. graph-only
- Tune expansion depth `d` and context budget per query category

***

## Key Takeaways

- **GraphRAG is an additive layer**: keep CocoIndex vector search; add a deterministic AST graph alongside it. The two are complementary retrieval primitives, not competitors.[^5][^1]
- **Use AST parsing (DKB), not LLM-based graph extraction**: LLM-KB skips ~30% of files and costs 20–45× more. Tree-sitter completes in seconds and is fully deterministic.[^2]
- **Your 5-service topology is a first-class graph asset**: model inter-service Feign/Kafka dependencies as `HTTP_CALLS` and `ASYNC_CALLS` edges — this dimension is unique to microservice systems and unlocks impact analysis.[^21][^11]
- **Bidirectional traversal is non-negotiable**: successor-only graphs miss upstream consumers (controllers that inject services); the interface-consumer expansion fixes Spring DI wiring gaps.[^1][^2]
- **Route queries to the right layer**: structural questions go to the graph, semantic questions go to vectors, and complex flows use both with RRF fusion.[^9][^22][^10]

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

