"""Opt-in actual Foundation candidate contract; no fake store or transport.

Run with the exact candidate installed into the isolated test environment and
CI_OPTIMIZE_FOUNDATION_CONTRACT=1. Ordinary installs deliberately skip this
cross-repository gate rather than pretending to select an unpinned dependency.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import importlib.metadata
import io
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from context_intelligence.optimize import StagedImage, apply, preview, resume, stage
from context_intelligence.optimize.capture import (
    capture_group_sources,
    iter_capture_projection,
    write_record_mapping,
)
from context_intelligence.prune.coordination import PruneError, replay_guard

FOUNDATION = "87d257e4057865688e9d0294ba170fe4ad1d1cec"
pytestmark = pytest.mark.skipif(
    os.environ.get("CI_OPTIMIZE_FOUNDATION_CONTRACT") != "1",
    reason="requires explicitly installed exact Foundation candidate",
)


@pytest.fixture(params=[None, "2035-01-01T00:00:00+00:00"])
def actual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[dict[str, Any]]:
    # Optional candidate integration is deliberately not a production/dev
    # dependency. Enabling this gate requires the exact real imports to succeed.
    Image = importlib.import_module("PIL.Image")
    media = importlib.import_module("amplifier_foundation.session.media")
    conversion = importlib.import_module("amplifier_foundation.session.media_conversion_owner")
    MediaPolicy, SessionMediaStore = media.MediaPolicy, media.SessionMediaStore
    BINDING = conversion.GROUP_BINDING
    CaptureMediaOwner, diagnostic_image_ref = (
        conversion.CaptureGroupMediaOwner,
        conversion.diagnostic_image_ref,
    )

    direct = json.loads(
        importlib.metadata.distribution("amplifier-foundation").read_text("direct_url.json") or "{}"
    )
    assert direct["vcs_info"]["commit_id"] == FOUNDATION, "wrong candidate is not this gate"
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    monkeypatch.setattr(
        "context_intelligence.prune.recovery.default_recovery_root", lambda: tmp_path / "recovery"
    )
    value = io.BytesIO()
    Image.new("RGB", (3, 2), "red").save(value, format="PNG")
    pixels = value.getvalue()
    other = io.BytesIO()
    Image.new("RGB", (2, 3), "blue").save(other, format="PNG")
    second_pixels = other.getvalue()
    path = tmp_path / "events.jsonl"
    record = {
        "event": "tool:post",
        "session_id": "s1",
        "event_id": "original-event",
        "timestamp": "2025-01-01T00:00:00+00:00",
        "data": {
            "tool_call_id": "call-original",
            "status": "unknown",
            "usage": {"input": 9},
            "result": {
                "success": False,
                "output": {"receipt": "original"},
                "content": [
                    {
                        "type": "image",
                        "visibility": "user",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(pixels).decode(),
                        },
                    }
                ],
            },
        },
    }
    second_block = json.loads(json.dumps(record["data"]["result"]["content"][0]))
    second_block["source"]["data"] = base64.b64encode(second_pixels).decode()
    record["data"]["result"]["content"].append(second_block)
    path.write_text(json.dumps(record) + "\n")
    original = path.read_bytes()
    original_offsets = {
        original.index(json.dumps(block).encode()): position
        for position, block in enumerate(record["data"]["result"]["content"])
    }
    operation = tmp_path / "convert"
    checkpoint_calls = []

    def checkpoint(receipt: Any) -> Iterator[dict[str, Any]]:
        """Fixture host authority: actual installed file+mapping+closure, paged."""
        assert receipt == {
            "session_id": "diagnostic-s1",
            "source": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mapping_sha256": hashlib.sha256(
                (operation / "records.jsonl").read_bytes()
            ).hexdigest(),
        }
        with (operation / "records.jsonl").open("rb") as mappings:
            header = json.loads(next(mappings))
            assert header["old"]["sha256"] == hashlib.sha256(original).hexdigest()
            assert header["new"]["sha256"] == receipt["sha256"]
            rows = list(mappings)  # fixture has exactly one record, not production verifier
            assert len(rows) == 1 and json.loads(rows[0])["eventId"] == "original-event"
        installed = json.loads(path.read_bytes())
        assert installed["data"]["tool_call_id"] == "call-original"
        assert installed["event_id"] == record["event_id"]
        assert installed["timestamp"] == record["timestamp"]
        assert installed["data"]["status"] == record["data"]["status"]
        assert installed["data"]["usage"] == record["data"]["usage"]
        blocks = installed["data"]["result"]["content"]
        selected_positions = set()
        with (operation / "assets.jsonl").open("rb") as manifest:
            for raw in manifest:
                row = json.loads(raw)
                owner = row["owner"]
                # The whole owner group is NOT checkpoint reachability. Only the
                # row's selected ref at the exact original CI position is installed.
                position = original_offsets[row["origin"]["image_offset"]]
                assert position not in selected_positions
                selected_positions.add(position)
                ref = diagnostic_image_ref(blocks[position])
                assert owner["refs"][owner["selected"]] == ref
                yield ref
        for position, block in enumerate(blocks):
            if position not in selected_positions:
                assert block == record["data"]["result"]["content"][position]
        checkpoint_calls.append(receipt)

    store = SessionMediaStore(
        tmp_path / "private-diagnostic-only",
        "diagnostic-s1",
        policy=MediaPolicy(1024 * 1024, 10000, 10),
        checkpoint_verifier=checkpoint,
    )
    measured = set()

    def allocated(ref: dict[str, Any]) -> int:
        # One host-owned test generation; measure observed unique staged bytes.
        digest = ref["sha256"]
        if digest in measured:
            return 0
        with store.resolve_ref(ref, committed=False) as binary:
            observed = os.fstat(binary.fileno()).st_size
        measured.add(digest)
        return observed

    def origin_for(snapshot: dict[str, Any]) -> dict[str, Any]:
        assert snapshot["source_sha256"] == hashlib.sha256(original).hexdigest()
        assert snapshot["event_id"] == "original-event"
        return {
            "kind": "migration",
            "session_id": "s1",
            "id": "fixture-authorized-durable-image:original-event",
            "created_at": "2025-01-01T00:00:00+00:00",
            "expires_at": request.param,
        }  # Explicit fixture original policy, not inferred from migration time.

    def checkpoint_for(manifest: Path, *, source: Path, sha256: str) -> dict[str, Any]:
        assert manifest == operation / "assets.jsonl" and source == path
        return {
            "session_id": "diagnostic-s1",
            "source": str(source),
            "sha256": sha256,
            "mapping_sha256": hashlib.sha256(
                (operation / "records.jsonl").read_bytes()
            ).hexdigest(),
        }

    source = path.open("rb")  # Retained descriptor of the exact pre-conversion generation.
    context = capture_group_sources(
        source,
        session_id="s1",
        source_sha256=hashlib.sha256(original).hexdigest(),
        origin_for=origin_for,
        complete_group_for=lambda snapshot, original_policy: (
            snapshot["source_sha256"] == hashlib.sha256(original).hexdigest()
            and original_policy == origin_for(snapshot)
        ),  # Fixture source authority attests both original images are present.
    )
    groups = context.__enter__()
    binding = CaptureMediaOwner(
        store,
        group_sources_for=groups,
        staged_factory=StagedImage,
        origin_for=origin_for,
        checkpoint_receipt_for=checkpoint_for,
        new_asset_bytes_for=allocated,
        reader_ready=lambda name: name == BINDING,
    )
    try:
        yield {
            "path": path,
            "operation": operation,
            "binding": binding,
            "store": store,
            "pixels": pixels,
            "second_pixels": second_pixels,
            "original_record": record,
            "expires_at": request.param,
            "original": original,
            "read_ref": diagnostic_image_ref,
            "checkpoint_calls": checkpoint_calls,
        }
    finally:
        try:
            context.__exit__(None, None, None)
        finally:
            source.close()
            store.close()


def test_actual_store_ci_preview_stage_apply_resume(actual: dict[str, Any]) -> None:
    MediaRefusal = importlib.import_module("amplifier_foundation.session.media").MediaRefusal

    path, operation, binding = actual["path"], actual["operation"], actual["binding"]
    assert preview(path, session_id="s1")["images"] == 2
    staged = stage(path, operation, session_id="s1", media=binding, writers_stopped=True)
    assert staged["counts"]["new_asset_bytes"] == len(actual["pixels"]) + len(
        actual["second_pixels"]
    )
    assert path.read_bytes() == actual["original"]
    block = json.loads((operation / "candidate").read_bytes())["data"]["result"]["content"][0]
    ref = actual["read_ref"](block)
    assert block["visibility"] == "user"
    assert ref["provenance"]["created_at"] == "2025-01-01T00:00:00+00:00"
    assert ref["provenance"]["expires_at"] == actual["expires_at"]
    with pytest.raises(MediaRefusal):
        actual["store"].resolve_ref(ref)  # staged is not committed
    result = apply(operation, media=binding, writers_stopped=True)
    assert result["state"] == "verified" and actual["checkpoint_calls"]
    assert (operation / "original").read_bytes() == actual["original"]
    with actual["store"].resolve_ref(ref) as binary:
        assert binary.read() == actual["pixels"]
    second = json.loads(path.read_bytes())["data"]["result"]["content"][1]
    with actual["store"].resolve_ref(actual["read_ref"](second)) as binary:
        assert binary.read() == actual["second_pixels"]
    # Saved owner closure remains repeatable after the actual selected ACK.
    binding.publish(operation / "assets.jsonl")
    binding.verify(operation / "assets.jsonl")
    binding.publish(operation / "assets.jsonl")
    binding.verify(operation / "assets.jsonl")
    with path.open("rb") as capture:
        projection = list(iter_capture_projection(capture, session_id="s1"))
    assert projection[0]["event_id"] == "original-event"  # no image hydration
    installed = path.read_bytes()
    assert (
        b"base64" not in installed and b"diagnostic reference only; no pixels supplied" in installed
    )
    assert resume(operation, media=binding, writers_stopped=True)["state"] == "verified"
    assert path.read_bytes() == installed
    with pytest.raises(PruneError), replay_guard(path):
        pass


@pytest.mark.parametrize("gate", ["reader", "origin"])
def test_actual_binding_refuses_unqualified_reader_or_unknown_policy(
    actual: dict[str, Any], gate: str
) -> None:
    Unsupported = importlib.import_module(
        "amplifier_foundation.session.media_conversion"
    ).Unsupported

    binding = actual["binding"]
    if gate == "reader":
        binding.reader_ready = lambda _: False
    else:
        binding.origin_for = lambda _: {}
    with pytest.raises(Unsupported):
        stage(
            actual["path"],
            actual["operation"],
            session_id="s1",
            media=binding,
            writers_stopped=True,
        )
    assert actual["path"].read_bytes() == actual["original"]


def test_actual_checkpoint_acknowledges_selected_installed_refs_only(
    actual: dict[str, Any],
) -> None:
    """Real owner/store selected-only checkpoint, separate from CI apply qualification."""
    MediaRefusal = importlib.import_module("amplifier_foundation.session.media").MediaRefusal
    path, operation, binding = actual["path"], actual["operation"], actual["binding"]
    stage(path, operation, session_id="s1", media=binding, writers_stopped=True)
    manifest = operation / "assets.jsonl"
    rows = manifest.read_bytes().splitlines(keepends=True)
    assert len(rows) == 2
    first = json.loads(rows[0])
    assert first["owner"]["selected"] == 0 and len(first["owner"]["refs"]) == 2
    # Host selects only the first position. The other published group member
    # remains inline and therefore has no diagnostic checkpoint reachability.
    candidate = json.loads((operation / "candidate").read_bytes())
    candidate["data"]["result"]["content"][1] = actual["original_record"]["data"]["result"][
        "content"
    ][1]
    partial = operation / "partial-candidate"
    partial.write_text(json.dumps(candidate) + "\n")
    partial.replace(path)  # Keep the retained source descriptor immutable.
    manifest.write_bytes(rows[0])
    with (
        io.BytesIO(actual["original"]) as before,
        path.open("rb") as after,
        (operation / "records.jsonl").open("wb") as mapping,
    ):
        write_record_mapping(before, after, mapping, session_id="s1")
    binding.publish(manifest)
    binding.verify(manifest)
    installed_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    binding.acknowledge(manifest, source=path, sha256=installed_hash)
    binding.acknowledge(manifest, source=path, sha256=installed_hash)
    selected, unselected = first["owner"]["refs"]
    with actual["store"].resolve_ref(selected) as binary:
        assert binary.read() == actual["pixels"]
    with pytest.raises(MediaRefusal):
        actual["store"].resolve_ref(unselected)
    with actual["store"].resolve_published_ref(unselected) as binary:
        assert binary.read() == actual["second_pixels"]


@pytest.mark.parametrize("fault", ["truncated", "reordered"])
def test_actual_owner_refuses_incomplete_or_reordered_saved_groups(
    actual: dict[str, Any], fault: str
) -> None:
    MediaRefusal = importlib.import_module("amplifier_foundation.session.media").MediaRefusal
    binding, operation = actual["binding"], actual["operation"]
    stage(actual["path"], operation, session_id="s1", media=binding, writers_stopped=True)
    manifest = operation / "assets.jsonl"
    rows = [json.loads(row) for row in manifest.read_bytes().splitlines()]
    for row in rows:
        owner = row["owner"]
        if fault == "truncated":
            selected = owner["selected"]
            owner["refs"] = [owner["refs"][selected]]
            owner["source_origins"] = [owner["source_origins"][selected]]
            owner["selected"] = 0
        else:
            owner["refs"].reverse()
            owner["source_origins"].reverse()
            owner["selected"] = 1 - owner["selected"]
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises(MediaRefusal, match="final retained publication"):
        binding.verify(manifest)  # Must fail read-only, not merely on republish.
    assert actual["path"].read_bytes() == actual["original"]


@pytest.mark.parametrize("gate", ["cardinality", "reader", "origin", "qualified"])
def test_actual_single_image_binding_cardinality_and_policy_contract(
    actual: dict[str, Any], gate: str
) -> None:
    """Same immutable installation also exercises the original single-image ABI."""
    conversion = importlib.import_module("amplifier_foundation.session.media_conversion_owner")
    Unsupported = importlib.import_module(
        "amplifier_foundation.session.media_conversion"
    ).Unsupported
    pixels = actual["pixels"]
    snapshot = {
        "session_id": "s1",
        "source_sha256": hashlib.sha256(pixels).hexdigest(),
        "ordinal": 1,
        "image_offset": 17,
        "event_id": "known-singleton",
        "sha256": hashlib.sha256(pixels).hexdigest(),
        "bytes": len(pixels),
    }
    original = {
        "kind": "migration",
        "session_id": "s1",
        "id": "fixture-proven-singleton",
        "created_at": "2025-01-01T00:00:00+00:00",
        "expires_at": actual["expires_at"],
    }
    binding = conversion.CaptureMediaOwner(
        actual["store"],
        staged_factory=StagedImage,
        origin_for=lambda _: {} if gate == "origin" else dict(original),
        checkpoint_receipt_for=lambda *_args, **_kwargs: pytest.fail("no checkpoint installed"),
        new_asset_bytes_for=lambda ref: ref["bytes"],
        reader_ready=lambda name: gate != "reader" and name == conversion.BINDING,
        # Fixture attests exactly one image at this original, not an inferred count.
        single_image_origin=lambda seen, origin: (
            gate != "cardinality" and seen == snapshot and origin == original
        ),
    )
    if gate != "qualified":
        with pytest.raises(Unsupported):
            binding.stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot)
        return
    staged = binding.stage_image(io.BytesIO(pixels), mime="image/png", origin=snapshot)
    assert staged.receipt["ref"]["provenance"] == original
    assert staged.receipt["ref"]["sha256"] == snapshot["sha256"]
    manifest = actual["path"].parent / "single-owner.jsonl"
    manifest.write_text(json.dumps({"origin": snapshot, "owner": staged.receipt}) + "\n")
    binding.publish(manifest)
    binding.verify(manifest)
    binding.publish(manifest)
    binding.verify(manifest)
