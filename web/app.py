"""Web UI for the Network Operations Agent.

Lightweight Flask wrapper around `examples.netops_agent.NetOpsAgent`,
exposing a chat endpoint and a device-inventory endpoint. Per-session
agents are kept in memory (sufficient for single-process demos; swap
in Redis or a DB for multi-process production use).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

# Make project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.netops_agent import NetOpsAgent  # noqa: E402
from tools.device_inventory import DeviceInventory  # noqa: E402


app = Flask(__name__)
CORS(app)

inventory = DeviceInventory()
agents: Dict[str, NetOpsAgent] = {}


def get_or_create_agent(session_id: str) -> NetOpsAgent:
    if session_id not in agents:
        agents[session_id] = NetOpsAgent()
    return agents[session_id]


@app.route("/")
def index():
    return render_template("index.html", cache_buster=int(time.time()))


@app.route("/api/devices", methods=["GET"])
def get_devices():
    devices = {}
    for device_id in inventory.list_devices():
        device = inventory.get_device(device_id) or {}
        safe_device = {
            k: v for k, v in device.items()
            if k not in ("password", "_password_error")
        }
        devices[device_id] = safe_device
    return jsonify({
        "devices": devices,
        "groups": {
            name: inventory.get_group_devices(name)
            for name in inventory.list_groups()
        },
    })


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        data = request.json or {}
        user_message = data.get("message")
        session_id = data.get("session_id", "default")

        if not user_message:
            return jsonify({"error": "Message is required"}), 400

        agent = get_or_create_agent(session_id)

        # Wrap each tool to capture (tool, args, result) for the UI
        tool_calls_info = []
        original_tools = agent.tools.copy()
        for tool_name, tool_func in original_tools.items():
            def make_wrapper(name, func):
                def wrapper(*args, **kwargs):
                    result = func(*args, **kwargs)
                    tool_calls_info.append({
                        "tool": name,
                        "args": kwargs if kwargs else args,
                        "result": result,
                    })
                    return result
                return wrapper
            agent.tools[tool_name] = make_wrapper(tool_name, tool_func)

        response = agent.chat(user_message)
        agent.tools = original_tools

        return jsonify({
            "response": response,
            "session_id": session_id,
            "tool_calls": tool_calls_info,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "service": "network-llm-agent",
        "devices_count": len(inventory.list_devices()),
    })


if __name__ == "__main__":
    print("Starting Network Operations Agent web server...")
    print("URL:     http://localhost:5000")
    print(f"Devices: {len(inventory.list_devices())}")
    print(f"Groups:  {len(inventory.list_groups())}")
    app.run(host="0.0.0.0", port=5000, debug=True)
