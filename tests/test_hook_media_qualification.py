"""Offline harness-oracle/argument checks only. Never a hook/server seam verdict."""

import asyncio
import base64
import importlib.util
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace
import csv
import hashlib
import io
import zipfile

import pytest
import yaml

PROFILE = Path(__file__).resolve().parents[1] / ".agents/digital-twin-universe/ci-hook-media"
spec = importlib.util.spec_from_file_location("qualification", PROFILE / "qualify.py")
assert spec and spec.loader
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)
provenance_spec = importlib.util.spec_from_file_location(
    "payload_provenance", PROFILE / "provenance.py"
)
assert provenance_spec and provenance_spec.loader
provenance = importlib.util.module_from_spec(provenance_spec)
provenance_spec.loader.exec_module(provenance)


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


def record_bytes(files):
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    for name, data in files.items():
        if name.endswith(".dist-info/RECORD"):
            writer.writerow([name, "", ""])
        else:
            sha256 = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            writer.writerow([name, "sha256=" + sha256, str(len(data))])
    return text.getvalue().encode()


@pytest.fixture
def independent_payload(tmp_path):
    """Synthetic packaging unit only: wheel expectation independent of installation."""
    info = "amplifier_core-2.0.2.dist-info"
    files = {
        "amplifier_core/__init__.py": b"# independent fixture\n",
        "amplifier_core/models.py": b"VALUE = 'original'\n",
        info + "/METADATA": b"Name: amplifier-core\nVersion: 2.0.2\n",
        info + "/RECORD": b"",
    }
    files[info + "/RECORD"] = record_bytes(files)
    wheel = tmp_path / "original.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    expected = provenance.wheel_reference(wheel, "amplifier-core", "2.0.2")
    site = tmp_path / "site"
    for name, data in files.items():
        path = site / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    dist = provenance.metadata.PathDistribution(site / info)
    return site, dist, expected, files


def test_independent_payload_and_record_positive(independent_payload):
    _site, dist, expected, _files = independent_payload
    result = provenance.verify_distribution(dist, expected)
    assert result["record_verified"] and result["independent_payload_verified"]


@pytest.mark.parametrize("rewrite_record", [False, True])
def test_changed_core_payload_refused_despite_same_metadata(independent_payload, rewrite_record):
    site, dist, expected, files = independent_payload
    files["amplifier_core/models.py"] = b"VALUE = 'tampered'\n"
    (site / "amplifier_core/models.py").write_bytes(files["amplifier_core/models.py"])
    if rewrite_record:
        (site / "amplifier_core-2.0.2.dist-info/RECORD").write_bytes(record_bytes(files))
    # Recomputed RECORD is not an independent source expectation either.
    with pytest.raises(ValueError, match="(RECORD hash|independent payload) mismatch"):
        provenance.verify_distribution(dist, expected)
    assert dist.version == "2.0.2"


def test_unrecorded_payload_and_loaded_origin_refused(independent_payload):
    site, dist, expected, _files = independent_payload
    verified = provenance.verify_distribution(dist, expected)
    source = site / "amplifier_core/models.py"
    loaded = {"amplifier_core.models": SimpleNamespace(__file__=str(source))}
    assert provenance.join_loaded([verified], list(loaded), loaded)
    loaded["amplifier_core.models"].__file__ = str(site / "shadow/models.py")
    with pytest.raises(ValueError, match="outside verified payload"):
        provenance.join_loaded([verified], list(loaded), loaded)
    (site / "amplifier_core/extra.py").write_text("# unrecorded executable")
    with pytest.raises(ValueError, match="payload set mismatch"):
        provenance.verify_distribution(dist, expected)


@pytest.mark.parametrize("change", ["manifest", "digest", "harness"])
def test_manifest_digest_or_copied_harness_drift_refused(tmp_path, change):
    source = tmp_path / "provenance.py"
    source.write_text("# reviewed harness\n")
    value = {
        "schema": "ci-independent-payload-v1",
        "harness": {"provenance.py": provenance.digest(source.read_bytes())},
    }
    raw = provenance.canonical(value)
    manifest = tmp_path / "reference.json"
    manifest.write_bytes(raw)
    manifest.with_suffix(".sha256").write_text(provenance.digest(raw))
    checked, _ = provenance.load_reference(manifest)
    assert provenance.verify_harness(checked, tmp_path)
    if change == "manifest":
        manifest.write_bytes(raw + b" ")
    elif change == "digest":
        manifest.with_suffix(".sha256").write_text("0" * 64)
    else:
        source.write_text("# changed with unchanged expectation\n")
    with pytest.raises(ValueError, match="(manifest digest mismatch|harness payload drift)"):
        checked, _ = provenance.load_reference(manifest)
        provenance.verify_harness(checked, tmp_path)


def test_wheel_with_changed_payload_and_old_record_refused(tmp_path):
    wheel = tmp_path / "broken.whl"
    files = {
        "amplifier_core/models.py": b"original",
        "amplifier_core-2.0.2.dist-info/METADATA": b"Name: amplifier-core\nVersion: 2.0.2\n",
        "amplifier_core-2.0.2.dist-info/RECORD": b"",
    }
    files["amplifier_core-2.0.2.dist-info/RECORD"] = record_bytes(files)
    files["amplifier_core/models.py"] = b"tampered"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    with pytest.raises(ValueError, match="wheel RECORD hash mismatch"):
        provenance.wheel_reference(wheel, "amplifier-core", "2.0.2")


@pytest.mark.parametrize("rewrite_record", [False, True])
def test_claimed_git_revision_does_not_qualify_changed_bytes(independent_payload, rewrite_record):
    site, dist, expected, files = independent_payload
    expected["revision"] = provenance.CI
    info = "amplifier_core-2.0.2.dist-info"
    files[info + "/direct_url.json"] = json.dumps(
        {
            "url": "https://example.invalid/synthetic-source",
            "vcs_info": {"vcs": "git", "commit_id": provenance.CI},
        }
    ).encode()
    (site / (info + "/direct_url.json")).write_bytes(files[info + "/direct_url.json"])
    (site / (info + "/RECORD")).write_bytes(record_bytes(files))
    assert provenance.verify_distribution(dist, expected)["record_verified"]
    files["amplifier_core/models.py"] = b"changed with same claimed git revision"
    (site / "amplifier_core/models.py").write_bytes(files["amplifier_core/models.py"])
    if rewrite_record:
        (site / (info + "/RECORD")).write_bytes(record_bytes(files))
    with pytest.raises(ValueError, match="(RECORD hash|independent payload) mismatch"):
        provenance.verify_distribution(dist, expected)


def test_frozen_harness_manifest_and_build_argument_wiring():
    raw = (PROFILE / "harness-manifest.json").read_bytes()
    harness = json.loads(raw)
    assert set(harness) == {p.name for p in PROFILE.glob("*.py")}
    assert provenance.verify_harness({"harness": harness}, PROFILE) == harness
    dockerfile = (PROFILE / "Dockerfile").read_text()
    assert provenance.digest(raw) in dockerfile
    assert "COPY harness-manifest.json /harness-manifest.json" in dockerfile
    assert "sha256sum --check --strict" in dockerfile
    assert "--harness-manifest /harness-manifest.json" in dockerfile
    # Cross-check all required argparse arguments against the actual Docker RUN.
    import ast

    tree = ast.parse((PROFILE / "provenance.py").read_text())
    required = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and any(
            k.arg == "required" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in node.keywords
        )
    ]
    assert required and all(arg in dockerfile for arg in required)
