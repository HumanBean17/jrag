<!-- LEGACY FORMAT - This document uses a legacy format and should not be used as a pattern for new documents -->
# `list_clients` MCP Tool — Outbound-Side Counterpart to `list_routes`

## Status

**Completed** — landed via PR-LC1, PR-LC2, and PR-LC3 (2026-05). Depended on
the brownfield annotations v2 propose
(`propose/BROWNFIELD-ANNOTATIONS-V2-PROPOSE.md`). This propose defined the MCP
tool plus the persistence shape it queries; the v2 annotations propose created
the data the tool consumes.

## Problem Statement

After the v2 annotations refactor, Feign declarations and other
outbound HTTP clients no longer live in the `Route` table. The
`list_routes` tool, post-v2, returns only inbound things this
service exposes (HTTP handlers + async listeners). A real workflow
is left without an entry point:

> "Show me every outbound HTTP call this service makes, what
> service it targets, and what kind of client it uses."

Today (pre-v2), an AMA agent answers this with
`list_routes(framework=feign)` — which is wrong on three counts:

1. It only returns Feign declarations; imperative `RestTemplate` /
   `WebClient` call sites are invisible because they don't carry a
   compile-time path (they synthesize phantom routes that don't
   surface in `list_routes`).
2. It conflates "this service exposes a Feign-fronted endpoint"
   with "this service calls a Feign-fronted endpoint" — two
   directions, same query.
3. After v2, the query returns nothing — Feign rows leave the
   `Route` table.

The agent needs a first-class outbound-client query tool. This
propose adds one and defines the persistence shape it reads from.

## Proposed Solution

### Two parts

1. **A new graph node `Client`** that stores outbound-client
   declarations (Feign methods, RestTemplate/WebClient call sites
   when `@CodebaseClient` is present). One row per
   `@CodebaseClient` annotation.

2. **A new MCP tool `list_clients`** that queries `Client` with
   filters symmetric to `list_routes`.

### `Client` graph node — schema

```sql
CREATE NODE TABLE Client (
    id              STRING,         -- deterministic: hash(microservice + member_fqn + clientKind + path + method)
    client_kind     STRING,         -- enum: feign_method | rest_template | web_client
    target_service  STRING,         -- e.g. "user-service" (optional; primarily Feign)
    path            STRING,         -- remote URL template (raw)
    path_template   STRING,         -- normalised ({} segments)
    path_regex      STRING,         -- compiled match regex
    method          STRING,         -- HTTP verb
    member_fqn      STRING,         -- declaring caller method FQN+sig
    member_id       STRING,         -- corresponding Symbol.id (for joins)
    microservice    STRING,         -- caller's microservice (where this client lives)
    module          STRING,         -- Maven/Gradle module of the caller
    filename        STRING,
    start_line      INT64,
    end_line        INT64,
    resolved        BOOLEAN,        -- did extraction succeed (vs SpEL placeholder)
    source_layer    STRING,         -- layer_a_meta | layer_b_ann | layer_b_fqn | layer_c_source | builtin
    PRIMARY KEY (id)
);

CREATE REL TABLE DECLARES_CLIENT (
    FROM Symbol TO Client,
    confidence DOUBLE,
    strategy STRING
);
```

The `Symbol → Client` `DECLARES_CLIENT` edge mirrors the existing
`Symbol → Route` `EXPOSES` edge — both link a member to its
direction-side declaration.

The existing `HTTP_CALLS` edge stays `Symbol → Route` (the call
edge resolves to the *callee* `http_endpoint` Route). The `Client`
node is the *caller-side* metadata holder: it's where the resolver
finds path+target hints to do the matching.

### `list_clients` MCP tool — surface

```python
@mcp.tool(
    name="list_clients",
    description=(
        "List outbound HTTP client declarations from the Kuzu graph "
        "(Feign methods, RestTemplate/WebClient call sites annotated "
        "with @CodebaseClient). Optional filters: microservice, "
        "client_kind, target_service, path_prefix, HTTP method."
    ),
)
async def list_clients(
    microservice: str | None = Field(default=None, description="Filter to one microservice key (the caller's microservice)."),
    client_kind: str | None = Field(default=None, description="Exact Client.client_kind: feign_method | rest_template | web_client."),
    target_service: str | None = Field(default=None, description="Exact Client.target_service match (e.g. 'user-service')."),
    path_prefix: str | None = Field(default=None, description="Client.path STARTS WITH this string."),
    method: str | None = Field(default=None, description="HTTP verb on Client.method (GET, POST, …); omit for any."),
    limit: int = Field(default=100, ge=1, le=500),
) -> ClientsListOutput:
    ...
```

DTO mirrors `RouteRowDto`:

```python
class ClientRowDto(BaseModel):
    id: str = ""
    client_kind: str = ""
    target_service: str = ""
    method: str = ""
    path: str = ""
    path_template: str = ""
    path_regex: str = ""
    member_fqn: str = ""
    member_id: str = ""
    microservice: str = ""
    module: str = ""
    filename: str = ""
    start_line: int = 0
    end_line: int = 0
    resolved: bool = True


class ClientsListOutput(BaseModel):
    success: bool
    clients: list[ClientRowDto] = Field(default_factory=list)
    message: str | None = None
```

### Companion tools (out of scope but worth flagging)

The `list_clients` shape implies three more tools that round out
the outbound side. These are **not in this propose** but are
listed here so the surface is coherent when they land:

- **`get_client_by_path(microservice, path_template, method)`** —
  symmetric with `get_route_by_path`. Resolves a single client.
- **`find_client_callers(client_id)`** — given a client
  declaration, list the call sites that invoke it (e.g. who
  calls a particular Feign method). Practically the same as
  walking `DECLARES_CLIENT` reversed plus following the
  declaring member's `CALLS` callers.
- **`find_client_target_route(client_id)`** — given a client,
  resolve to the most likely target `Route` on a remote service.
  This is essentially the matcher's output, exposed as a tool.

These three are sketched here to show the surface but punted to a
follow-up. The minimum viable list_clients propose is the node +
the listing tool.

### Companion: `list_async_producers` (parallel, also out of scope)

The same gap exists for `@CodebaseProducer` rows — currently they
don't appear in `list_routes` either. By symmetry, a future propose
should add a `Producer` node + `list_async_producers` tool. Punted
for now.

## Resolver Integration

### Extraction

`graph_enrich.py` and `ast_java.py` extract `@CodebaseClient`
annotations alongside the existing extraction passes. Each
annotation emits one `Client` row with deterministic `id`
(hash of `microservice + member_fqn + clientKind + path + method`)
and one `DECLARES_CLIENT` edge from the declaring `Symbol`.

For Feign interface methods (post-v2): the extractor synthesises
a `@CodebaseClient(clientKind=feign_method, targetService=<feign_name>,
path=<method-mapping path>, method=<HTTP verb>)` from the source
even when no explicit `@CodebaseClient` annotation is present.
Brownfield overrides remain available for cases where the source
is missing or wrong.

For imperative `RestTemplate` / `WebClient` call sites: extraction
happens **only** when the user adds `@CodebaseClient` explicitly.
The existing call-site heuristic for inferring path/method
(`build_ast_graph.py:1444`) remains in place for HTTP_CALLS
matching but does not synthesise `Client` rows on its own.

### Pass6 hint recovery (post-v2 change)

The current pass6 hint-recovery walk (`build_ast_graph.py:1741–1770`)
looks up the caller's `http_consumer` route to find path+target
hints. Post-v2, that lookup retargets to: "find the
`DECLARES_CLIENT` edge from the caller member to its `Client`
node and read `path` / `target_service` / `method` from there."

Same data, new home. The matcher's downstream logic (path-regex
match against `http_endpoint` routes on the target service)
doesn't change.

### `HTTP_CALLS` edge — unchanged

`HTTP_CALLS(Symbol → Route)` continues to point from the calling
member to the resolved `http_endpoint` Route on the target
service. The new `Client` node is *additional* metadata, not a
replacement. A typical Feign call has both:

- One `DECLARES_CLIENT(Symbol → Client)` — caller-side declaration
- One `HTTP_CALLS(Symbol → Route)` — resolved cross-service edge

Symbol-graph queries that walk only `HTTP_CALLS` (e.g.
`find_route_callers`) keep working with no changes.

## Acceptance Criteria

1. `Client` node table + `DECLARES_CLIENT` rel table created at
   graph build time. Schema matches the shape above. `ONTOLOGY_VERSION`
   bumps by 1 (next available — currently 9, this would be 10).
2. `graph_enrich` / `ast_java` emit one `Client` row per
   `@CodebaseClient` annotation and one row per Feign interface
   method (synthesised from source).
3. `list_clients` MCP tool registered, returns rows matching the
   declared filters. Empty results return `success=True` with
   `clients=[]`, not an error.
4. On `tests/bank-chat-system` after v2 lands and a hand-applied
   `@CodebaseClient` on at least one Feign interface:
   - `list_clients()` returns ≥1 row.
   - `list_clients(client_kind="feign_method")` returns only
     Feign rows.
   - `list_clients(microservice="chat-assign")` filters to that
     service's outbound calls.
   - The Feign row's `id` matches the `DECLARES_CLIENT` edge's
     target node id.
5. Pass6 hint recovery uses `Client` rows after v2 lands.
   Regression test: a Feign call from chat-core to chat-assign
   resolves to the right `http_endpoint` Route via the new
   `Client`-based hint path. Match outcome (`cross_service`)
   unchanged.
6. `find_route_callers` for an `http_endpoint` Route on
   chat-assign still returns the chat-core Feign caller as a
   caller. (i.e. removing Feign from `Route` doesn't lose the
   caller-side resolution path.)
7. New tests in `tests/test_list_clients.py` covering:
   - Each filter parameter independently.
   - Empty-result case.
   - Limit parameter clamping.
   - Deterministic `id` (rebuild produces stable ids).
8. Test baseline holds: full pytest suite green. Test count
   grows by ~6–8 new test cases in `test_list_clients.py`.

## Out of Scope

- **`get_client_by_path`, `find_client_callers`,
  `find_client_target_route`.** Sketched above; separate proposals
  if needed.
- **`Producer` node + `list_async_producers`.** Parallel work for
  the async outbound side; separate proposal.
- **YAML override schema for `Client` rows.** The existing
  `http_client_overrides` YAML key already exists in the
  override system (`graph_enrich.py:519` neighbourhood). It can
  remain a YAML feature and feed `Client` rows directly. No
  schema change in this propose.
- **Migrating existing `find_route_callers` to surface
  Client-side info.** Out of scope; the tool stays Route-centric.

## Open Questions

1. **Should `target_service` be a foreign-key reference to a
   `Microservice` node, or stay a plain string?** Today
   `Route.microservice` is a string; `Client.target_service`
   stays symmetric with that. Recommendation: keep as string for
   v1 of `list_clients`; revisit if/when a `Microservice` node
   table lands.

2. **Should the `Client` row also carry call-edge resolution
   outcome (matched / phantom / ambiguous), or is that purely a
   property of `HTTP_CALLS` edges?** Recommendation: leave on
   `HTTP_CALLS` only. `Client` is a static declaration; the
   call-edge match is a separate (sometimes multi-edge) result.

3. **Naming: `list_clients` vs `list_http_clients`.** The latter
   is more precise (an async producer is also a "client" in some
   sense). The shorter name reads better and matches the
   `@CodebaseClient` annotation it surfaces. Recommendation:
   `list_clients`. Async producers will get their own
   `list_async_producers` tool; no collision.

4. **Should imperative call sites (RestTemplate / WebClient) emit
   `Client` rows automatically, without `@CodebaseClient`?** This
   would mirror how `Route` is populated greenfield from
   `@RestController`. Recommendation: **no** for v1 — too noisy
   (every `restTemplate.exchange(...)` creates a row). Keep
   imperative-call-site Client rows opt-in via `@CodebaseClient`.
   Revisit after we see how brownfield projects use the tool.

## Notes

- This propose is structured to land **after** the v2 brownfield
  annotations PR (#36). The data the tool reads from doesn't exist
  until v2 reshapes how Feign declarations are stored.
- The `Client` node + `list_clients` tool are the minimum surface
  to recover the workflow that `list_routes(framework=feign)`
  served pre-v2 — and they do it more honestly (no direction
  conflation) and more completely (brownfield-annotated
  RestTemplate call sites become queryable too).
