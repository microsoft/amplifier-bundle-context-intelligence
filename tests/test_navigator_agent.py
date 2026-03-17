"""Tests for agents/context-intelligence-navigator.md.

Validates structure, content, and absence of prohibited server tools per spec.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BUNDLE_ROOT = Path(__file__).resolve().parent.parent
AGENT_FILE = BUNDLE_ROOT / "agents" / "context-intelligence-navigator.md"


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
        """meta.name must be 'context-intelligence-navigator'."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        assert meta["meta"]["name"] == "context-intelligence-navigator", (
            f"Expected meta.name='context-intelligence-navigator', "
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

    def test_meta_description_mentions_local_fallback(self) -> None:
        """meta.description must mention it is the local fallback agent."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert "fallback" in description.lower() or "local" in description.lower(), (
            "meta.description must mention local fallback navigation"
        )

    def test_meta_description_mentions_jsonl(self) -> None:
        """meta.description must mention JSONL files."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert "jsonl" in description.lower() or "JSONL" in description, (
            "meta.description must mention JSONL file navigation"
        )

    def test_meta_description_mentions_not_called_directly(self) -> None:
        """meta.description must note it is NOT called directly by external callers."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        # Should mention it's only delegated to by graph-analyst
        lower = description.lower()
        assert "not" in lower or "only" in lower, (
            "meta.description must note it is not called directly (only via graph-analyst)"
        )

    def test_meta_description_mentions_graph_analyst_delegation(self) -> None:
        """meta.description must mention delegation from graph-analyst."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        assert (
            "graph-analyst" in description.lower()
            or "graph analyst" in description.lower()
        ), "meta.description must mention delegation from graph-analyst"

    def test_meta_description_has_one_example(self) -> None:
        """meta.description must contain at least 1 example."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        description = meta["meta"].get("description", "")
        example_count = description.count("<example>")
        assert example_count >= 1, (
            f"meta.description must contain at least 1 example, found {example_count}"
        )

    def test_model_role_is_general(self) -> None:
        """model_role must be 'general'."""
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        model_role = meta.get("model_role", "")
        if isinstance(model_role, list):
            assert "general" in model_role, (
                f"model_role must contain 'general', got: {model_role}"
            )
        else:
            assert model_role == "general", (
                f"model_role must be 'general', got: {model_role}"
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

    def test_tool_filesystem_declared(self) -> None:
        """tool-filesystem must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-filesystem" in modules, (
            f"tool-filesystem not found in tools: {modules}"
        )

    def test_tool_filesystem_source(self) -> None:
        """tool-filesystem must reference the microsoft module."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-filesystem"), None)
        assert tool is not None, "tool-filesystem not found"
        source = tool.get("source", "")
        assert "microsoft/amplifier-module-tool-filesystem" in source, (
            f"tool-filesystem source must reference microsoft module, got: {source}"
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

    def test_tool_search_declared(self) -> None:
        """tool-search must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-search" in modules, f"tool-search not found in tools: {modules}"

    def test_tool_search_source(self) -> None:
        """tool-search must reference the microsoft module."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-search"), None)
        assert tool is not None, "tool-search not found"
        source = tool.get("source", "")
        assert "microsoft/amplifier-module-tool-search" in source, (
            f"tool-search source must reference microsoft module, got: {source}"
        )

    def test_tool_bash_declared(self) -> None:
        """tool-bash must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-bash" in modules, f"tool-bash not found in tools: {modules}"

    def test_tool_bash_source(self) -> None:
        """tool-bash must reference the microsoft module."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-bash"), None)
        assert tool is not None, "tool-bash not found"
        source = tool.get("source", "")
        assert "microsoft/amplifier-module-tool-bash" in source, (
            f"tool-bash source must reference microsoft module, got: {source}"
        )

    def test_tool_skills_declared(self) -> None:
        """tool-skills must be declared in tools."""
        modules = self._get_tool_modules()
        assert "tool-skills" in modules, f"tool-skills not found in tools: {modules}"

    def test_tool_skills_source(self) -> None:
        """tool-skills must reference the microsoft module."""
        tools = self._get_tools()
        tool = next((t for t in tools if t.get("module") == "tool-skills"), None)
        assert tool is not None, "tool-skills not found"
        source = tool.get("source", "")
        assert "microsoft/amplifier-module-tool-skills" in source, (
            f"tool-skills source must reference microsoft module, got: {source}"
        )

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
# Tests: Prohibited Server Tools
# ---------------------------------------------------------------------------


class TestNoServerTools:
    """Navigator MUST NOT have tool-graph-query or tool-blob-read (server tools)."""

    def _get_tool_modules(self) -> list[str]:
        text = _agent_text()
        meta, _ = _parse_frontmatter(text)
        tools = meta.get("tools", [])
        return [t.get("module", "") for t in tools]

    def test_no_tool_graph_query(self) -> None:
        """tool-graph-query must NOT be declared in navigator tools."""
        modules = self._get_tool_modules()
        assert "tool-graph-query" not in modules, (
            "Navigator must NOT have tool-graph-query (server tool). "
            "Navigator uses local JSONL only."
        )

    def test_no_tool_blob_read(self) -> None:
        """tool-blob-read must NOT be declared in navigator tools."""
        modules = self._get_tool_modules()
        assert "tool-blob-read" not in modules, (
            "Navigator must NOT have tool-blob-read (server tool). "
            "Navigator uses local JSONL only."
        )

    def test_no_server_tool_text_in_file(self) -> None:
        """'module: tool-graph-query' and 'module: tool-blob-read' must not appear in the file."""
        text = _agent_text()
        assert "module: tool-graph-query" not in text, (
            "Found 'module: tool-graph-query' in navigator file — navigator must not have server tools"
        )
        assert "module: tool-blob-read" not in text, (
            "Found 'module: tool-blob-read' in navigator file — navigator must not have server tools"
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
        assert "IDENTITY NOTICE" in body
        idx = body.index("IDENTITY NOTICE")
        vicinity = body[idx : idx + 500]
        assert (
            "yourself" in vicinity.lower()
            or "self" in vicinity.lower()
            or "loop" in vicinity.lower()
        ), "IDENTITY NOTICE must warn against self-delegation/infinite loop"

    def test_events_jsonl_critical_warning_present(self) -> None:
        """Body must contain a CRITICAL warning about events.jsonl."""
        body = self._body()
        assert "events.jsonl" in body, "Body must contain warning about events.jsonl"
        assert "CRITICAL" in body, "Body must mark the events.jsonl warning as CRITICAL"

    def test_events_jsonl_crash_warning(self) -> None:
        """events.jsonl warning must mention 100k+ token lines crashing session."""
        body = self._body()
        lower = body.lower()
        assert "100k" in lower or "100,000" in lower or "100k+" in lower, (
            "events.jsonl warning must mention 100k+ token lines"
        )
        assert "crash" in lower or "kill" in lower or "fatal" in lower, (
            "events.jsonl warning must mention session crash/kill risk"
        )

    def test_events_jsonl_never_grep_cat(self) -> None:
        """events.jsonl warning must say NEVER grep/cat events.jsonl directly."""
        body = self._body()
        lower = body.lower()
        assert "never" in lower, "events.jsonl warning must include NEVER directive"
        assert "grep" in lower or "cat" in lower, (
            "events.jsonl warning must mention grep/cat as forbidden"
        )

    def test_events_jsonl_safe_patterns_mentioned(self) -> None:
        """events.jsonl warning must mention safe extraction patterns (jq, sed, line-number-only grep)."""
        body = self._body()
        assert "jq" in body, "events.jsonl warning must mention jq streaming extraction"
        assert "sed" in body or "line" in body.lower(), (
            "events.jsonl warning must mention sed surgical extraction or line-number-only patterns"
        )

    def test_section_1_present(self) -> None:
        """Body must contain Section 1."""
        body = self._body()
        assert "Section 1" in body, "Body must contain Section 1"

    def test_section_1_identity_and_navigation(self) -> None:
        """Section 1 must be about Identity and Navigation Approach."""
        body = self._body()
        assert "Section 1" in body
        idx = body.index("Section 1")
        vicinity = body[idx : idx + 300]
        assert (
            "identity" in vicinity.lower()
            or "navigation" in vicinity.lower()
            or "approach" in vicinity.lower()
        ), "Section 1 must be about Identity and Navigation Approach"

    def test_section_1_self_delegation_guard(self) -> None:
        """Section 1 must include self-delegation guard."""
        body = self._body()
        idx = body.index("Section 1")
        next_section = body.find("Section 2", idx)
        section1_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        assert (
            "guard" in section1_content.lower()
            or "self" in section1_content.lower()
            or "delegate to" in section1_content.lower()
        ), "Section 1 must include self-delegation guard"

    def test_section_1_no_server_tools_statement(self) -> None:
        """Section 1 must state no server tools are available."""
        body = self._body()
        idx = body.index("Section 1")
        next_section = body.find("Section 2", idx)
        section1_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        lower = section1_content.lower()
        assert (
            "no server" in lower
            or "no graph" in lower
            or "no tool-graph" in lower
            or "without server" in lower
            or "server tools" in lower
            or "graph_query" in lower
        ), "Section 1 must state no server tools are available"

    def test_section_1_storage_path(self) -> None:
        """Section 1 must mention the storage path convention."""
        body = self._body()
        idx = body.index("Section 1")
        next_section = body.find("Section 2", idx)
        section1_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        assert "~/.amplifier/projects" in section1_content, (
            "Section 1 must mention the storage path ~/.amplifier/projects/{slug}/sessions/{id}/context-intelligence/"
        )
        assert "context-intelligence" in section1_content, (
            "Section 1 must mention context-intelligence in the storage path"
        )

    def test_section_2_present(self) -> None:
        """Body must contain Section 2."""
        body = self._body()
        assert "Section 2" in body, "Body must contain Section 2"

    def test_section_2_primary_capabilities(self) -> None:
        """Section 2 must be about Primary Capabilities."""
        body = self._body()
        idx = body.index("Section 2")
        vicinity = body[idx : idx + 300]
        assert "capabilities" in vicinity.lower() or "primary" in vicinity.lower(), (
            "Section 2 must be about Primary Capabilities"
        )

    def test_section_2_session_discovery(self) -> None:
        """Section 2 must cover Session Discovery."""
        body = self._body()
        idx = body.index("Section 2")
        next_section = body.find("Section 3", idx)
        section2_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 3000]
        )
        assert (
            "session discovery" in section2_content.lower()
            or "Session Discovery" in section2_content
        ), "Section 2 must cover Session Discovery"

    def test_section_2_event_search(self) -> None:
        """Section 2 must cover Event Search."""
        body = self._body()
        idx = body.index("Section 2")
        next_section = body.find("Section 3", idx)
        section2_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 3000]
        )
        assert (
            "event search" in section2_content.lower()
            or "Event Search" in section2_content
        ), "Section 2 must cover Event Search"

    def test_section_2_session_navigation(self) -> None:
        """Section 2 must cover Session Navigation."""
        body = self._body()
        idx = body.index("Section 2")
        next_section = body.find("Section 3", idx)
        section2_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 3000]
        )
        assert (
            "session navigation" in section2_content.lower()
            or "Session Navigation" in section2_content
        ), "Section 2 must cover Session Navigation"

    def test_section_2_bash_jq_patterns(self) -> None:
        """Section 2 must include bash/jq/grep code patterns."""
        body = self._body()
        idx = body.index("Section 2")
        next_section = body.find("Section 3", idx)
        section2_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 3000]
        )
        assert "jq" in section2_content, "Section 2 must include jq patterns"
        assert "find" in section2_content.lower() or "for " in section2_content, (
            "Section 2 must include find/for-loop patterns"
        )

    def test_section_3_present(self) -> None:
        """Body must contain Section 3."""
        body = self._body()
        assert "Section 3" in body, "Body must contain Section 3"

    def test_section_3_delegation_fallback(self) -> None:
        """Section 3 must be about Delegation Fallback."""
        body = self._body()
        idx = body.index("Section 3")
        vicinity = body[idx : idx + 300]
        assert "delegation" in vicinity.lower() or "fallback" in vicinity.lower(), (
            "Section 3 must be about Delegation Fallback"
        )

    def test_section_3_delegate_to_session_analyst(self) -> None:
        """Section 3 must mention delegating to foundation:session-analyst as safety net."""
        body = self._body()
        idx = body.index("Section 3")
        next_section = body.find("Section 4", idx)
        section3_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        assert (
            "foundation:session-analyst" in section3_content
            or "session-analyst" in section3_content
        ), "Section 3 must mention delegating to foundation:session-analyst"

    def test_section_3_never_delegate_to_graph_analyst(self) -> None:
        """Section 3 must warn never to delegate to graph-analyst."""
        body = self._body()
        idx = body.index("Section 3")
        next_section = body.find("Section 4", idx)
        section3_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        lower = section3_content.lower()
        assert "graph-analyst" in lower or "graph analyst" in lower, (
            "Section 3 must mention graph-analyst in context of prohibition"
        )
        assert "never" in lower or "not" in lower or "do not" in lower, (
            "Section 3 must warn never to delegate to graph-analyst"
        )

    def test_section_3_never_self_delegate(self) -> None:
        """Section 3 must warn never to delegate to self (context-intelligence-navigator)."""
        body = self._body()
        idx = body.index("Section 3")
        next_section = body.find("Section 4", idx)
        section3_content = (
            body[idx:next_section] if next_section != -1 else body[idx : idx + 2000]
        )
        lower = section3_content.lower()
        assert "yourself" in lower or "self" in lower or "navigator" in lower, (
            "Section 3 must warn against self-delegation"
        )

    def test_section_4_present(self) -> None:
        """Body must contain Section 4."""
        body = self._body()
        assert "Section 4" in body, "Body must contain Section 4"

    def test_section_4_context_file_references(self) -> None:
        """Section 4 must be about Context File References."""
        body = self._body()
        idx = body.index("Section 4")
        vicinity = body[idx : idx + 200]
        assert "context" in vicinity.lower() or "reference" in vicinity.lower(), (
            "Section 4 must be about Context File References"
        )

    def test_four_numbered_sections_total(self) -> None:
        """Body must contain all 4 numbered sections (Section 1 through Section 4)."""
        body = self._body()
        for n in range(1, 5):
            assert f"Section {n}" in body, f"Body must contain Section {n}"

    def test_context_reference_safe_extraction_patterns(self) -> None:
        """Section 4 must reference @context-intelligence:context/safe-extraction-patterns.md."""
        body = self._body()
        assert "@context-intelligence:context/safe-extraction-patterns.md" in body, (
            "Body must reference @context-intelligence:context/safe-extraction-patterns.md"
        )

    def test_context_reference_session_storage_knowledge(self) -> None:
        """Body must reference @context-intelligence:context/agents/session-storage-knowledge.md."""
        body = self._body()
        assert (
            "@context-intelligence:context/agents/session-storage-knowledge.md" in body
        ), (
            "Body must reference @context-intelligence:context/agents/session-storage-knowledge.md"
        )

    def test_context_reference_session_disk_layout(self) -> None:
        """Body must reference @context-intelligence:context/session-disk-layout.dot."""
        body = self._body()
        assert "@context-intelligence:context/session-disk-layout.dot" in body, (
            "Body must reference @context-intelligence:context/session-disk-layout.dot"
        )

    def test_context_reference_delegation_strategy(self) -> None:
        """Body must reference @context-intelligence:context/delegation-strategy.dot."""
        body = self._body()
        assert "@context-intelligence:context/delegation-strategy.dot" in body, (
            "Body must reference @context-intelligence:context/delegation-strategy.dot"
        )

    def test_four_context_file_references_total(self) -> None:
        """Body must contain all four required context file references."""
        body = self._body()
        refs = [
            "@context-intelligence:context/safe-extraction-patterns.md",
            "@context-intelligence:context/agents/session-storage-knowledge.md",
            "@context-intelligence:context/session-disk-layout.dot",
            "@context-intelligence:context/delegation-strategy.dot",
        ]
        for ref in refs:
            assert ref in body, f"Body must contain context reference: {ref}"
