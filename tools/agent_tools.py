"""Tool functions for SSH-managed network device operations.

Two tools are exposed to the LLM:
- device_mgmt: single-device operation with predefined or custom commands
- device_bulk_exec: concurrent execution across multiple devices or a group
"""

from __future__ import annotations

import json
import re
import time

import paramiko


# ---------------------------------------------------------------------------
# Low-level SSH execution
# ---------------------------------------------------------------------------


def run_ssh_command(
    host: str,
    username: str,
    password: str,
    command: str,
    port: int = 22,
    timeout: int = 10,
) -> str:
    """Execute a single CLI command on a network device via SSH.

    Tries `invoke_shell` first (works well with most network OSes that
    require pagination handling and an interactive prompt). Falls back
    to `exec_command` if the shell path fails.

    Returns the cleaned command output, or an error string starting
    with `SSH ` on failure.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        try:
            shell = client.invoke_shell()
            time.sleep(1)
            if shell.recv_ready():
                shell.recv(65535)

            # Disable pagination on common platforms
            shell.send("terminal length 0\n")
            time.sleep(0.5)
            if shell.recv_ready():
                shell.recv(65535)

            shell.send(command + "\n")
            time.sleep(2)

            output = ""
            while shell.recv_ready():
                output += shell.recv(65535).decode("utf-8", errors="ignore")
                time.sleep(0.1)
            if not output:
                time.sleep(1)
                if shell.recv_ready():
                    output = shell.recv(65535).decode("utf-8", errors="ignore")
            shell.close()
        except Exception:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            output = stdout.read().decode("utf-8", errors="ignore")
            err = stderr.read().decode("utf-8", errors="ignore")
            if err:
                output = output + "\n" + err

        # Strip ANSI escapes and a leading echoed-command line
        output = re.sub(r"\x1b\[[0-9;]*[mGKHf]", "", output)
        lines = output.strip().split("\n")
        if lines and command in lines[0]:
            lines = lines[1:]
        return "\n".join(lines).strip()

    except paramiko.AuthenticationException:
        return f"SSH authentication failed: bad credentials ({username}@{host})"
    except paramiko.SSHException as exc:
        return f"SSH connection error: {exc}"
    except Exception as exc:
        return f"SSH execution error: {exc}"
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Per-platform action -> command maps
# ---------------------------------------------------------------------------


_ROUTER_COMMANDS = {
    "show_version": "show version",
    "show_run": "show running-config",
    "show_interface": "show ip interface brief",
    "show_platform": "show platform",
    "show_redundancy": "show redundancy",
    "show_isis_neighbor": "show isis neighbor",
    "show_bgp_sessions": "show bgp sessions",
}

_SWITCH_COMMANDS = {
    "show_version": "show version",
    "show_run": "show running-config",
    "show_interface": "show interface brief",
    "show_vlan": "show vlan brief",
    "show_routing_ipv6": "show routing ipv6 unicast vrf all",
    "show_hardware": "show hardware",
}

_GENERIC_COMMANDS = {
    "show_version": "show version",
    "show_run": "show running-config",
    "show_interface": "show interface brief",
}


def _command_map_for(device_type: str) -> dict:
    if device_type == "switch":
        return dict(_SWITCH_COMMANDS)
    if device_type == "router":
        return dict(_ROUTER_COMMANDS)
    return dict(_GENERIC_COMMANDS)


# ---------------------------------------------------------------------------
# Tool: device_mgmt — single device, predefined or custom command
# ---------------------------------------------------------------------------


def device_mgmt(action: str, device_id: str = "default", command: str = "") -> str:
    """Run a single command on a preconfigured device.

    Args:
        action: predefined verb (e.g. "show_version") or "custom" with `command`.
        device_id: id from `config/devices.yaml`.
        command: required when `action == "custom"`.

    Returns:
        JSON string with `ok`, `device`, `device_type`, `host`, `command`, `output`.
    """
    try:
        from .device_inventory import DeviceInventory
        inventory = DeviceInventory()
    except Exception as exc:
        return json.dumps(
            {"ok": False, "error": f"Failed to load inventory: {exc}"},
            ensure_ascii=False,
        )

    device = inventory.get_device(device_id)
    if not device:
        return json.dumps(
            {
                "ok": False,
                "error": f"Device '{device_id}' not found",
                "available": inventory.list_devices(),
            },
            ensure_ascii=False,
        )

    if "_password_error" in device:
        return json.dumps(
            {"ok": False, "error": f"Password decryption failed: {device['_password_error']}"},
            ensure_ascii=False,
        )

    device_type = device.get("device_type", "router")
    cmd_map = _command_map_for(device_type)
    cmd_map["custom"] = command

    if action not in cmd_map:
        return json.dumps(
            {
                "ok": False,
                "error": f"Action '{action}' not supported for device_type '{device_type}'",
                "supported_actions": list(cmd_map.keys()),
            },
            ensure_ascii=False,
        )

    cmd = cmd_map[action]
    if not cmd:
        return json.dumps(
            {"ok": False, "error": "action='custom' requires a non-empty command"},
            ensure_ascii=False,
        )

    output = run_ssh_command(
        host=device["host"],
        username=device["username"],
        password=device["password"],
        command=cmd,
        port=device.get("port", 22),
        timeout=device.get("timeout", 10),
    )
    success = not output.startswith("SSH ")

    return json.dumps(
        {
            "ok": success,
            "device": device_id,
            "device_type": device_type,
            "host": device["host"],
            "command": cmd,
            "output": output,
        },
        ensure_ascii=False,
    )


# ---------------------------------------------------------------------------
# Tool: device_bulk_exec — multiple devices, single command
# ---------------------------------------------------------------------------


def device_bulk_exec(devices: str, command: str, max_workers: int = 5) -> str:
    """Execute one CLI command concurrently across many devices.

    Args:
        devices: comma-separated id list (e.g. "router-1,router-2") or a
            group name from devices.yaml (e.g. "production").
        command: raw CLI string.
        max_workers: max concurrent SSH sessions.

    Returns:
        JSON string with `summary` (counts) and per-device `results`.
    """
    try:
        from .device_inventory import DeviceInventory, execute_on_devices
    except Exception as exc:
        return json.dumps(
            {"ok": False, "error": f"Failed to load inventory module: {exc}"},
            ensure_ascii=False,
        )

    inventory = DeviceInventory()

    if "," in devices:
        device_list = [d.strip() for d in devices.split(",") if d.strip()]
    else:
        device_list = [devices]

    results = execute_on_devices(device_list, command, inventory, max_workers)
    success = sum(1 for r in results.values() if r.get("ok"))
    total = len(results)

    return json.dumps(
        {
            "ok": True,
            "summary": {
                "total": total,
                "success": success,
                "failed": total - success,
            },
            "results": results,
        },
        ensure_ascii=False,
        indent=2,
    )


TOOLS_MAP = {
    "device_mgmt": device_mgmt,
    "device_bulk_exec": device_bulk_exec,
}
