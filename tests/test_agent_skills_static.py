"""Static validation for skills/ directory SKILL.md files.

Imports allowlists from production code (mcp_v2, java_ontology) — not
hand-maintained lists. Validates:
  - frontmatter (name + description present)
  - MCP tool names referenced in skill bodies
  - find kind values
  - direction values
  - edge_types values
  - Tier 2 body structure (stop conditions, recursion limit)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

from java_ontology import NodeKind
from mcp_v2 import ComposedEdgeType, EdgeType

# ---------------------------------------------------------------------------
# Allowlists sourced from production code
# ---------------------------------------------------------------------------

_VALID_TOOLS: frozenset[str] = frozenset(["search", "find", "describe", "neighbors", "resolve"])

_VALID_KINDS: frozenset[str] = frozenset(k.lower() for k in get_args(NodeKind))

_VALID_DIRECTIONS: frozenset[str] = frozenset(["in", "out"])

_ALL_EDGE_TYPES: frozenset[str] = frozenset(get_args(EdgeType)) | frozenset(get_args(ComposedEdgeType))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

TIER1_NAMES = [
    "nl", "controllers", "routes", "clients",
    "callers", "callees", "handlers", "who-hits-route",
    "implements", "injects",
]

TIER2_NAMES = [
    "explain-feature", "impact-of", "trace-request-flow", "mini-map",
]

ALL_SKILL_NAMES = TIER1_NAMES + TIER2_NAMES


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse simple YAML frontmatter (key: value pairs only)."""
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    result: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _extract_tool_refs(body: str) -> set[str]:
    """Extract tool names referenced in MCP call patterns."""
    # Match patterns like `search(...)`, `find(kind=...)`, `describe(id=...)`,
    # `neighbors({ids:`, `resolve(identifier=`, also backtick-wrapped names.
    refs: set[str] = set()
    for m in re.finditer(r"`(search|find|describe|neighbors|resolve)\b", body):
        refs.add(m.group(1))
    # Also catch patterns like search(query=...)  find(kind=...) without backticks
    for m in re.finditer(r"\b(search|find|describe|neighbors|resolve)\s*[\(\{]", body):
        refs.add(m.group(1))
    return refs


def _extract_kind_refs(body: str) -> set[str]:
    """Extract find kind values from skill body."""
    refs: set[str] = set()
    for m in re.finditer(r'kind\s*=\s*["\']?(\w+)["\']?', body):
        val = m.group(1).lower()
        if val in _VALID_KINDS:
            refs.add(val)
    return refs


def _extract_direction_refs(body: str) -> set[str]:
    """Extract direction values from skill body."""
    refs: set[str] = set()
    for m in re.finditer(r'direction\s*:\s*["\']?(in|out)["\']?', body):
        refs.add(m.group(1))
    return refs


def _extract_edge_type_refs(body: str) -> set[str]:
    """Extract edge_types values referenced in skill body."""
    refs: set[str] = set()
    # Match edge_types lists: ["CALLS"] or ["HTTP_CALLS","ASYNC_CALLS","EXPOSES"]
    for m in re.finditer(r'edge_types\s*:\s*\[([^\]]+)\]', body):
        inner = m.group(1)
        for val in re.findall(r'"(\w[\w.]*)"', inner):
            if val in _ALL_EDGE_TYPES:
                refs.add(val)
    # Also match quoted edge names in backticked patterns
    for m in re.finditer(r'\["(\w[\w.]*)"', body):
        val = m.group(1)
        if val in _ALL_EDGE_TYPES:
            refs.add(val)
    return refs


def _read_skill(name: str) -> tuple[dict[str, str], str]:
    """Read a skill's SKILL.md and return (frontmatter, body)."""
    path = SKILLS_DIR / name / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    # Body is everything after the closing ---
    body = re.sub(r"^---\n.*?\n---\n*", "", text, count=1, flags=re.DOTALL)
    return fm, body


# ---------------------------------------------------------------------------
# Parametrized test ids
# ---------------------------------------------------------------------------

@pytest.fixture(params=ALL_SKILL_NAMES, ids=lambda n: f"skill:{n}")
def skill_name(request):
    return request.param


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillFrontmatter:
    """Every SKILL.md must have valid frontmatter."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_frontmatter_has_name_and_description(self, name: str):
        fm, _ = _read_skill(name)
        assert "name" in fm, f"skills/{name}/SKILL.md missing frontmatter 'name'"
        assert fm["name"] == name, f"skills/{name}/SKILL.md: name={fm['name']!r}, expected {name!r}"
        assert "description" in fm, f"skills/{name}/SKILL.md missing frontmatter 'description'"
        assert len(fm["description"]) >= 20, (
            f"skills/{name}/SKILL.md description too short ({len(fm['description'])} chars)"
        )

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_skill_file_exists(self, name: str):
        path = SKILLS_DIR / name / "SKILL.md"
        assert path.is_file(), f"Missing skills/{name}/SKILL.md"


class TestMCPToolReferences:
    """Tool names in skill bodies must be valid MCP navigation tools."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_tool_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        refs = _extract_tool_refs(body)
        invalid = refs - _VALID_TOOLS
        assert not invalid, f"skills/{name}/SKILL.md references invalid tools: {invalid}"

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_skill_references_at_least_one_tool(self, name: str):
        _, body = _read_skill(name)
        refs = _extract_tool_refs(body)
        assert refs, f"skills/{name}/SKILL.md references no MCP tools"


class TestKindAndEdgeReferences:
    """Kind, direction, and edge_type values must match production allowlists."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_kind_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        refs = _extract_kind_refs(body)
        invalid = refs - _VALID_KINDS
        assert not invalid, f"skills/{name}/SKILL.md references invalid find kinds: {invalid}"

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_direction_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        refs = _extract_direction_refs(body)
        invalid = refs - _VALID_DIRECTIONS
        assert not invalid, f"skills/{name}/SKILL.md references invalid directions: {invalid}"

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_edge_type_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        refs = _extract_edge_type_refs(body)
        invalid = refs - _ALL_EDGE_TYPES
        assert not invalid, f"skills/{name}/SKILL.md references invalid edge_types: {invalid}"


class TestTier2BodyStructure:
    """Tier 2 skills must have stop conditions and recursion limits."""

    @pytest.mark.parametrize("name", TIER2_NAMES)
    def test_has_stop_conditions(self, name: str):
        _, body = _read_skill(name)
        assert "## Stop conditions" in body, f"skills/{name}/SKILL.md missing '## Stop conditions'"

    @pytest.mark.parametrize("name", TIER2_NAMES)
    def test_has_recursion_limit(self, name: str):
        _, body = _read_skill(name)
        assert "## Recursion limit" in body, f"skills/{name}/SKILL.md missing '## Recursion limit'"

    def test_mini_map_has_classification_rules(self):
        _, body = _read_skill("mini-map")
        assert "### Step 4 — Skill heuristics" in body or "Classification" in body, (
            "skills/mini-map/SKILL.md missing classification rules"
        )

    def test_mini_map_has_output_shape(self):
        _, body = _read_skill("mini-map")
        assert "PERSISTS" in body and "DELEGATES" in body, (
            "skills/mini-map/SKILL.md missing output shape (PERSISTS/DELEGATES labels)"
        )


class TestWorkedExamples:
    """Every skill must have a worked example section."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_has_worked_example(self, name: str):
        _, body = _read_skill(name)
        assert "## Worked example" in body, f"skills/{name}/SKILL.md missing '## Worked example'"


class TestDirectoryIntegrity:
    """skills/ directory must contain exactly the expected skills."""

    def test_no_extra_skill_dirs(self):
        actual = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
        expected = set(ALL_SKILL_NAMES)
        extra = actual - expected
        assert not extra, f"Unexpected skill directories: {extra}"

    def test_no_missing_skill_dirs(self):
        actual = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
        expected = set(ALL_SKILL_NAMES)
        missing = expected - actual
        assert not missing, f"Missing skill directories: {missing}"

    def test_readme_exists(self):
        assert (SKILLS_DIR / "README.md").is_file(), "skills/README.md missing"


class TestAgentGuideConsistency:
    """AGENT-GUIDE.md slash-aliases must point at skills/, not embed chains."""

    def test_guide_references_skills_directory(self):
        guide = Path(__file__).resolve().parent.parent / "docs" / "AGENT-GUIDE.md"
        text = guide.read_text(encoding="utf-8")
        assert "skills/" in text, "docs/AGENT-GUIDE.md must reference skills/ directory"
        assert "skills/README.md" in text or "skills/" in text, (
            "docs/AGENT-GUIDE.md must point to skills/ for navigation commands"
        )

    def test_guide_does_not_embed_full_slash_alias_bullets(self):
        """The old slash-style aliases section embedded full MCP chains.
        After the rewrite, it must reference skills/ instead."""
        guide = Path(__file__).resolve().parent.parent / "docs" / "AGENT-GUIDE.md"
        text = guide.read_text(encoding="utf-8")
        # The old format had lines like: /nl <text> → search({"query":...})
        # After rewrite, these should be gone (replaced by skills/ pointers)
        old_pattern = re.compile(r'^\- `/(nl|controllers|routes|clients|callers|callees|handlers|who-hits-route|implements|injects)\s+.*→\s*`(search|find|describe|neighbors)', re.MULTILINE)
        assert not old_pattern.search(text), (
            "docs/AGENT-GUIDE.md still contains old embedded slash-alias MCP chains — "
            "should reference skills/ instead"
        )
