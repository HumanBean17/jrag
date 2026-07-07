"""Unit tests for brownfield role / capability resolution (config + meta + @Codebase*)."""
from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from ast_java import parse_java
from graph_enrich import (
    BrownfieldOverrides,
    _load_brownfield_overrides,
    collect_annotation_meta_chain,
    resolve_role_and_capabilities,
)


def _tdecl(src: str):
    ast = parse_java(src.encode("utf-8"))
    assert ast.top_level_types, "expected a top-level type"
    return ast.top_level_types[0]


def _empty() -> BrownfieldOverrides:
    return BrownfieldOverrides({}, {}, {}, {}, {}, {})


@pytest.fixture(autouse=True)
def _clear_brownfield_caches() -> object:
    yield
    _load_brownfield_overrides.cache_clear()
    collect_annotation_meta_chain.cache_clear()


class TestResolveBasics:
    def test_empty_config_matches_stock_ast(self) -> None:
        t = _tdecl(
            """
            package p;
            @org.springframework.web.bind.annotation.RestController
            class C {}
        """
        )
        r, c = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=None)
        from ast_java import infer_capabilities_for_type, infer_role_for_type

        assert r == infer_role_for_type(t)
        assert c == sorted(set(infer_capabilities_for_type(t)))

    def test_annotation_map_sets_service(self) -> None:
        t = _tdecl(
            """
            package p;
            @AcmeService
            public class C {}
        """
        )
        r0, _ = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=None)
        assert r0 == "OTHER"
        ov = BrownfieldOverrides({"AcmeService": "SERVICE"}, {}, {}, {}, {}, {})
        r, _ = resolve_role_and_capabilities(t, overrides=ov, meta_chain=None)
        assert r == "SERVICE"

    def test_annotation_map_does_not_clobber_spring_stereotype(self) -> None:
        t = _tdecl(
            """
            package p;
            @AcmeService
            @org.springframework.web.bind.annotation.RestController
            public class C {}
        """
        )
        ov = BrownfieldOverrides({"AcmeService": "SERVICE"}, {}, {}, {}, {}, {})
        r, _ = resolve_role_and_capabilities(t, overrides=ov, meta_chain=None)
        assert r == "CONTROLLER"

    def test_capability_from_method_level_mapping(self) -> None:
        t = _tdecl(
            """
            package p;
            public class C {
                @CompanyKafkaTopic
                public void m() {}
            }
        """
        )
        ov = BrownfieldOverrides(
            {},
            {"CompanyKafkaTopic": ("MESSAGE_LISTENER",)},
            {},
            {},
            {},
            {},
        )
        _, c = resolve_role_and_capabilities(t, overrides=ov, meta_chain=None)
        assert "MESSAGE_LISTENER" in c

    def test_fqn_override_role_and_capability(self) -> None:
        t = _tdecl(
            """
            package com.legacy;
            public class X {}
        """
        )
        ov = BrownfieldOverrides(
            {},
            {},
            {"com.legacy.X": "SERVICE"},
            {"com.legacy.X": ("MESSAGE_LISTENER",)},
            {},
            {},
        )
        r, c = resolve_role_and_capabilities(t, overrides=ov, meta_chain=None)
        assert r == "SERVICE"
        assert "MESSAGE_LISTENER" in c

    def test_fqn_role_wins_over_ast(self) -> None:
        t = _tdecl(
            """
            package p;
            @org.springframework.context.annotation.Component
            public class X {}
        """
        )
        ov = BrownfieldOverrides({}, {}, {"p.X": "SERVICE"}, {}, {}, {})
        r, _ = resolve_role_and_capabilities(t, overrides=ov, meta_chain=None)
        assert r == "SERVICE"

    def test_missing_config_path_yields_empty_overrides(self) -> None:
        b = _load_brownfield_overrides("/nonexistent_path_brownfield_xyz_999")
        assert b.annotation_to_role == {}
        assert b.fqn_role == {}


class TestConfigWarnings:
    def test_unknown_role_dropped(self, tmp_path: Path) -> None:
        yml = tmp_path / ".java-codebase-rag.yaml"
        yml.write_text(
            "role_overrides:\n  annotations:\n    W: __NOT_A_REAL_ROLE__\n",
            encoding="utf-8",
        )
        f = io.StringIO()
        with redirect_stderr(f):
            b = _load_brownfield_overrides(str(tmp_path))
        assert "W" not in b.annotation_to_role
        assert "NOT_A_REAL" in f.getvalue() or "unknown" in f.getvalue().lower()

    def test_malformed_yaml_produces_empty_overrides(self, tmp_path: Path) -> None:
        (tmp_path / ".java-codebase-rag.yaml").write_text(
            "role_overrides: [\n  not closed\n",
            encoding="utf-8",
        )
        b = _load_brownfield_overrides(str(tmp_path))
        assert b == BrownfieldOverrides({}, {}, {}, {}, {}, {})


def _acme_with_meta(tmp_path: Path) -> None:
    ann = tmp_path / "ann" / "AcmeService.java"
    ann.parent.mkdir(parents=True, exist_ok=True)
    ann.write_text(
        "package ann;\n"
        "import org.springframework.stereotype.Service;\n"
        "@Service public @interface AcmeService {}\n",
        encoding="utf-8",
    )
    cj = tmp_path / "C.java"
    cj.write_text(
        "package p;\nimport ann.AcmeService;\n@AcmeService public class C {}\n",
        encoding="utf-8",
    )


class TestLayerAMeta:
    def test_meta_annotated_interface_gives_service(self, tmp_path: Path) -> None:
        _acme_with_meta(tmp_path)
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        tdecl = _tdecl((tmp_path / "C.java").read_text(encoding="utf-8"))
        r, _ = resolve_role_and_capabilities(
            tdecl, overrides=_empty(), meta_chain=m
        )
        assert r == "SERVICE"

    def test_two_hop_to_service(self, tmp_path: Path) -> None:
        ann = tmp_path / "x" / "ann"
        ann.mkdir(parents=True, exist_ok=True)
        (ann / "AcmeService.java").write_text(
            "package x.ann; import org.springframework.stereotype.Service; "
            "@Service public @interface AcmeService {}\n",
            encoding="utf-8",
        )
        (ann / "AcmeOrch.java").write_text(
            "package x.ann; @AcmeService public @interface AcmeOrchestrator {}\n",
            encoding="utf-8",
        )
        cj = tmp_path / "p" / "C.java"
        cj.parent.mkdir(parents=True, exist_ok=True)
        cj.write_text(
            "package p; import x.ann.AcmeOrchestrator; "
            "@AcmeOrchestrator public class C {}\n",
            encoding="utf-8",
        )
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        tdecl = _tdecl(cj.read_text(encoding="utf-8"))
        r, _ = resolve_role_and_capabilities(
            tdecl, overrides=_empty(), meta_chain=m
        )
        assert r == "SERVICE"

    def test_method_meta_gives_message_listener(
        self, tmp_path: Path
    ) -> None:
        ann = tmp_path / "a" / "CompanyKafkaTopic.java"
        ann.parent.mkdir(parents=True, exist_ok=True)
        ann.write_text(
            "package a; import org.springframework.kafka.annotation.KafkaListener; "
            "@KafkaListener public @interface CompanyKafkaTopic {}\n",
            encoding="utf-8",
        )
        cj = tmp_path / "C.java"
        cj.write_text(
            "package p; import a.CompanyKafkaTopic;\n"
            "class C { @CompanyKafkaTopic void m() {} }\n",
            encoding="utf-8",
        )
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        tdecl = _tdecl(cj.read_text(encoding="utf-8"))
        _, c = resolve_role_and_capabilities(
            tdecl, overrides=_empty(), meta_chain=m
        )
        assert "MESSAGE_LISTENER" in c

    def test_b_beats_a_regression(self, tmp_path: Path) -> None:
        ann = tmp_path / "a" / "P.java"
        ann.parent.mkdir(parents=True, exist_ok=True)
        ann.write_text(
            "package a; import org.springframework.stereotype.Service; "
            "@Service public @interface AcmeProcessor {}\n",
            encoding="utf-8",
        )
        cj = tmp_path / "C.java"
        cj.write_text(
            "package p; import a.AcmeProcessor; @AcmeProcessor public class C {}\n",
            encoding="utf-8",
        )
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        tdecl = _tdecl(cj.read_text(encoding="utf-8"))
        ov = BrownfieldOverrides({"AcmeProcessor": "COMPONENT"}, {}, {}, {}, {}, {})
        r, _ = resolve_role_and_capabilities(
            tdecl, overrides=ov, meta_chain=m
        )
        assert r == "COMPONENT"

    def test_meta_cycle_ab_does_not_crash_role_other(self, tmp_path: Path) -> None:
        ann = tmp_path / "a"
        ann.mkdir()
        (ann / "A.java").write_text(
            "package a; @B public @interface A {}\n",
            encoding="utf-8",
        )
        (ann / "B.java").write_text(
            "package a; @A public @interface B {}\n",
            encoding="utf-8",
        )
        cj = tmp_path / "C.java"
        cj.write_text(
            "package p; import a.A; @A public class C {}\n",
            encoding="utf-8",
        )
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        t = _tdecl(cj.read_text(encoding="utf-8"))
        r, _ = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=m)
        assert r == "OTHER"

    def test_meta_deep_chain_six_wrappers_does_not_resolve_service(
        self, tmp_path: Path
    ) -> None:
        """W1->…->W6->@Service: path cap yields OTHER (PLAN-BROWNFIELD depth test)."""
        mdir = tmp_path / "m"
        mdir.mkdir()
        for i in range(1, 6):
            nxt = f"W{i + 1}"
            (mdir / f"W{i}.java").write_text(
                f"package m; @{nxt} public @interface W{i} {{}}\n",
                encoding="utf-8",
            )
        (mdir / "W6.java").write_text(
            "package m; import org.springframework.stereotype.Service; "
            "@Service public @interface W6 {}\n",
            encoding="utf-8",
        )
        cj = tmp_path / "C.java"
        cj.write_text(
            "package p; import m.W1; @W1 public class C {}\n",
            encoding="utf-8",
        )
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        t = _tdecl(cj.read_text(encoding="utf-8"))
        r, _ = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=m)
        assert r == "OTHER"

    def test_fqn_override_wins_over_meta_and_annotation_b(
        self, tmp_path: Path
    ) -> None:
        ann = tmp_path / "a" / "Z.java"
        ann.parent.mkdir(parents=True, exist_ok=True)
        ann.write_text(
            "package a; import org.springframework.stereotype.Service; "
            "@Service public @interface Z {}\n",
            encoding="utf-8",
        )
        cj = tmp_path / "C.java"
        cj.write_text(
            "package p; import a.Z; @Z public class C {}\n",
            encoding="utf-8",
        )
        m = collect_annotation_meta_chain(str(tmp_path.resolve()))
        t = _tdecl(cj.read_text(encoding="utf-8"))
        ov = BrownfieldOverrides(
            {"Z": "COMPONENT"},
            {},
            {"p.C": "REPOSITORY"},
            {},
            {},
            {},
        )
        r, _ = resolve_role_and_capabilities(
            t, overrides=ov, meta_chain=m,
        )
        assert r == "REPOSITORY"


class TestLayerC:
    def test_codebase_role_plain_class(self) -> None:
        t = _tdecl(
            """
            package p;
            @CodebaseRole(CodebaseRoleKind.SERVICE)
            public class C {}
        """
        )
        r, _ = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=None)
        assert r == "SERVICE"

    def test_codebase_role_value_form(self) -> None:
        t = _tdecl(
            "package p; @CodebaseRole(value = CodebaseRoleKind.SERVICE) public class C {}"
        )
        r, _ = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=None)
        assert r == "SERVICE"

    def test_codebase_role_overrides_spring_stereotype(self) -> None:
        t = _tdecl(
            """
            package p;
            @CodebaseRole(CodebaseRoleKind.CONTROLLER)
            @org.springframework.stereotype.Service
            public class C {}
        """
        )
        r, _ = resolve_role_and_capabilities(t, overrides=_empty(), meta_chain=None)
        assert r == "CONTROLLER"

    def test_legacy_string_codebase_role_warns_and_is_ignored(self) -> None:
        t = _tdecl(
            """
            package p;
            @CodebaseRole("CONTROLLER")
            @org.springframework.stereotype.Service
            public class C {}
        """
        )
        f = io.StringIO()
        with redirect_stderr(f):
            r, _ = resolve_role_and_capabilities(
                t, overrides=_empty(), meta_chain=None
            )
        assert r == "SERVICE"
        st = f.getvalue()
        assert "no longer supported" in st

    def test_bogus_codebase_role_warns(self) -> None:
        t = _tdecl(
            """
            package p;
            @CodebaseRole(CodebaseRoleKind.BOGUS)
            @org.springframework.stereotype.Service
            public class C {}
        """
        )
        f = io.StringIO()
        with redirect_stderr(f):
            r, _ = resolve_role_and_capabilities(
                t, overrides=_empty(), meta_chain=None
            )
        assert r == "SERVICE"
        st = f.getvalue()
        assert "BOGUS" in st or "invalid" in st.lower()

    def test_codebase_capabilities_array(self) -> None:
        t = _tdecl(
            r"""
            package p;
            @CodebaseCapabilities({
                @CodebaseCapability(CodebaseCapabilityKind.MESSAGE_LISTENER),
                @CodebaseCapability(CodebaseCapabilityKind.MESSAGE_PRODUCER)
            })
            public class C {}
        """
        )
        _, c = resolve_role_and_capabilities(
            t, overrides=_empty(), meta_chain=None
        )
        assert c == ["MESSAGE_LISTENER", "MESSAGE_PRODUCER"]

    def test_stacked_separate_codebase_capability_annotations(self) -> None:
        t = _tdecl(
            r"""
            package p;
            @CodebaseCapability(CodebaseCapabilityKind.MESSAGE_LISTENER)
            @CodebaseCapability(CodebaseCapabilityKind.SCHEDULED_TASK)
            public class C {}
        """
        )
        _, c = resolve_role_and_capabilities(
            t, overrides=_empty(), meta_chain=None
        )
        assert c == ["MESSAGE_LISTENER", "SCHEDULED_TASK"]

    def test_codebase_capability_additive_with_ast_injection_capability(self) -> None:
        t = _tdecl(
            """
            package p;
            @org.springframework.stereotype.Service
            @CodebaseCapability(CodebaseCapabilityKind.MESSAGE_PRODUCER)
            public class C {
                org.springframework.kafka.core.KafkaTemplate t;
            }
        """
        )
        _, c = resolve_role_and_capabilities(
            t, overrides=_empty(), meta_chain=None,
        )
        assert "MESSAGE_PRODUCER" in c

    def test_legacy_string_codebase_capability_warns_and_is_ignored(self) -> None:
        t = _tdecl(
            """
            package p;
            @CodebaseCapability("MESSAGE_LISTENER")
            public class C {}
        """
        )
        f = io.StringIO()
        with redirect_stderr(f):
            _, c = resolve_role_and_capabilities(
                t, overrides=_empty(), meta_chain=None,
            )
        assert "MESSAGE_LISTENER" not in c
        st = f.getvalue()
        assert "no longer supported" in st


def test_fqn_fires_with_enrich_chunk_lance_path(tmp_path: Path) -> None:
    """Regression: role_overrides fqn + enrich_chunk feeds capabilities to callers."""
    from graph_enrich import enrich_chunk

    y = tmp_path / ".java-codebase-rag.yaml"
    y.write_text(
        "role_overrides:\n  fqn:\n"
        "    com.legacy.Foo: { role: SERVICE, capabilities: [MESSAGE_LISTENER] }\n",
        encoding="utf-8",
    )
    src = (
        "package com.legacy;\n"
        "public class Foo { void m() {} }\n"
    )
    ast = parse_java(src.encode("utf-8"))
    c = enrich_chunk(
        ast, chunk_start_byte=0, chunk_end_byte=500, file_path="a.java", project_root=tmp_path
    )
    assert c.capabilities == ["MESSAGE_LISTENER"]
    assert c.role == "SERVICE"


def test_tier1_java_lance_chunk_capabilities_list_type_matches_other_lists() -> None:
    """Pre-flight tier 1: `capabilities` uses the same Arrow list<string> as other list cols."""
    pytest.importorskip("cocoindex")  # java_index_flow_lancedb pulls cocoindex at import
    import java_index_flow_lancedb as java_lance
    from typing import Annotated, get_args, get_origin, get_type_hints

    from java_index_flow_lancedb import JavaLanceChunk

    def lance_anno(ftype: object) -> object:
        if get_origin(ftype) is not Annotated:
            return None
        args = get_args(ftype)
        return args[1] if len(args) >= 2 else None

    hints = get_type_hints(
        JavaLanceChunk,
        globalns=vars(java_lance),
        localns=vars(java_lance),
        include_extras=True,
    )
    l_cap = lance_anno(hints["capabilities"])
    l_ann = lance_anno(hints["annotations_on_type"])
    l_sym = lance_anno(hints["symbols"])
    assert l_cap is not None
    assert l_cap == l_ann == l_sym


def test_tier2_lance_row_carries_enrich_capabilities_without_lancedb() -> None:
    """Pre-flight tier 2: `JavaLanceChunk` row would carry the same `capabilities` as `enrich_chunk` (CocoIndex wiring)."""
    pytest.importorskip("cocoindex")  # java_index_flow_lancedb pulls cocoindex at import
    import numpy as np

    from graph_enrich import enrich_chunk
    from java_index_flow_lancedb import JavaLanceChunk
    from ast_java import ONTOLOGY_VERSION

    # Default SBERT (all-MiniLM-L6-v2) embedding size — no model download in CI.
    z = np.zeros((384,), dtype=np.float32)

    src = (
        "package p;\n"
        "import org.springframework.kafka.annotation.KafkaListener;\n"
        "class C { @KafkaListener void m() {} }\n"
    )
    ast = parse_java(src.encode("utf-8"))
    e = enrich_chunk(
        ast,
        chunk_start_byte=0,
        chunk_end_byte=len(src.encode("utf-8")),
        file_path="C.java",
        project_root=None,
    )
    assert "MESSAGE_LISTENER" in e.capabilities
    row = JavaLanceChunk(
        id="0",
        filename="C.java",
        language="java",
        text="x",
        range_start=0,
        range_end=1,
        start={},
        end={},
        embedding=z,
        package=e.package,
        module=e.module,
        microservice=e.microservice,
        primary_type_fqn=e.primary_type_fqn,
        primary_type_kind=e.primary_type_kind,
        role=e.role,
        capabilities=list(e.capabilities),
        annotations_on_type=e.annotations_on_type,
        symbols=e.symbols,
        ontology_version=ONTOLOGY_VERSION,
    )
    assert "MESSAGE_LISTENER" in row.capabilities


def test_lance_table_round_trips_list_capabilities(tmp_path: Path) -> None:
    """Lance can store and read list<string> `capabilities` (CocoIndex write path).

    Runs after tier1/tier2: importing lancedb/pyarrow first breaks cocoindex's
    numpy import in the same pytest process (numpy._core.numeric).
    """
    pytest.importorskip("lancedb")
    import lancedb
    import pyarrow as pa

    root = tmp_path / "lance"
    root.mkdir()
    db = lancedb.connect(str(root))
    tbl = db.create_table(
        "chunks",
        pa.table(
            {
                "id": [1],
                "capabilities": pa.array(
                    [["MESSAGE_LISTENER"]], type=pa.list_(pa.string())
                ),
            }
        ),
    )
    round_tbl = db.open_table(tbl.name)
    row = round_tbl.to_arrow()
    c = list(row["capabilities"].to_pylist()[0])
    assert c == ["MESSAGE_LISTENER"]
