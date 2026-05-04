"""Unit tests for call-site extraction (`ast_java.parse_java` → `MethodDecl.call_sites`)."""
from __future__ import annotations

from pathlib import Path

from ast_java import parse_java

_FIXTURE_JAVA = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "call_graph_smoke"
    / "src"
    / "main"
    / "java"
    / "smoke"
    / "StaticImportTest.java"
)


def _method_body_sites(src: str, *, type_name: str, method_name: str) -> list:
    ast = parse_java(src.encode())
    t = next(x for x in ast.all_types if x.name == type_name)
    m = next(x for x in t.methods if x.name == method_name)
    return m.call_sites


def test_bare_call_and_this() -> None:
    src = """
    package p;
    class C {
      void m() { foo(); this.bar(1,2); }
    }
    """
    sites = _method_body_sites(src, type_name="C", method_name="m")
    assert any(s.callee_simple == "foo" and s.receiver_expr == "" for s in sites)
    assert any(s.callee_simple == "bar" and s.receiver_expr == "this" and s.arg_count == 2 for s in sites)


def test_super_call() -> None:
    src = """
    package p;
    class P { void x() {} }
    class C extends P {
      void m() { super.x(); }
    }
    """
    ast = parse_java(src.encode())
    c = next(t for t in ast.all_types if t.name == "C")
    m = next(x for x in c.methods if x.name == "m")
    assert any(s.receiver_expr == "super" and s.callee_simple == "x" for s in m.call_sites)


def test_static_qualified_and_new() -> None:
    src = """
    package p;
    class D { D(int a) {} }
    class U { static void x() {} }
    class C {
      void m() { U.x(); new D(1); }
    }
    """
    sites = _method_body_sites(src, type_name="C", method_name="m")
    assert any(s.is_static_call and s.callee_simple == "x" for s in sites)
    assert any(s.callee_simple == "<init>" and s.arg_count == 1 for s in sites)


def test_method_reference_and_chained_skipped() -> None:
    src = """
    package p;
    class C {
      void m() { Runnable r = String::length; r = getX()::trim; a().b(); }
      String getX() { return ""; }
      String a() { return ""; }
      String b() { return ""; }
    }
    """
    sites = _method_body_sites(src, type_name="C", method_name="m")
    assert any(s.callee_simple == "length" and s.arg_count == -1 for s in sites)
    assert any("getX()" in s.receiver_expr for s in sites)
    assert any(s.callee_simple == "b" and "(" in s.receiver_expr for s in sites)


def test_static_import_maps() -> None:
    src = """
    package p;
    import static java.util.Objects.requireNonNull;
    class C {
      void m() { requireNonNull("x"); }
    }
    """
    ast = parse_java(src.encode())
    assert "requireNonNull" in ast.file_imports.static_methods
    sites = _method_body_sites(src, type_name="C", method_name="m")
    assert any(s.callee_simple == "requireNonNull" and s.receiver_expr == "" for s in sites)


def test_constructor_implicit_super_synthetic() -> None:
    src = """
    package p;
    class P {}
    class C extends P {
      C() {}
    }
    """
    ast = parse_java(src.encode())
    ctor = [x for t in ast.all_types for x in t.methods if x.is_constructor][0]
    assert any(
        s.receiver_expr == "super" and s.callee_simple == "<init>" and s.arg_count == 0
        for s in ctor.call_sites
    )


def test_call_graph_smoke_fixture_file() -> None:
    assert _FIXTURE_JAVA.is_file(), _FIXTURE_JAVA
    ast = parse_java(_FIXTURE_JAVA.read_bytes())
    assert "requireNonNull" in ast.file_imports.static_methods
    t = next(x for x in ast.all_types if x.name == "StaticImportTest")
    m = next(x for x in t.methods if x.name == "m")
    assert any(s.callee_simple == "requireNonNull" for s in m.call_sites)


def test_explicit_super_not_duplicated_implicit() -> None:
    src = """
    package p;
    class P { P(int x) {} }
    class C extends P {
      C() { super(1); }
    }
    """
    ast = parse_java(src.encode())
    c = next(t for t in ast.all_types if t.name == "C")
    ctor = next(x for x in c.methods if x.is_constructor)
    supers = [s for s in ctor.call_sites if s.receiver_expr == "super" and s.callee_simple == "<init>"]
    assert len(supers) == 1
    assert supers[0].arg_count == 1


def test_field_param_local_receiver_shapes_proposal_7_1_cases_4_to_6() -> None:
    """§7.1 #4–6: extraction records the same receiver text for field / param / local."""
    src = """
    package p;
    class Svc { void work() {} }
    class C {
      Svc fieldSvc;
      void byField() { fieldSvc.work(); }
      void byParam(Svc p) { p.work(); }
      void byLocal() { Svc local = new Svc(); local.work(); }
    }
    """
    ast = parse_java(src.encode())
    c = next(t for t in ast.all_types if t.name == "C")
    by_field = next(x for x in c.methods if x.name == "byField")
    by_param = next(x for x in c.methods if x.name == "byParam")
    by_local = next(x for x in c.methods if x.name == "byLocal")
    assert any(s.receiver_expr == "fieldSvc" and s.callee_simple == "work" for s in by_field.call_sites)
    assert any(s.receiver_expr == "p" and s.callee_simple == "work" for s in by_param.call_sites)
    assert any(s.receiver_expr == "local" and s.callee_simple == "work" for s in by_local.call_sites)
    assert ("local", "Svc") in by_local.local_vars


def test_this_super_field_chain_receiver_expr_d6() -> None:
    """D6: extractor preserves full `this.a.b.c` / `super.a.b.c` receiver text (resolved in pass3)."""
    src = """
    package p;
    class Leaf { void target() {} }
    class Mid { Leaf inner; }
    class Outer { Mid mid; }
    class Base { protected Outer root; }
    class Sub extends Base {
      void bySuper() { super.root.mid.inner.target(); }
    }
    class R {
      private Outer root;
      void byThis() { this.root.mid.inner.target(); }
    }
    """
    this_sites = _method_body_sites(src, type_name="R", method_name="byThis")
    assert any(
        s.receiver_expr == "this.root.mid.inner" and s.callee_simple == "target" for s in this_sites
    )
    super_sites = _method_body_sites(src, type_name="Sub", method_name="bySuper")
    assert any(
        s.receiver_expr == "super.root.mid.inner" and s.callee_simple == "target" for s in super_sites
    )


def test_overload_distinct_arities_arg_counts_proposal_7_1_case_12() -> None:
    """§7.1 #12: distinct arities at the call site."""
    src = """
    package p;
    class C {
      void ovl(int a) {}
      void ovl(int a, int b) {}
      void m() { ovl(1); ovl(1, 2); }
    }
    """
    sites = _method_body_sites(src, type_name="C", method_name="m")
    assert any(s.callee_simple == "ovl" and s.arg_count == 1 for s in sites)
    assert any(s.callee_simple == "ovl" and s.arg_count == 2 for s in sites)


def test_lambda_body_call_site_flag_proposal_7_1_case_11() -> None:
    """§7.1 #11: calls inside a lambda body are still collected on the enclosing method."""
    src = """
    package p;
    class C {
      void m() { Runnable r = () -> ping(); }
      void ping() {}
    }
    """
    sites = _method_body_sites(src, type_name="C", method_name="m")
    assert any(s.callee_simple == "ping" and s.in_lambda for s in sites)


def test_anonymous_class_body_call_site_proposal_7_1_case_16() -> None:
    """§7.1 #16: calls inside an anonymous class body attribute to that class's method (D3)."""
    src = """
    package p;
    class C {
      void m() {
        Runnable r = new Runnable() {
          @Override public void run() { ping(); }
        };
      }
      void ping() {}
    }
    """
    ast = parse_java(src.encode())
    c = next(x for x in ast.all_types if x.name == "C")
    m = next(x for x in c.methods if x.name == "m")
    assert not any(s.callee_simple == "ping" for s in m.call_sites), (
        "ping() must not be collected on C#m — it lives in the synthetic Runnable subclass"
    )
    anon = next(t for t in c.nested if t.name.startswith("<anon:"))
    run = next(x for x in anon.methods if x.name == "run")
    assert any(s.callee_simple == "ping" and not s.in_lambda for s in run.call_sites)


def test_method_ref_expression_qualifier_proposal_7_1_case_18() -> None:
    """§7.1 #18: `getX()::trim` — expression qualifier; extractor flags chained receiver."""
    src = """
    package p;
    import java.util.function.Consumer;
    class C {
      void m() { Consumer<String> c = getX()::trim; }
      String getX() { return ""; }
    }
    """
    sites = _method_body_sites(src, type_name="C", method_name="m")
    trim_sites = [s for s in sites if s.callee_simple == "trim"]
    assert trim_sites, "expected method_reference site for trim"
    assert any(
        "getX()" in s.receiver_expr and not s.in_lambda and s.chained_method_reference
        for s in trim_sites
    )


def test_wildcard_static_import_fixture_file_proposal_7_1_case_15() -> None:
    """§7.1 #15: wildcard static import recorded on the compilation unit."""
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "call_graph_smoke"
        / "src"
        / "main"
        / "java"
        / "smoke"
        / "WildcardStaticImport.java"
    )
    assert path.is_file(), path
    ast = parse_java(path.read_bytes())
    assert "smoke.WildUtils" in ast.file_imports.static_wildcards
    t = next(x for x in ast.all_types if x.name == "WildcardStaticImport")
    m = next(x for x in t.methods if x.name == "useWild")
    assert any(s.callee_simple == "wildHelper" and s.receiver_expr == "" for s in m.call_sites)


def test_default_ctor_synthesized_when_no_explicit_ctor() -> None:
    """B1: a class with no declared constructor must get a synthetic <init>() entry."""
    src = """
    package p;
    public class HasNoCtor {}
    """
    ast = parse_java(src.encode())
    t = next(x for x in ast.all_types if x.name == "HasNoCtor")
    ctors = [m for m in t.methods if m.is_constructor]
    assert len(ctors) == 1, f"expected exactly 1 synthetic ctor, got {ctors}"
    assert ctors[0].name == "<init>"
    assert ctors[0].signature == "<init>()"
    assert ctors[0].parameters == []


def test_default_ctor_not_synthesized_when_explicit_ctor_present() -> None:
    """B1 guard: a class with an explicit ctor must NOT get a second synthetic one."""
    src = """
    package p;
    public class HasCtor {
        HasCtor(int x) {}
    }
    """
    ast = parse_java(src.encode())
    t = next(x for x in ast.all_types if x.name == "HasCtor")
    ctors = [m for m in t.methods if m.is_constructor]
    assert len(ctors) == 1
    assert ctors[0].parameters[0].type_name == "int"


def test_default_ctor_not_synthesized_for_lombok_required_args() -> None:
    """B1 guard: @RequiredArgsConstructor / @AllArgsConstructor suppress synthesis."""
    for ann in ("RequiredArgsConstructor", "AllArgsConstructor"):
        src = f"""
        package p;
        @{ann}
        public class Foo {{
            private final String x;
        }}
        """
        ast = parse_java(src.encode())
        t = next(x for x in ast.all_types if x.name == "Foo")
        ctors = [m for m in t.methods if m.is_constructor]
        assert ctors == [], f"@{ann} should suppress default-ctor synthesis, got {ctors}"


def test_nested_calls_fixture_file_parse_proposal_7_1_11_16_18() -> None:
    """Parse on-disk NestedCalls.java (smoke fixture) for combined #11 / #16 / #18 shapes."""
    path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "call_graph_smoke"
        / "src"
        / "main"
        / "java"
        / "smoke"
        / "NestedCalls.java"
    )
    assert path.is_file(), path
    ast = parse_java(path.read_bytes())
    nested_calls = next(t for t in ast.all_types if t.name == "NestedCalls")
    m = next(x for x in nested_calls.methods if x.name == "m")
    assert not any(s.callee_simple == "pingFromAnon" for s in m.call_sites)
    anon = next(t for t in nested_calls.nested if t.name.startswith("<anon:"))
    run = next(x for x in anon.methods if x.name == "run")
    assert any(s.callee_simple == "pingFromAnon" and not s.in_lambda for s in run.call_sites)
    assert any(s.callee_simple == "pingFromLambda" and s.in_lambda for s in m.call_sites)
    assert any(
        s.callee_simple == "trim"
        and "getX()" in s.receiver_expr
        and not s.in_lambda
        and s.chained_method_reference
        for s in m.call_sites
    )
