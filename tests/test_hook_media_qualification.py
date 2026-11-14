"""Offline harness-oracle/argument checks only. Never a hook/server seam verdict."""

import asyncio
import base64
import importlib.util
import json
import logging
from pathlib import Path
import sys

import pytest
import yaml

PROFILE = Path(__file__).resolve().parents[1] / ".agents/digital-twin-universe/ci-hook-media"
spec = importlib.util.spec_from_file_location("qualification", PROFILE / "qualify.py")
assert spec and spec.loader
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


@pytest.mark.parametrize(
    "args", [[], ["run"], ["run", "--output", "unused"], ["preflight", "--execute-isolated"]]
)
def test_invalid_arguments_refuse_before_effects(args):
    with pytest.raises(SystemExit) as error:
        qualification.parser().parse_args(args)
    assert error.value.code == 2


def test_real_core_two_png_literal_oracle():
    # Installed Core/Pillow, no module mount, network, services, user history or inference.
    from amplifier_core.models import ToolResult

    if "content" not in ToolResult.model_fields:
        pytest.skip("this internal fixture check requires Core 2 rich ToolResult")
    pytest.importorskip("PIL.Image")
    event, pixels = qualification.fixture("synthetic-offline")
    assert len(pixels) == 2 and pixels[0] != pixels[1]
    assert all(p.startswith(b"\x89PNG\r\n\x1a\n") for p in pixels)
    expected = qualification.expected_projection(event)
    qualification.assert_private(json.dumps(expected).encode(), pixels)
    assert expected["status"] == event["status"]
    assert expected["usage"] == event["usage"]
    assert expected["result"]["success"] is False
    assert [b["text"] for b in expected["result"]["content"]] == [
        "before",
        qualification.OMISSION,
        qualification.OMISSION,
        "after",
    ]
    for pixel in pixels:
        with pytest.raises(AssertionError):
            qualification.assert_private(pixel, pixels)
        with pytest.raises(AssertionError):
            qualification.assert_private(base64.b64encode(pixel), pixels)


def test_scan_refuses_payload_and_links(tmp_path):
    path = tmp_path / "synthetic"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(AssertionError):
        qualification.scan(tmp_path, [], ())
    path.unlink()
    path.symlink_to(tmp_path / "absent")
    with pytest.raises(AssertionError):
        qualification.scan(tmp_path, [], ())


def test_profile_no_host_mount_or_provider_and_fixed_source():
    profile = yaml.safe_load((PROFILE / "compose.yaml").read_text())
    assert set(profile["services"]) == {"neo4j", "server", "twin"}
    assert profile["x-dtu"]["twin_machine"] == "twin"
    assert profile["services"]["neo4j"]["image"] == "neo4j:5.26.22-community"
    for service in profile["services"].values():
        assert "ports" not in service and "privileged" not in service
        assert "network_mode" not in service
        assert all(not v.startswith(("/", ".")) for v in service.get("volumes", []))
    dockerfile = (PROFILE / "Dockerfile").read_text()
    assert qualification.CI_REVISION in dockerfile
    assert qualification.SERVER_REVISION in dockerfile
    assert "@main" not in dockerfile.replace("# Override the hook's production @main", "")
    assert "amplifier-foundation" not in dockerfile


@pytest.mark.parametrize("text", ["iVBORw0KGgo-negative-control", "secret-negative-control"])
def test_formatted_exception_privacy_negative_control(text):
    capture = qualification.LogCapture(secrets=("secret-negative-control",))
    try:
        raise ValueError(text)
    except ValueError:
        record = logging.LogRecord(
            "synthetic", logging.WARNING, __file__, 1, "innocent message", (), sys.exc_info()
        )
        capture.emit(record)
    assert capture.privacy_failed
    assert capture.messages == []


@pytest.mark.parametrize("name", ["bad_auth", "unavailable"])
def test_unrelated_warning_cannot_pass_failure_visibility(name):
    case = {
        "logs": [{"level": "WARNING", "message": "startup roots disagree"}],
        "forwarding": [],
    }
    assert not qualification.failure_visible(name, case)


def test_actual_core_structured_wire_and_opaque_object_negative_control():
    from amplifier_core.models import ToolResult

    if "content" not in ToolResult.model_fields:
        pytest.skip("Core 2-only wire probe; not evidence from a legacy root lane")
    pytest.importorskip("PIL.Image")
    result = asyncio.run(qualification.core_wire_probe())
    assert result["explicit_model_dump_mapping_preserved"]
    assert result["typed_object_became_opaque_string"]
    assert result["typed_original_unchanged"]
    assert result["hook_server_executed"] is False


def test_conservation_requires_post_stimulus_measured_snapshot():
    metrics = {
        "stats_available": True,
        "as_of_seconds": 10,
        "accepted_total": 1,
        "written_total": 1,
        "in_queue_total": 0,
        "dead_letter_total": 0,
        "residual": 0,
    }
    assert not qualification.fresh_conservation(metrics, 2)
    metrics["as_of_seconds"] = 0.1
    assert qualification.fresh_conservation(metrics, 2)
    metrics["dead_letter_total"] = 1
    assert not qualification.fresh_conservation(metrics, 2)


def test_post_only_toolcall_absence_query_uses_physical_identity():
    # Pinned server upsert uses node_id; post-only ToolCall has no session_id.
    assert "n.node_id = $tid" in qualification.ABSENT_QUERY
    assert "n.node_id = $sid" in qualification.ABSENT_QUERY
    assert "n.session_id = $sid" in qualification.ABSENT_QUERY
    assert "n.id =" not in qualification.ABSENT_QUERY
