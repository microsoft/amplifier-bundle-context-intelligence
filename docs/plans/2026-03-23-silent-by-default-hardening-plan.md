# Silent-by-Default Hardening Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Harden `hook-context-intelligence` so its "silent by default" contract is unbreakable — two code fixes, three doc/diagram label corrections, and four tests that lock each guarantee.

**Architecture:** Single Python hook module (`LoggingHandler`) where disk writing is always-on and server dispatch is optional. Two `logger.exception()` calls currently emit ERROR-level output with full tracebacks — downgrading them eliminates the only ways the module can pollute a user session. Three doc files incorrectly label `logger.debug()` paths as `logger.warning()` — label corrections make docs honest about what the code already does.

**Tech Stack:** Python 3.11+, pytest, unittest.mock, asyncio, pathlib. No new dependencies.

---

## Working Directory

All commands run from:
```
/home/dicolomb/amplifier-context-intelligence-warnings-removal
```

The submodule lives at `amplifier-bundle-context-intelligence/` within that directory.

---

## Pre-Flight: Verify the Baseline

Before touching anything, confirm the test suite is green:

```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence
python -m pytest ../../tests/ -q
```

Expected: all existing tests pass. If anything is already red, stop and investigate before proceeding.

---

## Task 1: Write the Four Tests (No Code Changes Yet)

**File to create:**
- `amplifier-bundle-context-intelligence/tests/test_silent_by_default.py`

**Step 1: Create the test file**

Create the file at the exact path above with this complete content:

```python
"""Tests locking the silent-by-default contract for LoggingHandler.

Four guarantees under test:
1. Disk always writes even without server config (always-on JSONL)
2. No server config → zero log records at WARNING or above
3. URL set, no API key → zero log records at WARNING or above
4. Disk I/O error → exactly one WARNING record, zero ERROR or above
"""

import asyncio
import logging
import types
from pathlib import Path

import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import LoggingHandler

_LOGGER_NAME = "amplifier_module_hook_context_intelligence"


def _make_resolver(tmp_path, *, server_url=None, api_key=None):
    """Build a minimal SimpleNamespace resolver for LoggingHandler.__init__.

    LoggingHandler uses getattr(resolver, attr, default) for all resolver
    access, so a SimpleNamespace with the required attributes is sufficient.
    """
    return types.SimpleNamespace(
        context_intelligence_server_url=server_url,
        context_intelligence_api_key=api_key,
        workspace="test-workspace",
        dispatch_timeout=10.0,
        dispatch_failure_threshold=3,
        dispatch_queue_capacity=256,
        close_drain_timeout=0.5,
        session_dir=lambda session_id: tmp_path / session_id / "context-intelligence",
    )


class TestDiskAlwaysWrites:
    """Guarantee 1: disk writes happen regardless of server configuration."""

    def test_disk_always_writes_without_server_config(self, tmp_path):
        """events.jsonl is written even when no server URL is configured.

        This test should be GREEN before any code changes — it documents
        existing correct behavior.
        """
        resolver = _make_resolver(tmp_path)
        handler = LoggingHandler(resolver)

        assert handler._dispatch_enabled is False, (
            "_dispatch_enabled should be False when no server URL is set"
        )

        data = {"session_id": "test-session-001", "timestamp": "2026-01-01T00:00:00"}
        asyncio.run(handler.__call__("tool:pre", data))

        expected_file = tmp_path / "test-session-001" / "context-intelligence" / "events.jsonl"
        assert expected_file.exists(), (
            f"events.jsonl was not written at {expected_file}. "
            "Disk writing must be unconditional regardless of server config."
        )


class TestSilentWithoutServerConfig:
    """Guarantee 2: no log noise when server is not configured."""

    def test_no_server_config_emits_nothing_above_debug(self, tmp_path, caplog):
        """No WARNING or above records when no server URL is set.

        This test should be GREEN before any code changes.
        """
        resolver = _make_resolver(tmp_path)
        handler = LoggingHandler(resolver)

        data = {"session_id": "test-session-002", "timestamp": "2026-01-01T00:00:00"}

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            asyncio.run(handler.__call__("tool:pre", data))

        noisy_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(noisy_records) == 0, (
            f"Expected zero WARNING+ records but got {len(noisy_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in noisy_records)
        )

    def test_url_without_key_emits_nothing_above_debug(self, tmp_path, caplog):
        """URL set but no API key → dispatch disabled silently, zero WARNING+ records.

        This test should be GREEN before any code changes.
        """
        resolver = _make_resolver(tmp_path, server_url="http://localhost:9999")
        handler = LoggingHandler(resolver)

        assert handler._dispatch_enabled is False, (
            "_dispatch_enabled should be False when server URL is set but api_key is missing"
        )

        data = {"session_id": "test-session-003", "timestamp": "2026-01-01T00:00:00"}

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            asyncio.run(handler.__call__("tool:pre", data))

        noisy_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(noisy_records) == 0, (
            f"Expected zero WARNING+ records but got {len(noisy_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in noisy_records)
        )


class TestDiskErrorEmitsWarning:
    """Guarantee 3: disk I/O errors produce a WARNING, never an ERROR or traceback."""

    def test_disk_error_emits_warning_not_error(self, tmp_path, monkeypatch, caplog):
        """A PermissionError on mkdir emits exactly one WARNING, zero ERROR or above.

        THIS IS THE RED TEST before the fix.

        Before the fix (logging_handler.py line 148 uses logger.exception()):
          - logger.exception() emits at ERROR level with a full traceback
          - This test FAILS because there is an ERROR-level record

        After the fix (changed to logger.warning()):
          - logger.warning() emits at WARNING level, no traceback
          - This test PASSES
        """
        resolver = _make_resolver(tmp_path)
        handler = LoggingHandler(resolver)

        def raise_permission_error(*args, **kwargs):
            raise PermissionError("disk full")

        monkeypatch.setattr(Path, "mkdir", raise_permission_error)

        data = {"session_id": "test-session-004", "timestamp": "2026-01-01T00:00:00"}

        with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
            asyncio.run(handler.__call__("tool:pre", data))

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(error_records) == 0, (
            f"Expected zero ERROR+ records but got {len(error_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in error_records)
            + "\nHint: logger.exception() emits at ERROR. Change it to logger.warning()."
        )

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 1, (
            f"Expected exactly one WARNING record but got {len(warning_records)}: "
            + ", ".join(f"{r.levelname}: {r.message}" for r in warning_records)
        )
        assert "disk write error" in warning_records[0].message, (
            f"WARNING message should contain 'disk write error' but got: "
            f"'{warning_records[0].message}'"
        )
```

**Step 2: Verify the test file exists**
```bash
ls -la /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/tests/test_silent_by_default.py
```
Expected: file exists, non-zero size.

**Step 3: Commit the test file**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git add amplifier-bundle-context-intelligence/tests/test_silent_by_default.py
git commit -m "test: add four silent-by-default contract tests (one red)"
```

---

## Task 2: Run All Four Tests — Verify 3 Pass, 1 Fails

**Step 1: Run the new tests**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence
python -m pytest ../../tests/test_silent_by_default.py -v
```

**Expected output (exact pattern):**
```
PASSED tests/test_silent_by_default.py::TestDiskAlwaysWrites::test_disk_always_writes_without_server_config
PASSED tests/test_silent_by_default.py::TestSilentWithoutServerConfig::test_no_server_config_emits_nothing_above_debug
PASSED tests/test_silent_by_default.py::TestSilentWithoutServerConfig::test_url_without_key_emits_nothing_above_debug
FAILED tests/test_silent_by_default.py::TestDiskErrorEmitsWarning::test_disk_error_emits_warning_not_error
```

3 passed, 1 failed.

**Step 2: Read the failure message**

The failure for `test_disk_error_emits_warning_not_error` should say something like:
```
AssertionError: Expected zero ERROR+ records but got 1: ERROR: LoggingHandler error processing tool:pre
```

This confirms `logger.exception()` is emitting at ERROR level. That is the bug we are about to fix.

**If the wrong tests fail, stop.** Do not proceed until the failure matches exactly `test_disk_error_emits_warning_not_error` and only that test.

---

## Task 3: Fix `logging_handler.py` Line 148

**File to modify:**
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py
```

**Step 1: Verify the current line 148**
```bash
sed -n '145,152p' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py
```
Expected to see (around line 148):
```python
        except Exception:
            logger.exception("LoggingHandler error processing %s", event)
```

**Step 2: Apply the fix**

Change line 148 from:
```python
            logger.exception("LoggingHandler error processing %s", event)
```
to:
```python
            logger.warning("LoggingHandler disk write error processing %s", event)
```

The surrounding `try/except` block (lines 127–148) should look like this after the edit:
```python
        try:
            session_id = sanitized_data.get("session_id")
            if not session_id:
                return HookResult(action="continue")

            session_dir = self._session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)

            # Lazy metadata init: create metadata.json on the very first
            # event we see for a given session_id, regardless of event type.
            if session_id not in self._seen_sessions:
                self._seen_sessions.add(session_id)
                self._ensure_metadata(session_dir, session_id, sanitized_data)

            if event in ("session:start", "session:fork"):
                self._enrich_metadata_from_session_init(session_dir, session_id, sanitized_data)
            elif event in ("session:end", "execution:end"):
                self._finalize_metadata(session_dir, sanitized_data)

            self._append_event(session_dir, event, sanitized_data)
        except Exception:
            logger.warning("LoggingHandler disk write error processing %s", event)
```

Two things changed on that one line:
1. `exception` → `warning` (eliminates ERROR-level emission and traceback)
2. `"LoggingHandler error processing %s"` → `"LoggingHandler disk write error processing %s"` (message now names the error class explicitly)

**Step 3: Verify the edit landed correctly**
```bash
sed -n '145,152p' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py
```
Expected:
```python
        except Exception:
            logger.warning("LoggingHandler disk write error processing %s", event)
```

---

## Task 4: Run All Four Tests — Verify All Pass

**Step 1: Run the tests**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence
python -m pytest ../../tests/test_silent_by_default.py -v
```

**Expected: 4 passed, 0 failed.**

```
PASSED tests/test_silent_by_default.py::TestDiskAlwaysWrites::test_disk_always_writes_without_server_config
PASSED tests/test_silent_by_default.py::TestSilentWithoutServerConfig::test_no_server_config_emits_nothing_above_debug
PASSED tests/test_silent_by_default.py::TestSilentWithoutServerConfig::test_url_without_key_emits_nothing_above_debug
PASSED tests/test_silent_by_default.py::TestDiskErrorEmitsWarning::test_disk_error_emits_warning_not_error
```

**If any test still fails, stop.** Do not proceed to the next task until all four pass.

**Step 2: Commit the fix**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git add amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py
git commit -m "fix(logging_handler): exception→warning for disk write errors (no traceback)"
```

---

## Task 5: Fix `__init__.py` Line 90

**File to modify:**
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py
```

**Step 1: Verify the current line 90**
```bash
sed -n '87,96p' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py
```
Expected to see:
```python
        try:
            await logging_handler.close()
        except Exception:
            log.exception("LoggingHandler.close() failed during cleanup")
```

**Step 2: Apply the fix**

Change line 90 from:
```python
            log.exception("LoggingHandler.close() failed during cleanup")
```
to:
```python
            log.debug("LoggingHandler.close() failed during cleanup")
```

The `cleanup()` function block (lines 84–100) should look like this after the edit:
```python
    async def cleanup() -> None:
        # Drain pending dispatch tasks and close the HTTP client *before*
        # unregistering hooks — this gives in-flight POSTs a chance to land.
        try:
            await logging_handler.close()
        except Exception:
            log.debug("LoggingHandler.close() failed during cleanup")

        for unreg in unregister_fns:
            try:
                unreg()
            except Exception:
                pass
        try:
            coordinator.register_capability("context_intelligence.config_resolver", None)
        except Exception:
            pass
```

**Step 3: Verify the edit**
```bash
sed -n '87,96p' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py
```
Expected:
```python
        try:
            await logging_handler.close()
        except Exception:
            log.debug("LoggingHandler.close() failed during cleanup")
```

**Step 4: Commit the fix**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git add amplifier-bundle-context-intelligence/modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py
git commit -m "fix(__init__): exception→debug for cleanup failure (not user-actionable)"
```

---

## Task 6: Fix `README.md` — Circuit Breaker Section

**File to modify:**
```
amplifier-bundle-context-intelligence/README.md
```

**Step 1: Search for `logger.warning` in README**
```bash
grep -n "logger.warning" /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/README.md
```

**Expected: zero results.** The README does not contain the literal string `logger.warning(`. The inaccuracy is expressed in plain English prose, not as a code snippet.

**Step 2: Find the circuit breaker section**
```bash
grep -n "warning is emitted\|One clear warning\|logger.debug\|logger.warning\|circuit" /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/README.md
```

You will find a line that reads:
```
3. One clear warning is emitted:
```
(currently at line 138)

**Step 3: Read the full circuit breaker section to understand context**
```bash
sed -n '134,145p' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/README.md
```

You will see:
```markdown
### Circuit breaker

1. Every failed dispatch (network error or non-2xx response) increments the consecutive failure counter.
2. Once the counter reaches `dispatch_failure_threshold`, dispatch is permanently disabled for the session.
3. One clear warning is emitted:
   > `Context intelligence server unreachable after N attempts — dispatch disabled for this session. Local JSONL capture continues.`
4. Subsequent events are silently skipped (no further log noise); local JSONL capture continues unaffected.
```

**Step 4: Apply the fix**

Change line 138 from:
```markdown
3. One clear warning is emitted:
```
to:
```markdown
3. One debug message is emitted (only visible when log level is set to DEBUG):
```

The circuit breaker section should look like this after the edit:
```markdown
### Circuit breaker

1. Every failed dispatch (network error or non-2xx response) increments the consecutive failure counter.
2. Once the counter reaches `dispatch_failure_threshold`, dispatch is permanently disabled for the session.
3. One debug message is emitted (only visible when log level is set to DEBUG):
   > `Context intelligence server unreachable after N attempts — dispatch disabled for this session. Local JSONL capture continues.`
4. Subsequent events are silently skipped (no further log noise); local JSONL capture continues unaffected.
```

**Step 5: Verify the edit**
```bash
sed -n '134,145p' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/README.md
```
Confirm "One debug message is emitted (only visible when log level is set to DEBUG):" is on the relevant line and "One clear warning is emitted:" is gone.

**Step 6: Confirm no remaining incorrect warning references in the circuit breaker context**
```bash
grep -n "warning is emitted\|One clear warning" /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/README.md
```
Expected: zero results.

**Step 7: Commit**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git add amplifier-bundle-context-intelligence/README.md
git commit -m "docs(README): correct circuit breaker log level description to debug"
```

---

## Task 7: Fix `docs/dispatch-circuit-breaker.dot` — Three Node Labels

**File to modify:**
```
amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot
```

**Step 1: Confirm the three incorrect labels**
```bash
grep -n "logger.warning" /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot
```

Expected output — exactly three lines:
```
47:            label="queue full\n─────────────────────\n_dispatch_enabled = False\nlogger.warning(\n  \"server_dispatch_queue_full\"\n)\nlocal JSONL capture continues",
116:            label="on_failure\n─────────────────────\n_consecutive_failures += 1\nlogger.warning(\n  \"server_dispatch_failed:\n  attempt N/threshold\n  event=... url=...\"\n)\",
133:            label="TRIP circuit breaker\n─────────────────────\n_dispatch_enabled = False\nlogger.warning(\n  \"server unreachable after\n  N attempts — dispatch\n  disabled for session\"\n)",
```

**Step 2: Replace all three occurrences in one command**

Run this sed command (replaces every `logger.warning(` with `logger.debug(` throughout the file):
```bash
sed -i 's/logger\.warning(/logger.debug(/g' /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot
```

**Step 3: Verify — zero `logger.warning` remain**
```bash
grep -n "logger.warning" /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot
```
Expected: zero results (no output).

**Step 4: Verify — three `logger.debug` now present where warnings were**
```bash
grep -n "logger.debug" /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot
```
Expected: exactly three results on lines 47, 116, and 133.

**Step 5: Sanity-check the file is still valid DOT syntax (optional but recommended)**
```bash
which dot && dot -Tsvg /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot -o /tmp/dispatch-circuit-breaker-check.svg && echo "DOT syntax OK" || echo "dot not available — skip"
```

**Step 6: Commit**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git add amplifier-bundle-context-intelligence/docs/dispatch-circuit-breaker.dot
git commit -m "docs(dispatch-circuit-breaker.dot): correct 3 node labels warning→debug"
```

---

## Task 8: Run Full Test Suite — Verify No Regressions

**Step 1: Run all bundle-level tests**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence
python -m pytest ../../tests/ -v
```

**Expected:** Every test that was green before Task 1 is still green, **plus** all four new tests pass. Zero failures. Zero errors.

**Step 2: Confirm the four new tests are included**

In the output, verify these four tests appear and are marked PASSED:
```
PASSED ...::TestDiskAlwaysWrites::test_disk_always_writes_without_server_config
PASSED ...::TestSilentWithoutServerConfig::test_no_server_config_emits_nothing_above_debug
PASSED ...::TestSilentWithoutServerConfig::test_url_without_key_emits_nothing_above_debug
PASSED ...::TestDiskErrorEmitsWarning::test_disk_error_emits_warning_not_error
```

**If any pre-existing test has turned red, stop and investigate.** Do not proceed to the commit in Task 9.

---

## Task 9: Final Commit

**Step 1: Verify the staged files are exactly right**
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git status
```

At this point, all five changed files should already be committed in individual commits from Tasks 3–7. Confirm with:
```bash
git log --oneline -6
```

Expected recent commits (newest first):
```
<hash> docs(dispatch-circuit-breaker.dot): correct 3 node labels warning→debug
<hash> docs(README): correct circuit breaker log level description to debug
<hash> fix(__init__): exception→debug for cleanup failure (not user-actionable)
<hash> fix(logging_handler): exception→warning for disk write errors (no traceback)
<hash> test: add four silent-by-default contract tests (one red)
```

**Step 2: Squash all five commits into one**

If you prefer a single atomic commit (recommended for this plan), squash them:
```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal
git rebase -i HEAD~5
```

In the interactive rebase editor, change all lines except the first from `pick` to `squash` (or `s`). Save. The commit message editor will open — replace the entire content with:

```
fix: harden silent-by-default contract — demote exception() calls, align docs

- logging_handler: exception→warning for disk errors (no traceback in user session)
- __init__: exception→debug for cleanup failure (not user-actionable)
- README: logger.warning() refs corrected to logger.debug()
- dispatch-circuit-breaker.dot: 3 node labels corrected
- tests: 4 new tests locking the silent-by-default guarantee
```

**Step 3: Verify final state**
```bash
git log --oneline -3
```

The top commit should be the squashed "fix: harden silent-by-default contract…" commit.

```bash
cd /home/dicolomb/amplifier-context-intelligence-warnings-removal/amplifier-bundle-context-intelligence/modules/hook-context-intelligence
python -m pytest ../../tests/ -q
```

All tests must pass. If they do, the work is complete.

---

## File Change Summary

| File | Change |
|------|--------|
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/logging_handler.py` | Line 148: `exception(` → `warning("…disk write error…"` |
| `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/__init__.py` | Line 90: `exception(` → `debug(` |
| `README.md` | Line 138: `"One clear warning is emitted:"` → `"One debug message is emitted (only visible when log level is set to DEBUG):"` |
| `docs/dispatch-circuit-breaker.dot` | Lines 47, 116, 133: `logger.warning(` → `logger.debug(` in three node labels |
| `tests/test_silent_by_default.py` | New file — four tests |

## Test Expectations Before and After the Fix

| Test | Before fix | After fix |
|------|-----------|-----------|
| `test_disk_always_writes_without_server_config` | PASS | PASS |
| `test_no_server_config_emits_nothing_above_debug` | PASS | PASS |
| `test_url_without_key_emits_nothing_above_debug` | PASS | PASS |
| `test_disk_error_emits_warning_not_error` | **FAIL** | PASS |
