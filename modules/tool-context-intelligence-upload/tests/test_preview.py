"""Tests for preview.py — the pre-upload preview summary and confirmation gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from amplifier_module_hook_context_intelligence.config_resolver import Destination

from amplifier_module_tool_context_intelligence_upload.preview import (
    TOP_FOLDER_LIMIT,
    UNKNOWN_FOLDER_LABEL,
    ConfirmationRequiredError,
    abbreviate_home,
    build_preview_text,
    confirm_upload,
    describe_auth,
    group_by_folder,
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


def _entries(*pairs: tuple[str | None, int]) -> list[tuple[str | None, int]]:
    """Build the (working_dir, event_count) list build_preview_text consumes."""
    return list(pairs)


def _preview(*args, source_format: str = "context-intelligence", **kwargs) -> str:
    """Call build_preview_text with a default format.

    source_format is keyword-only and required on the real function so that no
    caller can silently mislabel a run.  Tests that are not about the format
    section still need *a* value, so they get one here.
    """
    return build_preview_text(*args, source_format=source_format, **kwargs)


class TestAbbreviateHome:
    """Long absolute paths collapse to ~ so the folder column stays readable."""

    def test_path_under_home_is_abbreviated(self):
        assert abbreviate_home(str(Path.home() / "repos" / "api")) == "~/repos/api"

    def test_home_itself_is_a_bare_tilde(self):
        assert abbreviate_home(str(Path.home())) == "~"

    def test_path_outside_home_is_unchanged(self):
        assert abbreviate_home("/var/data/sessions") == "/var/data/sessions"

    def test_sibling_of_home_is_not_mistaken_for_a_child(self):
        # A directory that merely starts with the home path -- e.g. home
        # plus a "-backup" suffix -- is a sibling, not a child, and must
        # not be abbreviated to "~-backup".
        sibling = str(Path.home()) + "-backup"
        assert abbreviate_home(sibling) == sibling

    def test_empty_string_is_unchanged(self):
        assert abbreviate_home("") == ""


class TestGroupByFolder:
    """Per-session pairs aggregate into a deterministic folder table."""

    def test_sessions_in_the_same_folder_are_summed(self):
        rows = group_by_folder(_entries(("/a", 10), ("/a", 5), ("/b", 1)))
        assert rows[0] == ("/a", 2, 15)

    def test_rows_are_sorted_by_session_count_descending(self):
        rows = group_by_folder(_entries(("/small", 1), ("/big", 1), ("/big", 1)))
        assert [label for label, _, _ in rows] == ["/big", "/small"]

    def test_ties_break_on_event_count_then_label(self):
        rows = group_by_folder(_entries(("/b", 5), ("/a", 5), ("/c", 99)))
        # /c wins on events; /a and /b tie on both counts, so label decides.
        assert [label for label, _, _ in rows] == ["/c", "/a", "/b"]

    def test_grouping_is_deterministic_across_input_orderings(self):
        forward = _entries(("/a", 1), ("/b", 2), ("/c", 3))
        assert group_by_folder(forward) == group_by_folder(list(reversed(forward)))

    def test_unknown_working_dirs_collapse_into_one_visible_bucket(self):
        rows = group_by_folder(_entries((None, 4), (None, 6)))
        assert rows == [(UNKNOWN_FOLDER_LABEL, 2, 10)]

    def test_empty_input_produces_no_rows(self):
        assert group_by_folder([]) == []


class TestDescribeAuth:
    """The auth line names the mode without ever printing the key."""

    def test_static_mode_is_described_without_the_key(self):
        text = describe_auth(_destination(auth_mode="static", api_key="hunter2"))
        assert "static" in text
        assert "hunter2" not in text

    def test_default_empty_auth_mode_is_treated_as_static(self):
        assert "static" in describe_auth(_destination(auth_mode=""))

    def test_entra_mode_names_the_resource(self):
        text = describe_auth(_destination(auth_mode="entra", auth_resource="api://0000-1111"))
        assert "entra" in text
        assert "api://0000-1111" in text

    def test_entra_without_a_resource_says_so(self):
        text = describe_auth(_destination(auth_mode="entra", auth_resource=""))
        assert "no auth_resource" in text


class TestBuildPreviewText:
    """The preview shown before a destination-mode upload."""

    def test_preview_names_the_destination(self):
        text = _preview(_destination(), _entries(("/a", 120)), 2)
        assert "team" in text

    def test_preview_shows_the_full_post_endpoint_not_a_bare_base_url(self):
        text = _preview(_destination(), _entries(("/a", 1)), 0)
        assert "https://context-intelligence.team.example.com/events" in text

    def test_trailing_slash_on_the_url_does_not_double_up(self):
        dest = _destination(url="https://ci.example.com/")
        text = _preview(dest, _entries(("/a", 1)), 0)
        assert "https://ci.example.com/events" in text
        assert "//events" not in text

    def test_preview_reports_the_session_count(self):
        text = _preview(_destination(), _entries(*[("/a", 0)] * 47), 0)
        assert "47" in text

    def test_preview_reports_the_approximate_event_count(self):
        text = _preview(_destination(), _entries(("/a", 9001)), 0)
        assert "9,001" in text

    def test_large_counts_are_thousands_separated(self):
        text = _preview(_destination(), _entries(("/a", 184032)), 0)
        assert "184,032" in text

    def test_preview_reports_the_filtered_out_count(self):
        text = _preview(_destination(), _entries(("/a", 120)), 12)
        assert "12" in text
        assert "filtered" in text.lower()

    def test_preview_lists_the_include_and_exclude_patterns(self):
        dest = _destination(include=("repos/**", "work/**"), exclude=("scratch/**",))
        text = _preview(dest, _entries(("/a", 1)), 0)
        assert "repos/**" in text
        assert "work/**" in text
        assert "scratch/**" in text

    def test_absent_exclude_patterns_render_as_none(self):
        text = _preview(_destination(exclude=()), _entries(("/a", 1)), 0)
        assert "(none)" in text

    def test_empty_include_warns_that_nothing_matches(self):
        text = _preview(_destination(include=()), _entries(), 0)
        assert "matches no sessions" in text

    def test_folders_are_listed_with_their_counts(self):
        text = _preview(_destination(), _entries(("/repos/api", 300), ("/repos/api", 200)), 0)
        assert "/repos/api" in text
        assert "2 sessions" in text
        assert "500 events" in text

    def test_folder_list_is_capped_and_rolls_the_rest_up(self):
        entries = _entries(*[(f"/folder-{i}", 1) for i in range(12)])
        text = _preview(_destination(), entries, 0)
        # 12 distinct folders -> TOP_FOLDER_LIMIT shown, the remainder rolled up.
        remaining = 12 - TOP_FOLDER_LIMIT
        assert f"in {remaining} other folders" in text
        assert f"+ {remaining} sessions" in text

    def test_no_rollup_line_when_folders_fit_under_the_cap(self):
        entries = _entries(*[(f"/folder-{i}", 1) for i in range(TOP_FOLDER_LIMIT)])
        text = _preview(_destination(), entries, 0)
        assert "other folder" not in text

    def test_rollup_line_is_singular_for_exactly_one_extra_folder(self):
        entries = _entries(*[(f"/folder-{i}", 1) for i in range(TOP_FOLDER_LIMIT + 1)])
        text = _preview(_destination(), entries, 0)
        assert "in 1 other folder" in text
        assert "other folders" not in text

    def test_folder_section_stays_within_five_lines(self):
        entries = _entries(*[(f"/folder-{i}", 1) for i in range(50)])
        text = _preview(_destination(), entries, 0)
        lines = text.splitlines()
        # Count only the indented rows that belong to the folder section -- other
        # sections (format, for one) also indent, so anchor on the header.
        start = next(i for i, line in enumerate(lines) if line.startswith("  from "))
        folder_lines = [
            line for line in lines[start + 1 :] if line.startswith("    ") and line.strip()
        ]
        assert len(folder_lines) <= TOP_FOLDER_LIMIT + 1

    def test_sessions_with_unknown_folders_are_still_visible(self):
        text = _preview(_destination(), _entries((None, 7)), 0)
        assert UNKNOWN_FOLDER_LABEL in text

    def test_zero_sessions_renders_without_a_folder_section(self):
        text = _preview(_destination(), _entries(), 5)
        assert "sessions" in text
        assert "from 0 folders" not in text

    def test_api_key_never_appears_in_the_preview(self):
        text = _preview(_destination(api_key="super-secret"), _entries(("/a", 1)), 0)
        assert "super-secret" not in text

    def test_preview_is_multi_line(self):
        text = _preview(_destination(), _entries(("/a", 1)), 1)
        assert len(text.splitlines()) >= 4


class TestFormatSection:
    """The --format value gets its own labelled block.

    Both formats POST to the same /events endpoint, so nothing else in the
    preview distinguishes them -- but they differ in replay semantics.  An
    operator has to be able to see which pipeline is about to run.
    """

    def test_format_has_its_own_section_header(self):
        text = _preview(_destination(), _entries(("/a", 1)), 0)
        assert "  format:" in text.splitlines()

    def test_the_format_value_is_on_its_own_line(self):
        text = _preview(_destination(), _entries(("/a", 1)), 0, source_format="logging-hook")
        assert "    logging-hook" in text.splitlines()

    def test_context_intelligence_format_says_what_it_reads(self):
        text = _preview(
            _destination(), _entries(("/a", 1)), 0, source_format="context-intelligence"
        )
        assert "already in Context Intelligence's own format" in text

    def test_context_intelligence_format_says_a_re_run_is_safe(self):
        """The re-run line must not imply a second copy of the data.

        ``replay=true`` bypasses the server's 7-day dedup cache, but Neo4j
        MERGE still converges to the same graph state.  Wording that suggests
        duplicate data is wrong, not merely imprecise.
        """
        text = _preview(
            _destination(), _entries(("/a", 1)), 0, source_format="context-intelligence"
        )
        assert "re-running is safe" in text
        assert "converges to the same state" in text

    def test_logging_hook_format_says_what_it_reads_and_that_disk_is_untouched(self):
        text = _preview(_destination(), _entries(("/a", 1)), 0, source_format="logging-hook")
        assert "legacy hooks-logging sessions" in text
        assert "nothing on disk changes" in text

    def test_logging_hook_format_states_the_seven_day_skip_window(self):
        text = _preview(_destination(), _entries(("/a", 1)), 0, source_format="logging-hook")
        assert "already received in the last 7 days" in text

    def test_neither_format_note_uses_the_word_replay(self):
        """'Replay' reads as 'uploads it again' to an operator.

        The flag is named --no-replay, but the preview is not the place to
        surface that name: the concept it maps to (cache bypass) is the
        opposite of what the plain-English reading suggests.
        """
        for fmt in ("context-intelligence", "logging-hook"):
            text = _preview(_destination(), _entries(("/a", 1)), 0, source_format=fmt)
            note = text.split("  format:")[1]
            assert "replay" not in note.lower()

    def test_the_two_formats_render_differently(self):
        ci = _preview(_destination(), _entries(("/a", 1)), 0, source_format="context-intelligence")
        legacy = _preview(_destination(), _entries(("/a", 1)), 0, source_format="logging-hook")
        assert ci != legacy

    def test_an_unrecognised_format_says_so_rather_than_rendering_blank(self):
        text = _preview(_destination(), _entries(("/a", 1)), 0, source_format="wat")
        assert "    wat" in text.splitlines()
        assert "unrecognised format" in text

    def test_source_format_is_required_so_no_run_can_be_mislabelled(self):
        with pytest.raises(TypeError):
            build_preview_text(_destination(), _entries(("/a", 1)), 0)  # type: ignore[call-arg]


class TestSessionsAreTheUnit:
    """Counts are stated in sessions; events ride along parenthetically.

    Sessions are the unit an operator reasons about.  The earlier layout put
    'sessions' and 'events' on sibling rows and then labelled a bare number
    'filtered out', which left it ambiguous which unit that number was in.
    """

    def test_the_upload_count_leads_with_sessions(self):
        text = _preview(_destination(), _entries(*[("/a", 10)] * 3), 0)
        assert "3 sessions" in text

    def test_the_event_count_is_parenthetical_not_a_sibling_row(self):
        text = _preview(_destination(), _entries(("/a", 9001)), 0)
        assert "(~9,001 events)" in text

    def test_filtered_out_states_its_unit_as_sessions(self):
        text = _preview(_destination(), _entries(("/a", 1)), 3463)
        assert "3,463 sessions" in text

    def test_no_bare_number_is_labelled_filtered_out(self):
        # The regression this guards: "filtered out: 3,463" with no unit.
        text = _preview(_destination(), _entries(("/a", 1)), 3463)
        line = next(ln for ln in text.splitlines() if "filtered out" in ln)
        assert "session" in line

    def test_a_single_session_is_not_pluralised(self):
        text = _preview(_destination(), _entries(("/a", 1)), 1)
        assert "1 session " in text
        assert "1 sessions" not in text

    def test_counts_stay_thousands_separated_in_the_new_layout(self):
        text = _preview(_destination(), _entries(*[("/a", 100)] * 1200), 45678)
        assert "1,200 sessions" in text
        assert "45,678 sessions" in text
        assert "120,000 events" in text


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
