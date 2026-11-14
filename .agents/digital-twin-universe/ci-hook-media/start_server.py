"""Record installed server identity in the isolated volume, then exec the real server."""

import json
import os
from pathlib import Path

from qualify import SERVER_REVISION, installed

provenance = installed("context-intelligence-server", SERVER_REVISION)
path = Path("/state/qualification-server.json")
with path.open("x") as stream:
    json.dump(provenance, stream)
path.chmod(0o644)
os.execvp("context-intelligence-server", ["context-intelligence-server"])
