# Addendum: HTTP brownfield enum method, `@CodebaseHttpClient` rename, inbound exclusivity

This file extends **[`BROWNFIELD-ANNOTATIONS-V2-PROPOSE.md`](./BROWNFIELD-ANNOTATIONS-V2-PROPOSE.md)** without editing that document’s body (immutable parent).

## References

- Design: [`../HTTP-ROUTE-METHOD-ENUM-PROPOSE.md`](../HTTP-ROUTE-METHOD-ENUM-PROPOSE.md)
- Execution plan: [`../../plans/completed/PLAN-HTTP-ROUTE-METHOD-ENUM.md`](../../plans/completed/PLAN-HTTP-ROUTE-METHOD-ENUM.md)
- Agent-facing summary: [`../../docs/AGENT-GUIDE.md`](../../docs/AGENT-GUIDE.md) (brownfield HTTP exclusivity subsection)

## What landed (summary)

- **`@CodebaseClient` / `@CodebaseClients`** renamed to **`@CodebaseHttpClient` / `@CodebaseHttpClients`** on source stubs and in the extractor; no backward-compat alias for the old simple names.
- Shared **`CodebaseHttpMethod`** enum (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`, `HEAD`, `OPTIONS`) on both **`@CodebaseHttpRoute`** and **`@CodebaseHttpClient`** stubs; `method` is mandatory on clients and is no longer a string on the annotation surface.
- **Inbound HTTP brownfield exclusivity:** layer-C **`@CodebaseHttpRoute`** rows **replace** same-method built-in Spring HTTP rows in merge (aligned with async behaviour); wire-format `http_method` strings remain enum `.name()` values.
- **Structured stderr events:** **`brownfield-exclusivity-shadowing`** (INFO) on extractor co-presence of brownfield HTTP annotations with shadowable framework annotations; **`brownfield-method-string-literal`** (WARN) when `method` is still a string literal mid-migration. Merge code does not emit the INFO event (single trigger in `ast_java.py`).
- **`ONTOLOGY_VERSION` 12**; operators should **re-index** after upgrading so `meta_chain` keys and annotation simple names match the post-rename extractor (see README “Re-index required” callouts).
