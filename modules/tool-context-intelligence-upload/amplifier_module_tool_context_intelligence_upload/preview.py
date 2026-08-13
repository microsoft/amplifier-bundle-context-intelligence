"""Preview + confirmation gate shown before a destination-mode upload.

Rendered between filtering and upload (design step 2b).  All output goes to
stderr because stdout is reserved for the machine-readable result JSON.
"""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.config_resolver import Destination


def build_preview_text(
    destination: Destination,
    session_count: int,
    approx_event_count: int,
    filtered_out: int,
) -> str:
    """Return the multi-line preview summary shown before upload.

    Shows the destination name + URL, how many sessions will be uploaded, the
    approximate total event count, and how many sessions the destination's
    include/exclude patterns filtered out.
    """
    return (
        "about to upload:\n"
        f"  destination:       {destination.name} ({destination.url})\n"
        f"  sessions:          {session_count}\n"
        f"  events (approx):   {approx_event_count}\n"
        f"  filtered out:      {filtered_out}"
    )
