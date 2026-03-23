# Silent-by-Default Hardening Design

## Goal

Harden the `hook-context-intelligence` module so that its "silent by default" contract is unbreakable and consistently documented across code, README, and diagrams — guaranteeing three properties before the bundle ships live:

1. Default behavior without server configuration always writes to disk (flat file)
2. When server configuration is absent, zero warnings or text appear in the app-cli screen
3. When errors occur or the circuit breaker triggers, user experience is not polluted

## Context

This is a proactive hardening exercise — no observed bug triggered it. Codebase exploration revealed that the runtime behavior is already almost correct:

- Disk writing (`events.jsonl` + `metadata.json`) is unconditional — always runs regardless of server config.
- Server dispatch is gated on `self._server_url and self._dispatch_enabled` — when no URL is set, the entire server path is skipped.
- All failure messages (dispatch failures, circuit-breaker trip, queue-full, missing API key) already use `logger.debug()` — silent at INFO/WARNING levels.

Two gaps remain:

- **Code gap:** Two `logger.exception()` calls emit ERROR-level output with full tracebacks — one on disk I/O failure in `handlers/logging_handler.py`, one on cleanup failure in `__init__.py`. These can pollute the user's session.
- **Documentation gap:** The README and both DOT diagrams (`dispatch-circuit-breaker.dot`, `logging-handler-flow.dot`) incorrectly document `logger.warning()` for the circuit-breaker and queue-full paths that actually use `logger.debug()`.

## Chosen Approach

**Option A — Documentation + diagram alignment + ERROR→WARNING for disk errors.**

The code's `logger.debug()` calls are already correct. Fix the README and two DOT diagrams to match the code. The only real noise risk is the `logger.exception()` (ERROR + full traceback) that fires on disk I/O failures — downgrade that to `logger.warning()` so operators see a single line, not a stacktrace. Downgrade the cleanup exception to `logger.debug()` since cleanup failures at session teardown are not user-actionable.

No new abstractions, no architectural changes, no new dependencies.

## Code Changes

Two files change. The disk writing path, server dispatch guard, circuit-breaker logic, and all existing `logger.debug()` calls are untouched.

### `handlers/logging_handler.py`

One change:

```python
# Before
logger.exception("LoggingHandler error processing %s", event)

# After
logger.warning("LoggingHandler disk write error processing %s", event)
```

Downgrade from ERROR + traceback to a single WARNING line. The message is renamed to explicitly identify this as a disk error, not a dispatch error. If a disk write fails the operator sees one clean line, not a stacktrace in the user's session.

All other log calls in this file are already `DEBUG` and stay `DEBUG`. No changes to the circuit-breaker path, queue-full path, missing-key path, or dispatch-failure path.

### `__init__.py`

One change:

```python
# Before
log.exception("LoggingHandler.close() failed during cleanup")

# After
log.debug("LoggingHandler.close() failed during cleanup")
```

Cleanup happens at session teardown; an exception here is not user-actionable. Dropping to `DEBUG` keeps teardown completely silent.

### What Is Explicitly Not Changing

- The disk write path — unconditional, always runs
- The `_server_url` guard — already correct
- All circuit-breaker, queue-full, and missing-key `logger.debug()` calls — already correct
- The `log_level` property in `ConfigResolver` — its default (`WARNING`) is fine since the module doesn't call `logging.setLevel()` directly

## Documentation Changes

Three files updated to match the code's `logger.debug()` reality. No new concepts introduced, no diagram restructuring — purely making the docs honest about what the code already does.

### `README.md`

- The section describing circuit-breaker behavior currently says a `logger.warning()` is emitted when the threshold is hit. Change to `logger.debug()` and note it is only visible when log level is DEBUG.
- Any mention of `logger.warning()` for queue-full or missing API key corrected to `logger.debug()`.

### `docs/dispatch-circuit-breaker.dot`

The two nodes labelled `logger.warning(...)` (one for queue-full disable, one for the circuit-trip) are relabelled to `logger.debug(...)`. No structural changes — label corrections only.

### `docs/logging-handler-flow.dot`

Same treatment: any node or edge label that says `logger.warning(...)` in the failure path is corrected to `logger.debug(...)`.

## Testing Strategy

Four test cases added to the existing test suite. All are unit-level — no network, no filesystem mocking beyond what already exists. Existing tests are untouched; these four are purely additive.

### Test 1 — No server config → disk always writes

Instantiate `LoggingHandler` with no `context_intelligence_server_url`. Fire a sample event. Assert that `events.jsonl` was written to disk. Assert `_dispatch_enabled` is `False` and the server enqueue path was never called. Confirms guarantee 1.

### Test 2 — No server config → nothing above DEBUG logged

Same setup. Use `unittest.mock.patch` to capture log records emitted during the call. Assert zero records at WARNING or above. Confirms guarantee 2.

### Test 3 — URL set, no API key → nothing above DEBUG logged

Instantiate with `context_intelligence_server_url` set but no `context_intelligence_api_key`. Assert `_dispatch_enabled` is `False` pre-emptively. Assert zero log records at WARNING or above during a call. Confirms the edge case stays silent.

### Test 4 — Dispatch failure / circuit breaker → nothing above WARNING logged

Instantiate with a URL and key. Make `_dispatch_to_server` raise repeatedly until the circuit trips. Assert no log records above WARNING (the disk error path is WARNING; the dispatch failure path stays DEBUG). Confirms guarantee 3.

## Open Questions

None. The design is fully scoped to label corrections, two log-level changes, and four additive tests.
