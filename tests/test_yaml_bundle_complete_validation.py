"""Task-15: Validate bundle YAML parses correctly.

This verification test mirrors the spec's Python snippet:
  import yaml; from pathlib import Path;
  data = yaml.safe_load(Path('behaviors/context-intelligence.yaml').read_text());
  [print(f'  - {t["module"]}') for t in data.get('tools', [])];
  [print(f'  - {h["module"]}') for h in data.get('hooks', [])];
  print('YAML validates OK')

Expected output must list:
  - tool-graph-query
  - tool-blob-read
  - tool-context-intelligence-upload
  - hook-context-intelligence
  YAML validates OK
"""

from pathlib import Path

import pytest
import yaml

BUNDLE_DIR = Path(__file__).parent.parent
BEHAVIOR_YAML = BUNDLE_DIR / "behaviors" / "context-intelligence.yaml"

EXPECTED_TOOLS = [
    "tool-graph-query",
    "tool-blob-read",
    "tool-context-intelligence-upload",
]

EXPECTED_HOOKS = [
    "hook-context-intelligence",
]


@pytest.fixture(scope="session")
def behavior_data() -> dict:
    """Load and parse the behavior YAML (mirrors the spec's snippet)."""
    return yaml.safe_load(BEHAVIOR_YAML.read_text())


class TestYamlBundleCompleteValidation:
    """Verify behaviors/context-intelligence.yaml parses correctly and has expected modules.

    This test class mirrors the exact verification performed by the spec's snippet.
    """

    def test_yaml_parses_without_errors(self, behavior_data: dict) -> None:
        """YAML file must parse without any errors."""
        assert behavior_data is not None
        assert isinstance(behavior_data, dict)

    def test_tools_section_contains_exactly_three_tools(
        self, behavior_data: dict
    ) -> None:
        """Tools section must contain exactly: tool-graph-query, tool-blob-read, tool-context-intelligence-upload."""
        tools = behavior_data.get("tools", [])
        modules = [t["module"] for t in tools if isinstance(t, dict)]
        assert modules == EXPECTED_TOOLS, (
            f"tools section must contain exactly {EXPECTED_TOOLS} in order, got {modules}"
        )

    def test_hooks_section_contains_hook_context_intelligence(
        self, behavior_data: dict
    ) -> None:
        """Hooks section must contain: hook-context-intelligence."""
        hooks = behavior_data.get("hooks", [])
        modules = [h["module"] for h in hooks if isinstance(h, dict)]
        assert EXPECTED_HOOKS[0] in modules, (
            f"hooks section must contain '{EXPECTED_HOOKS[0]}', got {modules}"
        )

    def test_spec_snippet_output_format(self, behavior_data: dict) -> None:
        """Verify the exact output format produced by the spec's Python snippet.

        The snippet prints all tool modules and hook modules, then 'YAML validates OK'.
        Expected lines (in order):
          - tool-graph-query
          - tool-blob-read
          - tool-context-intelligence-upload
          - hook-context-intelligence
        """
        # Collect output as the spec snippet would
        output_lines = []
        for t in behavior_data.get("tools", []):
            output_lines.append(f"  - {t['module']}")
        for h in behavior_data.get("hooks", []):
            output_lines.append(f"  - {h['module']}")

        expected_lines = [
            "  - tool-graph-query",
            "  - tool-blob-read",
            "  - tool-context-intelligence-upload",
            "  - hook-context-intelligence",
        ]

        assert output_lines == expected_lines, (
            f"Spec snippet output format mismatch.\n"
            f"Expected:\n{chr(10).join(expected_lines)}\n"
            f"Got:\n{chr(10).join(output_lines)}"
        )
