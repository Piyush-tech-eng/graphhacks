"""Print reproducible fixture benchmark results without starting the web server."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from graphops.engine import GraphOpsEngine

if __name__ == "__main__":
    print(json.dumps(GraphOpsEngine().benchmark(), indent=2))
