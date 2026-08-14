"""Tests for preview.py — the pre-upload preview summary and confirmation gate."""

from __future__ import annotations

import pytest
from amplifier_module_hook_context_intelligence.config_resolver import Destination

from amplifier_module_tool_context_intelligence_upload.preview import (
    ConfirmationRequiredError,
    build_preview_text,
    confirm_upload,
)


def _destination(**overrides) -> Destination:
    """Build a Destination for tests, with sensible defaults."""
    fields: dict = {
        "name": "team",
        "url": "https://context-intelligence.team.example.com",
        "api_key": "secret",
        "include": ("repos/**",),
        "exclude": (),
    }
    fields.update(overrides)
    return Destination(**fields)


class TestBuildPreviewText:
    """The preview shown before a destination-mode upload."""

    def test_preview_names_the_destination_and_its_url(self):
        text = build_preview_text(_destination(), 3, 120, 2)
        assert "team" in text
        assert "https://context-intelligence.team.example.com" in text

    def test_preview_reports_the_session_count(self):
        text = build_preview_text(_destination(), 47, 9001, 0)
        assert "47" in text

    def test_preview_reports_the_approximate_event_count(self):
        text = build_preview_text(_destination(), 3, 9001, 0)
        assert "9001" in text

    def test_preview_reports_the_filtered_out_count(self):
        text = build_preview_text(_destination(), 3, 120, 12)
        assert "12" in text
        assert "filtered" in text.lower()

    def test_preview_is_multi_line(self):
        text = build_preview_text(_destination(), 1, 1, 1)
        assert len(text.splitlines()) >= 4


class TestConfirmUpload:
    """The Proceed? [y/N] gate — default NO, with an --auto-approve escape hatch."""

    def test_auto_approve_returns_true_without_prompting(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("input() must not be called when auto_approve=True")

        monkeypatch.setattr("builtins.input", _boom)
        assert confirm_upload(auto_approve=True, interactive=True) is True

    def test_auto_approve_returns_true_even_when_not_interactive(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("input() must not be called when auto_approve=True")

        monkeypatch.setattr("builtins.input", _boom)
        assert confirm_upload(auto_approve=True, interactive=False) is True

    @pytest.mark.parametrize(
        "answer,expected",
        [
            ("y", True),
            ("Y", True),
            ("yes", True),
            ("  y  ", True),
            ("", False),
            ("n", False),
            ("no", False),
            ("nope", False),
            ("maybe", False),
        ],
    )
    def test_interactive_answer_decides_and_defaults_to_no(self, monkeypatch, answer, expected):
        monkeypatch.setattr("builtins.input", lambda: answer)
        assert confirm_upload(auto_approve=False, interactive=True) is expected

    def test_prompt_is_written_to_stderr_not_stdout(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda: "y")
        confirm_upload(auto_approve=False, interactive=True)
        captured = capsys.readouterr()
        assert "Proceed? [y/N]" in captured.err
        assert captured.out == ""

    def test_non_interactive_without_auto_approve_raises(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("input() must not be called when not interactive")

        monkeypatch.setattr("builtins.input", _boom)
        with pytest.raises(ConfirmationRequiredError):
            confirm_upload(auto_approve=False, interactive=False)
