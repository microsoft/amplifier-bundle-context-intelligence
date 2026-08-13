"""Preview + confirmation gate shown before a destination-mode upload.

Rendered between filtering and upload (design step 2b).  All output goes to
stderr because stdout is reserved for the machine-readable result JSON.
"""

from __future__ import annotations

import sys

from amplifier_module_hook_context_intelligence.config_resolver import Destination


class ConfirmationRequiredError(Exception):
    """Raised when confirmation is required but cannot be obtained.

    Happens when the process is not interactive (stdin/stdout are not a TTY)
    and ``--auto-approve`` was not passed: we must neither hang on ``input()``
    nor silently upload.
    """


def confirm_upload(*, auto_approve: bool, interactive: bool) -> bool:
    """Ask the operator whether to proceed.  The default answer is NO.

    - ``auto_approve=True``  -> return True without prompting (CI/automation).
    - ``interactive=True``   -> prompt ``Proceed? [y/N]`` on stderr and read stdin.
    - otherwise              -> raise :class:`ConfirmationRequiredError`.
    """
    if auto_approve:
        return True
    if not interactive:
        raise ConfirmationRequiredError("confirmation required but stdin/stdout is not a TTY")
    sys.stderr.write("Proceed? [y/N] ")
    sys.stderr.flush()
    answer = input()
    return answer.strip().lower() in {"y", "yes"}


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
