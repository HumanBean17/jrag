"""Three-way consistency tripwire for the ``annotations/`` Maven module.

The ``annotations/`` module is the canonical, compilable source of truth for
the brownfield annotation surface. Everything else that spells those shapes —
the parser vocabulary under ``src/java_codebase_rag/``, the fenced Java blocks
in ``docs/CONFIGURATION.md`` §4.3, and the copy-paste fixtures under
``tests/fixtures/brownfield_*_stubs/`` — must stay in sync with it. These
tests are pure file reads: no Java toolchain, no index, fast.

A failure here means one of four surfaces drifted: the module, the parser
vocabulary, the docs blocks, or the fixtures. Fix the drifted surface — do not
weaken the assertions. Vocabulary growth (a new enum constant or annotation)
is a signal that the published ``io.github.humanbean17:jrag-annotations``
artifact needs a minor version bump and a release-annotations workflow run.
"""

from __future__ import annotations

import re
from pathlib import Path

from java_codebase_rag.ast.ast_java import (
    CODEBASE_HTTP_CLIENT_ANNOTATIONS,
    CODEBASE_PRODUCER_ANNOTATIONS,
    CODEBASE_ROUTE_ANNOTATIONS,
    _BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS,
)
from java_codebase_rag.graph.java_ontology import (
    VALID_CAPABILITIES,
    VALID_CLIENT_KINDS,
    VALID_PRODUCER_KINDS,
    VALID_ROLES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MODULE_DIR = (
    REPO_ROOT / "annotations" / "src" / "main" / "java"
    / "io" / "github" / "humanbean17" / "jrag" / "annotations"
)
CONFIGURATION_MD = REPO_ROOT / "docs" / "CONFIGURATION.md"
FIXTURE_STUB_DIRS = (
    REPO_ROOT / "tests" / "fixtures" / "brownfield_route_stubs",
    REPO_ROOT / "tests" / "fixtures" / "brownfield_client_stubs",
)
PARSER_SOURCES = (
    REPO_ROOT / "src" / "java_codebase_rag" / "graph" / "graph_enrich.py",
    REPO_ROOT / "src" / "java_codebase_rag" / "ast" / "ast_java.py",
)

# Role/capability annotation names have no frozenset constant in the parser —
# it compares them inline (ann.name == "CodebaseRole" etc.). This is the
# module-side expectation for those inline literals.
_ROLE_CAPABILITY_ANNOTATIONS = frozenset(
    {"CodebaseRole", "CodebaseCapability", "CodebaseCapabilities"}
)

_DECL_RE = re.compile(
    r"public\s+(@interface|enum)\s+(\w+)\s*\{", re.DOTALL
)
_MEMBER_RE = re.compile(
    r"^(\S+)\s+(\w+)\(\)(?:\s+default\s+(.+))?$"
)


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", " ", text)
    return text


def _balanced_body(text: str, start: int) -> str:
    """Return the brace-balanced body starting at ``text[start] == '{'``."""
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : idx]
    raise AssertionError("unbalanced braces in Java declaration body")


def _extract_declarations(java_text: str) -> dict[str, dict]:
    """Map declared type name -> {"kind": "annotation"|"enum", ...} shape.

    Annotations carry ``members`` (list of (type, name, default-or-None));
    enums carry ``constants`` (list of names). Only ``public @interface`` /
    ``public enum`` declarations count — usage examples and classes are
    ignored. Formatting-independent: comments, layout, and meta-annotation
    usage lines (@Target/@Retention/@Repeatable) never enter the shape.
    """
    cleaned = _strip_comments(java_text)
    declarations: dict[str, dict] = {}
    for match in _DECL_RE.finditer(cleaned):
        kind, name = match.group(1), match.group(2)
        body = _balanced_body(cleaned, match.end() - 1)
        if kind == "@interface":
            members = []
            for statement in body.split(";"):
                statement = " ".join(statement.split())
                if not statement:
                    continue
                member = _MEMBER_RE.match(statement)
                assert member is not None, (
                    f"unparsable annotation member {statement!r} in {name}"
                )
                members.append(
                    (member.group(1), member.group(2), member.group(3))
                )
            declarations[name] = {"kind": "annotation", "members": members}
        else:
            tokens = [t.strip() for t in body.replace("\n", " ").split(",")]
            constants = [t for t in tokens if t and t.split("(")[0] == t]
            declarations[name] = {
                "kind": "enum",
                "constants": constants,
            }
    return declarations


def _module_declarations() -> dict[str, dict]:
    result: dict[str, dict] = {}
    for path in sorted(MODULE_DIR.glob("*.java")):
        found = _extract_declarations(path.read_text(encoding="utf-8"))
        assert len(found) == 1, f"{path.name} must declare exactly one type"
        result.update(found)
    return result


def _docs_section_43_declarations() -> dict[str, dict]:
    text = CONFIGURATION_MD.read_text(encoding="utf-8")
    start = text.find("### 4.3 Source stubs")
    end = text.find("### 4.4")
    assert start != -1 and end != -1, "CONFIGURATION.md §4.3/§4.4 headings missing"
    section = text[start:end]
    result: dict[str, dict] = {}
    for block in re.findall(r"```java\n(.*?)```", section, flags=re.DOTALL):
        result.update(_extract_declarations(block))
    return result


def test_module_matches_parser_vocabulary() -> None:
    """Module annotation/enum names and constants equal the parser's sets."""
    module = _module_declarations()

    parser_annotations = (
        CODEBASE_ROUTE_ANNOTATIONS
        | CODEBASE_HTTP_CLIENT_ANNOTATIONS
        | CODEBASE_PRODUCER_ANNOTATIONS
        | _ROLE_CAPABILITY_ANNOTATIONS
    )
    module_annotations = {
        name for name, decl in module.items() if decl["kind"] == "annotation"
    }
    assert module_annotations == parser_annotations, (
        "annotation surface drifted between annotations/ module and parser: "
        f"module-only={sorted(module_annotations - parser_annotations)} "
        f"parser-only={sorted(parser_annotations - module_annotations)}"
    )

    enum_expectations = {
        "CodebaseRoleKind": (set(VALID_ROLES), "VALID_ROLES"),
        "CodebaseCapabilityKind": (set(VALID_CAPABILITIES), "VALID_CAPABILITIES"),
        "CodebaseClientKind": (set(VALID_CLIENT_KINDS), "VALID_CLIENT_KINDS"),
        "CodebaseProducerKind": (set(VALID_PRODUCER_KINDS), "VALID_PRODUCER_KINDS"),
    }
    module_enums = {
        name: decl for name, decl in module.items() if decl["kind"] == "enum"
    }
    assert set(module_enums) == set(enum_expectations) | {"CodebaseHttpMethod"}, (
        f"enum set drifted: module={sorted(module_enums)}"
    )
    for name, (expected, vocab_name) in enum_expectations.items():
        constants = set(module_enums[name]["constants"])
        assert constants == expected, (
            f"{name} constants != parser {vocab_name}: "
            f"module-only={sorted(constants - expected)} "
            f"parser-only={sorted(expected - constants)}"
        )

    # CodebaseHttpMethod: subset (the shadowable set also contains framework
    # mapping-annotation names, so equality does not hold by design).
    http_methods = set(module_enums["CodebaseHttpMethod"]["constants"])
    assert http_methods <= set(_BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS), (
        f"CodebaseHttpMethod constants not recognized by parser: "
        f"{sorted(http_methods - set(_BROWNFIELD_SHADOWABLE_HTTP_FRAMEWORK_METHOD_ANNOTATIONS))}"
    )

    # Role/capability names are inline literals in the parser sources — bind
    # them textually (no frozenset exists to import).
    for literal in sorted(_ROLE_CAPABILITY_ANNOTATIONS):
        for source in PARSER_SOURCES:
            assert literal in source.read_text(encoding="utf-8"), (
                f"parser source {source.name} no longer mentions {literal!r}"
            )


def test_docs_section_43_blocks_match_module() -> None:
    """The §4.3 fenced Java blocks restate the module's shapes exactly."""
    module = _module_declarations()
    docs = _docs_section_43_declarations()
    assert len(docs) == 16, (
        f"expected 16 declared types in §4.3 blocks, found {len(docs)}: {sorted(docs)}"
    )
    for name, docs_decl in docs.items():
        assert name in module, f"§4.3 declares {name} but the module does not"
        module_decl = module[name]
        assert docs_decl["kind"] == module_decl["kind"], (
            f"{name}: §4.3 says {docs_decl['kind']}, module says {module_decl['kind']}"
        )
        if docs_decl["kind"] == "annotation":
            assert docs_decl["members"] == module_decl["members"], (
                f"{name}: member drift between §4.3 and module — "
                f"docs={docs_decl['members']} module={module_decl['members']}"
            )
        else:
            assert docs_decl["constants"] == module_decl["constants"], (
                f"{name}: constant drift between §4.3 and module — "
                f"docs={docs_decl['constants']} module={module_decl['constants']}"
            )


def test_fixture_stubs_match_module() -> None:
    """The copy-paste fixtures restate the module's names and shapes.

    Fixtures keep their ``com.example.rag`` package deliberately — they are
    the test for the zero-dependency copy-in-source path. Parity is about
    names, kinds, members, and constants, not packages.
    """
    module = _module_declarations()
    checked = 0
    for stub_dir in FIXTURE_STUB_DIRS:
        for path in sorted(stub_dir.rglob("*.java")):
            for name, fixture_decl in _extract_declarations(
                path.read_text(encoding="utf-8")
            ).items():
                assert name in module, (
                    f"fixture {path.name} declares {name}, absent from module"
                )
                module_decl = module[name]
                assert fixture_decl["kind"] == module_decl["kind"], (
                    f"{name}: fixture kind {fixture_decl['kind']} != module "
                    f"{module_decl['kind']}"
                )
                if fixture_decl["kind"] == "annotation":
                    assert fixture_decl["members"] == module_decl["members"], (
                        f"{name}: member drift between fixture and module"
                    )
                else:
                    assert fixture_decl["constants"] == module_decl["constants"], (
                        f"{name}: constant drift between fixture and module"
                    )
                checked += 1
    assert checked >= 12, f"expected >=12 fixture declarations, checked {checked}"
