"""Validation test: scan bundle files for prohibited Neo4j terms.

Ensures zero Neo4j API leakage across agents, skills, and the hook module.

Scans:
  - agents/
  - skills/
  - modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/

Skip patterns (files / directories excluded from scanning):
  - config_resolver.py  (backward-compat neo4j_config property)
  - test_no_neo4j_leakage.py  (self-references)
  - plans/  (design documents)
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BUNDLE_ROOT = Path(__file__).resolve().parent.parent

_SCAN_DIRS = [
    BUNDLE_ROOT / "agents",
    BUNDLE_ROOT / "skills",
    BUNDLE_ROOT
    / "modules"
    / "hook-context-intelligence"
    / "amplifier_module_hook_context_intelligence",
    BUNDLE_ROOT
    / "modules"
    / "tool-graph-query"
    / "amplifier_module_tool_graph_query",
]

_SKIP_NAMES = {"config_resolver.py", "test_no_neo4j_leakage.py"}
_SKIP_DIRS = {"plans"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _should_skip(path: Path) -> bool:
    """Return True if the path matches any skip pattern."""
    if path.name in _SKIP_NAMES:
        return True
    # Use parts for directory matching — avoids false positives like 'deployment-plans/'
    if _SKIP_DIRS.intersection(path.parts):
        return True
    return False


def _scan_files() -> list[Path]:
    """Collect all .py and .md files from the configured scan directories."""
    files: list[Path] = []
    for scan_dir in _SCAN_DIRS:
        if not scan_dir.exists():
            continue
        for suffix in ("*.py", "*.md"):
            for f in scan_dir.rglob(suffix):
                if not _should_skip(f):
                    files.append(f)
    return files


# Computed once at import time; all helpers share the same list
_FILES: list[Path] = _scan_files()


def _check_term(term: str, *, case_insensitive: bool = False) -> list[str]:
    """Search scanned files for a prohibited term.

    Returns a list of violation strings in 'file:line: content' format.
    """
    violations: list[str] = []
    search_term = term.lower() if case_insensitive else term

    for path in _FILES:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for lineno, line in enumerate(text.splitlines(), start=1):
            haystack = line.lower() if case_insensitive else line
            if search_term in haystack:
                rel = path.relative_to(BUNDLE_ROOT)
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_no_neo4j_case_insensitive() -> None:
    """'neo4j' (case-insensitive) must not appear in any scanned file."""
    violations = _check_term("neo4j", case_insensitive=True)
    assert violations == [], (
        f"Found {len(violations)} prohibited 'neo4j' reference(s):\n"
        + "\n".join(violations)
    )


def test_no_neo4j_graph_store() -> None:
    """'Neo4jGraphStore' must not appear in any scanned file."""
    violations = _check_term("Neo4jGraphStore")
    assert violations == [], (
        f"Found {len(violations)} prohibited 'Neo4jGraphStore' reference(s):\n"
        + "\n".join(violations)
    )


def test_no_graph_forest_name() -> None:
    """'graph_forest_name' must not appear in any scanned file."""
    violations = _check_term("graph_forest_name")
    assert violations == [], (
        f"Found {len(violations)} prohibited 'graph_forest_name' reference(s):\n"
        + "\n".join(violations)
    )


def test_no_execute_query() -> None:
    """'execute_query' must not appear in any scanned file."""
    violations = _check_term("execute_query")
    assert violations == [], (
        f"Found {len(violations)} prohibited 'execute_query' reference(s):\n"
        + "\n".join(violations)
    )


def test_no_old_skill_directory() -> None:
    """The old 'context-intelligence-neo4j-search' skill directory must not exist."""
    old_skill_dir = BUNDLE_ROOT / "skills" / "context-intelligence-neo4j-search"
    assert not old_skill_dir.exists(), (
        f"Old Neo4j skill directory still exists: {old_skill_dir}"
    )


def test_no_old_skill_name_in_agents() -> None:
    """'context-intelligence-neo4j-search' must not appear in any scanned file."""
    violations = _check_term("context-intelligence-neo4j-search")
    assert violations == [], (
        f"Found {len(violations)} prohibited 'context-intelligence-neo4j-search' reference(s):\n"
        + "\n".join(violations)
    )
