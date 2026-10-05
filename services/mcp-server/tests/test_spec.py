import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("export_tool_spec", ROOT / "scripts" / "export_tool_spec.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_tool_spec_matches_live_tools():
    """If this fails, tools changed: run `python scripts/export_tool_spec.py` and commit the result."""
    committed = json.loads((ROOT / "docs" / "mcp-tool-spec.json").read_text())
    assert committed == json.loads(json.dumps(_load().build_spec()))


def test_every_documented_error_code_is_producible():
    from app.tools import agent_message
    from app.adapter_client import AdapterError
    codes = {e["code"] for e in _load().ERRORS}
    produced = {m.split(":")[0] for m in (agent_message(AdapterError(c, "x")) for c in
                ["rate_limited", "not_found", "credentials_not_found", "upstream_auth_error",
                 "validation_error", "upstream_error"])}
    assert codes == produced


def test_descriptions_are_clean_single_paragraphs():
    for t in _load().build_spec()["tools"]:
        assert "\n" not in t["description"] and "  " not in t["description"]
