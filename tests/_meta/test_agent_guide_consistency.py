"""Static validation for the AGENT-GUIDE.md copy-paste block.

Recovered from ``test_agent_skills_static.py`` when the skill/agent artifacts
were removed (the CLI surface ships a ``jrag prime`` SessionStart hook instead
of files). These two tests read a LIVE file — ``docs/AGENT-GUIDE.md`` — and
neither touched the deleted tree, so they stay as a tripwire on the guide's
copy-paste block.
"""

from __future__ import annotations

from pathlib import Path


GUIDE = Path(__file__).resolve().parent.parent.parent / "docs" / "AGENT-GUIDE.md"


class TestAgentGuideConsistency:
    """AGENT-GUIDE.md copy-paste block must be self-contained."""

    def test_guide_has_navigation_patterns_table(self):
        """The copy-paste block must include a navigation patterns section."""
        text = GUIDE.read_text(encoding="utf-8")
        # NOTE: the BEGIN/END marker string is intentionally the OLD brand
        # ("java-codebase-rag"), not "jrag": this marker is load-bearing for
        # already-deployed skill copies (consumers copy-paste the block verbatim
        # and the install flow matches on it). Do NOT "fix" it to the new name.
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
        text = GUIDE.read_text(encoding="utf-8")
        # See test_guide_has_navigation_patterns_table: the OLD-brand marker is
        # load-bearing for deployed skill copies; do not rename it.
        begin = text.find("<!-- BEGIN java-codebase-rag MCP guide -->")
        end = text.find("<!-- END java-codebase-rag MCP guide -->")
        assert begin != -1 and end != -1, "AGENT-GUIDE.md missing BEGIN/END markers"
        block = text[begin:end]
        assert "skills/" not in block, (
            "AGENT-GUIDE.md copy-paste block references skills/ — "
            "this path won't resolve in a consumer project. "
            "Keep skills/ references outside the copy-paste block."
        )
