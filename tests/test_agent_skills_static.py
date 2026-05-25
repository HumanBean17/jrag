"""Static validation for skills/ directory SKILL.md files.

Imports allowlists from production code (mcp_v2, java_ontology) — not
hand-maintained lists. Validates:
  - frontmatter (name + description present)
  - MCP tool names referenced in skill body
  - find kind values
  - direction values
  - edge_types values
  - worked example section present
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
SKILL_NAME = "explore-codebase"
SKILL_PATH = SKILLS_DIR / SKILL_NAME / "SKILL.md"


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


def _read_skill() -> tuple[dict[str, str], str]:
    """Read the explore-codebase SKILL.md and return (frontmatter, body)."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    fm = _parse_frontmatter(text)
    body = re.sub(r"^---\n.*?\n---\n*", "", text, count=1, flags=re.DOTALL)
    return fm, body


def _extract_tool_refs(body: str) -> set[str]:
    """Extract tool names referenced in MCP call patterns."""
    refs: set[str] = set()
    for m in re.finditer(r"`(search|find|describe|neighbors|resolve)\b", body):
        refs.add(m.group(1))
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
    for m in re.finditer(r'edge_types\s*:\s*\[([^\]]+)\]', body):
        inner = m.group(1)
        for val in re.findall(r'"(\w[\w.]*)"', inner):
            if val in _ALL_EDGE_TYPES:
                refs.add(val)
    for m in re.finditer(r'\["(\w[\w.]*)"', body):
        val = m.group(1)
        if val in _ALL_EDGE_TYPES:
            refs.add(val)
    return refs


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSkillFrontmatter:
    """SKILL.md must have valid frontmatter."""

    def test_skill_file_exists(self):
        assert SKILL_PATH.is_file(), f"Missing {SKILL_PATH}"

    def test_frontmatter_has_name_and_description(self):
        fm, _ = _read_skill()
        assert "name" in fm, "SKILL.md missing frontmatter 'name'"
        assert fm["name"] == SKILL_NAME, f"name={fm['name']!r}, expected {SKILL_NAME!r}"
        assert "description" in fm, "SKILL.md missing frontmatter 'description'"
        assert len(fm["description"]) >= 20, (
            f"description too short ({len(fm['description'])} chars)"
        )


class TestMCPToolReferences:
    """Tool names in skill body must be valid MCP navigation tools."""

    def test_tool_refs_are_valid(self):
        _, body = _read_skill()
        refs = _extract_tool_refs(body)
        invalid = refs - _VALID_TOOLS
        assert not invalid, f"SKILL.md references invalid tools: {invalid}"

    def test_skill_references_all_five_tools(self):
        _, body = _read_skill()
        refs = _extract_tool_refs(body)
        missing = _VALID_TOOLS - refs
        assert not missing, f"SKILL.md does not reference all 5 tools, missing: {missing}"


class TestKindAndEdgeReferences:
    """Kind, direction, and edge_type values must match production allowlists."""

    def test_kind_refs_are_valid(self):
        _, body = _read_skill()
        refs = _extract_kind_refs(body)
        invalid = refs - _VALID_KINDS
        assert not invalid, f"SKILL.md references invalid find kinds: {invalid}"

    def test_direction_refs_are_valid(self):
        _, body = _read_skill()
        refs = _extract_direction_refs(body)
        invalid = refs - _VALID_DIRECTIONS
        assert not invalid, f"SKILL.md references invalid directions: {invalid}"

    def test_edge_type_refs_are_valid(self):
        _, body = _read_skill()
        refs = _extract_edge_type_refs(body)
        invalid = refs - _ALL_EDGE_TYPES
        assert not invalid, f"SKILL.md references invalid edge_types: {invalid}"


class TestBodyStructure:
    """Skill body must contain key sections."""

    def test_has_worked_example(self):
        _, body = _read_skill()
        assert "## Worked example" in body, "SKILL.md missing '## Worked example'"

    def test_has_decision_tree(self):
        _, body = _read_skill()
        assert "## Decision tree" in body, "SKILL.md missing '## Decision tree'"

    def test_has_recovery_playbook(self):
        _, body = _read_skill()
        assert "## Recovery playbook" in body, "SKILL.md missing '## Recovery playbook'"

    def test_has_edge_taxonomy(self):
        _, body = _read_skill()
        assert "## Edge taxonomy" in body, "SKILL.md missing '## Edge taxonomy'"

    def test_has_navigation_patterns(self):
        _, body = _read_skill()
        assert "## Common navigation patterns" in body, "SKILL.md missing '## Common navigation patterns'"

    def test_has_reasoning_preamble(self):
        _, body = _read_skill()
        assert "## Forced reasoning preamble" in body, "SKILL.md missing '## Forced reasoning preamble'"


class TestDirectoryIntegrity:
    """skills/ must have expected structure."""

    def test_skill_dir_exists(self):
        assert (SKILLS_DIR / SKILL_NAME).is_dir(), f"skills/{SKILL_NAME}/ missing"

    def test_no_tier_dirs(self):
        """Old tier-1/ and tier-2/ directories must not exist."""
        for tier in ("tier-1", "tier-2"):
            assert not (SKILLS_DIR / tier).is_dir(), f"Old skills/{tier}/ still exists — remove it"

    def test_readme_exists(self):
        assert (SKILLS_DIR / "README.md").is_file(), "skills/README.md missing"

    def test_no_other_skill_dirs(self):
        """Only explore-codebase/ should exist as a skill directory."""
        skill_dirs = {
            p.name for p in SKILLS_DIR.iterdir()
            if p.is_dir() and (p / "SKILL.md").exists()
        }
        assert skill_dirs == {SKILL_NAME}, (
            f"Expected only skills/{SKILL_NAME}/, found: {skill_dirs}"
        )


class TestAgentGuideConsistency:
    """AGENT-GUIDE.md copy-paste block must be self-contained."""

    def test_guide_has_navigation_patterns_table(self):
        """The copy-paste block must include a navigation patterns section."""
        guide = Path(__file__).resolve().parent.parent / "docs" / "AGENT-GUIDE.md"
        text = guide.read_text(encoding="utf-8")
        begin = text.find("<!-- BEGIN java-codebase-rag MCP guide -->")
        end = text.find("<!-- END java-codebase-rag MCP guide -->")
        assert begin != -1 and end != -1, "AGENT-GUIDE.md missing BEGIN/END markers"
        block = text[begin:end]
        assert "### Common navigation patterns" in block, (
            "AGENT-GUIDE.md copy-paste block missing '### Common navigation patterns'"
        )
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
