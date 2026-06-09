"""Network Operations Agent: LLM-driven tool-calling for SSH-managed devices.

Implements a self-contained tool-calling loop without depending on a
provider-specific function-calling API. The model is instructed to emit a
single JSON object when it needs a tool; the agent executes it, feeds the
result back as a `TOOL_RESULT:` user message, and loops up to
`MAX_TOOL_ROUNDS` times before returning a natural-language reply.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Allow `python examples/netops_agent.py` from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import LLMClient
from tools.agent_tools import TOOLS_MAP
from tools.device_inventory import DeviceInventory


# ---------------------------------------------------------------------------
# Output formatter — CLI-style display for tool results
# ---------------------------------------------------------------------------


_COLORS = {
    "green": "\033[92m",
    "red": "\033[91m",
    "cyan": "\033[96m",
    "yellow": "\033[93m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


def _highlight_lines(output: str) -> List[str]:
    """Apply lightweight syntax highlighting to device CLI output."""
    cyan, reset = _COLORS["cyan"], _COLORS["reset"]
    green, red = _COLORS["green"], _COLORS["red"]

    out: List[str] = []
    for line in output.strip().split("\n"):
        line = re.sub(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
            lambda m: f"{cyan}{m.group()}{reset}",
            line,
        )
        for token, color in (
            ("up", green), ("UP", green),
            ("down", red), ("DOWN", red),
            ("active", green), ("ACTIVE", green),
            ("established", green), ("ESTABLISHED", green),
        ):
            line = line.replace(token, f"{color}{token}{reset}")
        out.append(line)
    return out


def _format_device_output(result: str) -> str:
    """Render a JSON tool result as a structured CLI block."""
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return result

    cyan = _COLORS["cyan"]
    bold = _COLORS["bold"]
    reset = _COLORS["reset"]
    green = _COLORS["green"]
    red = _COLORS["red"]
    yellow = _COLORS["yellow"]

    if "device" in data and "output" in data:
        device_id = data.get("device", "unknown")
        device_type = data.get("device_type", "unknown")
        host = data.get("host", "unknown")
        command = data.get("command", "unknown")
        output = data.get("output", "")
        ok = data.get("ok", False)

        status_icon = f"{green}OK{reset}" if ok else f"{red}FAIL{reset}"
        return (
            f"\n{cyan}{'=' * 80}{reset}\n"
            f"[{status_icon}] Device: {bold}{device_id}{reset} ({device_type})\n"
            f"  Host: {host}\n"
            f"{cyan}{'-' * 80}{reset}\n"
            f"Command: {yellow}{command}{reset}\n"
            f"{cyan}{'-' * 80}{reset}\n"
            f"{chr(10).join(_highlight_lines(output))}\n"
            f"{cyan}{'=' * 80}{reset}\n"
        )

    if "results" in data:
        summary = data.get("summary", {})
        results = data.get("results", {})
        total = summary.get("total", 0)
        ok_count = summary.get("success", 0)
        fail_count = summary.get("failed", 0)

        lines = [
            f"\n{cyan}{'=' * 80}{reset}",
            f"{bold}Bulk Execution Summary{reset}",
            f"  Total: {total} devices",
            f"  Success: {green}{ok_count}{reset}",
            f"  Failed: {red}{fail_count}{reset}",
            f"{cyan}{'=' * 80}{reset}\n",
        ]
        for idx, (dev_id, dev_result) in enumerate(results.items()):
            if idx > 0:
                lines.append(f"\n{cyan}{'=' * 80}{reset}\n")
            ok = dev_result.get("ok", False)
            host = dev_result.get("host", "unknown")
            output = dev_result.get("output", "")
            status_icon = f"{green}OK{reset}" if ok else f"{red}FAIL{reset}"
            lines.extend([
                f"[{status_icon}] Device: {bold}{dev_id}{reset}",
                f"  Host: {host}",
                f"{cyan}{'-' * 80}{reset}",
            ])
            if output:
                lines.extend(_highlight_lines(output))
            else:
                lines.append("  (no output)")
        lines.append(f"\n{cyan}{'=' * 80}{reset}")
        return "\n".join(lines)

    return result


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


def _build_inventory_summary(inventory: DeviceInventory) -> str:
    """Render the inventory as a compact text block for the system prompt."""
    if not inventory.list_devices():
        return "(no devices configured)"

    lines = ["Available devices:"]
    for dev_id in inventory.list_devices():
        device = inventory.get_device(dev_id) or {}
        host = device.get("host", "?")
        dtype = device.get("device_type", "unknown")
        lines.append(f"  - {dev_id} ({host}) — {dtype}")

    if inventory.list_groups():
        lines.append("Device groups:")
        for grp in inventory.list_groups():
            members = inventory.get_group_devices(grp)
            lines.append(f"  - {grp}: {members}")
    return "\n".join(lines)


SYSTEM_PROMPT_TEMPLATE = """You are a network operations assistant with access to two tools.

When you need to invoke a tool, output a single JSON object only — no prose:
  {{"action": "<tool_name>", "args": {{...}}}}

After the tool runs, the next user message will be:
  TOOL_RESULT: <json>
At that point, summarize the result in plain English for the operator.

If no tool is needed, reply directly in plain English.

{inventory_block}

Tools:
- device_mgmt(action, device_id, command?)
    Single-device operation against a preconfigured device.
    `action` is one of the predefined verbs (e.g. show_version, show_interface,
    show_bgp_sessions) or "custom" with an explicit `command`.
    Examples:
      {{"action":"device_mgmt","args":{{"device_id":"router-1","action":"show_version"}}}}
      {{"action":"device_mgmt","args":{{"device_id":"router-1","action":"custom","command":"show run router bgp"}}}}

- device_bulk_exec(devices, command, max_workers?)
    Execute a single CLI command concurrently across many devices or a group.
    `devices` is a comma-separated id list or a group name.
    `command` is the raw CLI string (with spaces, e.g. "show interface").
    Example:
      {{"action":"device_bulk_exec","args":{{"devices":"production","command":"show version"}}}}

Guidelines:
- Prefer device_mgmt for one device + a predefined action.
- Use device_bulk_exec for multiple devices or arbitrary CLI commands.
- Output pure JSON when calling a tool — no commentary, no markdown fences.
"""


class NetOpsAgent:
    """LLM-driven agent for natural-language network device operations."""

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client or LLMClient()
        self.inventory = DeviceInventory()
        self.history: List[Dict[str, str]] = []
        self.system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            inventory_block=_build_inventory_summary(self.inventory),
        )
        self.history.append({"role": "system", "content": self.system_prompt})
        self.tools: Dict[str, Callable[..., Any]] = TOOLS_MAP
        self.debug = bool(int(os.getenv("AGENT_DEBUG", "1")))
        self.max_tool_rounds = int(os.getenv("MAX_TOOL_ROUNDS", "3"))

    def _call_model(self, user_content: str) -> str:
        self.history.append({"role": "user", "content": user_content})
        resp = self.client.chat_completion(self.history, stop=["<|im_end|>"])
        reply = resp["choices"][0]["message"]["content"]
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def _extract_tool_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Extract tool JSON from a model reply, tolerating wrapping prose."""
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict) and "action" in obj:
                    return obj
            except json.JSONDecodeError:
                return None
        first, last = text.find("{"), text.rfind("}")
        if first >= 0 and last > first:
            try:
                obj = json.loads(text[first:last + 1])
                if isinstance(obj, dict) and "action" in obj:
                    return obj
            except json.JSONDecodeError:
                return None
        return None

    def chat(self, user_input: str) -> str:
        """Process one user input, looping through tool calls as needed."""
        pending = user_input
        rounds = 0
        final_reply = ""
        while rounds <= self.max_tool_rounds:
            reply = self._call_model(pending)
            spec = self._extract_tool_json(reply)
            if spec is None:
                final_reply = reply
                break
            action = spec.get("action")
            args = spec.get("args", {}) or {}
            if self.debug:
                print(f"[debug] tool request: {spec}")
            if action not in self.tools:
                final_reply = f"Model requested unknown tool: {action}"
                break
            try:
                result = self.tools[action](**args)
                if self.debug:
                    print("\n[debug] tool result:")
                    print(_format_device_output(result))
            except Exception as exc:
                result = f"Tool execution error: {exc}"
            pending = f"TOOL_RESULT: {json.dumps(result, ensure_ascii=False)}"
            rounds += 1
        if rounds > self.max_tool_rounds and not final_reply:
            final_reply = "Reached maximum tool-calling rounds; aborting."
        return final_reply

    def save(self, filename: str) -> None:
        Path(filename).write_text(
            json.dumps(self.history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        self.history = [self.history[0]]


def main() -> None:
    agent = NetOpsAgent()
    print("=== Network Operations Agent ===")
    print("Commands: quit | clear | save <filename>")
    print("Set AGENT_DEBUG=0 to silence tool-call traces.\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        if not user_input:
            continue
        cmd = user_input.lower()
        if cmd == "quit":
            print("bye.")
            break
        if cmd == "clear":
            agent.clear()
            print("(context cleared)")
            continue
        if cmd.startswith("save"):
            _, *rest = user_input.split()
            name = rest[0] if rest else "conversation.json"
            agent.save(name)
            print(f"(saved to {name})")
            continue

        try:
            reply = agent.chat(user_input)
            print(f"\nagent> {reply}\n")
        except Exception as exc:
            print(f"(error: {exc})")


if __name__ == "__main__":
    main()
