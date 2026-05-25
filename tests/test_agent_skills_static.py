"""Static validation for skills/ directory SKILL.md files.

Imports allowlists from production code (mcp_v2, java_ontology) — not
hand-maintained lists. Validates:
  - frontmatter (name + description present)
  - MCP tool names referenced in skill bodies
  - find kind values
  - direction values
  - edge_types values
  - Tier 2 body structure (stop conditions, recursion limit)

Known gap (intentional — see AGENT-SKILLS-AND-COMMANDS-PROPOSE §11):
  - edge_filter parameters (callee_declaring_role, min_confidence,
    exclude_callee_declaring_roles, dedup_calls, include_unresolved)
    referenced in /mini-map are NOT validated against mcp_v2 parameter
    definitions. The static validator does not parse edge_filter dicts.
    On re-index, manually verify /mini-map against the MCP surface.
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
TIER1_DIR = SKILLS_DIR / "tier-1"
TIER2_DIR = SKILLS_DIR / "tier-2"

TIER1_NAMES = [
    "nl", "controllers", "routes", "clients", "producers",
    "callers", "callees", "handlers", "who-hits-route",
    "implements", "injects",
]

TIER2_NAMES = [
    "explain-feature", "impact-of", "trace-request-flow", "mini-map",
]

ALL_SKILL_NAMES = TIER1_NAMES + TIER2_NAMES


def _skill_dir(name: str) -> Path:
    """Return the tier directory for a skill name."""
    if name in TIER1_NAMES:
        return TIER1_DIR / name
    if name in TIER2_NAMES:
        return TIER2_DIR / name
    raise ValueError(f"Unknown skill name: {name}")


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
    path = _skill_dir(name) / "SKILL.md"
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
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        assert "name" in fm, f"{rel}/SKILL.md missing frontmatter 'name'"
        assert fm["name"] == name, f"{rel}/SKILL.md: name={fm['name']!r}, expected {name!r}"
        assert "description" in fm, f"{rel}/SKILL.md missing frontmatter 'description'"
        assert len(fm["description"]) >= 20, (
            f"{rel}/SKILL.md description too short ({len(fm['description'])} chars)"
        )

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_skill_file_exists(self, name: str):
        path = _skill_dir(name) / "SKILL.md"
        assert path.is_file(), f"Missing {path.relative_to(SKILLS_DIR.parent)}"


class TestMCPToolReferences:
    """Tool names in skill bodies must be valid MCP navigation tools."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_tool_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        refs = _extract_tool_refs(body)
        invalid = refs - _VALID_TOOLS
        assert not invalid, f"{rel}/SKILL.md references invalid tools: {invalid}"

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_skill_references_at_least_one_tool(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        refs = _extract_tool_refs(body)
        assert refs, f"{rel}/SKILL.md references no MCP tools"


class TestKindAndEdgeReferences:
    """Kind, direction, and edge_type values must match production allowlists."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_kind_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        refs = _extract_kind_refs(body)
        invalid = refs - _VALID_KINDS
        assert not invalid, f"{rel}/SKILL.md references invalid find kinds: {invalid}"

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_direction_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        refs = _extract_direction_refs(body)
        invalid = refs - _VALID_DIRECTIONS
        assert not invalid, f"{rel}/SKILL.md references invalid directions: {invalid}"

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_edge_type_refs_are_valid(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        refs = _extract_edge_type_refs(body)
        invalid = refs - _ALL_EDGE_TYPES
        assert not invalid, f"{rel}/SKILL.md references invalid edge_types: {invalid}"



class TestTier2BodyStructure:
    """Tier 2 skills must have stop conditions and recursion limits."""

    @pytest.mark.parametrize("name", TIER2_NAMES)
    def test_has_stop_conditions(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        assert "## Stop conditions" in body, f"{rel}/SKILL.md missing '## Stop conditions'"

    @pytest.mark.parametrize("name", TIER2_NAMES)
    def test_has_recursion_limit(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        assert "## Recursion limit" in body, f"{rel}/SKILL.md missing '## Recursion limit'"

    def test_mini_map_has_classification_rules(self):
        _, body = _read_skill("mini-map")
        assert "### Step 4 — Skill heuristics" in body or "Classification" in body, (
            "skills/tier-2/mini-map/SKILL.md missing classification rules"
        )

    def test_mini_map_has_output_shape(self):
        _, body = _read_skill("mini-map")
        assert "PERSISTS" in body and "DELEGATES" in body, (
            "skills/tier-2/mini-map/SKILL.md missing output shape (PERSISTS/DELEGATES labels)"
        )


class TestWorkedExamples:
    """Every skill must have a worked example section."""

    @pytest.mark.parametrize("name", ALL_SKILL_NAMES)
    def test_has_worked_example(self, name: str):
        _, body = _read_skill(name)
        rel = _skill_dir(name).relative_to(SKILLS_DIR.parent)
        assert "## Worked example" in body, f"{rel}/SKILL.md missing '## Worked example'"


class TestDirectoryIntegrity:
    """skills/ must split into tier-1/ and tier-2/ with the expected skills."""

    def test_tier_dirs_exist(self):
        assert TIER1_DIR.is_dir(), "skills/tier-1/ missing"
        assert TIER2_DIR.is_dir(), "skills/tier-2/ missing"

    def test_tier1_no_extra_dirs(self):
        actual = {p.name for p in TIER1_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
        expected = set(TIER1_NAMES)
        extra = actual - expected
        assert not extra, f"Unexpected skills under skills/tier-1/: {extra}"

    def test_tier1_no_missing_dirs(self):
        actual = {p.name for p in TIER1_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
        expected = set(TIER1_NAMES)
        missing = expected - actual
        assert not missing, f"Missing skills under skills/tier-1/: {missing}"

    def test_tier2_no_extra_dirs(self):
        actual = {p.name for p in TIER2_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
        expected = set(TIER2_NAMES)
        extra = actual - expected
        assert not extra, f"Unexpected skills under skills/tier-2/: {extra}"

    def test_tier2_no_missing_dirs(self):
        actual = {p.name for p in TIER2_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists()}
        expected = set(TIER2_NAMES)
        missing = expected - actual
        assert not missing, f"Missing skills under skills/tier-2/: {missing}"

    def test_no_skills_at_root(self):
        """Skills must live under tier-1/ or tier-2/, not at the root of skills/."""
        root_skill_dirs = {
            p.name for p in SKILLS_DIR.iterdir()
            if p.is_dir() and p.name not in ("tier-1", "tier-2") and (p / "SKILL.md").exists()
        }
        assert not root_skill_dirs, (
            f"Found skills at skills/ root (must be moved into tier-1/ or tier-2/): {root_skill_dirs}"
        )

    def test_readme_exists(self):
        assert (SKILLS_DIR / "README.md").is_file(), "skills/README.md missing"


class TestAgentGuideConsistency:
    """AGENT-GUIDE.md copy-paste block must be self-contained."""

    def test_guide_has_navigation_patterns_table(self):
        """The copy-paste block must include a navigation patterns section
        (it's standalone — no external file references work in a consumer project)."""
        guide = Path(__file__).resolve().parent.parent / "docs" / "AGENT-GUIDE.md"
        text = guide.read_text(encoding="utf-8")
        # Extract the copy-paste block (marker on its own line)
        begin = text.find("<!-- BEGIN java-codebase-rag MCP guide -->")
        end = text.find("<!-- END java-codebase-rag MCP guide -->")
        assert begin != -1 and end != -1, "AGENT-GUIDE.md missing BEGIN/END markers"
        block = text[begin:end]
        assert "### Common navigation patterns" in block, (
            "AGENT-GUIDE.md copy-paste block missing '### Common navigation patterns'"
        )
        # Verify key patterns are present
        for pattern in ["CALLS", "EXPOSES", "IMPLEMENTS", "INJECTS"]:
            assert pattern in block, f"AGENT-GUIDE.md copy-paste block missing {pattern} pattern"

    def test_guide_copy_block_does_not_reference_skills_dir(self):
        """The copy-paste block must not reference skills/ — it won't exist
        in the consumer's project."""
        guide = Path(__file__).resolve().parent.parent / "docs" / "AGENT-GUIDE.md"
        text = guide.read_text(encoding="utf-8")
        begin = text.find("<!-- BEGIN java-codebase-rag MCP guide -->")
        end = text.find("<!-- END java-codebase-rag MCP guide -->")
        assert begin != -1 and end != -1, "AGENT-GUIDE.md missing BEGIN/END markers"
        block = text[begin:end]
        assert "skills/" not in block, (
            "AGENT-GUIDE.md copy-paste block references skills/ — "
            "this path won't resolve in a consumer project. "
            "Keep skills/ references outside the copy-paste block."
        )

    def test_guide_copy_block_has_no_slash_command_aliases(self):
        """The copy-paste block must not contain slash-command alias bullets
        like `/nl <text>` → ... — these imply commands that don't exist
        and will mislead the agent. Incidental mentions (e.g. cross-references
        in prose) are fine."""
        guide = Path(__file__).resolve().parent.parent / "docs" / "AGENT-GUIDE.md"
        text = guide.read_text(encoding="utf-8")
        begin = text.find("<!-- BEGIN java-codebase-rag MCP guide -->")
        end = text.find("<!-- END java-codebase-rag MCP guide -->")
        block = text[begin:end]
        # Match alias definition lines: - `/skillname ...` → tool(...)
        skill_names_pattern = "|".join(re.escape(n) for n in ALL_SKILL_NAMES)
        alias_pattern = re.compile(
            rf"^- `/(?:{skill_names_pattern})\s",
            re.MULTILINE,
        )
        matches = alias_pattern.findall(block)
        assert not matches, (
            f"AGENT-GUIDE.md copy-paste block contains slash-command alias bullets: "
            f"{alias_pattern.findall(block)}. "
            "These are not real commands and will mislead the agent."
        )
