"""Host-independent session-reference matching."""

import pytest

from context_intelligence import SessionResolutionError, resolve_session_id
from context_intelligence.session_ids import is_safe_session_id


def test_exact_id_wins_even_after_other_prefix_matches():
    assert resolve_session_id("session-a", iter(["session-ab", "session-a"])) == "session-a"
    assert resolve_session_id("abc", ["abcd", "abc"]) == "abc"


def test_unique_prefix_resolves_opaque_and_child_ids():
    assert resolve_session_id("session-", ["other", "session-full"]) == "session-full"
    child = "0000000000000000-abcdef1234567890_self"
    assert resolve_session_id("0000000000000000-abcdef12", [child]) == child


def test_duplicate_ids_are_one_identity():
    assert resolve_session_id("abcdef12", ["abcdef123", "abcdef123"]) == "abcdef123"


def test_ambiguity_reports_bounded_sorted_candidates():
    with pytest.raises(SessionResolutionError) as caught:
        resolve_session_id("abcdef12", [f"abcdef12-{i}" for i in range(9, -1, -1)])
    assert caught.value.code == "ambiguous_session"
    assert len(caught.value.candidates) == 5
    assert caught.value.candidates[0] == "abcdef12-0"
    assert "10 sessions" in str(caught.value)


@pytest.mark.parametrize("reference", ["missing1", "ABCDEF12"])
def test_missing_is_not_fuzzy_or_case_insensitive(reference):
    with pytest.raises(SessionResolutionError) as caught:
        resolve_session_id(reference, ["abcdef123"])
    assert caught.value.code == "session_not_found"


def test_short_prefix_requires_more_characters():
    with pytest.raises(SessionResolutionError) as caught:
        resolve_session_id("abcdef", ["abcdef123"])
    assert caught.value.code == "invalid_session_id"


@pytest.mark.parametrize(
    "reference", ["", ".", "..", "../abc", "a/b", "a\\b", "a*", "a?", "[a]", "a\0", "a b", "\n"]
)
def test_unsafe_references_rejected_without_consuming_candidates(reference):
    def candidates():
        raise AssertionError("unsafe input must not trigger lookup")
        yield ""

    assert not is_safe_session_id(reference)
    with pytest.raises(SessionResolutionError) as caught:
        resolve_session_id(reference, candidates())
    assert caught.value.code == "invalid_session_id"
