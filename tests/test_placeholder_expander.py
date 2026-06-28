"""Tests for _expand_env_placeholders — RED phase for the regex bug fix (slice 2).

Proves that ${VAR}, ${VAR:}, ${VAR:default}, and embedded forms like
api://${X}/y all expand correctly after the backslash is removed from
_PLACEHOLDER_RE.
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestExpandEnvPlaceholders:
    """_expand_env_placeholders must handle all documented forms."""

    def test_simple_var_set(self) -> None:
        """`${FOO}` expands to the env var value when FOO is set."""
        from context_intelligence.config import _expand_env_placeholders

        with patch.dict(os.environ, {"FOO": "bar"}, clear=False):
            assert _expand_env_placeholders("${FOO}") == "bar"

    def test_simple_var_unset_gives_empty(self) -> None:
        """`${FOO}` with FOO unset expands to empty string."""
        from context_intelligence.config import _expand_env_placeholders

        env = {k: v for k, v in os.environ.items() if k != "FOO"}
        with patch.dict(os.environ, env, clear=True):
            assert _expand_env_placeholders("${FOO}") == ""

    def test_var_with_empty_default_set(self) -> None:
        """`${VAR:}` with VAR set → env value."""
        from context_intelligence.config import _expand_env_placeholders

        with patch.dict(os.environ, {"MYVAR": "hello"}, clear=False):
            assert _expand_env_placeholders("${MYVAR:}") == "hello"

    def test_var_with_empty_default_unset(self) -> None:
        """`${VAR:}` with VAR unset → empty string."""
        from context_intelligence.config import _expand_env_placeholders

        env = {k: v for k, v in os.environ.items() if k != "MISSING_VAR_XYZZY"}
        with patch.dict(os.environ, env, clear=True):
            assert _expand_env_placeholders("${MISSING_VAR_XYZZY:}") == ""

    def test_var_with_default_set(self) -> None:
        """`${VAR:default}` with VAR set → env value (not default)."""
        from context_intelligence.config import _expand_env_placeholders

        with patch.dict(os.environ, {"MYVAR": "actual"}, clear=False):
            assert _expand_env_placeholders("${MYVAR:fallback}") == "actual"

    def test_var_with_default_unset(self) -> None:
        """`${MISSING:default}` with MISSING unset → default."""
        from context_intelligence.config import _expand_env_placeholders

        env = {k: v for k, v in os.environ.items() if k != "MISSING_VAR_XYZZY"}
        with patch.dict(os.environ, env, clear=True):
            assert _expand_env_placeholders("${MISSING_VAR_XYZZY:mydefault}") == "mydefault"

    def test_no_placeholder_unchanged(self) -> None:
        """A plain string without ${} passes through unchanged."""
        from context_intelligence.config import _expand_env_placeholders

        assert _expand_env_placeholders("api://some-fixed-id") == "api://some-fixed-id"

    def test_embedded_placeholder_expanded(self) -> None:
        """`api://${X}/y` expands the embedded ${X}."""
        from context_intelligence.config import _expand_env_placeholders

        with patch.dict(os.environ, {"X": "abc123"}, clear=False):
            result = _expand_env_placeholders("api://${X}/y")
        assert result == "api://abc123/y"

    def test_multiple_placeholders_in_one_string(self) -> None:
        """Multiple ${} in one string all expand."""
        from context_intelligence.config import _expand_env_placeholders

        with patch.dict(os.environ, {"HOST": "myhost", "PORT": "9000"}, clear=False):
            result = _expand_env_placeholders("http://${HOST}:${PORT}/events")
        assert result == "http://myhost:9000/events"

    def test_empty_string_unchanged(self) -> None:
        """Empty string passes through unchanged."""
        from context_intelligence.config import _expand_env_placeholders

        assert _expand_env_placeholders("") == ""
