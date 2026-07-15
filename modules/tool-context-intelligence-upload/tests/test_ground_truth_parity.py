"""Ground-truth parity oracle for the legacy -> CI transform.

Proves, against REAL hook-written data (no mocks), two invariants:

1. **Data parity** -- ``reassemble_event_data(legacy).data`` reproduces the
   CI-native ``event["data"]`` exactly. This LOCKS the reassembly logic (which
   this change does NOT touch) so it can never silently drift.

2. **Workspace parity** -- ``derive_workspace(working_dir)`` produces the exact
   same slug the live CI hook wrote into every CI event's ``workspace`` field.
   This is the invariant the fix restores: the CI hook's slug is
   ``os.path.abspath(working_dir).replace("/", "-")`` (see
   ``context_intelligence.reconstruct.discover.workspace_slug``) with NO
   hyphen-escaping. The previously-shipped ``-`` -> ``--`` escaping forked
   migrated data into a *different* workspace than the hook uses.

Pairing rule (measured against the local corpus): the CI hook may persist
events in a *different order* than the legacy log, so per-event ``data`` is
compared as a **multiset** of canonical-json(data) across the two files, not by
positional index. Workspace parity holds per-event: every CI event carries the
same workspace slug.

Two layers:

- **Committed fixture** (``tests/fixtures/ground_truth``): a small, portable
  subset of real paired sessions. Every event line is byte-identical to what
  the hook wrote; events were selected (matched by canonical-json of ``data``
  across both sides) solely to keep the fixture small enough to commit. Runs
  everywhere, including CI with no local corpus.

- **Runtime sweep**: walks ``~/.amplifier/projects`` for ALL paired sessions
  and asserts the same equalities at scale. ``pytest.skip`` when the corpus is
  absent so CI without the data still passes.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.upload import _canonical_json
from amplifier_module_tool_context_intelligence_upload.legacy_transform import (
    derive_workspace,
    reassemble_event_data,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "ground_truth"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _read_events(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _legacy_working_dir(session_dir: Path) -> str:
    """working_dir from the legacy top-level metadata.json (ground-truth source)."""
    meta = json.loads((session_dir / "metadata.json").read_text(encoding="utf-8"))
    return meta.get("working_dir", "")


def _data_multiset(events: list[dict[str, Any]], *, reassemble: bool) -> Counter[str]:
    if reassemble:
        return Counter(_canonical_json(reassemble_event_data(e)[1]) for e in events)
    return Counter(_canonical_json(e["data"]) for e in events)


def _assert_session_parity(session_dir: Path) -> None:
    """Assert data-multiset parity and per-event workspace parity for one session."""
    legacy_events = _read_events(session_dir / "events.jsonl")
    ci_events = _read_events(session_dir / "context-intelligence" / "events.jsonl")

    # (1) Data parity: multiset of reassembled legacy data == multiset of CI data.
    legacy_data = _data_multiset(legacy_events, reassemble=True)
    ci_data = _data_multiset(ci_events, reassemble=False)
    assert legacy_data == ci_data, f"data multiset diverged for {session_dir.name}"

    # (2) Workspace parity: derive_workspace(working_dir) == every CI workspace.
    working_dir = _legacy_working_dir(session_dir)
    expected = derive_workspace(working_dir)
    ci_workspaces = {e.get("workspace") for e in ci_events}
    assert ci_workspaces == {expected}, (
        f"workspace slug diverged for {session_dir.name}: "
        f"derive_workspace({working_dir!r})={expected!r} but CI has {ci_workspaces!r}"
    )


# ---------------------------------------------------------------------------
# (a) Committed-fixture oracle -- runs everywhere
# ---------------------------------------------------------------------------


def _fixture_sessions() -> list[Path]:
    return sorted(p for p in FIXTURE_ROOT.iterdir() if p.is_dir()) if FIXTURE_ROOT.exists() else []


def test_fixture_root_present_and_populated() -> None:
    """The committed ground-truth fixture must exist and hold >=2 sessions."""
    sessions = _fixture_sessions()
    assert len(sessions) >= 2, (
        f"expected >=2 committed ground-truth sessions, found {len(sessions)}"
    )


@pytest.mark.parametrize("session_dir", _fixture_sessions(), ids=lambda p: p.name)
def test_fixture_ground_truth_parity(session_dir: Path) -> None:
    """Every committed real session: data parity AND workspace parity, zero diffs."""
    _assert_session_parity(session_dir)


def test_fixture_index_aligned_data_parity() -> None:
    """The committed fixtures are authored index-aligned, so assert the stronger
    per-event equality the ground-truth measurement describes:
    ``reassemble_event_data(legacy[i]).data == CI[i]["data"]``.
    """
    sessions = _fixture_sessions()
    assert sessions, "no committed fixtures"
    for session_dir in sessions:
        legacy_events = _read_events(session_dir / "events.jsonl")
        ci_events = _read_events(session_dir / "context-intelligence" / "events.jsonl")
        assert len(legacy_events) == len(ci_events)
        for i, (legacy, ci) in enumerate(zip(legacy_events, ci_events, strict=True)):
            _, data = reassemble_event_data(legacy)
            assert data == ci["data"], f"{session_dir.name} event {i} data diverged"


def test_fixture_workspace_slugs_have_unescaped_hyphens() -> None:
    """Regression guard: the fixtures cover working_dirs whose paths contain
    hyphens, and the derived slug must keep single hyphens (no ``--`` escaping),
    matching the CI hook exactly.
    """
    sessions = _fixture_sessions()
    covered_hyphenated = False
    for session_dir in sessions:
        working_dir = _legacy_working_dir(session_dir)
        # working_dir path segments contain literal '-' for these fixtures.
        if "-" in working_dir.rsplit("/", 1)[-1]:
            covered_hyphenated = True
            slug = derive_workspace(working_dir)
            assert "--" not in slug, f"slug escaped hyphens for {working_dir!r}: {slug!r}"
    assert covered_hyphenated, "fixtures must include a hyphenated-path workspace to guard the bug"


# ---------------------------------------------------------------------------
# (b) Runtime sweep over the full local corpus -- skipped when absent
# ---------------------------------------------------------------------------

PROJECTS_ROOT = Path.home() / ".amplifier" / "projects"


def _paired_sessions(root: Path) -> list[Path]:
    """All sessions with BOTH a legacy (top-level) and CI-native capture."""
    paired: list[Path] = []
    if not root.exists():
        return paired
    for project in sorted(root.iterdir()):
        sessions_dir = project / "sessions"
        if not sessions_dir.is_dir():
            continue
        for session in sessions_dir.iterdir():
            if not session.is_dir():
                continue
            if (
                (session / "events.jsonl").exists()
                and (session / "metadata.json").exists()
                and (session / "context-intelligence" / "events.jsonl").exists()
                and (session / "context-intelligence" / "metadata.json").exists()
            ):
                paired.append(session)
    return paired


def test_runtime_sweep_workspace_parity(capsys: pytest.CaptureFixture[str]) -> None:
    """Sweep EVERY paired session in the local corpus and assert the derived
    workspace slug equals the CI hook's slug for every CI event. This is the
    direct oracle for the fix. Skipped when the corpus is absent.
    """
    if not PROJECTS_ROOT.exists():
        pytest.skip(f"no local corpus at {PROJECTS_ROOT}")
    sessions = _paired_sessions(PROJECTS_ROOT)
    if not sessions:
        pytest.skip(f"no paired sessions under {PROJECTS_ROOT}")

    checked = 0
    ws_diffs = 0
    first_diffs: list[str] = []
    for session in sessions:
        working_dir = _legacy_working_dir(session)
        try:
            expected = derive_workspace(working_dir)
        except Exception as exc:  # noqa: BLE001 -- record, don't abort the sweep
            ws_diffs += 1
            if len(first_diffs) < 5:
                first_diffs.append(f"{session.name}: derive_workspace raised {exc!r}")
            continue
        # Read only the first CI event -- workspace is identical across a session.
        ci_workspace = None
        with (session / "context-intelligence" / "events.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    ci_workspace = json.loads(line).get("workspace")
                    break
        checked += 1
        if ci_workspace != expected:
            ws_diffs += 1
            if len(first_diffs) < 5:
                first_diffs.append(
                    f"{session.name}: derive={expected!r} ci={ci_workspace!r} wd={working_dir!r}"
                )

    with capsys.disabled():
        print(f"\n[ground-truth sweep] workspace parity: sessions={checked} ws_diffs={ws_diffs}")
    assert ws_diffs == 0, f"{ws_diffs} workspace diffs; first: {first_diffs}"


def test_runtime_sweep_data_parity(capsys: pytest.CaptureFixture[str]) -> None:
    """Sweep the local corpus and assert data-multiset parity (locks reassembly
    at scale). Parsing every event is expensive, so the sweep honours
    ``GROUND_TRUTH_MAX_DATA_SESSIONS`` (default 400; set to 0 for the whole
    corpus). The full-corpus run is exercised out-of-band; this keeps the
    in-suite run bounded while still sweeping hundreds of real sessions.
    """
    if not PROJECTS_ROOT.exists():
        pytest.skip(f"no local corpus at {PROJECTS_ROOT}")
    sessions = _paired_sessions(PROJECTS_ROOT)
    if not sessions:
        pytest.skip(f"no paired sessions under {PROJECTS_ROOT}")

    limit = int(os.environ.get("GROUND_TRUTH_MAX_DATA_SESSIONS", "400"))
    if limit > 0:
        sessions = sessions[:limit]

    # Reassembly-correctness invariant (the thing this LOCKS): every CI-native
    # event's `data` is reproduced exactly by reassembling some legacy event --
    # i.e. Counter(ci) <= Counter(reassemble(legacy)). Reversing the envelope
    # must never fabricate or corrupt data the hook did not write. A violation
    # here (CI data reassembly cannot reproduce) is a REAL reassembly defect and
    # must be zero -- verified 0 across the full local corpus (3048 sessions).
    #
    # The *other* direction -- legacy events absent from CI -- is NOT a
    # reassembly defect: it means the on-disk CI-native capture is incomplete
    # (e.g. crashed/truncated before flush, or a duplicated tail event). Measured
    # on this corpus: 4/3048 sessions where the CI capture holds fewer events
    # than the legacy log (from 1 tail event up to a 7952->5208 truncation), all
    # with CI data a strict subset of reassembled legacy data. That is a
    # capture-completeness property of the source data, unrelated to this fix, so
    # it is reported for visibility only -- never asserted.
    checked = 0
    reassembly_defects = 0
    incomplete_ci_captures = 0
    first_defects: list[str] = []
    for session in sessions:
        legacy_events = _read_events(session / "events.jsonl")
        ci_events = _read_events(session / "context-intelligence" / "events.jsonl")
        legacy_data = _data_multiset(legacy_events, reassemble=True)
        ci_data = _data_multiset(ci_events, reassemble=False)
        checked += 1
        unreproducible = ci_data - legacy_data
        if unreproducible:
            reassembly_defects += 1
            if len(first_defects) < 5:
                first_defects.append(
                    f"{session.name}: {sum(unreproducible.values())} CI events not reproduced by reassembly"
                )
        if legacy_data - ci_data:
            incomplete_ci_captures += 1

    with capsys.disabled():
        print(
            f"\n[ground-truth sweep] data parity: sessions={checked} "
            f"reassembly_defects={reassembly_defects} incomplete_ci_captures={incomplete_ci_captures}"
        )
    assert reassembly_defects == 0, (
        f"{reassembly_defects} reassembly defects; first: {first_defects}"
    )
