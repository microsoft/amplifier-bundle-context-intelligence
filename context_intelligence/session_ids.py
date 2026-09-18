"""Resolve session references without knowing a host's storage layout.

Hosts supply IDs from their authorized scope. Lookup never reads transcripts,
queries a server, guesses a match, or expands that scope.
"""

from __future__ import annotations

from collections.abc import Iterable


class SessionResolutionError(ValueError):
    """A reference is invalid, absent, or ambiguous within the supplied scope."""

    def __init__(self, code: str, message: str, candidates: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.candidates = candidates


def is_safe_session_id(value: object) -> bool:
    """Accept opaque IDs, never paths, whitespace, or glob expressions."""
    return (
        isinstance(value, str)
        and bool(value)
        and value not in {".", ".."}
        and not any(character.isspace() for character in value)
        and not any(character in value for character in "/\\\0*?[]")
    )


def resolve_session_id(reference: str, candidates: Iterable[str]) -> str:
    """Prefer an exact ID; otherwise require one unique prefix of 8+ characters.

    Exact opaque IDs may be shorter than eight characters. Duplicate IDs denote
    one identity here; the host must still reject ambiguous capture locations.
    """
    if not is_safe_session_id(reference):
        raise SessionResolutionError("invalid_session_id", "session reference must be a safe ID")
    matches: set[str] = set()
    for candidate in candidates:
        if candidate == reference:
            return candidate
        if candidate.startswith(reference):
            matches.add(candidate)
    if len(reference) < 8:
        raise SessionResolutionError(
            "invalid_session_id", "use an exact session ID or a prefix of at least 8 characters"
        )
    if not matches:
        raise SessionResolutionError("session_not_found", f"no session matches {reference!r}")
    if len(matches) > 1:
        shown = tuple(sorted(matches)[:5])
        raise SessionResolutionError(
            "ambiguous_session",
            f"{reference!r} matches {len(matches)} sessions; use a longer prefix or full ID. "
            f"Candidates (up to 5): {', '.join(shown)}",
            shown,
        )
    return matches.pop()
