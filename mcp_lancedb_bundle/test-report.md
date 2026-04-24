# `lancedb-code` MCP Test Report

## Overall verdict

`lancedb-code` genuinely helped. It was fast enough to trace the chat assignment flow across `chat-core` and `chat-assign`, and semantic search surfaced core logic without requiring exact symbol names.

## What worked well

- Fast discovery of end-to-end behavior across microservices.
- Good semantic recall for intent-style queries (for example, operator selection logic).
- `list_by_role` is very effective for quickly locating controllers and services.
- Java + YAML retrieval in one index is useful for behavior + configuration reasoning.

## Issues observed

- **Metadata formatting bug/noise:** `symbols` and `annotations_on_type` sometimes return as character arrays instead of clean strings.
- **Ranking drift:** some config/entity chunks are ranked above actionable business-logic methods for flow questions.
- **Limited context in snippets:** some chunks are too narrow, requiring extra queries to reconstruct control flow.
- **Path filtering still broad in practice:** scoped searches can still include nearby but weakly relevant files.
- **No explicit flow view:** tracing still requires manual stitching across controllers/services/integrations.

## Suggested improvements (priority order)

1. Fix metadata serialization for `symbols` and annotations.
2. Improve behavioral ranking for trace questions (prefer orchestrators/processors/integration calls).
3. Add a "flow mode" that returns a probable execution chain (entrypoint -> service -> downstream integration).
4. Return richer default context around matches (method signature + surrounding lines).
5. Add role labels in results (`ENTRYPOINT`, `ORCHESTRATOR`, `REPOSITORY`, `CONFIG`).
6. Include ranking rationale/confidence per result.

## Realistic trace queries (regression set)

Use these to benchmark retrieval quality over time.

### 1) New incoming client message assignment path

- **Query:** `What happens when a new client message arrives and the chat has no assigned operator?`
- **Expected top files:**
  - `chat-core/chat-engine/src/main/java/com/bank/chat/engine/processors/ClientMessageProcessor.java`
  - `chat-core/chat-engine/src/main/java/com/bank/chat/engine/assign/ConfigurableChatAssignment.java`
  - `chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java`

### 2) Operator selection policy and fairness/capacity

- **Query:** `How does chat-assign choose which operator gets the next queued chat?`
- **Expected top files:**
  - `chat-assign/src/main/java/com/bank/chat/assign/service/DistributionChunkService.java`
  - `chat-assign/src/main/java/com/bank/chat/assign/repo/AssignOperatorSessionRepository.java`
  - `chat-assign/src/main/resources/application.yml`

### 3) Queue trigger and distribution execution

- **Query:** `What triggers distribution processing after a chat is enqueued?`
- **Expected top files:**
  - `chat-assign/src/main/java/com/bank/chat/assign/kafka/DistributionTriggerPublisher.java`
  - `chat-assign/src/main/java/com/bank/chat/assign/kafka/DistributionTriggerListener.java`
  - `chat-assign/src/main/java/com/bank/chat/assign/service/DistributionService.java`

### 4) Callback path when operator is assigned

- **Query:** `After chat-assign picks an operator, how is chat-core updated?`
- **Expected top files:**
  - `chat-assign/src/main/java/com/bank/chat/assign/integration/ChatCoreJoinClient.java`
  - `chat-core/chat-app/src/main/java/com/bank/chat/app/web/JoinOperatorController.java`
  - `chat-core/chat-engine/src/main/java/com/bank/chat/engine/processors/OperatorAssignedProcessor.java`

### 5) Re-assignment when operator becomes unavailable

- **Query:** `What happens to assigned chats when an operator session is closed or goes offline?`
- **Expected top files:**
  - `chat-assign/src/main/java/com/bank/chat/assign/service/OperatorSessionService.java`
  - `chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java`
  - `chat-assign/src/main/java/com/bank/chat/assign/service/DistributionChunkService.java`

## Re-test update (2026-04-24, after improvements)

Second validation run was performed using the same style of trace questions.

### What improved

- Metadata quality is better:
  - `annotations_on_type` now returns clean tokens (for example, `["Service"]`) instead of character-level arrays.
  - `symbols` are now readable symbol names (for example, `["pickEligibleOperator"]`).
- Ranking diagnostics improved:
  - Results now include `score_components` (for example, `distance`, `role_weight`), which is helpful for debugging relevance.
- Structural helper quality remained good:
  - `list_by_role` still returned the expected controllers for `chat-assign`.

### Remaining issues

- Behavioral ranking still needs work for trace-style questions:
  - For assignment-flow questions, `ChatManagementService` enqueue logic frequently ranks above core selection/callback logic.
  - Some less-central files still appear high for "what happens when..." prompts.
- Context expansion appears incomplete:
  - `context_before` and `context_after` were returned empty in tested queries.

### Regression introduced

- `trace_flow` failed during test with:
  - `trace failed: Binder exception: Variable s is not in scope.`
- This currently blocks the intended end-to-end "flow trace" experience.

### Updated recommendation priority

1. Fix `trace_flow` binder failure first (critical path for flow analysis).
2. Tune ranking weights for behavioral queries to boost orchestrator/selector/callback methods.
3. Ensure `context_before` / `context_after` are populated when requested or by default.
4. Keep the metadata/token and scoring-explanation format as-is (this part is improved).

## Benchmark scorecard (latest run)

Legend:
- **Top-1:** first result is clearly relevant to the core question.
- **Top-3:** at least one highly relevant core file appears in top 3.
- **Noise:** unrelated or low-value files ranked prominently.

| # | Query (short) | Top-1 | Top-3 | Noise | Notes |
|---|---|---|---|---|---|
| 1 | New client message -> assignment path | Partial | Yes | Medium | `ChatManagementService` ranked first; expected `ClientMessageProcessor`/assignment entrypoint not top-ranked. |
| 2 | Operator selection policy | Partial | Yes | Medium | `DistributionChunkService` appears, but ranking still mixed with non-core items. |
| 3 | Queue trigger -> distribution | Yes | Yes | Low | Results include enqueue + trigger flow (`ChatManagementService`, `Distribution*`). |
| 4 | Callback to chat-core after pick | No | Yes | Medium | `ChatCoreJoinClient` appears, but callback consumer (`JoinOperatorController`, `OperatorAssignedProcessor`) not strongly ranked. |
| 5 | Re-assignment on operator unavailability | Not run | Not run | N/A | Not rerun in the second pass yet. |

### Flow-trace tool status

| Tool | Status | Details |
|---|---|---|
| `trace_flow` | Fail | `Binder exception: Variable s is not in scope.` |
