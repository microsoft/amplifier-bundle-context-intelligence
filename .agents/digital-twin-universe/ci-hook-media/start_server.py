"""Record installed server identity in the isolated volume, then exec the real server."""

import json
import hashlib
from pathlib import Path

from provenance import verify_group, join_loaded


def save_receipt(path, value):
    """Preserve earlier verified bootstrap generations on this owned volume."""
    if path.is_symlink():
        raise ValueError("redirected server receipt")
    encoded = json.dumps(value).encode()
    if path.exists():
        with path.open("rb") as source:
            previous = source.read(1024 * 1024 + 1)
        if len(previous) > 1024 * 1024:
            raise ValueError("oversized server receipt")
        if previous == encoded:
            return
        archive = path.with_name(
            "qualification-server-" + hashlib.sha256(previous).hexdigest() + ".json"
        )
        if archive.exists() or archive.is_symlink():
            if archive.is_symlink() or archive.read_bytes() != previous:
                raise ValueError("server receipt archive conflict")
            path.unlink()
        else:
            path.rename(archive)
    with path.open("xb") as stream:
        stream.write(encoded)
    path.chmod(0o644)


def main():
    verified, proof = verify_group(["context-intelligence-server"])
    provenance = verified["context-intelligence-server"]
    # Stay in the same Python process as the real CLI/Gunicorn master; its worker
    # inherits these verified imports. Do not exec away the module-origin join.
    import context_intelligence_server.cli
    import context_intelligence_server.main

    proof["loaded_modules"] = join_loaded(
        list(verified.values()),
        [
            "context_intelligence_server.cli",
            "context_intelligence_server.main",
        ],
    )
    provenance.update(proof)
    path = Path("/state/qualification-server.json")
    save_receipt(path, provenance)
    context_intelligence_server.cli.main([])


if __name__ == "__main__":
    main()
