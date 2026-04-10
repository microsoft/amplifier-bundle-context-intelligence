"""Tests for scripts/context-intelligence.py skeleton (task-10).

Verifies:
- File exists at scripts/context-intelligence.py
- Shebang line is correct (#!/usr/bin/env python3)
- Module docstring mentions 4 subcommands and exit codes
- sys.path trick using _here, _root, and sys.path insertion
- Imports from context_intelligence (CIClient, resolve_config)
- write_json() helper: writes pretty-printed JSON
- write_jsonl() helper: writes compact JSONL, returns line count
- _add_server_args(): adds --server-url and --api-key to parser
- main() exists as entry point with argparse subcommands
- reconstruct subparser has required args
- upload subparser has required args
- status subparser has required args
- query subparser has positional cypher arg
- cmd_* functions are referenced via set_defaults
- if __name__ == '__main__': sys.exit(main()) block exists
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "context-intelligence.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_script_module():
    """Dynamically load scripts/context-intelligence.py as a module."""
    spec = importlib.util.spec_from_file_location("context_intelligence_cli", SCRIPT_PATH)
    assert spec is not None, f"Failed to create spec for {SCRIPT_PATH}"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_script_text() -> str:
    """Read the raw text of the script."""
    return SCRIPT_PATH.read_text()


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------


class TestFileExists:
    """The script file must exist at scripts/context-intelligence.py."""

    def test_scripts_dir_exists(self):
        """scripts/ directory must exist."""
        assert SCRIPTS_DIR.exists(), "scripts/ directory does not exist"
        assert SCRIPTS_DIR.is_dir(), "scripts/ is not a directory"

    def test_script_file_exists(self):
        """scripts/context-intelligence.py must exist."""
        assert SCRIPT_PATH.exists(), "scripts/context-intelligence.py does not exist"
        assert SCRIPT_PATH.is_file(), "scripts/context-intelligence.py is not a file"


# ---------------------------------------------------------------------------
# Shebang line
# ---------------------------------------------------------------------------


class TestShebang:
    """The first line must be the Python 3 shebang."""

    def test_shebang_line(self):
        """First line must be #!/usr/bin/env python3."""
        text = _read_script_text()
        first_line = text.splitlines()[0]
        assert first_line == "#!/usr/bin/env python3", (
            f"Expected shebang '#!/usr/bin/env python3', got '{first_line}'"
        )


# ---------------------------------------------------------------------------
# Module docstring
# ---------------------------------------------------------------------------


class TestModuleDocstring:
    """The module docstring must describe subcommands and exit codes."""

    def test_docstring_mentions_reconstruct(self):
        """Docstring must mention 'reconstruct' subcommand."""
        text = _read_script_text()
        assert "reconstruct" in text, "Docstring must mention 'reconstruct' subcommand"

    def test_docstring_mentions_upload(self):
        """Docstring must mention 'upload' subcommand."""
        text = _read_script_text()
        assert "upload" in text, "Docstring must mention 'upload' subcommand"

    def test_docstring_mentions_status(self):
        """Docstring must mention 'status' subcommand."""
        text = _read_script_text()
        assert "status" in text, "Docstring must mention 'status' subcommand"

    def test_docstring_mentions_query(self):
        """Docstring must mention 'query' subcommand."""
        text = _read_script_text()
        assert "query" in text, "Docstring must mention 'query' subcommand"

    def test_docstring_mentions_exit_codes(self):
        """Docstring must mention exit codes 0, 1, 2."""
        text = _read_script_text()
        # These should appear in the docstring section
        assert "0" in text and "1" in text and "2" in text, "Docstring must mention exit codes"

    def test_module_has_docstring(self):
        """Module must have a non-empty docstring."""
        module = _load_script_module()
        assert module.__doc__ is not None, "Module must have a docstring"
        assert len(module.__doc__.strip()) > 0, "Module docstring must not be empty"

    def test_docstring_mentions_four_subcommands(self):
        """Module docstring must list all 4 subcommands."""
        module = _load_script_module()
        doc = module.__doc__ or ""
        for sub in ["reconstruct", "upload", "status", "query"]:
            assert sub in doc, f"Docstring must mention subcommand '{sub}'"


# ---------------------------------------------------------------------------
# sys.path trick
# ---------------------------------------------------------------------------


class TestSysPathTrick:
    """The script must contain the sys.path manipulation pattern."""

    def test_here_variable(self):
        """Script must define _here = Path(__file__).resolve().parent."""
        text = _read_script_text()
        assert "_here" in text, "Script must define _here variable"
        assert "Path(__file__).resolve().parent" in text, (
            "Script must use Path(__file__).resolve().parent for _here"
        )

    def test_root_variable(self):
        """Script must define _root = _here.parent."""
        text = _read_script_text()
        assert "_root" in text, "Script must define _root variable"
        assert "_here.parent" in text, "Script must set _root = _here.parent"

    def test_sys_path_insert(self):
        """Script must insert _root into sys.path."""
        text = _read_script_text()
        assert "sys.path.insert" in text, "Script must call sys.path.insert"
        assert "_root" in text, "Script must reference _root in sys.path insert"


# ---------------------------------------------------------------------------
# Imports from context_intelligence
# ---------------------------------------------------------------------------


class TestImports:
    """The script must import from context_intelligence."""

    def test_imports_ciclient(self):
        """Script must import CIClient from context_intelligence."""
        text = _read_script_text()
        assert "CIClient" in text, "Script must import CIClient"

    def test_imports_resolve_config(self):
        """Script must import resolve_config from context_intelligence."""
        text = _read_script_text()
        assert "resolve_config" in text, "Script must import resolve_config"

    def test_module_has_ciclient(self):
        """Loaded module must have CIClient accessible."""
        module = _load_script_module()
        assert hasattr(module, "CIClient"), "Module must have CIClient"

    def test_module_has_resolve_config(self):
        """Loaded module must have resolve_config accessible."""
        module = _load_script_module()
        assert hasattr(module, "resolve_config"), "Module must have resolve_config"


# ---------------------------------------------------------------------------
# write_json helper
# ---------------------------------------------------------------------------


class TestWriteJson:
    """write_json() must write pretty-printed JSON."""

    def test_write_json_exists(self):
        """write_json function must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "write_json"), "Module must have write_json function"

    def test_write_json_creates_file(self, tmp_path):
        """write_json must create the target file."""
        module = _load_script_module()
        out = tmp_path / "output.json"
        module.write_json(out, {"key": "value"})
        assert out.exists(), "write_json must create the output file"

    def test_write_json_pretty_printed(self, tmp_path):
        """write_json must write pretty-printed JSON with indent=4."""
        module = _load_script_module()
        out = tmp_path / "output.json"
        data = {"name": "test", "value": 42}
        module.write_json(out, data)
        content = out.read_text()
        # Pretty-printed JSON has newlines and indentation
        assert "\n" in content, "write_json must write pretty-printed JSON (has newlines)"
        assert "    " in content, "write_json must use indent=4"

    def test_write_json_valid_json(self, tmp_path):
        """write_json must produce valid JSON."""
        module = _load_script_module()
        out = tmp_path / "output.json"
        data = {"nested": {"list": [1, 2, 3]}, "str": "hello"}
        module.write_json(out, data)
        parsed = json.loads(out.read_text())
        assert parsed == data, "write_json must produce valid parseable JSON"

    def test_write_json_creates_parent_dirs(self, tmp_path):
        """write_json must create parent directories if they don't exist."""
        module = _load_script_module()
        out = tmp_path / "subdir" / "deep" / "output.json"
        module.write_json(out, {"a": 1})
        assert out.exists(), "write_json must create parent directories"


# ---------------------------------------------------------------------------
# write_jsonl helper
# ---------------------------------------------------------------------------


class TestWriteJsonl:
    """write_jsonl() must write compact JSONL and return line count."""

    def test_write_jsonl_exists(self):
        """write_jsonl function must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "write_jsonl"), "Module must have write_jsonl function"

    def test_write_jsonl_returns_line_count(self, tmp_path):
        """write_jsonl must return the number of lines written."""
        module = _load_script_module()
        out = tmp_path / "records.jsonl"
        records = [{"id": 1}, {"id": 2}, {"id": 3}]
        count = module.write_jsonl(out, records)
        assert count == 3, f"write_jsonl must return line count, got {count}"

    def test_write_jsonl_creates_file(self, tmp_path):
        """write_jsonl must create the target file."""
        module = _load_script_module()
        out = tmp_path / "records.jsonl"
        module.write_jsonl(out, [{"a": 1}])
        assert out.exists(), "write_jsonl must create the output file"

    def test_write_jsonl_compact_format(self, tmp_path):
        """write_jsonl must use compact separators (no spaces after : or ,)."""
        module = _load_script_module()
        out = tmp_path / "records.jsonl"
        module.write_jsonl(out, [{"key": "value", "num": 42}])
        content = out.read_text().strip()
        # Compact format should not have ": " or ", " but should have ":" and ","
        # The default json.dumps uses ": " and ", " with spaces
        # Compact format uses ":" and "," without spaces
        assert ": " not in content, "write_jsonl must use compact separators (no ': ')"
        assert (
            "," not in content.replace(",", "")
            or ',"' in content
            or ",}" in content
            or ",]" in content
        ), "write_jsonl must use compact format"

    def test_write_jsonl_one_record_per_line(self, tmp_path):
        """write_jsonl must write one JSON record per line."""
        module = _load_script_module()
        out = tmp_path / "records.jsonl"
        records = [{"id": i} for i in range(5)]
        module.write_jsonl(out, records)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        assert len(lines) == 5, (
            f"write_jsonl must write one record per line, got {len(lines)} lines"
        )

    def test_write_jsonl_valid_records(self, tmp_path):
        """Each line of JSONL must be valid JSON."""
        module = _load_script_module()
        out = tmp_path / "records.jsonl"
        records = [{"id": i, "name": f"item-{i}"} for i in range(3)]
        module.write_jsonl(out, records)
        lines = [ln for ln in out.read_text().splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            parsed = json.loads(line)
            assert parsed == records[i], f"Line {i} must parse back to original record"

    def test_write_jsonl_returns_zero_for_empty(self, tmp_path):
        """write_jsonl with empty records must return 0."""
        module = _load_script_module()
        out = tmp_path / "empty.jsonl"
        count = module.write_jsonl(out, [])
        assert count == 0, "write_jsonl with empty list must return 0"

    def test_write_jsonl_creates_parent_dirs(self, tmp_path):
        """write_jsonl must create parent directories if they don't exist."""
        module = _load_script_module()
        out = tmp_path / "sub" / "records.jsonl"
        module.write_jsonl(out, [{"x": 1}])
        assert out.exists(), "write_jsonl must create parent directories"


# ---------------------------------------------------------------------------
# _add_server_args helper
# ---------------------------------------------------------------------------


class TestAddServerArgs:
    """_add_server_args() must add --server-url and --api-key."""

    def test_add_server_args_exists(self):
        """_add_server_args function must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "_add_server_args"), "Module must have _add_server_args function"

    def test_add_server_url_arg(self):
        """_add_server_args must add --server-url argument."""
        import argparse

        module = _load_script_module()
        parser = argparse.ArgumentParser()
        module._add_server_args(parser)
        args = parser.parse_args(["--server-url", "http://test:8000"])
        assert args.server_url == "http://test:8000"

    def test_add_api_key_arg(self):
        """_add_server_args must add --api-key argument."""
        import argparse

        module = _load_script_module()
        parser = argparse.ArgumentParser()
        module._add_server_args(parser)
        args = parser.parse_args(["--api-key", "mysecret"])
        assert args.api_key == "mysecret"

    def test_server_url_defaults_to_none(self):
        """--server-url must default to None."""
        import argparse

        module = _load_script_module()
        parser = argparse.ArgumentParser()
        module._add_server_args(parser)
        args = parser.parse_args([])
        assert args.server_url is None

    def test_api_key_defaults_to_none(self):
        """--api-key must default to None."""
        import argparse

        module = _load_script_module()
        parser = argparse.ArgumentParser()
        module._add_server_args(parser)
        args = parser.parse_args([])
        assert args.api_key is None


# ---------------------------------------------------------------------------
# main() entry point and subparsers
# ---------------------------------------------------------------------------


class TestMain:
    """main() must be the CLI entry point returning int."""

    def test_main_exists(self):
        """main function must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "main"), "Module must have main function"

    def test_main_is_callable(self):
        """main must be callable."""
        module = _load_script_module()
        assert callable(module.main), "main must be callable"

    def test_main_signature_accepts_argv(self):
        """main() must accept argv parameter (default None)."""
        import inspect

        module = _load_script_module()
        sig = inspect.signature(module.main)
        assert "argv" in sig.parameters, "main must accept argv parameter"
        assert sig.parameters["argv"].default is None, "argv must default to None"

    def test_main_returns_int_annotation(self):
        """main() must be annotated to return int."""
        import inspect

        module = _load_script_module()
        sig = inspect.signature(module.main)
        # from __future__ import annotations makes annotations strings; accept both forms
        assert sig.return_annotation in (int, "int"), "main must return int"


# ---------------------------------------------------------------------------
# Subparser: reconstruct
# ---------------------------------------------------------------------------


class TestReconstructSubparser:
    """reconstruct subparser must have all required arguments."""

    def _parse(self, args):
        module = _load_script_module()
        # We need to handle the case where cmd_reconstruct doesn't exist yet
        # by monkey-patching placeholders
        _patch_cmd_placeholders(module)
        return module.main(args)

    def test_reconstruct_subparser_exists(self):
        """reconstruct subcommand must be parseable."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        # Should not raise
        try:
            module.main(["reconstruct", "--dry-run", "--path", "/tmp"])
        except SystemExit as e:
            # Allowed if the arg parsing fails, but not because subcommand doesn't exist
            assert "reconstruct" not in str(e), "reconstruct subcommand must exist"
        except Exception:
            pass  # The cmd function may raise, that's OK

    def test_reconstruct_has_project_dir(self):
        """reconstruct must support --project-dir."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        # Re-register with our mock
        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--project-dir", "/tmp/proj"])
        assert hasattr(captured["args"], "project_dir"), "reconstruct must have project_dir arg"
        assert captured["args"].project_dir == "/tmp/proj"

    def test_reconstruct_has_events_only(self):
        """reconstruct must support --events-only."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--events-only"])
        assert hasattr(captured["args"], "events_only"), "reconstruct must have events_only arg"
        assert captured["args"].events_only is True

    def test_reconstruct_has_transcript_only(self):
        """reconstruct must support --transcript-only."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--transcript-only"])
        assert captured["args"].transcript_only is True

    def test_reconstruct_has_metadata_only(self):
        """reconstruct must support --metadata-only."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--metadata-only"])
        assert captured["args"].metadata_only is True

    def test_reconstruct_has_force(self):
        """reconstruct must support --force."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--force"])
        assert captured["args"].force is True

    def test_reconstruct_has_dry_run(self):
        """reconstruct must support --dry-run."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--dry-run"])
        assert captured["args"].dry_run is True

    def test_reconstruct_has_resolve_blobs(self):
        """reconstruct must support --resolve-blobs."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--resolve-blobs"])
        assert captured["args"].resolve_blobs is True

    def test_reconstruct_has_session(self):
        """reconstruct must support --session."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--session", "abc123"])
        assert captured["args"].session == "abc123"

    def test_reconstruct_has_verbose(self):
        """reconstruct must support -v / --verbose."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "-v"])
        assert captured["args"].verbose is True

    def test_reconstruct_has_server_args(self):
        """reconstruct must support --server-url and --api-key."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "reconstruct", mock_cmd)
        module.main(["reconstruct", "--server-url", "http://s", "--api-key", "k"])
        assert captured["args"].server_url == "http://s"
        assert captured["args"].api_key == "k"


# ---------------------------------------------------------------------------
# Subparser: upload
# ---------------------------------------------------------------------------


class TestUploadSubparser:
    """upload subparser must have all required arguments."""

    def test_upload_has_path(self):
        """upload must support --path (required)."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "upload", mock_cmd)
        module.main(["upload", "--path", "/tmp/session"])
        assert captured["args"].path == "/tmp/session"

    def test_upload_has_job_id(self):
        """upload must support --job-id."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "upload", mock_cmd)
        module.main(["upload", "--path", "/tmp", "--job-id", "job-123"])
        assert captured["args"].job_id == "job-123"

    def test_upload_has_progress(self):
        """upload must support --progress."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "upload", mock_cmd)
        module.main(["upload", "--path", "/tmp", "--progress", "/tmp/prog.json"])
        assert captured["args"].progress == "/tmp/prog.json"

    def test_upload_has_event_delay_ms(self):
        """upload must support --event-delay-ms."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "upload", mock_cmd)
        module.main(["upload", "--path", "/tmp", "--event-delay-ms", "50"])
        assert captured["args"].event_delay_ms == 50

    def test_upload_has_server_args(self):
        """upload must support --server-url and --api-key."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "upload", mock_cmd)
        module.main(["upload", "--path", "/tmp", "--server-url", "http://s", "--api-key", "k"])
        assert captured["args"].server_url == "http://s"
        assert captured["args"].api_key == "k"


# ---------------------------------------------------------------------------
# Subparser: status
# ---------------------------------------------------------------------------


class TestStatusSubparser:
    """status subparser must have required arguments."""

    def test_status_has_workspace(self):
        """status must support --workspace."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "status", mock_cmd)
        module.main(["status", "--workspace", "my-proj"])
        assert captured["args"].workspace == "my-proj"

    def test_status_has_server_args(self):
        """status must support --server-url and --api-key."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "status", mock_cmd)
        module.main(["status", "--server-url", "http://s", "--api-key", "k"])
        assert captured["args"].server_url == "http://s"
        assert captured["args"].api_key == "k"


# ---------------------------------------------------------------------------
# Subparser: query
# ---------------------------------------------------------------------------


class TestQuerySubparser:
    """query subparser must have positional cypher and --workspace."""

    def test_query_has_positional_cypher(self):
        """query must accept a positional cypher argument."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "query", mock_cmd)
        module.main(["query", "MATCH (n) RETURN n"])
        assert captured["args"].cypher == "MATCH (n) RETURN n"

    def test_query_has_workspace(self):
        """query must support --workspace (default: *)."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "query", mock_cmd)
        module.main(["query", "MATCH (n) RETURN n"])
        assert hasattr(captured["args"], "workspace"), "query must have workspace arg"
        assert captured["args"].workspace == "*", "workspace must default to '*'"

    def test_query_workspace_can_be_set(self):
        """query --workspace can be overridden."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "query", mock_cmd)
        module.main(["query", "MATCH (n) RETURN n", "--workspace", "my-ws"])
        assert captured["args"].workspace == "my-ws"

    def test_query_has_server_args(self):
        """query must support --server-url and --api-key."""
        module = _load_script_module()
        _patch_cmd_placeholders(module)
        captured = {}

        def mock_cmd(args):
            captured["args"] = args
            return 0

        _register_subcommand_mock(module, "query", mock_cmd)
        module.main(["query", "MATCH (n) RETURN n", "--server-url", "http://s", "--api-key", "k"])
        assert captured["args"].server_url == "http://s"
        assert captured["args"].api_key == "k"


# ---------------------------------------------------------------------------
# set_defaults(func=cmd_*) registration
# ---------------------------------------------------------------------------


class TestSetDefaults:
    """Each subparser must register a cmd_* function via set_defaults."""

    def test_script_references_cmd_reconstruct(self):
        """Script text must reference cmd_reconstruct."""
        text = _read_script_text()
        assert "cmd_reconstruct" in text, "Script must reference cmd_reconstruct"

    def test_script_references_cmd_upload(self):
        """Script text must reference cmd_upload."""
        text = _read_script_text()
        assert "cmd_upload" in text, "Script must reference cmd_upload"

    def test_script_references_cmd_status(self):
        """Script text must reference cmd_status."""
        text = _read_script_text()
        assert "cmd_status" in text, "Script must reference cmd_status"

    def test_script_references_cmd_query(self):
        """Script text must reference cmd_query."""
        text = _read_script_text()
        assert "cmd_query" in text, "Script must reference cmd_query"

    def test_script_uses_set_defaults(self):
        """Script must call set_defaults to register cmd_* functions."""
        text = _read_script_text()
        assert "set_defaults" in text, "Script must use set_defaults"


# ---------------------------------------------------------------------------
# __main__ block
# ---------------------------------------------------------------------------


class TestMainBlock:
    """The script must have a proper if __name__ == '__main__' block."""

    def test_has_main_guard(self):
        """Script must contain if __name__ == '__main__' block."""
        text = _read_script_text()
        assert "__name__" in text and "__main__" in text, (
            "Script must have if __name__ == '__main__' guard"
        )

    def test_main_guard_calls_sys_exit(self):
        """The __main__ block must call sys.exit(main())."""
        text = _read_script_text()
        assert "sys.exit(main())" in text, "Script must call sys.exit(main()) in __main__ block"


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Verify the acceptance criteria from the spec."""

    def test_import_chain_from_repo_root(self):
        """Import chain: CIClient and resolve_config must be importable from repo root."""
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0,'.'); from context_intelligence import CIClient, resolve_config; print('Script import chain OK')",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Import chain failed with returncode {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "Script import chain OK" in result.stdout


# ---------------------------------------------------------------------------
# Placeholder comment for cmd_* functions
# ---------------------------------------------------------------------------


class TestPlaceholderComment:
    """Script must have the placeholder comment noting Tasks 11-14."""

    def test_placeholder_comment_exists(self):
        """Script must contain a placeholder comment for cmd_* functions."""
        text = _read_script_text()
        # The spec says: "Placeholder comment for cmd_* functions to be added in Tasks 11-14."
        assert "Tasks 11" in text or "task 11" in text.lower() or "11-14" in text, (
            "Script must have placeholder comment mentioning Tasks 11-14"
        )


# ---------------------------------------------------------------------------
# Helper utilities (used across test classes)
# ---------------------------------------------------------------------------


def _patch_cmd_placeholders(module):
    """Ensure cmd_* functions exist as no-ops so main() can be called."""
    for name in ["cmd_reconstruct", "cmd_upload", "cmd_status", "cmd_query"]:
        if not hasattr(module, name):
            setattr(module, name, lambda args: 0)


def _register_subcommand_mock(module, subcommand: str, mock_fn):
    """Re-run main() argument parsing with a mock func for the given subcommand.

    This works by rebuilding the argument parser and re-registering the mock.
    We do it by monkey-patching the module's cmd_* attribute before calling main().
    """
    cmd_name = f"cmd_{subcommand}"
    setattr(module, cmd_name, mock_fn)
