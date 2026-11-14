"""Record installed server identity in the isolated volume, then exec the real server."""

import json
from pathlib import Path

from provenance import verify_group, join_loaded


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
    with path.open("x") as stream:
        json.dump(provenance, stream)
    path.chmod(0o644)
    context_intelligence_server.cli.main([])


if __name__ == "__main__":
    main()
