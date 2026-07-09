from __future__ import annotations

from java_codebase_rag.ast.ast_java import parse_java


def test_string_value_atoms_renamed_call_sites_still_work() -> None:
    src = """
    package smoke;
    import org.springframework.web.bind.annotation.*;
    import org.springframework.kafka.annotation.KafkaListener;
    @RequestMapping("/api")
    class C {
      @GetMapping("/users")
      void a() {}
      @KafkaListener(topics = "${topic}")
      void b(String x) {}
    }
    """
    ast = parse_java(src.encode(), filename="RenameGuard.java")
    t = ast.all_types[0]
    routes = [r for m in t.methods for r in m.routes]
    assert any(r.path == "/api/users" for r in routes)
    assert any(r.topic == "${topic}" and r.resolution_strategy == "spel" for r in routes)
