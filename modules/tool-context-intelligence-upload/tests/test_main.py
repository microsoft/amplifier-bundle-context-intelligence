"""Tests for __main__.py — python -m entry point.

Verifies that:
- Running ``python -m amplifier_module_tool_context_intelligence_upload -h`` exits 0.
- Compact help is printed to stdout (not stderr).
- The file has the correct structure: docstring, import, and if __name__ guard only.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODULE_DIR = Path(__file__).parent.parent / "amplifier_module_tool_context_intelligence_upload"

MAIN_FILE = MODULE_DIR / "__main__.py"


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


class TestMainFileExists:
    """The __main__.py file must exist at the expected path."""

    def test_file_exists(self):
        assert MAIN_FILE.exists(), f"Expected {MAIN_FILE} to exist"


# ---------------------------------------------------------------------------
# python -m … -h → exit 0, compact help to stdout
# ---------------------------------------------------------------------------


class TestPythonMInvocation:
    """`python -m amplifier_module_tool_context_intelligence_upload -h` behaviour."""

    def test_minus_h_exits_zero(self):
        result = subprocess.run(
            [sys.executable, "-m", "amplifier_module_tool_context_intelligence_upload", "-h"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 but got {result.returncode}. stderr: {result.stderr!r}"
        )

    def test_minus_h_prints_to_stdout(self):
        result = subprocess.run(
            [sys.executable, "-m", "amplifier_module_tool_context_intelligence_upload", "-h"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip(), "Expected compact help on stdout but got nothing"

    def test_minus_h_stdout_contains_usage(self):
        result = subprocess.run(
            [sys.executable, "-m", "amplifier_module_tool_context_intelligence_upload", "-h"],
            capture_output=True,
            text=True,
        )
        assert "context-intelligence-upload" in result.stdout, (
            f"Expected 'context-intelligence-upload' in stdout: {result.stdout!r}"
        )


# ---------------------------------------------------------------------------
# File structure — only import + if __name__ guard, nothing else
# ---------------------------------------------------------------------------


class TestMainFileStructure:
    """The file must contain only the docstring, import, and if __name__ guard."""

    def _parse(self) -> ast.Module:
        source = MAIN_FILE.read_text(encoding="utf-8")
        return ast.parse(source)

    def test_has_module_docstring(self):
        tree = self._parse()
        first = tree.body[0] if tree.body else None
        assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant), (
            "Expected a module-level docstring as the first statement"
        )

    def test_imports_main_from_cli(self):
        tree = self._parse()
        import_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        found = any(
            n.module and "cli" in n.module and any(alias.name == "main" for alias in n.names)
            for n in import_nodes
        )
        assert found, "Expected 'from ... cli import main' in __main__.py"

    def test_has_if_name_main_guard(self):
        tree = self._parse()
        if_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.If)]
        guards = [
            n
            for n in if_nodes
            if (
                isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name)
                and n.test.left.id == "__name__"
            )
        ]
        assert guards, "Expected 'if __name__ == \"__main__\":' guard"

    def test_calls_main_inside_guard(self):
        """main() must be called inside the if __name__ == '__main__' guard."""
        tree = self._parse()
        if_nodes = [n for n in ast.walk(tree) if isinstance(n, ast.If)]
        guards = [
            n
            for n in if_nodes
            if (
                isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name)
                and n.test.left.id == "__name__"
            )
        ]
        assert guards, "No if __name__ guard found"
        guard = guards[0]
        call_nodes = [n for n in ast.walk(guard) if isinstance(n, ast.Call)]
        calls_main = any(isinstance(n.func, ast.Name) and n.func.id == "main" for n in call_nodes)
        assert calls_main, "Expected main() to be called inside the if __name__ guard"

    def test_no_extra_top_level_statements(self):
        """Only docstring, import statement(s), and the if guard at module level."""
        tree = self._parse()
        for node in tree.body:
            assert isinstance(
                node,
                (ast.Expr, ast.ImportFrom, ast.Import, ast.If),
            ), f"Unexpected top-level statement type {type(node).__name__} in __main__.py"
