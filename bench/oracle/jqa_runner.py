"""jqassistant oracle runner (Plan 1, Task 7).

Runs a bare ``.cypher`` rule against a scanned Java tree and returns the result
rows as ``list[dict]``. Pipeline: compile ``.java`` -> ``.class`` (javac) if no
compiled classes are present, scan into a temp jqassistant store, run ``analyze``
with the rule wrapped in a ``default`` group, parse the XML report.

The bare ``.cypher`` files use ``$name`` placeholders for parameters
(e.g. ``$callee``); they are text-substituted to quoted Cypher string literals
before execution. This keeps the rule files readable and avoids the CLI's rule
parameter plumbing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

RULES_NS = "http://schema.jqassistant.org/rule/v2.0"
REPORT_NS = "http://schema.jqassistant.org/report/v2.9"


class OracleError(RuntimeError):
    """Raised on scan/compile/query failure in the jqassistant oracle."""


def find_jqassistant_bin() -> str:
    env = os.environ.get("JQASSISTANT_BIN")
    if env and Path(env).is_file():
        return env
    hits = sorted(Path.home().glob("jqassistant-cli/*/bin/jqassistant"))
    if hits:
        return str(hits[0])
    which = shutil.which("jqassistant")
    if which:
        return which
    raise OracleError(
        "jqassistant CLI not found. Set JQASSISTANT_BIN or install under "
        "~/jqassistant-cli/<dist>/bin/jqassistant."
    )


def _ensure_compiled(checkout: Path, workdir: Path) -> Path:
    """Return a directory of ``.class`` files, compiling ``.java`` if needed."""
    classes = [p for p in checkout.rglob("*.class")]
    if classes:
        return checkout
    sources = list(checkout.rglob("*.java"))
    if not sources:
        raise OracleError(f"no .java or .class files under {checkout}")
    out = workdir / "classes"
    out.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        ["javac", "-d", str(out), *[str(s) for s in sources]],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise OracleError(f"javac failed for {checkout}:\n{res.stderr.strip()}")
    return out


def _wrap_rule(rule_path: Path, params: dict[str, str] | None) -> str:
    cypher = rule_path.read_text(encoding="utf-8")
    # Stage 1: swap each provided $param for a '$'-free sentinel. The sentinel
    # cannot be matched by the NULL pass below, so a value containing "$word"
    # (e.g. an inner-class FQN "com.x.Foo$Inner") survives intact rather than
    # being corrupted to "com.x.FooNULL".
    sentinel_to_quoted: dict[str, str] = {}
    for i, (key, value) in enumerate((params or {}).items()):
        sentinel = f"\x00PARAM{i}\x00"
        cypher = cypher.replace(f"${key}", sentinel)
        sentinel_to_quoted[sentinel] = json.dumps(str(value))
    # Stage 2: any $param NOT provided becomes NULL, so rules can use the
    # `($x IS NULL OR ...)` idiom to make filtering optional. Only unreplaced
    # placeholders are touched (provided ones are now sentinels).
    cypher = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "NULL", cypher)
    # Stage 3: materialize the quoted values. No further '$' pass runs, so their
    # contents — even a literal "$word" — are final.
    for sentinel, quoted in sentinel_to_quoted.items():
        cypher = cypher.replace(sentinel, quoted)
    rid = "rule:" + re.sub(r"[^A-Za-z0-9]", "_", rule_path.stem)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<jqassistant-rules xmlns="{RULES_NS}">\n'
        f'  <group id="default"><includeConcept refId="{rid}"/></group>\n'
        f'  <concept id="{rid}">\n'
        "    <description>oracle rule</description>\n"
        "    <cypher><![CDATA[\n"
        f"{cypher}\n"
        "    ]]></cypher>\n"
        "  </concept>\n"
        "</jqassistant-rules>\n"
    )


def _run(bin_path: str, args: list[str], cwd: Path) -> None:
    res = subprocess.run(
        [bin_path, *args], cwd=str(cwd), capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise OracleError(
            f"jqassistant {' '.join(args[:1])} failed (rc={res.returncode}):\n"
            f"{res.stdout}\n{res.stderr}"
        )


def _parse_report(report_xml: Path) -> list[dict]:
    if not report_xml.is_file():
        raise OracleError(f"no report at {report_xml}")
    tree = ET.parse(report_xml)
    root = tree.getroot()
    concepts = root.findall(f".//{{{REPORT_NS}}}concept")
    if not concepts:
        return []
    conc = concepts[0]
    result = conc.find(f"{{{REPORT_NS}}}result")
    if result is None:
        return []
    rows_el = result.find(f"{{{REPORT_NS}}}rows")
    out: list[dict] = []
    if rows_el is None:
        return out
    for row in rows_el.findall(f"{{{REPORT_NS}}}row"):
        record: dict = {}
        for col in row.findall(f"{{{REPORT_NS}}}column"):
            name = col.get("name")
            value_el = col.find(f"{{{REPORT_NS}}}value")
            record[name] = value_el.text if value_el is not None else ""
        out.append(record)
    return out


def run_rule(
    checkout_path: str | Path,
    rule_path: str | Path,
    params: dict[str, str] | None = None,
    *,
    jqassistant_bin: str | None = None,
) -> list[dict]:
    """Scan ``checkout_path`` and execute ``rule_path`` -> rows as list[dict]."""
    bin_path = jqassistant_bin or find_jqassistant_bin()
    checkout = Path(checkout_path).resolve()
    rule = Path(rule_path).resolve()

    workdir = Path(tempfile.mkdtemp(prefix="jqa-runner-"))
    try:
        classes = _ensure_compiled(checkout, workdir)
        rules_dir = workdir / "jqassistant" / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "rule.xml").write_text(_wrap_rule(rule, params), encoding="utf-8")

        _run(bin_path, ["scan", "-f", f"java:classpath::{classes}"], cwd=workdir)
        _run(bin_path, ["analyze"], cwd=workdir)
        return _parse_report(workdir / "jqassistant" / "report" / "jqassistant-report.xml")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
