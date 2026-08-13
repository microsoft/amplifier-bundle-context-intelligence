"""Shared post-discovery session filter (thin glue over the hook's own helpers).

After a format's ``discover_fn`` returns ``list[(session_dir, metadata)]``,
this module decides which of those sessions the SELECTED destination should
receive. It reuses the live hook's capture-time helpers verbatim --
``fanout.normalize_match_key`` + ``fanout.destination_is_active`` -- so an
upload-time include/exclude decision is identical to the decision the hook
made when the session was captured. The matching rules are never
reimplemented here.
"""

from __future__ import annotations

from pathlib import Path


def default_scan_root() -> Path:
    """Return the default auto-discovery root: ``~/.amplifier/projects``.

    This is the app-cli project root the live hook writes under. Phase 3 uses
    it as the discovery target when ``--path`` is omitted (the zero-arg
    gesture). Pure path computation -- the directory need not exist.
    """
    return Path.home() / ".amplifier" / "projects"
