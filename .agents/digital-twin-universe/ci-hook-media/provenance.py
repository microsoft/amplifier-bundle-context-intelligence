"""Independent build references and fail-closed installed payload/RECORD joins.

Reference inputs are pinned Git archives and downloaded wheel ZIPs, NEVER the
installed tree. Root-owned build references plus their digest are the trust
boundary; this is not attestation against a malicious image builder/root.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.metadata as metadata
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import tarfile
import tomllib
import zipfile

CI = "e6de12c343a533187b836f4a99042c415eed2f18"
SERVER = "1622b48b05fcd93f935e5d55b962081a2c0cb229"
REFERENCE = Path("/qualification/reference.json")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def regular(path: Path) -> bytes:
    require(not path.is_symlink() and path.is_file(), "regular file required")
    # Refuse symlink parents, including a redirected package tree.
    require(all(not p.is_symlink() for p in path.parents), "symlink parent refused")
    return path.read_bytes()


def safe_relative(value: str) -> str:
    parts = PurePosixPath(value).parts
    require(
        bool(parts) and not value.startswith("/") and ".." not in parts, "unsafe reference path"
    )
    return value


def record_rows(data: bytes) -> dict:
    rows = {}
    for row in csv.reader(io.StringIO(data.decode())):
        require(len(row) == 3 and row[0] not in rows, "invalid/duplicate RECORD row")
        rows[row[0]] = (row[1], row[2])
    require(bool(rows), "empty RECORD")
    return rows


def record_matches(data: bytes, hash_value: str, size: str) -> bool:
    # This qualification uses SHA256 records, not weak/fallback algorithms.
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return hash_value == "sha256=" + encoded and size == str(len(data))


def archive_reference(archive: Path, revision: str, prefix: str, project: str) -> dict:
    """Git archive bytes fetched by full SHA separately from the installer."""
    files = {}
    with tarfile.open(archive, "r:") as source:
        require(source.pax_headers.get("comment") == revision, "Git archive commit mismatch")
        members = {m.name: m for m in source.getmembers()}
        config = source.extractfile(members[project + "pyproject.toml"])
        require(config is not None, "missing source project metadata")
        info = tomllib.loads(config.read().decode())["project"]
        for name, member in members.items():
            if not name.startswith(prefix + "/") or member.isdir():
                continue
            require(member.isfile(), "nonregular source payload refused")
            stream = source.extractfile(member)
            require(stream is not None, "missing archive payload")
            relative = safe_relative(name[len(project) :])
            files[relative] = digest(stream.read())
    root = prefix[len(project) :]
    require(bool(files) and root + "/__init__.py" in files, "empty package source")
    return {
        "package": info["name"],
        "version": info["version"],
        "revision": revision,
        "kind": "git-archive",
        "source_url": (
            "https://github.com/microsoft/amplifier-bundle-context-intelligence"
            if revision == CI
            else "https://github.com/microsoft/amplifier-context-intelligence"
        ),
        "input_sha256": digest(regular(archive)),
        "roots": [root],
        "files": files,
    }


def wheel_reference(wheel: Path, package: str, version: str) -> dict:
    """Expected bytes originate in the wheel ZIP, before installation."""
    with zipfile.ZipFile(wheel) as source:
        names = [safe_relative(n) for n in source.namelist() if not n.endswith("/")]
        require(len(names) == len(set(names)), "duplicate wheel member")
        # Unsupported .data relocation must fail, never silently lose a payload.
        require(not any(".data/" in n for n in names), "wheel relocation unsupported")
        records = [n for n in names if n.endswith(".dist-info/RECORD")]
        require(len(records) == 1, "wheel RECORD required")
        rows = record_rows(source.read(records[0]))
        require(set(rows) == set(names), "wheel RECORD coverage mismatch")
        files = {}
        for name in names:
            value = source.read(name)
            hash_value, size = rows[name]
            if name == records[0]:
                require((hash_value, size) == ("", ""), "wheel RECORD self hash")
            else:
                require(record_matches(value, hash_value, size), "wheel RECORD hash mismatch")
                files[name] = digest(value)
        dist_info = records[0].split("/")[0]
        fields = source.read(dist_info + "/METADATA").decode()
        declared = dict(re.findall(r"^(Name|Version): (.+)$", fields, flags=re.MULTILINE))
        require(declared == {"Name": package, "Version": version}, "wrong wheel identity")
    roots = sorted({n.split("/")[0] for n in files if not n.startswith(dist_info + "/")})
    require(bool(roots), "wheel payload empty")
    return {
        "package": package,
        "version": version,
        "revision": None,
        "kind": "wheel",
        "input_sha256": digest(regular(wheel)),
        "roots": roots,
        "files": files,
    }


def load_reference(path: Path = REFERENCE) -> tuple[dict, str]:
    raw = regular(path)
    expected = regular(path.with_suffix(".sha256")).decode().strip()
    require(re.fullmatch("[0-9a-f]{64}", expected) is not None, "invalid manifest digest")
    require(digest(raw) == expected, "reference manifest digest mismatch")
    value = json.loads(raw)
    require(value["schema"] == "ci-independent-payload-v1", "reference schema mismatch")
    return value, expected


def verify_harness(reference: dict, directory: Path) -> dict:
    files = reference["harness"]
    require(bool(files) and "provenance.py" in files, "harness reference incomplete")
    for name, expected in files.items():
        require(
            digest(regular(directory / safe_relative(name))) == expected, "harness payload drift"
        )
    return files


def verify_distribution(dist, expected: dict) -> dict:
    """RECORD integrity AND independent reference equality; neither alone suffices."""
    require(dist.metadata["Name"] == expected["package"], "installed name mismatch")
    require(dist.version == expected["version"], "installed version mismatch")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    require(not direct.get("dir_info", {}).get("editable"), "editable install refused")
    if expected["revision"]:
        require(
            direct.get("vcs_info", {}).get("commit_id") == expected["revision"],
            "installed revision mismatch",
        )
        if "source_url" in expected:
            require(
                direct.get("url", "").removeprefix("git+").removesuffix(".git")
                == expected["source_url"],
                "installed source URL mismatch",
            )
    rows = record_rows((dist.read_text("RECORD") or "").encode())
    root = Path(dist.locate_file("")).resolve()
    record_files = [n for n in rows if n.endswith(".dist-info/RECORD")]
    require(len(record_files) == 1, "installed RECORD missing")
    record_name = record_files[0]
    for name, (hash_value, size) in rows.items():
        value = regular(Path(dist.locate_file(name)))
        if name == record_name:
            require((hash_value, size) == ("", ""), "installed RECORD self hash")
        else:
            require(record_matches(value, hash_value, size), "installed RECORD hash mismatch")
    for name, expected_hash in expected["files"].items():
        safe_relative(name)
        require(name in rows, "expected payload absent from RECORD")
        require(digest(regular(root / name)) == expected_hash, "independent payload mismatch")
    # No unrecorded/extra package code, caches or data. Reject .pyc rather than
    # silently allowing a stale/mutated bytecode module to execute.
    actual = set()
    for package in expected["roots"]:
        base = root / safe_relative(package)
        require(base.is_dir() and not base.is_symlink(), "package root missing/redirected")
        for file in base.rglob("*"):
            require(not file.is_symlink(), "payload symlink refused")
            if file.is_file():
                actual.add(str(file.relative_to(root)))
    wanted = {n for n in expected["files"] if n.split("/")[0] in expected["roots"]}
    require(actual == wanted, "package payload set mismatch")
    return {
        "package": expected["package"],
        "version": dist.version,
        "direct_url": direct,
        "reference_input_sha256": expected["input_sha256"],
        "record_sha256": digest((dist.read_text("RECORD") or "").encode()),
        "files": expected["files"],
        "site_root": str(root),
        "roots": expected["roots"],
        "record_verified": True,
        "independent_payload_verified": True,
    }


def installed(name: str, reference: dict) -> dict:
    expected = reference["distributions"][name]
    return verify_distribution(metadata.distribution(name), expected)


def join_loaded(verified: list[dict], required: list[str], modules=None) -> dict:
    """Join actually imported module origins to verified independent payload bytes."""
    modules = sys.modules if modules is None else modules
    allowed = {}
    prefixes = set()
    for receipt in verified:
        prefixes.update(r for r in receipt["roots"] if "." not in r)
        for name, sha256 in receipt["files"].items():
            allowed[str(Path(receipt["site_root"]) / name)] = sha256
    matched = {}
    for name, module in list(modules.items()):
        if name.split(".")[0] not in prefixes:
            continue
        path = getattr(module, "__file__", None)
        require(isinstance(path, str), "loaded package origin unavailable")
        require(path in allowed, "loaded module outside verified payload")
        require(digest(regular(Path(path))) == allowed[path], "loaded module payload drift")
        matched[name] = {"file": path, "sha256": allowed[path]}
    require(all(name in matched for name in required), "required module not loaded/verified")
    return matched


def verify_group(names: list[str], directory: Path = Path("/qualification")) -> tuple[dict, dict]:
    reference, manifest_hash = load_reference(directory / "reference.json")
    harness = verify_harness(reference, directory)
    receipts = {name: installed(name, reference) for name in names}
    return receipts, {
        "reference_sha256": manifest_hash,
        "harness": harness,
        "independent_payloads": {n: r["reference_input_sha256"] for n, r in receipts.items()},
    }


def build(args) -> None:
    entries = [
        archive_reference(args.ci_archive, CI, "context_intelligence", ""),
        archive_reference(
            args.ci_archive,
            CI,
            "modules/hook-context-intelligence/amplifier_module_hook_context_intelligence",
            "modules/hook-context-intelligence/",
        ),
        archive_reference(args.server_archive, SERVER, "context_intelligence_server", ""),
    ]
    for package, version, pattern in (
        ("amplifier-core", "2.0.2", "amplifier_core-2.0.2-*.whl"),
        ("pillow", "13.0.0", "pillow-13.0.0-*.whl"),
    ):
        wheels = list(args.wheels.glob(pattern))
        require(len(wheels) == 1, "one exact wheel required")
        # PyPI metadata uses Pillow or pillow depending on the release.
        with zipfile.ZipFile(wheels[0]) as wheel:
            meta = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
            actual = re.search(r"^Name: (.+)$", wheel.read(meta).decode(), re.MULTILINE)[1]
        require(actual.lower().replace("_", "-") == package, "wheel package mismatch")
        entry = wheel_reference(wheels[0], actual, version)
        entry["lookup"] = "Pillow" if package == "pillow" else package
        entries.append(entry)
    # Frozen reviewed manifest supplied separately from the copied execution
    # files. Dockerfile pins its digest; never regenerate expected hashes from
    # whichever harness bytes happened to be copied into the image.
    harness = json.loads(regular(args.harness_manifest))
    verify_harness({"harness": harness}, args.harness)
    value = {
        "schema": "ci-independent-payload-v1",
        "distributions": {e.get("lookup", e["package"]): e for e in entries},
        "harness": harness,
        "harness_manifest_sha256": digest(regular(args.harness_manifest)),
    }
    raw = canonical(value)
    args.output.write_bytes(raw)
    args.output.with_suffix(".sha256").write_text(digest(raw) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci-archive", type=Path, required=True)
    parser.add_argument("--server-archive", type=Path, required=True)
    parser.add_argument("--wheels", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--harness-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args())
