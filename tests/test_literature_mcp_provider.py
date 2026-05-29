from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.literature import retrieve_literature_sources


def test_retrieve_literature_sources_uses_mcp_command() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        script = root / "mock_mcp.py"
        script.write_text(
            "\n".join(
                [
                    "import json",
                    "import sys",
                    "query=''",
                    "for i,v in enumerate(sys.argv):",
                    "    if v == '--query' and i + 1 < len(sys.argv):",
                    "        query = sys.argv[i+1]",
                    "payload={'results':[{'title':f'MCP {query}','url':'https://example.org/paper'}]}",
                    "print(json.dumps(payload))",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        prev_provider = os.environ.get("WARD_OPENEVIDENCE_PROVIDER")
        prev_cmd = os.environ.get("WARD_OPENEVIDENCE_MCP_COMMAND")
        os.environ["WARD_OPENEVIDENCE_PROVIDER"] = "mcp"
        os.environ["WARD_OPENEVIDENCE_MCP_COMMAND"] = f"{sys.executable} {script}"
        try:
            payload = retrieve_literature_sources(
                {"search_targets": ["acute hepatitis guideline"], "clinical_classification": {}},
                max_queries=1,
                results_per_query=1,
                timeout=10,
            )
            assert payload["ok"] is True
            assert payload["provider_used"] == "mcp"
            assert int(payload["source_count"]) >= 1
            assert str(payload["sources"][0]["url"]).startswith("https://example.org/")
        finally:
            if prev_provider is None:
                os.environ.pop("WARD_OPENEVIDENCE_PROVIDER", None)
            else:
                os.environ["WARD_OPENEVIDENCE_PROVIDER"] = prev_provider
            if prev_cmd is None:
                os.environ.pop("WARD_OPENEVIDENCE_MCP_COMMAND", None)
            else:
                os.environ["WARD_OPENEVIDENCE_MCP_COMMAND"] = prev_cmd


if __name__ == "__main__":
    test_retrieve_literature_sources_uses_mcp_command()
    print(json.dumps({"ok": True, "message": "literature mcp provider tests passed"}))
