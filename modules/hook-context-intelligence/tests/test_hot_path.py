"""
HOT-PATH PROOF — transitive AST allowlist gate for enqueue().

enqueue() must add zero cost to the hook's hot path.  This is proved
structurally via a transitive AST allowlist:

  * ALLOWLIST, not denylist — any call NOT in _ALLOWED_HOT_PATH_CALLS fails
    the structural gate immediately.  A denylist is trivially defeated by
    renaming; an allowlist is not.

  * TRANSITIVE — recurse into every same-class non-coroutine method the hot
    path calls (e.g. _ensure_worker, which enqueue() calls on every
    invocation).  Coroutine functions (e.g. _worker) are scheduled via
    asyncio.create_task — they are NOT run inline and are recorded as named
    calls but NOT recursed into.

  * REQUIRED RED PROOF — before treating this gate as valid, a real blocking
    call was injected into the hot path, the test was observed to FAIL for the
    right reason, then the injection was reverted and the test confirmed green.
    A proof you never saw fail is a ritual.

The nightly wall-clock micro-benchmark is a separate, non-gating check.
Wall-clock assertions are flaky on shared CI (scheduler variance), so they
do NOT block the required gate.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import statistics
import time
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Allowlist — the ONLY call names permitted in enqueue() and every synchronous
# method it transitively calls (coroutine methods excepted).
#
# To add an entry: justify why the call is genuinely zero-cost on the hot path.
# ---------------------------------------------------------------------------
_ALLOWED_HOT_PATH_CALLS: frozenset[str] = frozenset(
    {
        # enqueue() → _ensure_worker() (same-class sync method; recursed into)
        "_ensure_worker",
        # enqueue() → asyncio.Queue.put_nowait()
        "put_nowait",
        # _ensure_worker() → asyncio.Task.done()
        "done",
        # _ensure_worker() → asyncio.create_task()
        "create_task",
        # _ensure_worker() → self._worker() [coroutine: scheduled, NOT entered]
        "_worker",
    }
)


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _get_class_methods(
    tree: ast.AST, class_name: str
) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return {method_name: node} for all methods of *class_name* in *tree*."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                n.name: n
                for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return {}


def _calls_in_function(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Collect all Call attribute/name strings in *func_node* (non-recursive)."""
    calls: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                calls.add(func.attr)
            elif isinstance(func, ast.Name):
                calls.add(func.id)
    return calls


def _hot_path_calls_transitive(
    tree: ast.AST,
    cls: type,
    class_name: str,
    entry: str,
) -> set[str]:
    """Return all call names transitively reachable from *entry*.

    Rules
    -----
    * Walk the AST of *entry* and collect every Call node's function name.
    * If a collected name resolves to a same-class **non-coroutine** method,
      recurse into it.
    * Coroutine methods (``async def``) are recorded as call names but are NOT
      recursed into — they are scheduled via ``asyncio.create_task``, not run
      inline on the hot path.
    * The entry method itself is excluded from the returned set (it is the
      method under analysis, not a call it makes).
    """
    methods = _get_class_methods(tree, class_name)
    visited: set[str] = set()
    all_calls: set[str] = set()

    def _visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        func_node = methods.get(name)
        if func_node is None:
            return
        for call in _calls_in_function(func_node):
            all_calls.add(call)
            # Recurse into same-class, synchronous-only methods.
            if call in methods and call not in visited:
                real_fn = getattr(cls, call, None)
                if real_fn is not None and not asyncio.iscoroutinefunction(real_fn):
                    _visit(call)

    _visit(entry)
    all_calls.discard(entry)  # entry is not "called by" itself
    return all_calls


# ---------------------------------------------------------------------------
# PRIMARY GATE — transitive AST allowlist (required; blocks CI on failure)
# ---------------------------------------------------------------------------


def test_enqueue_hot_path_transitive_ast_allowlist() -> None:
    """enqueue() and every sync method it transitively calls must make only
    allowlisted calls.

    Design rationale
    ----------------
    ALLOWLIST: any new call added to the hot path fails this test until it is
    explicitly added to ``_ALLOWED_HOT_PATH_CALLS``.  A denylist can be
    circumvented by renaming; an allowlist cannot — every call is rejected by
    default.

    TRANSITIVE: the test recurses into ``_ensure_worker`` (which enqueue()
    calls on *every* invocation) but stops at ``_worker`` (a coroutine
    function — it is scheduled, not run inline).

    RED PROOF (performed during development):
    An injection of ``time.sleep(0)`` into ``enqueue()`` caused this test to
    fail with violation ``{'sleep'}``.  The injection was reverted and the
    test returned green.  The gate is confirmed defeat-resistant.
    """
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
        _DestinationDispatcher,
    )

    module = inspect.getmodule(_DestinationDispatcher)
    assert module is not None, "Cannot resolve module for _DestinationDispatcher"
    source = inspect.getsource(module)
    tree = ast.parse(source)

    all_calls = _hot_path_calls_transitive(
        tree, _DestinationDispatcher, "_DestinationDispatcher", "enqueue"
    )
    violations = all_calls - _ALLOWED_HOT_PATH_CALLS

    assert not violations, (
        f"Hot-path violation: enqueue() transitively reaches non-allowlisted "
        f"call(s): {sorted(violations)!r}.\n"
        f"Full transitive call set: {sorted(all_calls)!r}\n"
        f"Allowed:                  {sorted(_ALLOWED_HOT_PATH_CALLS)!r}\n"
        "Only add to _ALLOWED_HOT_PATH_CALLS if the call is genuinely zero-cost."
    )


# ---------------------------------------------------------------------------
# NIGHTLY — wall-clock micro-benchmark (non-gating, excluded from default CI)
# ---------------------------------------------------------------------------


@pytest.mark.nightly
async def test_enqueue_p99_wall_clock_nightly() -> None:
    """Wall-clock micro-benchmark: p99 latency of enqueue() on a hot queue.

    Marked ``@pytest.mark.nightly`` — NOT part of the required CI gate.
    Wall-clock assertions are flaky on shared CI due to scheduler variance.

    This benchmark characterises performance; it does not hard-fail on timing
    except for a loose 10 ms sanity bound (any realistic hardware beats this).

    Run with::

        uv run pytest tests/test_hot_path.py -m nightly -v -s

    Excluded from the default gate::

        uv run pytest -m 'not nightly'
    """
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
        _DestinationDispatcher,
    )

    d = _DestinationDispatcher(
        name="bench",
        url="http://localhost:9999",
        api_key="bench-key",
        workspace="bench-ws",
        dispatch_timeout=10.0,
        failure_threshold=5,
        queue_capacity=10_000,
        close_drain_timeout=0.0,
    )

    event_data: dict[str, Any] = {
        "session_id": "bench",
        "timestamp": "2024-01-01T00:00:00Z",
    }

    # Pre-seed the worker task so _ensure_worker() never calls create_task
    # during the timed section (we measure the steady-state hot path, not
    # the first-call path that also creates the asyncio.Task).
    #
    # Using a no-op sleep task avoids any real HTTP connections.
    sentinel = asyncio.create_task(asyncio.sleep(9999))
    d._worker_task = sentinel

    n = 10_000
    times: list[float] = []

    try:
        for _ in range(n):
            t0 = time.perf_counter()
            d.enqueue("bench:event", event_data)
            times.append(time.perf_counter() - t0)
    except asyncio.QueueFull:
        # Queue capacity reached — timing samples are still valid.
        pass

    # Teardown: cancel the sentinel task before exiting the async context.
    sentinel.cancel()
    try:
        await sentinel
    except (asyncio.CancelledError, Exception):
        pass

    assert times, "No timing samples collected — benchmark did not run"

    sorted_times = sorted(times)
    p50_us = statistics.median(times) * 1e6
    p99_us = sorted_times[int(len(times) * 0.99)] * 1e6
    p_max_us = sorted_times[-1] * 1e6

    # Informational output — visible only with -s / --capture=no.
    print(
        f"\nenqueue() wall-clock (n={len(times):,}):"
        f"  p50={p50_us:.2f}µs"
        f"  p99={p99_us:.2f}µs"
        f"  max={p_max_us:.2f}µs"
    )

    # Sanity bound only: p99 must be under 10 ms on any reasonable hardware.
    # This is intentionally very loose — it catches absurd regressions while
    # remaining stable on loaded CI machines.
    assert p99_us < 10_000, (
        f"p99 {p99_us:.2f}µs exceeds 10 ms sanity bound — "
        "enqueue() may have acquired a blocking call"
    )
