"""Print the deterministic GraphRoute catalog-scale evaluation as JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from graphops.catalog_evaluation import evaluate_catalog_scale


if __name__ == "__main__":
    print(json.dumps(evaluate_catalog_scale(), indent=2))
