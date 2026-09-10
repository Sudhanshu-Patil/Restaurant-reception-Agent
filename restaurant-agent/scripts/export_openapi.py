"""Write the agent's OpenAPI schema to docs/openapi.json.

python -m scripts.export_openapi
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# The app builds an AgentService at import time; give it a harmless key so a
# schema dump never needs real credentials.
os.environ.setdefault("GROQ_API_KEY", "schema-export")

from agent.api import app

OUT = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
