"""Deterministic, synthetic installed-hook seam evaluation. No model calls.

Run only inside the allocated ci-hook-media profile. Exit 0 is insufficient:
inspect evaluation.json checks, observed evidence, source identities and limitations.
This does not qualify settings expansion, Foundation conversion, other producers,
Entra auth, sustained-outage escalation or the pre-hook serialization boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import io
import json
import logging
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

CI_REVISION = "e6de12c343a533187b836f4a99042c415eed2f18"
SERVER_REVISION = "1622b48b05fcd93f935e5d55b962081a2c0cb229"
FOUNDATION_REFERENCE = "496adcd887cc55d1e79a7539dd6235926ac737e7"
OMISSION = "[Image not retained in diagnostic capture; no pixels stored or forwarded.]"
SERVER = "http://server:8000"
UNAVAILABLE = "http://127.0.0.1:1"
CHECKS = (
    "installed_sources",
    "real_backend_ready",
    "real_neo4j_apoc",
    "caller_unchanged",
    "local_and_enqueue",
    "real_http_acceptance",
    "server_graph_and_blob",
    "bad_auth_loud",
    "unavailable_loud",
    "failed_sessions_not_ingested",
    "payload_free_diagnostics",
    "bounded_requests",
)

if not __debug__:
    raise RuntimeError("qualification assertions require non-optimized Python")


def installed(name: str, revision: str | None = None) -> dict:
    """Verify noneditable installation; retain actual installed bytes, not a claimed SHA."""
    dist = metadata.distribution(name)
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    if revision is not None:
        assert direct.get("vcs_info", {}).get("commit_id") == revision
        assert not direct.get("dir_info", {}).get("editable")
    files = {}
    for item in dist.files or []:
        if str(item).endswith((".py", ".so", ".pyd")):
            path = Path(dist.locate_file(item))
            files[str(item)] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert files
    return {"package": name, "version": dist.version, "direct_url": direct, "files": files}


def preflight() -> dict:
    hook = installed("amplifier-module-hook-context-intelligence", CI_REVISION)
    library = installed("amplifier-bundle-context-intelligence", CI_REVISION)
    core = installed("amplifier-core")
    pillow = installed("Pillow")
    assert core["version"] == "2.0.2" and pillow["version"] == "13.0.0"
    # These are not dependencies of the hook. Do not silently qualify with a host CLI.
    for name in ("amplifier-foundation", "amplifier-app-cli"):
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        raise ValueError("unexpected host application dependency")
    from amplifier_core.models import ToolResult
    from amplifier_core import _engine

    assert "content" in ToolResult.model_fields
    assert _engine.__version__ == "2.0.2"
    return {
        "hook": hook,
        "library": library,
        "core": core,
        "pillow": pillow,
        "native_core_version": _engine.__version__,
    }


def fixture(sid: str) -> tuple[dict, list[bytes]]:
    from amplifier_core.models import ToolResult
    from PIL import Image

    if "content" not in ToolResult.model_fields:
        raise ValueError("canonical rich ToolResult requires Core 2")
    pixels = []
    blocks = [{"type": "text", "text": "before"}]
    for size, color in (((3, 2), "red"), ((2, 3), "blue")):
        stream = io.BytesIO()
        Image.new("RGB", size, color).save(stream, format="PNG")
        pixels.append(stream.getvalue())
        blocks.append(
            {
                "type": "image",
                "visibility": "user",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(pixels[-1]).decode(),
                },
            }
        )
    blocks.append({"type": "text", "text": "after"})
    result = ToolResult.model_validate(
        {
            "success": False,
            "error": {"code": "partial"},
            "output": {"receipt": "original"},
            "content": blocks,
        }
    )
    return {
        "session_id": sid,
        "event_id": "original-" + sid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": "synthetic-two-png",
        "tool_call_id": "call-" + sid,
        "status": "unknown",
        "usage": {"input_tokens": 23, "output_tokens": 7},
        "result": result,
    }, pixels


def expected_projection(event: dict) -> dict:
    """Independent literal oracle. Never call the implementation's projection helper."""
    expected = deepcopy(event)
    result = event["result"].model_dump()
    for index in (1, 2):
        result["content"][index] = {
            "type": "text",
            "visibility": "user",
            "text": OMISSION,
            "context_intelligence.media": {
                "v": 1,
                "status": "not_retained",
                "reason": "diagnostic_capture",
                "original_type": "image",
            },
        }
    expected["result"] = result
    return expected


def assert_private(value: bytes, pixels: list[bytes], secrets: tuple[str, ...] = ()) -> None:
    for image in pixels:
        assert image not in value
        assert base64.b64encode(image) not in value
    assert b"\x89PNG\r\n\x1a\n" not in value
    assert b"iVBORw0KGgo" not in value
    for secret in secrets:
        assert secret.encode() not in value


def scan(root: Path, pixels: list[bytes], secrets: tuple[str, ...]) -> dict:
    """Only the profile's NEW synthetic state volume or this invocation's output."""
    files = {}
    total = 0
    for path in root.rglob("*"):
        assert not path.is_symlink()
        if not path.is_file():
            continue
        assert len(files) < 100 and path.stat().st_size <= 2 * 1024 * 1024
        data = path.read_bytes()
        total += len(data)
        assert total <= 16 * 1024 * 1024
        assert_private(data, pixels, secrets)
        files[str(path.relative_to(root))] = hashlib.sha256(data).hexdigest()
    assert files
    return files


class LogCapture(logging.Handler):
    def __init__(self, pixels=(), secrets=()):
        super().__init__()
        self.messages = []
        self.pixels = list(pixels)
        self.secrets = tuple(secrets)
        self.privacy_failed = False

    def emit(self, record):
        rendered = logging.Formatter().format(record).encode()
        try:
            assert_private(rendered, self.pixels, self.secrets)
        except AssertionError:
            self.privacy_failed = True
            return  # never retain or print unsafe exception/stack/message text
        self.messages.append({"level": record.levelname, "message": record.getMessage()})


def failure_visible(name: str, case: dict) -> bool:
    status = 401 if name == "bad_auth" else None
    message = "bad_auth" if name == "bad_auth" else "unavailable shutdown:"
    return any(
        row["level"] in {"WARNING", "ERROR"}
        and message in row["message"]
        and ("rejecting auth" in row["message"] or "shutdown:" in row["message"])
        for row in case["logs"]
    ) and any(
        row["kind"] in {"auth_failure", "shutdown_undelivered"} and row["http_status"] == status
        for row in case["forwarding"]
    )


def wire_event(event: dict) -> dict:
    """Explicit producer boundary: Core JSON-compatible event data, not opaque strings."""
    result = deepcopy(event)
    result["result"] = event["result"].model_dump(mode="json")
    return result


def fresh_conservation(metrics: dict, elapsed: float) -> bool:
    age = metrics["as_of_seconds"]
    return (
        metrics["stats_available"]
        and isinstance(age, (int, float))
        and 0 <= age < elapsed - 0.25  # conservative margin: snapshot AFTER stimulus marker
        and metrics["accepted_total"] == metrics["written_total"] == 1
        and metrics["in_queue_total"] == metrics["dead_letter_total"] == metrics["residual"] == 0
    )


ABSENT_QUERY = (
    "MATCH (n) WHERE n.session_id = $sid OR n.node_id = $sid OR n.node_id = $tid "
    "RETURN count(n) AS count"
)


async def core_wire_probe() -> dict:
    """Offline real Core coordinator only: no CI hook, sinks, network or services."""
    from amplifier_core import ModuleCoordinator
    from amplifier_core.models import HookResult

    event, _pixels = fixture("synthetic-core-wire-probe")
    original = deepcopy(event)
    observed = []
    coordinator = ModuleCoordinator(
        SimpleNamespace(config={}, session_id=event["session_id"], parent_id=None)
    )

    async def observe(_name, data):
        observed.append(data)
        return HookResult(action="continue")

    coordinator.hooks.register("tool:post", observe, name="offline-wire-probe")
    await coordinator.hooks.emit("tool:post", event)
    assert len(observed) == 1 and isinstance(observed[0]["result"], str)
    wire = wire_event(event)
    await coordinator.hooks.emit("tool:post", wire)
    assert len(observed) == 2
    stamped = {**wire, "timestamp": observed[1]["timestamp"]}
    assert observed[1] == stamped
    assert event == original
    return {
        "typed_object_became_opaque_string": True,
        "explicit_model_dump_mapping_preserved": True,
        "typed_original_unchanged": True,
        "core_replaces_emitted_timestamp": observed[1]["timestamp"] != wire["timestamp"],
        "emitted": 2,
        "hook_server_executed": False,
        "raw_string_or_pixels_retained": False,
    }


async def hook_case(name: str, root: Path, token: str) -> dict:
    import httpx
    from amplifier_core import ModuleCoordinator
    import amplifier_module_hook_context_intelligence as module

    sid = "ci-hook-" + name + "-" + uuid4().hex
    event, pixels = fixture(sid)
    original = deepcopy(event)
    wire = wire_event(event)
    wire_original = deepcopy(wire)
    expected = expected_projection(event)
    config = {
        "base_path": str(root / "captures"),
        "project_slug": "synthetic",
        "workspace": "ci-hook-media-qualification",
        "forwarding_log_dir": str(root / "forwarding"),
        "dispatch_connect_timeout": 1,
        "dispatch_read_timeout": 2,
        "dispatch_timeout": 2,
        "close_drain_timeout": 0.1,
        "dispatch_failure_threshold": 1,
        # One initial request. No rapid retry census or real-time escalation wait.
        "dispatch_backoff_initial": 60,
        "dispatch_backoff_max": 60,
        "dispatch_backoff_jitter": False,
        "destinations": {
            name: {
                "url": UNAVAILABLE if name == "unavailable" else SERVER,
                "api_key": "synthetic-invalid-token" if name == "bad_auth" else token,
                "include": ["**"],
                "auth_mode": "static",
            }
        },
    }
    coordinator = ModuleCoordinator(SimpleNamespace(config={}, session_id=sid, parent_id=None))
    coordinator.register_capability("session.working_dir", "/synthetic/ci-hook")

    async def observe_core_boundary(_name, data):
        from amplifier_core.models import HookResult

        # Core stamps emission time. Preserve that actual stamp across BOTH sinks;
        # do not misrepresent it as the producer's incoming timestamp.
        assert data == {**wire_original, "timestamp": data["timestamp"]}
        expected["timestamp"] = data["timestamp"]
        return HookResult(action="continue")

    coordinator.hooks.register(
        "tool:post", observe_core_boundary, priority=-100, name="core-boundary-observer"
    )
    # Actual installed module entrypoint, real Core registry and ready lifecycle.
    entry = next(
        e
        for e in metadata.entry_points(group="amplifier.modules")
        if e.name == "hook-context-intelligence"
    )
    cleanup = await entry.load()(coordinator, config)
    requests, responses, outcomes, queued = [], [], [], []
    capture = LogCapture(pixels, (token,))
    logger = logging.getLogger(module.__name__)
    old_propagate = logger.propagate
    logger.propagate = False  # inspect formatted diagnostics before any presentation
    logger.addHandler(capture)
    try:
        await module.on_session_ready(coordinator)
        state = coordinator.get_capability("context_intelligence._hook_state")
        handler = state["logging_handler"]
        (dispatcher,) = handler._dispatchers
        enqueue = dispatcher.enqueue

        def observe_enqueue(event_name, data):
            assert data == expected
            queued.append({"event": event_name, "data": deepcopy(data)})
            return enqueue(event_name, data)  # outbound spy, never a replacement sink

        dispatcher.enqueue = observe_enqueue

        async def request_spy(request):
            assert len(requests) < 2
            body = json.loads(request.content)
            assert body["event"] == "tool:post" and body["data"] == expected
            assert_private(request.content, pixels, (token,))
            requests.append({"method": request.method, "url": str(request.url), "body": body})

        async def response_spy(response):
            receipt = {"status_code": response.status_code}
            if response.status_code == 202:
                await response.aread()
                body = response.json()
                # Do not retain arbitrary error bodies, headers or credentials.
                receipt["body"] = {key: body.get(key) for key in ("status", "session_id")}
            responses.append(receipt)

        dispatcher._client = httpx.AsyncClient(
            timeout=2,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"request": [request_spy], "response": [response_spy]},
        )
        post = dispatcher._post

        async def observe_outcome(event_name, data):
            outcome = await post(event_name, data)
            outcomes.append({"outcome": outcome, "http_status": dispatcher._last_status})
            return outcome

        dispatcher._post = observe_outcome
        await coordinator.hooks.emit("tool:post", wire)
        for _ in range(100):
            if outcomes:
                break
            await asyncio.sleep(0.05)
        assert outcomes
        assert event == original
        assert wire == wire_original
        path = state["resolver"].session_dir(sid) / "events.jsonl"
        # This is a one-record synthetic fixture created by this invocation, not user history.
        disk = json.loads(path.read_bytes())
        assert disk["event"] == "tool:post" and disk["data"] == expected
        assert disk["timestamp"] == expected["timestamp"]
        assert queued == [{"event": "tool:post", "data": expected}]
    finally:
        await cleanup()
        logger.removeHandler(capture)
        logger.propagate = old_propagate
    assert not capture.privacy_failed
    assert_private(json.dumps(capture.messages).encode(), pixels, (token,))
    forwarding = []
    for path in (root / "forwarding").glob("forwarding-*.jsonl"):
        assert path.stat().st_size < 2 * 1024 * 1024
        raw = path.read_bytes()  # only this invocation's synthetic diagnostic sink
        assert_private(raw, pixels, (token,))
        forwarding.extend(
            row for line in raw.splitlines() if (row := json.loads(line))["destination"] == name
        )
    return {
        "session_id": sid,
        "expected": expected,
        "producer_timestamp": original["timestamp"],
        "requests": requests,
        "responses": responses,
        "outcomes": outcomes,
        "queued": queued,
        "logs": capture.messages,
        "forwarding": forwarding,
        "pixels": pixels,
    }


async def evaluate(root: Path, report: dict, token: str) -> None:
    import httpx

    checks = report["checks"]
    report["installed"] = preflight()
    server = json.loads(Path("/backend-state/qualification-server.json").read_bytes())
    assert server["direct_url"]["vcs_info"]["commit_id"] == SERVER_REVISION
    report["server_installation"] = server
    checks["installed_sources"] = True
    async with httpx.AsyncClient(
        base_url=SERVER, timeout=5, trust_env=False, headers={"Authorization": "Bearer " + token}
    ) as client:

        async def get(path):
            response = await client.get(path)
            response.raise_for_status()
            return response.json()

        async def query(cypher, params=None):
            response = await client.post(
                "/cypher",
                json={
                    "query": cypher,
                    "params": params or {},
                    "workspace": "ci-hook-media-qualification",
                },
            )
            response.raise_for_status()
            return response.json()["results"]

        status = await get("/status")
        assert all(
            status[k] is True
            for k in (
                "neo4j_connected",
                "neo4j_query_connected",
                "identity_ready",
                "recovery_complete",
            )
        )
        assert status["recovery_error"] is None and status["auth"]["mode"] == "static"
        assert status["metrics"]["accepted_total"] == status["metrics"]["written_total"] == 0
        report["baseline_conservation"] = status["metrics"]
        version = await get("/version")
        assert version["version"] == server["version"]
        report["readiness"] = {
            k: status[k]
            for k in (
                "neo4j_connected",
                "neo4j_query_connected",
                "identity_ready",
                "recovery_complete",
                "auth",
            )
        }
        report["version"] = version
        checks["real_backend_ready"] = True
        apoc = await query(
            "CALL dbms.components() YIELD versions "
            "RETURN versions[0] AS neo4j, apoc.version() AS apoc"
        )
        assert len(apoc) == 1 and apoc[0]["neo4j"] == "5.26.22" and apoc[0]["apoc"]
        report["database"] = apoc
        checks["real_neo4j_apoc"] = True
        cases, pixels = {}, []
        for name in ("healthy", "bad_auth", "unavailable"):
            case = await hook_case(name, root, token)
            pixels.extend(case.pop("pixels"))
            # Validate before adding any transport evidence to the retained report.
            assert_private(json.dumps(case).encode(), pixels, (token,))
            cases[name] = case
            report["cases"] = cases
        checks["caller_unchanged"] = checks["local_and_enqueue"] = True
        healthy = cases["healthy"]
        assert healthy["responses"] == [
            {
                "status_code": 202,
                "body": {"status": "queued", "session_id": healthy["session_id"]},
            }
        ]
        assert healthy["outcomes"] == [{"outcome": "delivered", "http_status": 202}]
        checks["real_http_acceptance"] = True
        cypher = (
            "MATCH (n:Event) WHERE n.session_id = $sid AND n.event_name = 'tool:post' "
            "RETURN n.data AS data"
        )
        rows = []
        for _ in range(80):
            rows = await query(cypher, {"sid": healthy["session_id"]})
            if rows:
                break
            await asyncio.sleep(0.25)
        assert len(rows) == 1
        graph = json.loads(rows[0]["data"])
        ref = graph["result"]["$blob_ref"]
        assert ref.startswith("ci-blob://" + healthy["session_id"] + "/")
        blob = await get("/blobs/" + ref.removeprefix("ci-blob://"))
        # The endpoint returns stored JSON directly (not a guessed envelope).
        assert blob == healthy["expected"]["result"]
        expected = healthy["expected"]
        assert all(
            graph[k] == expected[k]
            for k in (
                "session_id",
                "event_id",
                "timestamp",
                "tool_call_id",
                "status",
                "usage",
            )
        )
        assert_private(json.dumps({"graph": graph, "blob": blob}).encode(), pixels, (token,))
        report["server_stored"] = {"query": cypher, "rows": rows, "result_blob": blob}
        checks["server_graph_and_blob"] = True
        bad, down = cases["bad_auth"], cases["unavailable"]
        assert bad["outcomes"] == [{"outcome": "transient", "http_status": 401}]
        assert bad["responses"] == [{"status_code": 401}]
        assert failure_visible("bad_auth", bad)
        checks["bad_auth_loud"] = True
        assert down["outcomes"] == [{"outcome": "transient", "http_status": None}]
        assert not down["responses"]
        assert failure_visible("unavailable", down)
        checks["unavailable_loud"] = True
        stimulus_complete = time.monotonic()
        metrics = {}
        for _ in range(80):
            requested_at = time.monotonic()
            metrics = (await get("/status"))["metrics"]
            if fresh_conservation(metrics, requested_at - stimulus_complete):
                break
            await asyncio.sleep(0.25)
        elapsed = requested_at - stimulus_complete
        assert fresh_conservation(metrics, elapsed)
        report["conservation_checkpoint"] = {
            "metrics": metrics,
            "requested_seconds_after_stimuli": elapsed,
        }
        report["failed_identity_absence"] = []
        for case in (bad, down):
            absent = await query(
                ABSENT_QUERY,
                {"sid": case["session_id"], "tid": "call-" + case["session_id"]},
            )
            assert absent == [{"count": 0}]
            report["failed_identity_absence"].append(
                {
                    "session_id": case["session_id"],
                    "query": ABSENT_QUERY,
                    "rows": absent,
                }
            )
            # Read ONLY fresh isolated synthetic queue/blob state, never host histories.
            for directory in ("queues", "blobs"):
                base = Path("/backend-state") / directory
                for path in base.rglob("*"):
                    assert not path.is_symlink()
                    assert case["session_id"] not in str(path.relative_to(base))
                    if path.is_file():
                        assert path.stat().st_size <= 2 * 1024 * 1024
                        assert case["session_id"].encode() not in path.read_bytes()
        checks["failed_sessions_not_ingested"] = True
        assert all(len(c["requests"]) == 1 for c in cases.values())
        checks["bounded_requests"] = True
        report["local_state_hashes"] = scan(root, pixels, (token,))
        report["backend_state_hashes"] = scan(Path("/backend-state"), pixels, (token,))
        checks["payload_free_diagnostics"] = True


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    run = sub.add_parser("run")
    run.add_argument(
        "--execute-isolated",
        action="store_true",
        required=True,
        help="explicit operator allocation; never inferred from preparation",
    )
    run.add_argument(
        "--output", type=Path, required=True, help="NEW output directory inside the twin"
    )
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "preflight":
        preflight()
        print('{"preflight": true, "seam_executed": false}')
        return 0
    token = os.environ.get("CI_QUALIFICATION_TOKEN", "")
    if not token or len(token) < 24:
        raise ValueError("isolated token of at least 24 characters required")
    root = args.output.resolve()
    root.mkdir(mode=0o700)  # refuse reuse, including an unknown previous execution
    os.environ["AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"] = str(root / "captures")
    report = {
        "schema": "ci-hook-media-evaluation-v1",
        "passed": False,
        "checks": dict.fromkeys(CHECKS, False),
        "execution": "started",
        "foundation_reference_not_loaded": FOUNDATION_REFERENCE,
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limitations": [
            "NOT a full bundle-validator verdict or program upstream acceptance",
            "NOT Foundation conversion, settings expansion, Entra or provider qualification",
            "NOT proof of pre-hook diagnostic safety or sustained outage escalation",
            "Only explicit Core ToolResult.model_dump JSON mapping emission is qualified",
            "Direct ToolResult object emission becomes an opaque string: UNQUALIFIED",
            "Core stamps emission timestamp; that actual stamp is compared at both sinks",
            "Hook namespace formatted logs and fresh backend files only; not other producers",
            "Synthetic deterministic N=1 per healthy/bad-auth/refused-connection condition",
        ],
    }
    try:
        report["core_wire_probe"] = asyncio.run(core_wire_probe())
        asyncio.run(asyncio.wait_for(evaluate(root, report, token), timeout=90))
        report["execution"] = "completed"
        report["passed"] = all(report["checks"].values())
    except Exception as exc:
        report["execution"] = "failed"
        report["error_type"] = type(exc).__name__  # never retain exception text/payload
    path = root / "evaluation.json"
    with path.open("x") as stream:
        json.dump(report, stream, indent=2)
    path.chmod(0o600)
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "checks": report["checks"],
                "execution": report["execution"],
            }
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
