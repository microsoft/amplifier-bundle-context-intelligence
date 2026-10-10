"""Offline (network-unavailable) classification and credential-log capture.

A network outage must queue events quietly, never look like an auth failure.
This module holds the two small mechanisms the dispatcher needs for that:

* ``is_offline_error`` -- decide whether an exception (plus any captured
  credential-library log text) is caused by the network being unavailable
  rather than by a genuine credential rejection.
* ``capture_credential_logs`` -- while the auth strategy mints a token, divert
  ``azure.*`` / ``msal.*`` log records into a list instead of the terminal.
  azure-identity logs the full ``az`` CLI traceback at WARNING on every failed
  ``get_token``; during an outage that is thousands of lines of spam.
"""

from __future__ import annotations

import errno
import logging
import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

_NETWORK_ERRNOS = frozenset(
    getattr(errno, name)
    for name in (
        "EINVAL",  # OSError 22 from ssl getpeername on a dead socket
        "ENETDOWN",
        "ENETUNREACH",
        "EHOSTUNREACH",
        "EHOSTDOWN",
        "ECONNREFUSED",
        "ECONNRESET",
        "ECONNABORTED",
        "ETIMEDOUT",
        "ENOTCONN",
        "EPIPE",
        "EADDRNOTAVAIL",
    )
    if hasattr(errno, name)
)

# Genuine credential rejections. Checked FIRST: these are actionable
# ("run az login") and must never be downgraded to "offline".
_AUTH_MARKERS = re.compile(
    r"AADSTS|invalid_grant|interaction_required|invalid_client|unauthorized_client"
    r"|please run ['\"`]?az login|az login --|no accounts were found|\bHTTP 40[13]\b",
    re.IGNORECASE,
)

_NETWORK_MARKERS = re.compile(
    r"getaddrinfo|name resolution|name or service not known|nodename nor servname"
    r"|failed to resolve|nameresolutionerror|failed to establish a new connection"
    r"|max retries exceeded|connection (?:refused|reset|aborted|error)|connectionerror"
    r"|network is (?:unreachable|down)|no route to host|timed out|timeout"
    r"|getpeername|remote end closed|temporary failure|"
    r"\[errno (?:-2|-3|-5|8|22|50|51|53|54|60|61|64|65|101|104|110|111|113)\]",
    re.IGNORECASE,
)

_MAX_TEXT = 4000


def _chain(exc: BaseException | None) -> Iterator[BaseException]:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def is_offline_error(exc: BaseException, extra_text: str = "") -> bool:
    """True when *exc* (or its cause chain / captured log text) is a network outage.

    ``extra_text`` is the concatenated azure/msal log output captured while the
    exception was raised -- azure-identity often hides the real cause (DNS
    failure in the ``az`` subprocess) there, behind a generic
    ``CredentialUnavailableError("Failed to invoke the Azure CLI")``.
    """
    parts = [extra_text[:_MAX_TEXT]]
    typed_network = False
    for e in _chain(exc):
        parts.append(f"{type(e).__name__}: {e}"[:_MAX_TEXT])
        if isinstance(e, (socket.gaierror, TimeoutError, httpx.TransportError)):
            typed_network = True
        elif isinstance(e, OSError) and (
            isinstance(e, ConnectionError) or e.errno in _NETWORK_ERRNOS
        ):
            typed_network = True
    text = "\n".join(parts)
    if _AUTH_MARKERS.search(text):
        return False
    return typed_network or bool(_NETWORK_MARKERS.search(text))


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:  # noqa: BLE001 - a log capture must never raise
            pass


@contextmanager
def capture_credential_logs() -> Iterator[list[str]]:
    """Divert ``azure`` / ``msal`` log records into the yielded list.

    Records are captured and NOT propagated to the root/terminal handlers for
    the duration of the block. Logger state is restored on exit.
    """
    handler = _ListHandler()
    saved: list[tuple[logging.Logger, bool, int]] = []
    for name in ("azure", "msal"):
        lg = logging.getLogger(name)
        saved.append((lg, lg.propagate, lg.level))
        lg.addHandler(handler)
        lg.propagate = False
        if lg.level == logging.NOTSET or lg.level > logging.WARNING:
            lg.setLevel(logging.WARNING)
    try:
        yield handler.messages
    finally:
        for lg, propagate, level in saved:
            lg.removeHandler(handler)
            lg.propagate = propagate
            lg.setLevel(level)
