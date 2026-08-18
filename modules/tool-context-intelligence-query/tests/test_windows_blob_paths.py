"""Windows-compatibility tests for BlobReadTool's disk-write path.

Two bugs were fixed (both in blob_read_tool.py):
  1. ``_BLOB_DIR = Path("/tmp/ci-blobs")`` -- a hardcoded POSIX path. On Windows
     "/tmp" is a driveless-rooted path that resolves against the current drive
     (C:\\tmp), a permission-restricted location where mkdir can fail outright.
     Now uses ``tempfile.gettempdir()`` -> %TEMP% on Windows, /tmp on POSIX.
  2. ``dest.write_text(data)`` on the raw-string branch had no ``encoding=``, so a
     non-ASCII blob (CJK, emoji, extended Latin -- routine session data) raised
     UnicodeEncodeError under Windows' cp1252 default. Now ``encoding="utf-8"``.

These tests drive ``BlobReadTool.execute()`` and assert on what actually lands on
disk. They pass on every platform; on an un-fixed Windows checkout the write
tests raise UnicodeEncodeError and the location test resolves off %TEMP%.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import amplifier_module_tool_context_intelligence_query.blob_read_tool as blob_mod

# asyncio_mode = "auto" (pyproject.toml) drives the async tests below; no marker needed.


def _make_tool() -> Any:
    """Build a BlobReadTool wired to a mock hook resolver with one destination."""
    resolver = MagicMock()
    resolver.context_intelligence_server_url = "http://localhost:8080"
    resolver.context_intelligence_api_key = None
    resolver.destinations = {
        "default": SimpleNamespace(name="default", url="http://localhost:8080", api_key=""),
    }
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability.return_value = resolver
    return blob_mod.BlobReadTool(coordinator)


def _patch_fetch(return_value: Any):
    """Patch AsyncCIClient so fetch_blob returns *return_value*."""
    instance = MagicMock()
    instance.fetch_blob = AsyncMock(return_value=return_value)
    return patch(
        "amplifier_module_tool_context_intelligence_query.blob_read_tool.AsyncCIClient",
        MagicMock(return_value=instance),
    )


@pytest.fixture(autouse=True)
def _clean_blob_dir():  # type: ignore[no-untyped-def]
    """Clear the blob store between tests, reading the real path from the module."""
    blob_dir = pathlib.Path(blob_mod._BLOB_DIR)
    if blob_dir.exists():
        shutil.rmtree(blob_dir)
    yield


async def test_blob_lands_under_os_tempdir() -> None:
    """The written file must live under the OS temp dir, not a hardcoded /tmp.

    On Windows the un-fixed code targeted C:\\tmp, where mkdir can raise
    PermissionError outright.
    """
    with _patch_fetch({"data": "test"}):
        result = await _make_tool().execute({"uri": "ci-blob://my-session/my-key"})

    assert result.success is True
    assert result.output is not None
    written = pathlib.Path(str(result.output["path"]))

    expected_root = pathlib.Path(tempfile.gettempdir()) / "ci-blobs"
    assert blob_mod._BLOB_DIR == expected_root
    assert written.is_relative_to(expected_root)
    assert written.is_file()


async def test_nonascii_raw_string_blob_roundtrips() -> None:
    """A non-ASCII raw-string blob must survive the write/read cycle.

    This is the branch that had no ``encoding=``: on Windows it used cp1252 and
    raised UnicodeEncodeError on exactly this content.
    """
    payload = "caf\u00e9 \u2713 \u2014 \u65e5\u672c\u8a9e \U0001f600"

    with _patch_fetch(payload):
        result = await _make_tool().execute({"uri": "ci-blob://my-session/unicode-key"})

    assert result.success is True
    assert result.output is not None
    written = pathlib.Path(str(result.output["path"]))
    assert written.read_text(encoding="utf-8") == payload


async def test_nonascii_dict_blob_roundtrips() -> None:
    """The json.dumps branch must also read back cleanly as UTF-8."""
    payload = {"greeting": "\u65e5\u672c\u8a9e", "emoji": "\U0001f600"}

    with _patch_fetch(payload):
        result = await _make_tool().execute({"uri": "ci-blob://my-session/dict-key"})

    assert result.success is True
    assert result.output is not None
    written = pathlib.Path(str(result.output["path"]))
    assert json.loads(written.read_text(encoding="utf-8")) == payload
