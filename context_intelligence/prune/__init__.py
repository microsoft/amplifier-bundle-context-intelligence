"""Local raw-payload retention. Offline library; no Click or runtime imports.

Pruning is irreversible without backups and may remove the only raw copy.
Marked captures cannot subsequently be replayed as original by this repository's
upload/recovery tools. It does not prove any destination received the originals.
"""

from .coordination import CaptureBusy, PruneError, PrunedCapture, replay_guard
from .operations import PruneRequest, PruneSummary, iter_prune_tree, prune_file, result_dict
from .policy import Policy, parse_duration, parse_timestamp, prune_record
from .storage import FileResult, discover_events

__all__ = [
    "CaptureBusy",
    "FileResult",
    "Policy",
    "PruneError",
    "PrunedCapture",
    "PruneRequest",
    "PruneSummary",
    "discover_events",
    "iter_prune_tree",
    "parse_duration",
    "parse_timestamp",
    "prune_file",
    "prune_record",
    "replay_guard",
    "result_dict",
]
