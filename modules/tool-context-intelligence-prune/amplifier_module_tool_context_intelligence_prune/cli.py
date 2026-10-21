"""Click presentation only; all retention behavior lives in the shared library."""

from pathlib import Path
from typing import Any

import click
from context_intelligence.prune import PruneRequest, PruneSummary, iter_prune_tree, result_dict


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("root", default=".", type=click.Path(path_type=Path))
@click.option("--older-than", help="Event age such as 14d or 2w, not file mtime.")
@click.option("--before", help="Alternative exclusive ISO8601 cutoff with timezone.")
@click.option(
    "--include-responses", is_flag=True, help="Also remove raw responses; harms reconstruction."
)
@click.option("--apply", is_flag=True, help="Permanently prune selected raw payloads.")
@click.option("--dry-run", is_flag=True, help="Explicit preview (the default).")
@click.option(
    "--writers-stopped",
    is_flag=True,
    help="Assert ALL relevant writers stopped; otherwise rename can lose appends.",
)
@click.option(
    "--inactive-only",
    is_flag=True,
    help="Fence old released ROOT sessions using existing Foundation execution locks; skip unknown/legacy/children.",
)
@click.option(
    "--backup", is_flag=True, help="Retain private .bak originals; no net space recovery."
)
@click.option(
    "--min-idle",
    default="1h",
    show_default=True,
    help="Mtime guard; inactive-only enforces at least 14d.",
)
@click.option(
    "--recovery-state-dir",
    multiple=True,
    type=click.Path(path_type=Path),
    help="Additional custom recovery state root (standard root always inspected).",
)
@click.option(
    "--json", "json_output", is_flag=True, help="JSON Lines receipts and summary, never payloads."
)
def main(
    root: Path,
    dry_run: bool,
    json_output: bool,
    recovery_state_dir: tuple[Path, ...],
    **options: Any,
) -> None:
    """Prune ONLY direct data.raw of llm:request in recursive events.jsonl.

    Dry run by default. Apply requires --writers-stopped or --inactive-only.
    Usage, event records, prompt transcript events and blob references remain.
    No backups unless requested. Raw content MAY BE THE ONLY COPY: pruning
    prevents later upload/recovery of the entire marked capture as original.
    Every present prune receipt blocks replay, including interrupted receipts.
    Never remove that marker merely to bypass refusal or copy a bare pruned log.

    Inactive-only protects cooperating current Foundation root writers, not
    arbitrary appenders. Other clients must stop. Recovery-registered captures
    are skipped; unknown/busy state fails closed. No graph calls are made.
    Rewrites need temporary disk space; malformed files remain unchanged.
    Transaction scope is ONE file, not the entire subtree.
    """
    import json

    if dry_run and options.get("apply"):
        raise click.UsageError("--dry-run and --apply are mutually exclusive")
    try:
        request = PruneRequest.from_options(root, recovery_roots=recovery_state_dir, **options)
    except (ValueError, OverflowError, TypeError) as exc:
        raise click.UsageError(str(exc)) from exc
    if not json_output:
        click.echo("APPLY" if request.apply else "DRY RUN")
        click.echo(
            "Raw content may be the only copy. Marked captures cannot be replayed as original."
        )
    summary = PruneSummary()
    for result in iter_prune_tree(request):
        summary.add(result)
        if json_output:
            click.echo(json.dumps(result_dict(result)))
        elif result.status != "unchanged":
            click.echo(
                f"{result.status}: {result.path} | {result.payloads} payloads | {result.bytes_saved:,} bytes | {result.reason or ''}"
            )
    if json_output:
        click.echo(
            json.dumps({"mode": "apply" if request.apply else "dry-run", **summary.to_dict()})
        )
    else:
        click.echo(
            f"{summary.payloads:,} payloads; {summary.bytes_saved:,} bytes; "
            f"{summary.changed_files} changed; {summary.skipped_files} skipped; {summary.errors} errors"
        )
    raise click.exceptions.Exit(summary.exit_code)
