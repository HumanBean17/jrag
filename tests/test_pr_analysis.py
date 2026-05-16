"""PR-B: unified diff parsing, hunk→symbol mapping, and risk scoring (plan §4 tests 31–37)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from kuzu_queries import find_symbols_in_file_range
from pr_analysis import (
    DiffHunk,
    analyze_pr_pipeline,
    compute_risk,
    map_hunks_to_symbols,
    parse_unified_diff,
)


def test_31_parse_unified_diff_single_file_one_hunk() -> None:
    diff = """diff --git a/foo.java b/foo.java
--- a/foo.java
+++ b/foo.java
@@ -1,3 +1,3 @@
 a
-b
+c
 d
"""
    hunks = parse_unified_diff(diff)
    assert len(hunks) == 1
    assert isinstance(hunks[0], DiffHunk)
    assert hunks[0].target_path == "foo.java"
    assert hunks[0].source_line_start == 1


def test_32_parse_unified_diff_multi_file() -> None:
    diff = """diff --git a/a.java b/a.java
--- a/a.java
+++ b/a.java
@@ -1,2 +1,2 @@
 x
-y
+z
diff --git a/b.java b/b.java
--- a/b.java
+++ b/b.java
@@ -10,1 +10,1 @@
-old
+new
"""
    hunks = parse_unified_diff(diff)
    assert len(hunks) == 2
    paths = {h.target_path for h in hunks}
    assert paths == {"a.java", "b.java"}


def test_33_map_hunks_to_symbols_chat_management_service_assign(kuzu_graph) -> None:
    diff = """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
@@ -48,5 +48,5 @@
     @Transactional
     public void assign(AssignmentRequest request) {
-        if (request.getConversationId() == null || request.getConversationId().isBlank()) {
+        if (request.getConversationId() == null || request.getConversationId().isBlank() ) {
             throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "conversationId required");
         }
"""
    changed = map_hunks_to_symbols(kuzu_graph, parse_unified_diff(diff))
    fqns = {c.fqn for c in changed}
    assert any(f.endswith("ChatManagementService#assign(AssignmentRequest)") for f in fqns), fqns
    assign = next(c for c in changed if c.fqn.endswith("assign(AssignmentRequest)"))
    assert assign.change_type == "modified"


def test_34_compute_risk_leaf_private_method_low_blast(kuzu_graph) -> None:
    from pr_analysis import ChangedSymbol

    sym = find_symbols_in_file_range(
        kuzu_graph,
        filename="chat-assign/src/main/java/com/bank/chat/assign/service/DistributionChunkService.java",
        start_line=89,
        end_line=95,
    )
    pick = next(s for s in sym if s.fqn.endswith("pickEligibleOperator(UUID)"))
    rep = compute_risk(
        kuzu_graph,
        [
            ChangedSymbol(
                symbol_id=pick.id,
                fqn=pick.fqn,
                kind="method",
                change_type="modified",
                file=pick.filename,
                hunk_lines=[89],
            ),
        ],
    )
    assert rep.risk_band == "low"
    assert rep.blast_radius_total <= 2


def test_35_compute_risk_controller_route_and_high_band_when_saturated(
    monkeypatch, kuzu_graph,
) -> None:
    """Fixture corpus: controller diff surfaces EXPOSES routes; `high` needs saturated metrics."""
    from pr_analysis import ChangedSymbol

    diff = """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/web/ChatManagementController.java b/chat-assign/src/main/java/com/bank/chat/assign/web/ChatManagementController.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/web/ChatManagementController.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/web/ChatManagementController.java
@@ -25,3 +25,3 @@
     @PostMapping("/assign")
     public ResponseEntity<Void> assign(@Valid @RequestBody AssignmentRequest body) {
-        chatManagementService.assign(body);
+        chatManagementService.assign(body); // comment
         return ResponseEntity.accepted().build();
     }
"""
    rep0 = analyze_pr_pipeline(kuzu_graph, diff)
    assert rep0.routes_touched, rep0
    assert any("assign(AssignmentRequest)" in s.fqn for s in rep0.changed_symbols), rep0

    hit = kuzu_graph.impact_analysis("AssignChatRepository", depth=2, limit=10)[0]

    def fake_ia(self, name, **kwargs):
        del name, kwargs
        return [hit] * 100

    def fake_fc(self, name, **kwargs):
        del name, kwargs
        out = []
        for i in range(20):
            out.append(
                SimpleNamespace(
                    src=SimpleNamespace(microservice="svc-a"),
                    dst=SimpleNamespace(microservice="svc-b"),
                ),
            )
        return out

    monkeypatch.setattr(type(kuzu_graph), "impact_analysis", fake_ia)
    monkeypatch.setattr(type(kuzu_graph), "find_callers", fake_fc)

    sym = next(
        s
        for s in find_symbols_in_file_range(
            kuzu_graph,
            filename="chat-assign/src/main/java/com/bank/chat/assign/web/ChatManagementController.java",
            start_line=25,
            end_line=29,
        )
        if s.fqn.endswith("assign(AssignmentRequest)")
    )
    rep = compute_risk(
        kuzu_graph,
        [
            ChangedSymbol(
                symbol_id=sym.id,
                fqn=sym.fqn,
                kind="method",
                change_type="modified",
                file=sym.filename,
                hunk_lines=[26],
            ),
        ],
    )
    assert rep.risk_band == "high"
    assert rep.routes_touched


def test_35a_compute_risk_cross_service_bonus_saturates_to_one(monkeypatch) -> None:
    from pr_analysis import ChangedSymbol

    class _FakeGraph:
        def _rows(self, query, params):
            if "MATCH (s:Symbol) WHERE s.id = $id RETURN" in query:
                return [{
                    "id": params["id"],
                    "kind": "method",
                    "name": "handler",
                    "fqn": "com.acme.Controller#handler()",
                    "package": "com.acme",
                    "module": "svc-a",
                    "microservice": "svc-a",
                    "filename": "src/main/java/com/acme/Controller.java",
                    "start_line": 10,
                    "end_line": 20,
                    "start_byte": 0,
                    "end_byte": 0,
                    "modifiers": [],
                    "annotations": [],
                    "capabilities": [],
                    "role": "CONTROLLER",
                    "signature": "handler()",
                    "parent_id": "",
                    "resolved": True,
                }]
            if "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid})" in query:
                return [{"id": str(i)} for i in range(6)]
            if "MATCH (s:Symbol)-[e:ASYNC_CALLS]->(r:Route {id: $rid})" in query:
                return []
            return []

        def impact_analysis(self, name, **kwargs):
            del name, kwargs
            return []

        def find_callers(self, name, **kwargs):
            del name, kwargs
            return []

    monkeypatch.setattr("pr_analysis._route_ids_for_symbol", lambda graph, sid: ["route-1"])
    rep = compute_risk(
        _FakeGraph(),
        [
            ChangedSymbol(
                symbol_id="sym-1",
                fqn="com.acme.Controller#handler()",
                kind="method",
                change_type="modified",
                file="src/main/java/com/acme/Controller.java",
                hunk_lines=[12],
            ),
        ],
    )
    assert rep.risk_score == 1.0


def test_35b_compute_risk_single_cross_service_bonus_is_point_two(monkeypatch) -> None:
    from pr_analysis import ChangedSymbol

    class _FakeGraph:
        def __init__(self, *, include_callers: bool) -> None:
            self._include_callers = include_callers

        def _rows(self, query, params):
            if "MATCH (s:Symbol) WHERE s.id = $id RETURN" in query:
                return [{
                    "id": params["id"],
                    "kind": "method",
                    "name": "handler",
                    "fqn": "com.acme.Controller#handler()",
                    "package": "com.acme",
                    "module": "svc-a",
                    "microservice": "svc-a",
                    "filename": "src/main/java/com/acme/Controller.java",
                    "start_line": 10,
                    "end_line": 20,
                    "start_byte": 0,
                    "end_byte": 0,
                    "modifiers": [],
                    "annotations": [],
                    "capabilities": [],
                    "role": "CONTROLLER",
                    "signature": "handler()",
                    "parent_id": "",
                    "resolved": True,
                }]
            if "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route {id: $rid})" in query:
                if self._include_callers:
                    return [{"id": "caller-1"}]
                return []
            if "MATCH (s:Symbol)-[e:ASYNC_CALLS]->(r:Route {id: $rid})" in query:
                return []
            return []

        def impact_analysis(self, name, **kwargs):
            del name, kwargs
            return []

        def find_callers(self, name, **kwargs):
            del name, kwargs
            return []

    monkeypatch.setattr("pr_analysis._route_ids_for_symbol", lambda graph, sid: ["route-1"])
    rep = compute_risk(
        _FakeGraph(include_callers=True),
        [
            ChangedSymbol(
                symbol_id="sym-1",
                fqn="com.acme.Controller#handler()",
                kind="method",
                change_type="modified",
                file="src/main/java/com/acme/Controller.java",
                hunk_lines=[12],
            ),
        ],
    )
    baseline = compute_risk(
        _FakeGraph(include_callers=False),
        [
            ChangedSymbol(
                symbol_id="sym-1",
                fqn="com.acme.Controller#handler()",
                kind="method",
                change_type="modified",
                file="src/main/java/com/acme/Controller.java",
                hunk_lines=[12],
            ),
        ],
    )
    # Keep raw terms identical in both runs; only cross-service route-callers differ.
    assert rep.routes_touched == baseline.routes_touched == ["route-1"]
    assert abs((rep.risk_score - baseline.risk_score) - 0.2) < 1e-9


def test_pr_analysis_changed_methods_finds_routes_via_declares_client(
    kuzu_db_path_cross_service_smoke: Path,
) -> None:
    from kuzu_queries import KuzuGraph

    g = KuzuGraph(str(kuzu_db_path_cross_service_smoke))
    rows = g._rows(  # noqa: SLF001
        "MATCH (s:Symbol)-[:DECLARES_CLIENT]->(c:Client)-[e:HTTP_CALLS]->(r:Route) "
        "WHERE e.match = 'cross_service' RETURN count(*) AS n",
        {},
    )
    assert int(rows[0].get("n") or 0) >= 1


def test_36_removed_symbol_from_minus_only_hunk(kuzu_graph) -> None:
    diff = """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
@@ -83,5 +83,0 @@
-    @Transactional
-    public void closeChat(String conversationId) {
-        assignChatRepository.findByConversationId(conversationId)
-                .ifPresent(assignChatRepository::delete);
-    }
"""
    changed = map_hunks_to_symbols(kuzu_graph, parse_unified_diff(diff))
    fqns = {c.fqn for c in changed}
    assert any(f.endswith("closeChat(String)") for f in fqns), fqns
    close = next(c for c in changed if c.fqn.endswith("closeChat(String)"))
    assert close.change_type == "removed"


def test_37_added_method_surfaces_not_yet_indexed_note(kuzu_graph) -> None:
    diff = """diff --git a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
--- a/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
+++ b/chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java
@@ -103,6 +103,11 @@
         chat.setOperatorSession(target);
         assignChatRepository.save(chat);

         chatCoreJoinClient.joinOperator(chat.getConversationId(), target.getOperatorId(), chat.getEpkId());
     }
+
+    public void syntheticBrandNewMethodForPrTest() {
+    }
+    public void syntheticSecondMethodForPrTest() {
+    }
 }
 """
    rep = analyze_pr_pipeline(kuzu_graph, diff)
    joined = " ".join(rep.notes).lower()
    assert "not yet indexed" in joined, rep.notes


def test_find_symbols_in_file_range_query(kuzu_graph) -> None:
    rows = find_symbols_in_file_range(
        kuzu_graph,
        filename="chat-assign/src/main/java/com/bank/chat/assign/service/ChatManagementService.java",
        start_line=47,
        end_line=50,
    )
    assert any("assign(AssignmentRequest)" in r.fqn for r in rows)


def test_binary_and_rename_diffs_do_not_crash(kuzu_graph) -> None:
    diff = """diff --git a/x.bin b/x.bin
index 111..222 100644
Binary files a/x.bin and b/x.bin differ
diff --git a/old.java b/new.java
similarity index 55%
rename from old.java
rename to new.java
index 111..222 100644
--- a/old.java
+++ b/new.java
@@ -1,2 +1,2 @@
 a
-b
+c
 d
"""
    rep = analyze_pr_pipeline(kuzu_graph, diff)
    assert rep.risk_band in ("low", "medium", "high")
    notes = " ".join(rep.notes).lower()
    assert "binary" in notes or "skipped binary" in notes
    assert "rename" in notes
