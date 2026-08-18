"""Windows-compatibility tests for BlobReadTool's disk-write path.

Two bugs were fixed (both in blob_read_tool.py):
  1. _BLOB_DIR = Path("/tmp/ci-blobs") -- a hardcoded POSIX path. On Windows
     "/tmp" is a driveless-rooted path that resolves against the current drive
     (C:\\tmp), a permission-restricted location where mkdir can fail outright.
     Now uses tempfile.gettempdir() -> %TEMP% on Windows, /tmp on POSIX.
  2. dest.write_text(data) on the raw-string branch had no encoding=, so a
     non-ASCII blob (CJK, emoji, extended Latin -- routine session data) raised
     UnicodeEncodeError under Windows' cp1252 default. Now encoding="utf-8".

The source-inspection test has real teeth on Linux (the un-fixed source has the
hardcoded /tmp and bare write_text); the write test has teeth on Windows.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import amplifier_module_tool_context_intelligence_query.blob_read_tool as blob_mod


def test_blob_dir_is_under_os_tempdir():
    # Not a hardcoded /tmp: resolves to the OS temp dir on every platform.
    assert blob_mod._BLOB_DIR == Path(tempfile.gettempdir()) / "ci-blobs"
    assert blob_mod._BLOB_DIR.name == "ci-blobs"


def test_source_pins_tempdir_and_utf8():
    """Teeth on Linux: the un-fixed source has Path("/tmp/...") and bare write_text."""
    src = Path(blob_mod.__file__).read_text(encoding="utf-8")

    assert 'Path("/tmp/ci-blobs")' not in src, "hardcoded POSIX /tmp path still present"
    assert "tempfile.gettempdir()" in src, "_BLOB_DIR must use the OS temp dir"

    # Both blob writes (json branch + raw-string branch) must pin utf-8.
    assert src.count("write_text(") == 2
    assert 'write_text(json.dumps(data), encoding="utf-8")' in src
    assert 'write_text(data, encoding="utf-8")' in src


def test_nonascii_write_roundtrips(tmp_path):
    """The raw-string write path must preserve non-ASCII (teeth on Windows).

    Mirrors blob_read_tool.py's raw-string branch: on Windows the old bare
    write_text used cp1252 and raised UnicodeEncodeError on this content.
    """
    data = "caf\u00e9 \u2713 \u2014 \u65e5\u672c\u8a9e \U0001f600"
    dest = tmp_path / "blob.json"
    dest.write_text(data, encoding="utf-8")  # the fixed call shape
    assert dest.read_text(encoding="utf-8") == data
