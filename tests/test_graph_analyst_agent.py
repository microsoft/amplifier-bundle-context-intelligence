"""Tests for agents/context-intelligence-graph-analyst.md.

Validates structure, content, and absence of prohibited terms per spec.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = BUNDLE_ROOT / "agents" / "context-intelligence-graph-analyst.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter (between --- markers) from body content."""
    if not text.startswith("---"):
        raise ValueError("No YAML frontmatter found")
    end = text.index("---", 3)
    yaml_text = text[3:end].strip()
    body = text[end + 3 :].strip()
    return yaml.safe_load(yaml_text), body


# ---------------------------------------------------------------------------
# Fixtures loaded once
# ---------------------------------------------------------------------------


def _agent_text() -> str:
    return AGENT_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests: File Existence
# ---------------------------------------------------------------------------


class TestFileExists:
    def test_agent_file_exists(self) -> None:
        """The agent file must exist."""
        assert AGENT_FILE.exists(), f"Agent file not found: {AGENT_FILE}"

    def test_agent_file_is_not_empty(self) -> None:
        """The agent file must not be empty."""
        assert AGENT_FILE.stat().st_size > 0, "Agent file is empty"


# ---------------------------------------------------------------------------
# Tests: YAML Frontmatter
# ---------------------------------------------------------------------------


class TestFrontmatter:
    def test_frontmatter_parses_without_error(self) -> None:
        """YAML frontmatter must parse without errors."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        assert meta is not None

    def test_meta_name_correct(self) -> None:
        """meta.name must be 'context-intelligence-graph-analyst'."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        assert meta["meta"]["name"] == "context-intelligence-graph-analyst", (
            f"Expected meta.name='context-intelligence-graph-analyst', "
            f"got: {meta.get('meta', {}).get('name')}"
        )

    def test_meta_description_present(self) -> None:
        """meta.description must be present and non-empty."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert description and len(description.strip()) > 0, (
            "meta.description must be present and non-empty"
        )

    def test_meta_description_mentions_graph(self) -> None:
        """meta.description must mention graph-powered analysis."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert "graph" in description.lower(), (
            "meta.description must mention graph-powered analysis"
        )

    def test_meta_description_mentions_cypher(self) -> None:
        """meta.description must mention Cypher queries."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert "cypher" in description.lower(), (
            "meta.description must mention Cypher queries"
        )

    def test_meta_description_mentions_blob(self) -> None:
        """meta.description must mention blob resolution."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert "blob" in description.lower(), (
            "meta.description must mention blob resolution"
        )

    def test_meta_description_mentions_navigator_delegation(self) -> None:
        """meta.description must mention delegation to navigator when server unreachable."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert "navigator" in description.lower(), (
            "meta.description must mention delegation to navigator"
        )

    def test_meta_description_has_two_examples(self) -> None:
        """meta.description must contain 2 examples."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        example_count = description.count("<example>")
        assert example_count >= 2, (
            f"meta.description must contain at least 2 examples, found {example_count}"
        )

    def test_model_role_contains_reasoning(self) -> None:
        """model_role must contain 'reasoning'."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        model_role = meta.get("model_role", [])
        if isinstance(model_role, str):
            model_role = [model_role]
        assert "reasoning" in model_role, (
            f"model_role must contain 'reasoning', got: {model_role}"
        )

    def test_model_role_contains_general(self) -> None:
        """model_role must contain 'general'."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        model_role = meta.get("model_role", [])
        if isinstance(model_role, str):
            model_role = [model_role]
        assert "general" in model_role, (
            f"model_role must contain 'general', got: {model_role}"
        )


# ---------------------------------------------------------------------------
# Tests: Tools in Frontmatter
# ---------------------------------------------------------------------------


class TestToolDeclarations:
    def _get_tools(self) -> list[dict]:
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        return meta.get("tools", [])

    def _get_tool_modules(self) -> list[str]:
        return [t.get("module", "") for t in self._get_tools()]

    def test_tool_graph_query_declared(self) -> None:
        """tool-graph-query must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-graph-query" in modules, (
            f"tool-graph-query not found in tools: {modules}"
        )

    def test_tool_graph_query_source(self) -> None:
        """tool-graph-query must reference the colombod context-intelligence bundle."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-graph-query"), None)
        assert tool is not None, "tool-graph-query not found"
        source = tool.get("source", "")
        assert "colombod/amplifier-bundle-context-intelligence" in source, (
            f"tool-graph-query source must reference colombod bundle, got: {source}"
        )
        assert "tool-graph-query" in source, (
            f"tool-graph-query source must include subdirectory=modules/tool-graph-query, got: {source}"
        )

    def test_tool_blob_read_declared(self) -> None:
        """tool-blob-read must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-blob-read" in modules, (
            f"tool-blob-read not found in tools: {modules}"
        )

    def test_tool_blob_read_source(self) -> None:
        """tool-blob-read must reference the colombod context-intelligence bundle."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-blob-read"), None)
        assert tool is not None, "tool-blob-read not found"
        source = tool.get("source", "")
        assert "colombod/amplifier-bundle-context-intelligence" in source, (
            f"tool-blob-read source must reference colombod bundle, got: {source}"
        )
        assert "tool-blob-read" in source, (
            f"tool-blob-read source must include subdirectory=modules/tool-blob-read, got: {source}"
        )

    def test_tool_filesystem_declared(self) -> None:
        """tool-filesystem must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-filesystem" in modules, (
            f"tool-filesystem not found in tools: {modules}"
        )

    def test_tool_filesystem_allowed_write_paths(self) -> None:
        """tool-filesystem must have allowed_write_paths configured."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-filesystem"), None)
        assert tool is not None, "tool-filesystem not found"
        config = tool.get("config", {})
        paths = config.get("allowed_write_paths", [])
        assert "." in paths, f"allowed_write_paths must contain '.', got: {paths}"
        assert "~/.amplifier/projects" in paths, (
            f"allowed_write_paths must contain '~/.amplifier/projects', got: {paths}"
        )

    def test_tool_bash_declared(self) -> None:
        """tool-bash must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-bash" in modules, f"tool-bash not found in tools: {modules}"

    def test_tool_skills_declared(self) -> None:
        """tool-skills must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-skills" in modules, f"tool-skills not found in tools: {modules}"

    def test_tool_skills_config(self) -> None:
        """tool-skills must have skills config pointing to context-intelligence:skills/."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-skills"), None)
        assert tool is not None, "tool-skills not found"
        config = tool.get("config", {})
        skills = config.get("skills", [])
        assert "context-intelligence:skills/" in skills, (
            f"tool-skills skills must contain 'context-intelligence:skills/', got: {skills}"
        )


# ---------------------------------------------------------------------------
# Tests: Prohibited Terms
# ---------------------------------------------------------------------------


class TestProhibitedTerms:
    def test_no_neo4j(self) -> None:
        """'neo4j' (case-insensitive) must not appear in the agent file."""
        text = _agent_text()
        lower = text.lower()
        count = lower.count("neo4j")
        assert count == 0, f"Found {count} occurrence(s) of 'neo4j' in agent file"

    def test_no_graph_forest_name(self) -> None:
        """'graph_forest_name' must not appear in the agent file."""
        text = _agent_text()
        count = text.count("graph_forest_name")
        assert count == 0, (
            f"Found {count} occurrence(s) of 'graph_forest_name' in agent file"
        )


# ---------------------------------------------------------------------------
# Tests: Body Content Structure
# ---------------------------------------------------------------------------


class TestBodyContent:
    def _body(self) -> str:
        text = _agent_text()
        _, body = _parse_frontmatter(text)
        return body

    def test_identity_notice_present(self) -> None:
        """Body must contain an IDENTITY NOTICE section."""
        body = self._body()
        assert "IDENTITY NOTICE" in body, "Body must contain an IDENTITY NOTICE section"

    def test_identity_notice_anti_self_delegation(self) -> None:
        """IDENTITY NOTICE must warn against self-delegation."""
        body = self._body()
        # Check that the identity notice mentions the anti-self-delegation warning
        assert "IDENTITY NOTICE" in body
        # Find the identity notice section and verify it contains self-delegation warning
        idx = body.index("IDENTITY NOTICE")
        vicinity = body[idx : idx + 500]
        assert (
            "yourself" in vicinity.lower()
            or "self" in vicinity.lower()
            or "loop" in vicinity.lower()
        ), "IDENTITY NOTICE must warn against self-delegation/infinite loop"

    def test_server_availability_check_present(self) -> None:
        """Body must contain a CRITICAL Server Availability Check section."""
        body = self._body()
        assert "Server Availability" in body or "server availability" in body.lower(), (
            "Body must contain a Server Availability Check section"
        )

    def test_server_availability_check_is_critical(self) -> None:
        """Server availability section must be marked as CRITICAL."""
        body = self._body()
        assert "CRITICAL" in body, (
            "Server availability section must be marked as CRITICAL"
        )

    def test_server_availability_health_check(self) -> None:
        """Server availability check must include a health check Cypher query."""
        body = self._body()
        # Should contain some kind of Cypher health check query
        assert "MATCH" in body or "cypher" in body.lower(), (
            "Server availability check must include a Cypher health check query"
        )

    def test_server_availability_fallback_to_navigator(self) -> None:
        """Server availability check must describe fallback to navigator."""
        body = self._body()
        assert "navigator" in body.lower(), (
            "Server availability section must describe fallback to navigator"
        )

    def test_blob_safety_section_present(self) -> None:
        """Body must contain a CRITICAL Large Blob Safety section."""
        body = self._body()
        assert "Blob" in body or "blob" in body, (
            "Body must contain a Blob Safety section"
        )

    def test_blob_safety_is_critical(self) -> None:
        """Blob safety section must be marked as CRITICAL."""
        body = self._body()
        lower = body.lower()
        # Must have CRITICAL near a blob mention
        assert "CRITICAL" in body and ("blob" in lower), (
            "Blob safety section must be marked as CRITICAL"
        )

    def test_blob_safety_never_cat(self) -> None:
        """Blob safety section must warn against cat/read_file for large blobs."""
        body = self._body()
        lower = body.lower()
        assert "never" in lower and ("cat" in lower or "read_file" in lower), (
            "Blob safety must warn: NEVER cat/read_file large blobs"
        )

    def test_blob_safety_check_size_first(self) -> None:
        """Blob safety section must say to check size first."""
        body = self._body()
        lower = body.lower()
        assert "size" in lower, "Blob safety section must mention checking size first"

    def test_blob_safety_use_jq(self) -> None:
        """Blob safety section must recommend using jq."""
        body = self._body()
        assert "jq" in body, "Blob safety section must recommend using jq"

    def test_section_1_graph_powered_analysis(self) -> None:
        """Body must contain Section 1 about Graph-Powered Analysis."""
        body = self._body()
        assert "Section 1" in body, "Body must contain Section 1"
        # Section 1 should be about graph-powered analysis
        idx = body.index("Section 1")
        vicinity = body[idx : idx + 200]
        assert "graph" in vicinity.lower() or "Graph" in vicinity, (
            "Section 1 must be about graph-powered analysis"
        )

    def test_section_2_blob_resolution(self) -> None:
        """Body must contain Section 2 about Blob Resolution Workflow."""
        body = self._body()
        assert "Section 2" in body, "Body must contain Section 2"
        idx = body.index("Section 2")
        vicinity = body[idx : idx + 200]
        assert "blob" in vicinity.lower() or "Blob" in vicinity, (
            "Section 2 must be about Blob Resolution"
        )

    def test_section_3_delegation_fallback(self) -> None:
        """Body must contain Section 3 about Delegation Fallback."""
        body = self._body()
        assert "Section 3" in body, "Body must contain Section 3"
        idx = body.index("Section 3")
        vicinity = body[idx : idx + 200]
        assert "delegation" in vicinity.lower() or "fallback" in vicinity.lower(), (
            "Section 3 must be about Delegation Fallback"
        )

    def test_section_4_context_file_references(self) -> None:
        """Body must contain Section 4 about Context File References."""
        body = self._body()
        assert "Section 4" in body, "Body must contain Section 4"

    def test_four_numbered_sections_total(self) -> None:
        """Body must contain exactly 4 numbered sections (Section 1 through Section 4)."""
        body = self._body()
        for n in range(1, 5):
            assert f"Section {n}" in body, f"Body must contain Section {n}"

    def test_context_reference_graph_model(self) -> None:
        """Body must reference @context-intelligence:context/graph-model-reference.md."""
        body = self._body()
        assert "@context-intelligence:context/graph-model-reference.md" in body, (
            "Body must reference @context-intelligence:context/graph-model-reference.md"
        )

    def test_context_reference_delegation_strategy(self) -> None:
        """Body must reference @context-intelligence:context/delegation-strategy.dot."""
        body = self._body()
        assert "@context-intelligence:context/delegation-strategy.dot" in body, (
            "Body must reference @context-intelligence:context/delegation-strategy.dot"
        )

    def test_context_reference_config_resolution(self) -> None:
        """Section 4 must reference config-resolution.dot."""
        body = self._body()
        assert "config-resolution.dot" in body, (
            "graph-analyst Section 4 must reference context/config-resolution.dot"
        )

    def test_three_context_file_references_total(self) -> None:
        """Body must contain all three required context file references."""
        body = self._body()
        refs = [
            "@context-intelligence:context/graph-model-reference.md",
            "@context-intelligence:context/config-resolution.dot",
            "@context-intelligence:context/delegation-strategy.dot",
        ]
        for ref in refs:
            assert ref in body, f"Body must contain context reference: {ref}"

    def test_section_1_loads_graph_query_skill(self) -> None:
        """Section 1 must mention loading the context-intelligence-graph-query skill."""
        body = self._body()
        assert "context-intelligence-graph-query" in body, (
            "Section 1 must mention loading the context-intelligence-graph-query skill"
        )

    def test_section_2_five_step_workflow(self) -> None:
        """Section 2 blob resolution must describe a multi-step workflow."""
        body = self._body()
        # The section should have multiple steps (at least mention steps/numbered items)
        idx = body.index("Section 2")
        next_section = body.find("Section 3", idx)
        section2_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        # Should have at least 3 steps described
        assert (
            "1." in section2_content
            or "Step 1" in section2_content
            or "1)" in section2_content
        ), "Section 2 must describe a multi-step blob resolution workflow"

    def test_section_3_never_retry_server(self) -> None:
        """Section 3 must warn to never retry the server repeatedly."""
        body = self._body()
        idx = body.index("Section 3")
        next_section = body.find("Section 4", idx)
        section3_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        assert (
            "retry" in section3_content.lower()
            or "repeatedly" in section3_content.lower()
        ), "Section 3 must warn against retrying the server repeatedly"

    def test_section_3_never_read_local_jsonl(self) -> None:
        """Section 3 must warn to never read local JSONL files directly."""
        body = self._body()
        idx = body.index("Section 3")
        next_section = body.find("Section 4", idx)
        section3_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        assert "jsonl" in section3_content.lower() or "JSONL" in section3_content, (
            "Section 3 must warn against reading local JSONL files"
        )

    def test_section_3_never_delegate_to_yourself(self) -> None:
        """Section 3 must warn to never delegate to yourself."""
        body = self._body()
        idx = body.index("Section 3")
        next_section = body.find("Section 4", idx)
        section3_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        assert (
            "yourself" in section3_content.lower() or "self" in section3_content.lower()
        ), "Section 3 must warn against self-delegation"
