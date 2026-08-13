"""Tests for preview.py — the pre-upload preview summary and confirmation gate."""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.config_resolver import Destination

from amplifier_module_tool_context_intelligence_upload.preview import build_preview_text


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
