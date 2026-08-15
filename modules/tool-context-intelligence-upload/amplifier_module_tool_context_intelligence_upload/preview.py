"""Preview + confirmation gate shown before a destination-mode upload.

Rendered between filtering and upload (design step 2b).  All output goes to
stderr because stdout is reserved for the machine-readable result JSON.

The preview answers five questions before anything leaves the machine:

  WHICH source format      -- context-intelligence (native) or logging-hook
                              (legacy), because the two differ in replay
                              semantics even though they share an endpoint
  WHAT rules decided this  -- the destination's include/exclude patterns
  WHAT is going            -- how many SESSIONS, with the approximate event
                              count in parentheses, and the folders they
                              came from
  WHAT was held back       -- how many SESSIONS the patterns filtered out
  WHERE it is going        -- the exact POST endpoint and auth mode

Counts are always stated in sessions first, with events parenthesised.  A
session is the unit an operator reasons about ("I ran a few thousand
sessions in that project folder"); an event count is an implementation
detail of how big those sessions were.  Every count line therefore carries the literal word
"sessions" so no number on screen is ambiguous about its unit.

Folder grouping is capped at :data:`TOP_FOLDER_LIMIT` rows plus a single
roll-up line, so the section stays scannable no matter how many folders a
scan touched.
"""

from __future__ import annotations

import sys
from pathlib import Path

from amplifier_module_hook_context_intelligence.config_resolver import Destination

#: Number of folders listed individually before the roll-up line.  Four rows
#: plus one roll-up keeps the section at the five-line ceiling.
TOP_FOLDER_LIMIT = 4

#: Column width for the ``label: value`` rows, so every value starts in the
#: same column across all three sections.
_LABEL_WIDTH = 18

#: Label used for sessions whose working directory could not be derived.
#: These sessions are still uploaded (``filter_sessions`` includes anything
#: undecidable rather than dropping it), so they must be visible here.
UNKNOWN_FOLDER_LABEL = "(unknown working dir)"

#: Shown when a destination declares no include patterns.  Per the Destination
#: contract an empty include set matches NOTHING, which is worth saying out
#: loud rather than rendering a silent blank.
_NO_INCLUDE_NOTE = "(none configured -- this destination matches no sessions)"

#: Explanation lines per ``--format`` value.  Both formats POST to the same
#: ``/events`` endpoint, so the endpoint line alone cannot tell an operator
#: which pipeline is running.  Each entry is a tuple of lines: what the format
#: reads, then what a re-run does.
#:
#: The re-run line deliberately avoids the word "replay".  ``replay=true``
#: (the context-intelligence default) does NOT mean the session lands twice --
#: it means the server's 7-day in-memory dedup cache is bypassed and every
#: event is re-sent.  Neo4j's ``MERGE + SET n += row.props`` still converges to
#: the same graph state.  Saying "replays by default" invites the reader to
#: assume duplicate data, which is exactly backwards.
_FORMAT_NOTES = {
    "context-intelligence": (
        "reads sessions already in Context Intelligence's own format",
        "re-sends every event; re-running is safe -- the graph converges to the same state",
    ),
    "logging-hook": (
        "reads legacy hooks-logging sessions, converted in memory (nothing on disk changes)",
        "the server skips any event it already received in the last 7 days",
    ),
}

#: Rendered when a caller passes a format string not in :data:`_FORMAT_NOTES`.
#: Unknown is stated rather than silently blanked -- an unrecognised format is
#: information the operator needs, not a cosmetic gap.
_UNKNOWN_FORMAT_NOTE = "(unrecognised format -- no description available)"


class ConfirmationRequiredError(Exception):
    """Raised when confirmation is required but cannot be obtained.

    Happens when the process is not interactive (stdin/stdout are not a TTY)
    and ``--auto-approve`` was not passed: we must neither hang on ``input()``
    nor silently upload.
    """


def confirm_upload(*, auto_approve: bool, interactive: bool) -> bool:
    """Ask the operator whether to proceed.  The default answer is NO.

    - ``auto_approve=True``  -> return True without prompting (CI/automation).
    - ``interactive=True``   -> prompt ``Proceed? [y/N]`` on stderr and read stdin.
    - otherwise              -> raise :class:`ConfirmationRequiredError`.
    """
    if auto_approve:
        return True
    if not interactive:
        raise ConfirmationRequiredError("confirmation required but stdin/stdout is not a TTY")
    sys.stderr.write("Proceed? [y/N] ")
    sys.stderr.flush()
    answer = input()
    return answer.strip().lower() in {"y", "yes"}


def abbreviate_home(path_str: str) -> str:
    """Return *path_str* with a leading home directory replaced by ``~``.

    Purely cosmetic: long absolute paths dominate the folder column and push
    the counts off the right edge of a normal terminal.  Anything that is not
    under the home directory is returned unchanged.
    """
    if not path_str:
        return path_str
    home = str(Path.home())
    if path_str == home:
        return "~"
    if path_str.startswith(home + "/"):
        return "~" + path_str[len(home) :]
    return path_str


def group_by_folder(
    folder_entries: list[tuple[str | None, int]],
) -> list[tuple[str, int, int]]:
    """Aggregate per-session ``(working_dir, event_count)`` pairs by folder.

    Returns ``[(folder_label, session_count, event_count), ...]`` sorted by
    session count descending, then event count descending, then folder label
    ascending -- a total order, so identical input always renders identically.

    ``None`` working directories collapse into a single
    :data:`UNKNOWN_FOLDER_LABEL` bucket rather than being dropped: those
    sessions ARE uploaded, so hiding them would make the preview lie.
    """
    sessions_by_folder: dict[str, int] = {}
    events_by_folder: dict[str, int] = {}

    for working_dir, event_count in folder_entries:
        label = abbreviate_home(working_dir) if working_dir else UNKNOWN_FOLDER_LABEL
        sessions_by_folder[label] = sessions_by_folder.get(label, 0) + 1
        events_by_folder[label] = events_by_folder.get(label, 0) + event_count

    return sorted(
        (
            (label, session_count, events_by_folder[label])
            for label, session_count in sessions_by_folder.items()
        ),
        key=lambda row: (-row[1], -row[2], row[0]),
    )


def describe_auth(destination: Destination) -> str:
    """Return a one-line description of how this upload will authenticate.

    Never renders the API key itself -- only which mode is in play, and for
    Entra the resource URI being requested (a public identifier, not a secret).
    """
    mode = destination.auth_mode or "static"
    if mode == "entra":
        resource = destination.auth_resource or "(no auth_resource configured)"
        return f"entra -- delegated token for {resource}"
    return "static -- configured API key"


def _labeled(label: str, value: str) -> str:
    """Render one ``  label:            value`` row at the shared column width."""
    return f"  {label:<{_LABEL_WIDTH}}{value}"


def _pattern_lines(label: str, patterns: tuple[str, ...], empty_note: str) -> list[str]:
    """Render a pattern list: first pattern on the label row, rest aligned under it."""
    if not patterns:
        return [_labeled(label, empty_note)]
    return [_labeled(label, patterns[0])] + [_labeled("", pattern) for pattern in patterns[1:]]


def _folder_lines(folders: list[tuple[str, int, int]]) -> list[str]:
    """Render the capped folder table plus, if needed, one roll-up line."""
    if not folders:
        return []

    shown = folders[:TOP_FOLDER_LIMIT]
    remaining = folders[TOP_FOLDER_LIMIT:]

    width = max(len(label) for label, _, _ in shown)
    lines = [
        f"    {label:<{width}}  {sessions:>5,} "
        f"{'session ' if sessions == 1 else 'sessions'}  (~{events:,} events)"
        for label, sessions, events in shown
    ]

    if remaining:
        rollup_sessions = sum(sessions for _, sessions, _ in remaining)
        rollup_events = sum(events for _, _, events in remaining)
        folder_word = "folder" if len(remaining) == 1 else "folders"
        session_word = "session" if rollup_sessions == 1 else "sessions"
        lines.append(
            f"    + {rollup_sessions:,} {session_word} in {len(remaining):,} "
            f"other {folder_word} (~{rollup_events:,} events)"
        )

    return lines


def build_preview_text(
    destination: Destination,
    folder_entries: list[tuple[str | None, int]],
    filtered_out: int,
    *,
    source_format: str,
    dangling_parent_count: int = 0,
) -> str:
    """Return the multi-line preview summary shown before upload.

    *folder_entries* carries one ``(working_dir, event_count)`` pair per
    session that WILL be uploaded -- the caller resolves each session's
    working directory with the same helper the filter used, so the folders
    shown here are the folders the include/exclude rules actually matched.

    *filtered_out* is the number of discovered SESSIONS the destination's
    patterns excluded; those sessions are NOT present in *folder_entries*.

    *source_format* is the ``--format`` value in play.  It is keyword-only and
    required on purpose: both formats POST to the same ``/events`` endpoint but
    differ in replay semantics, so a defaulted value could silently mislabel
    the run.  Callers must state which pipeline they are running.

    *dangling_parent_count* is the number of parent sessions referenced by
    sessions in *folder_entries* that are themselves not part of this upload.
    They still show up server-side as placeholders until their own session is
    uploaded -- worth surfacing here (in "what will be sent:", where the
    operator is already reasoning about what's included) rather than as a
    separate top-level note the operator has to hunt for. Zero suppresses the
    line entirely; this is the common case and adding noise for it would
    violate the "nothing on the happy path" bar the rest of this block holds.
    """
    session_count = len(folder_entries)
    event_count = sum(events for _, events in folder_entries)
    folders = group_by_folder(folder_entries)

    session_word = "session" if session_count == 1 else "sessions"
    filtered_word = "session" if filtered_out == 1 else "sessions"

    lines: list[str] = [
        "about to upload:",
        "",
        "  format:",
        f"    {source_format}",
        f"    {_FORMAT_NOTES.get(source_format, _UNKNOWN_FORMAT_NOTE)}",
        "",
        "  what will be sent:",
        _labeled(
            "  uploading:",
            f"{session_count:,} {session_word}  (~{event_count:,} events)",
        ),
        _labeled(
            "  filtered out:",
            f"{filtered_out:,} {filtered_word}  "
            f"(excluded by this destination's include/exclude rules)",
        ),
    ]

    if dangling_parent_count:
        parent_word = "parent" if dangling_parent_count == 1 else "parents"
        lines.append(
            _labeled(
                "  placeholders:",
                f"{dangling_parent_count:,} {parent_word} not included in this run "
                f"will appear as placeholders until uploaded",
            )
        )

    lines.append("")

    lines.extend(_pattern_lines("  include:", destination.include, _NO_INCLUDE_NOTE))
    lines.extend(_pattern_lines("  exclude:", destination.exclude, "(none)"))

    if folders:
        folder_word = "folder" if len(folders) == 1 else "folders"
        lines.append("")
        lines.append(f"  from {len(folders):,} {folder_word}:")
        lines.extend(_folder_lines(folders))

    lines.extend(
        [
            "",
            "destination:",
            _labeled("name:", destination.name),
            _labeled("endpoint:", f"{destination.url.rstrip('/')}/events"),
            _labeled("auth:", describe_auth(destination)),
        ]
    )

    return "\n".join(lines)
