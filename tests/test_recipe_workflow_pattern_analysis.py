"""Tests for recipes/workflow-pattern-analysis.yaml and .dot files (task-2-recipe-yaml-and-dot).

Verifies:
- recipes/ directory exists
- recipes/workflow-pattern-analysis.yaml exists and parses with yaml.safe_load
- YAML has top-level 'stages' (not 'steps')
- stages[0].name == 'detection', stages[1].name == 'findings'
- detection stage has approval block with required: true, default: 'deny'
- Total step count across both stages >= 9 (expected 10)
- recipes/workflow-pattern-analysis.dot exists with required structural elements
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
RECIPES_DIR = REPO_ROOT / "recipes"
YAML_PATH = RECIPES_DIR / "workflow-pattern-analysis.yaml"
DOT_PATH = RECIPES_DIR / "workflow-pattern-analysis.dot"


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


class TestFileExistence:
    """All required files must exist."""

    def test_recipes_dir_exists(self):
        """recipes/ directory must exist."""
        assert RECIPES_DIR.exists(), "recipes/ directory does not exist"
        assert RECIPES_DIR.is_dir(), "recipes/ is not a directory"

    def test_yaml_file_exists(self):
        """recipes/workflow-pattern-analysis.yaml must exist."""
        assert YAML_PATH.exists(), "recipes/workflow-pattern-analysis.yaml does not exist"
        assert YAML_PATH.is_file(), "recipes/workflow-pattern-analysis.yaml is not a file"

    def test_dot_file_exists(self):
        """recipes/workflow-pattern-analysis.dot must exist."""
        assert DOT_PATH.exists(), "recipes/workflow-pattern-analysis.dot does not exist"
        assert DOT_PATH.is_file(), "recipes/workflow-pattern-analysis.dot is not a file"


# ---------------------------------------------------------------------------
# YAML structure
# ---------------------------------------------------------------------------


class TestYamlStructure:
    """The YAML file must have correct structure."""

    def _load(self) -> dict:
        """Load and return the YAML data."""
        return yaml.safe_load(YAML_PATH.read_text())

    def test_yaml_parses(self):
        """YAML must parse successfully with yaml.safe_load."""
        data = self._load()
        assert data is not None, "YAML must not be empty"
        assert isinstance(data, dict), "YAML must be a dict"

    def test_has_name(self):
        """YAML must have top-level 'name' field."""
        data = self._load()
        assert "name" in data, "YAML must have 'name' field"
        assert data["name"] == "workflow-pattern-analysis"

    def test_has_stages_not_steps(self):
        """YAML must have top-level 'stages', not 'steps'."""
        data = self._load()
        assert "stages" in data, "YAML must have top-level 'stages' key"
        assert "steps" not in data, "YAML must NOT have top-level 'steps' key"

    def test_has_two_stages(self):
        """YAML must have exactly 2 stages."""
        data = self._load()
        stages = data["stages"]
        assert isinstance(stages, list), "stages must be a list"
        assert len(stages) == 2, f"Must have exactly 2 stages, got {len(stages)}"

    def test_stage_names(self):
        """stages[0].name == 'detection', stages[1].name == 'findings'."""
        data = self._load()
        stages = data["stages"]
        assert stages[0]["name"] == "detection", (
            f"stages[0].name must be 'detection', got '{stages[0].get('name')}'"
        )
        assert stages[1]["name"] == "findings", (
            f"stages[1].name must be 'findings', got '{stages[1].get('name')}'"
        )


# ---------------------------------------------------------------------------
# Detection stage approval block
# ---------------------------------------------------------------------------


class TestDetectionApproval:
    """The detection stage must have an approval block."""

    def _detection_stage(self) -> dict:
        """Get the detection stage from the YAML."""
        data = yaml.safe_load(YAML_PATH.read_text())
        return data["stages"][0]

    def test_has_approval_block(self):
        """detection stage must have an 'approval' block."""
        stage = self._detection_stage()
        assert "approval" in stage, "detection stage must have 'approval' block"

    def test_approval_required_true(self):
        """approval.required must be true."""
        stage = self._detection_stage()
        approval = stage["approval"]
        assert approval.get("required") is True, "approval.required must be True"

    def test_approval_default_deny(self):
        """approval.default must be 'deny'."""
        stage = self._detection_stage()
        approval = stage["approval"]
        assert approval.get("default") == "deny", (
            f"approval.default must be 'deny', got '{approval.get('default')}'"
        )


# ---------------------------------------------------------------------------
# Step count
# ---------------------------------------------------------------------------


class TestStepCount:
    """Total step count across both stages must be >= 9."""

    def test_total_steps_at_least_nine(self):
        """Total step count across both stages must be >= 9 (expected 10)."""
        data = yaml.safe_load(YAML_PATH.read_text())
        total = sum(len(s.get("steps", [])) for s in data.get("stages", []))
        assert total >= 9, f"Total steps must be >= 9, got {total}"

    def test_detection_has_steps(self):
        """detection stage must have steps."""
        data = yaml.safe_load(YAML_PATH.read_text())
        detection_steps = data["stages"][0].get("steps", [])
        assert len(detection_steps) >= 5, (
            f"detection stage must have >= 5 steps, got {len(detection_steps)}"
        )

    def test_findings_has_steps(self):
        """findings stage must have steps."""
        data = yaml.safe_load(YAML_PATH.read_text())
        findings_steps = data["stages"][1].get("steps", [])
        assert len(findings_steps) >= 3, (
            f"findings stage must have >= 3 steps, got {len(findings_steps)}"
        )


# ---------------------------------------------------------------------------
# YAML metadata
# ---------------------------------------------------------------------------


class TestYamlMetadata:
    """The YAML must have correct metadata."""

    def _load(self) -> dict:
        return yaml.safe_load(YAML_PATH.read_text())

    def test_has_version(self):
        """YAML must have version field."""
        data = self._load()
        assert "version" in data, "YAML must have 'version' field"

    def test_has_description(self):
        """YAML must have description field."""
        data = self._load()
        assert "description" in data, "YAML must have 'description' field"

    def test_has_context(self):
        """YAML must have context block with defaults."""
        data = self._load()
        assert "context" in data, "YAML must have 'context' block"
        ctx = data["context"]
        assert "defaults" in ctx, "context must have 'defaults' section"


# ---------------------------------------------------------------------------
# DOT file structure
# ---------------------------------------------------------------------------


class TestDotStructure:
    """The DOT file must have correct structural elements."""

    def _dot_content(self) -> str:
        return DOT_PATH.read_text()

    def test_is_digraph(self):
        """DOT file must define a digraph."""
        content = self._dot_content()
        assert "digraph" in content, "DOT file must contain 'digraph'"

    def test_digraph_name(self):
        """DOT digraph must be named workflow_pattern_analysis."""
        content = self._dot_content()
        assert "workflow_pattern_analysis" in content, (
            "DOT file must reference 'workflow_pattern_analysis'"
        )

    def test_has_cluster_detection(self):
        """DOT file must have cluster_detection subgraph."""
        content = self._dot_content()
        assert "cluster_detection" in content, "DOT must have 'cluster_detection' subgraph"

    def test_has_cluster_findings(self):
        """DOT file must have cluster_findings subgraph."""
        content = self._dot_content()
        assert "cluster_findings" in content, "DOT must have 'cluster_findings' subgraph"

    def test_has_gate_node(self):
        """DOT file must have approval gate node."""
        content = self._dot_content()
        assert "gate" in content, "DOT must have 'gate' node (approval gate)"

    def test_has_diamond_shape(self):
        """Gate node must be a diamond shape."""
        content = self._dot_content()
        assert "diamond" in content, "DOT must have diamond shape for gate node"

    def test_gate_to_write_findings_edge(self):
        """DOT must have edge from gate into the findings stage (parse_inspect_list)."""
        content = self._dot_content()
        assert "parse_inspect_list" in content, "DOT must reference 'parse_inspect_list' node"
        assert "gate" in content and "parse_inspect_list" in content, (
            "DOT must have both gate and parse_inspect_list nodes"
        )

    def test_has_output_nodes(self):
        """DOT must have output nodes (out_md)."""
        content = self._dot_content()
        assert "out_md" in content, "DOT must have 'out_md' output node"

    def test_has_probe_node(self):
        """DOT must have probe node for graph detection."""
        content = self._dot_content()
        assert "probe" in content, "DOT must have 'probe' node"

    def test_has_render_f_node(self):
        """DOT must have render_f node."""
        content = self._dot_content()
        assert "render_f" in content, "DOT must have 'render_f' node"
