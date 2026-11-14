"""Opt-in actual Foundation candidate contract; no fake store or transport.

Run with the exact candidate installed into the isolated test environment and
CI_OPTIMIZE_FOUNDATION_CONTRACT=1. Ordinary installs deliberately skip this
cross-repository gate rather than pretending to select an unpinned dependency.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib
import io
import json
import os
from pathlib import Path
from typing import Any, Iterator

import pytest

from context_intelligence.optimize import StagedImage, apply, preview, resume, stage
from context_intelligence.optimize.capture import iter_capture_projection
from context_intelligence.prune.coordination import PruneError, replay_guard

FOUNDATION = "66088e3a5647412db8f014684cfa9234ea7f695f"
pytestmark = pytest.mark.skipif(
    os.environ.get("CI_OPTIMIZE_FOUNDATION_CONTRACT") != "1",
    reason="requires explicitly installed exact Foundation candidate",
)


@pytest.fixture
def actual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    # Optional candidate integration is deliberately not a production/dev
    # dependency. Enabling this gate requires the exact real imports to succeed.
    Image = importlib.import_module("PIL.Image")
    media = importlib.import_module("amplifier_foundation.session.media")
    conversion = importlib.import_module("amplifier_foundation.session.media_conversion_owner")
    MediaPolicy, SessionMediaStore = media.MediaPolicy, media.SessionMediaStore
    BINDING = conversion.BINDING
    CaptureMediaOwner, diagnostic_image_ref = (
        conversion.CaptureMediaOwner,
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
    path.write_text(json.dumps(record) + "\n")
    original = path.read_bytes()
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
        for block in installed["data"]["result"]["content"]:
            ref = diagnostic_image_ref(block)
            with (operation / "assets.jsonl").open("rb") as manifest:
                assert json.loads(next(manifest))["owner"]["ref"] == ref
                assert manifest.readline() == b""
            yield ref
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
            "expires_at": None,
        }  # Explicit fixture durable policy, not inferred from migration time.

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

    binding = CaptureMediaOwner(
        store,
        staged_factory=StagedImage,
        origin_for=origin_for,
        checkpoint_receipt_for=checkpoint_for,
        new_asset_bytes_for=allocated,
        reader_ready=lambda name: name == BINDING,
    )
    try:
        yield dict(
            path=path,
            operation=operation,
            binding=binding,
            store=store,
            pixels=pixels,
            original=original,
            read_ref=diagnostic_image_ref,
            checkpoint_calls=checkpoint_calls,
        )
    finally:
        store.close()


def test_actual_store_ci_preview_stage_apply_resume(actual: dict[str, Any]) -> None:
    MediaRefusal = importlib.import_module("amplifier_foundation.session.media").MediaRefusal

    path, operation, binding = actual["path"], actual["operation"], actual["binding"]
    assert preview(path, session_id="s1")["images"] == 1
    staged = stage(path, operation, session_id="s1", media=binding, writers_stopped=True)
    assert staged["counts"]["new_asset_bytes"] == len(actual["pixels"])
    assert path.read_bytes() == actual["original"]
    block = json.loads((operation / "candidate").read_bytes())["data"]["result"]["content"][0]
    ref = actual["read_ref"](block)
    assert block["visibility"] == "user"
    assert ref["provenance"]["created_at"] == "2025-01-01T00:00:00+00:00"
    assert ref["provenance"]["expires_at"] is None
    with pytest.raises(MediaRefusal):
        actual["store"].resolve_ref(ref)  # staged is not committed
    result = apply(operation, media=binding, writers_stopped=True)
    assert result["state"] == "verified" and actual["checkpoint_calls"]
    assert (operation / "original").read_bytes() == actual["original"]
    with actual["store"].resolve_ref(ref) as binary:
        assert binary.read() == actual["pixels"]
    with path.open("rb") as capture:
        projection = list(iter_capture_projection(capture, session_id="s1"))
    assert projection[0]["event_id"] == "original-event"  # no image hydration
    installed = path.read_bytes()
    assert (
        b"base64" not in installed and b"diagnostic reference only; no pixels supplied" in installed
    )
    assert resume(operation, media=binding, writers_stopped=True)["state"] == "verified"
    assert path.read_bytes() == installed
    with pytest.raises(PruneError):
        with replay_guard(path):
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
