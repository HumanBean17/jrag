"""jdeps dependency-pair oracle (Plan 1, Task 8).

A JDK-native second oracle that cross-checks dependency edges at class
granularity. Parses ``jdeps -v`` output into ``(dependent_fqn, dependency_fqn)``
pairs.
"""
from __future__ import annotations

import re
import shutil
import subprocess

# Matches: "   <dependent> -> <dependency> <origin>" (3 tokens after indent).
# The header line "classes -> java.base" has only 2 tokens and is skipped.
_DEP_LINE = re.compile(r"^\s+(\S+)\s+->\s+(\S+)\s+\S+\s*$")


class OracleError(RuntimeError):
    """Raised when ``jdeps`` is missing or exits non-zero."""


def run(classpath_root: str, package_prefix: str | None = None) -> set[tuple[str, str]]:
    """Return ``(dependent_class_fqn, dependency_class_fqn)`` pairs from jdeps.

    If ``package_prefix`` is given, keep only pairs where BOTH FQNs start with it
    (intra-project dependencies; excludes JDK noise).
    """
    if not shutil.which("jdeps"):
        raise OracleError("jdeps not found on PATH (install a JDK)")
    res = subprocess.run(
        ["jdeps", "-v", str(classpath_root)], capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise OracleError(f"jdeps failed (rc={res.returncode}):\n{res.stderr.strip()}")

    pairs: set[tuple[str, str]] = set()
    for line in res.stdout.splitlines():
        m = _DEP_LINE.match(line)
        if not m:
            continue
        dependent, dependency = m.group(1), m.group(2)
        if package_prefix is not None:
            if not dependent.startswith(package_prefix) or not dependency.startswith(package_prefix):
                continue
        pairs.add((dependent, dependency))
    return pairs
