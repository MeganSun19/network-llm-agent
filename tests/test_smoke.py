"""Offline smoke test for network-llm-agent.

No real LLM call, no real SSH. Verifies:
  - all imports resolve
  - LLMConfig defaults
  - LLMClient.chat_completion returns a dict with the indexing path
    expected by NetOpsAgent (resp["choices"][0]["message"]["content"])
  - _extract_tool_json handles plain JSON and JSON-with-prose
  - DeviceInventory loads the placeholder yaml without crashing
  - device_mgmt returns a structured error for an unknown device id
  - NetOpsAgent end-to-end: mocked LLM emits a tool call -> agent
    parses, runs the tool (which fails on fake device), feeds the
    TOOL_RESULT back, mocked LLM produces a final natural-language
    reply.

Run:
    pytest -q tests/
or
    python tests/test_smoke.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Required by LLMConfig.from_env() during NetOpsAgent.__init__
os.environ.setdefault("OPENAI_API_KEY", "sk-smoke-test")
os.environ.setdefault("AGENT_DEBUG", "0")


def test_imports():
    from services import LLMClient, LLMConfig  # noqa: F401
    from tools.agent_tools import (  # noqa: F401
        TOOLS_MAP, device_bulk_exec, device_mgmt, run_ssh_command,
    )
    from tools.device_inventory import DeviceInventory  # noqa: F401
    from examples.netops_agent import NetOpsAgent  # noqa: F401
    from examples.basic_agent import BasicAgent  # noqa: F401


def test_llm_config_from_env():
    from services import LLMConfig
    cfg = LLMConfig.from_env()
    assert cfg.api_key == "sk-smoke-test"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.timeout > 0


def test_llm_client_returns_dict_with_choices():
    from services import LLMClient, LLMConfig
    cfg = LLMConfig(api_key="sk-smoke-test", model="gpt-4o-mini")
    client = LLMClient(cfg)

    fake = MagicMock()
    fake.model_dump.return_value = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "hello"},
            "finish_reason": "stop",
        }],
    }
    with patch.object(client._client.chat.completions, "create", return_value=fake):
        resp = client.chat_completion([{"role": "user", "content": "hi"}])
    # NetOpsAgent / BasicAgent rely on this exact shape:
    assert resp["choices"][0]["message"]["content"] == "hello"


def test_extract_tool_json():
    from examples.netops_agent import NetOpsAgent
    # Bypass __init__ — we only want the helper
    agent = NetOpsAgent.__new__(NetOpsAgent)

    # 1) Pure JSON
    spec = agent._extract_tool_json(
        '{"action":"device_mgmt","args":{"device_id":"x","action":"show_version"}}'
    )
    assert spec is not None and spec["action"] == "device_mgmt"

    # 2) JSON with surrounding prose
    spec = agent._extract_tool_json(
        'I will run a command:\n{"action":"device_mgmt","args":{"device_id":"x","action":"show_version"}}\nDone.'
    )
    assert spec is not None and spec["action"] == "device_mgmt"

    # 3) No JSON at all
    assert agent._extract_tool_json("Sorry, I cannot do that.") is None

    # 4) Malformed JSON
    assert agent._extract_tool_json("{not valid json}") is None


def test_device_inventory_loads_without_crash():
    from tools.device_inventory import DeviceInventory
    inv = DeviceInventory()
    devices = inv.list_devices()
    # placeholder yaml ships with three entries
    assert "router-1" in devices
    assert "switch-1" in devices
    # group resolution works
    groups = inv.list_groups()
    assert "production" in groups


def test_device_mgmt_unknown_device():
    from tools.agent_tools import device_mgmt
    out = json.loads(device_mgmt(action="show_version", device_id="does-not-exist"))
    assert out["ok"] is False
    assert "not found" in out["error"].lower()


def test_netops_agent_end_to_end_with_mocked_llm():
    """The full loop: model -> tool call -> tool result -> final reply."""
    from examples.netops_agent import NetOpsAgent

    # Mock the LLM so we control the conversation deterministically.
    agent = NetOpsAgent()

    # First call: model emits a tool-call JSON
    first = MagicMock()
    first.model_dump.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "action": "device_mgmt",
                    "args": {"device_id": "does-not-exist", "action": "show_version"},
                }),
            },
        }],
    }
    # Second call: model produces final natural-language reply
    second = MagicMock()
    second.model_dump.return_value = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "The device 'does-not-exist' is not in the inventory.",
            },
        }],
    }

    with patch.object(
        agent.client._client.chat.completions,
        "create",
        side_effect=[first, second],
    ):
        reply = agent.chat("show me the version of does-not-exist")

    assert "not in the inventory" in reply
    # History should contain: system, user, assistant(JSON), user(TOOL_RESULT),
    # assistant(final reply). At minimum 5 entries.
    assert len(agent.history) >= 5


if __name__ == "__main__":
    fns = [
        test_imports,
        test_llm_config_from_env,
        test_llm_client_returns_dict_with_choices,
        test_extract_tool_json,
        test_device_inventory_loads_without_crash,
        test_device_mgmt_unknown_device,
        test_netops_agent_end_to_end_with_mocked_llm,
    ]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"[FAIL] {fn.__name__}: {exc}")
    if failed:
        print(f"\n{failed} test(s) failed.")
        sys.exit(1)
    print(f"\nAll {len(fns)} tests passed.")
