# network-llm-agent

LLM-driven natural-language frontend for SSH-managed network devices.
Type a question in English; the agent decides which CLI command to run,
executes it across one or many devices over SSH, and returns a
plain-language summary plus the raw output.

> A practical demonstration of LLM tool-calling applied to network
> operations. Validated end-to-end against Cisco NX-OS and IOS-XR
> devices in lab.

## About this release

This codebase was extracted, sanitized, and re-released in 2026 from
an internal prototype I built in **2024**. It reflects the LLM
tool-calling patterns of that period — a hand-rolled prompt-driven
JSON loop, predating the OpenAI Responses API and the maturing of
mainstream agent frameworks (LangGraph, LlamaIndex agents, Strands,
etc.). The architecture has **not been refreshed since**; treat this
as a snapshot of "what shipped in 2024", not a state-of-the-art
reference. A 2026 rebuild would likely use a managed agent SDK and
structured output APIs.

## Why this exists

Most "AI for networks" demos stop at chat. This project closes the
loop: it turns intent ("show me BGP sessions on every router in the
production group") into actual device transactions, with explicit,
auditable tool calls in between.

The design is intentionally minimal:

- **No vendor SDKs.** Plain SSH + paramiko, so any device that speaks
  a CLI works.
- **No framework lock-in.** Provider-agnostic OpenAI-compatible client;
  swap between OpenAI, Azure OpenAI, vLLM, OpenRouter, Ollama, or any
  other compatible endpoint by changing one env var.
- **Prompt-driven tool calling, not OpenAI function-calling API.**
  The model emits a JSON object describing the tool call; the agent
  parses it, runs the tool, feeds the result back as
  `TOOL_RESULT: <json>`, and loops. Works against any chat model.

## Architecture

```
 user prompt
     |
     v
 NetOpsAgent.chat()
     |
     |---> services.LLMClient ---> chat completion API
     |                                 |
     |<--- assistant reply (JSON) -----+
     |
     | parse {"action": "...", "args": {...}}
     v
 tools.agent_tools
     |
     |---> device_mgmt(device_id, action, command?)
     |       └── tools.device_inventory.get_device()
     |       └── paramiko SSH (invoke_shell + exec_command fallback)
     |
     |---> device_bulk_exec(devices|group, command, max_workers)
     |       └── ThreadPoolExecutor → SSH per device
     |
     v
 TOOL_RESULT fed back to model → natural-language summary
```

Up to `MAX_TOOL_ROUNDS` (default 3) tool-call rounds per user turn.

## Quick start (CLI)

```bash
git clone https://github.com/MeganSun19/network-llm-agent.git
cd network-llm-agent

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
#  edit .env: set OPENAI_API_KEY, optionally OPENAI_MODEL / OPENAI_BASE_URL

# Generate Fernet key, encrypt your real device passwords,
# then paste the ciphertext into config/devices.yaml.
python scripts/encrypt_password.py

# Edit config/devices.yaml with real hosts, usernames,
# encrypted passwords, and device_type (router|switch).

python examples/netops_agent.py
```

Example interactions:

```
you> show version on router-1
you> get BGP sessions across the routers group
you> show me running-config of interface loopback0 on router-1
```

A simpler chat-only loop (no tools) is in `examples/basic_agent.py`.

## Web UI

```bash
python web/app.py
# http://localhost:5000
```

The page renders the device inventory, a chat box, and per-message
tool-call traces (which device, which command, raw output).

## Docker / VM deployment

```bash
docker build -t network-llm-agent .
docker run -d --name network-llm-agent \
    --restart unless-stopped \
    -p 5000:5000 \
    --env-file ./.env \
    -v "$(pwd)/config:/app/config:ro" \
    network-llm-agent
```

A reference one-shot script for a single Linux host is provided in
`deploy-vm.sh` (clone → build → restart). Adjust before using.

## LLM backend compatibility

Anything OpenAI-Chat-Completions-compatible works. Examples:

| Backend          | OPENAI_BASE_URL                                |
|------------------|------------------------------------------------|
| OpenAI (default) | (unset, defaults to `https://api.openai.com/v1`) |
| Azure OpenAI     | `https://<resource>.openai.azure.com/openai/deployments/<deployment>` |
| vLLM             | `http://<vllm-host>:8000/v1`                   |
| Ollama           | `http://<ollama-host>:11434/v1`                |
| OpenRouter       | `https://openrouter.ai/api/v1`                 |

Default model is `gpt-4o-mini`; override via `OPENAI_MODEL`.

## Security

- Device passwords are stored as Fernet ciphertext in
  `config/devices.yaml`. The decryption key is supplied via
  `FERNET_KEY` (env) or `FERNET_KEY_FILE` (file path). Generation and
  encryption helpers are in `scripts/encrypt_password.py`.
- `.env` is gitignored. The shipped `.env.example` is the only
  credential template in the repo.
- The default `config/devices.yaml` uses RFC 5737 documentation IPs
  (`192.0.2.0/24`, `198.51.100.0/24`) as placeholders — not real hosts.
- The agent only runs commands the model explicitly emits in its JSON
  tool call. Web UI exposes the full call trace, so you can audit what
  was executed.

## Project layout

```
.
├── examples/
│   ├── basic_agent.py        # multi-turn chat, no tools
│   └── netops_agent.py       # tool-calling agent (CLI)
├── services/
│   └── llm_client.py         # OpenAI-compatible client wrapper
├── tools/
│   ├── agent_tools.py        # device_mgmt, device_bulk_exec
│   └── device_inventory.py   # YAML inventory + Fernet decryption
├── scripts/
│   └── encrypt_password.py   # generate Fernet key, encrypt passwords
├── config/
│   └── devices.yaml          # device + group definitions (placeholders)
├── web/
│   ├── app.py                # Flask wrapper around NetOpsAgent
│   └── templates/index.html  # chat UI
├── Dockerfile
├── deploy-vm.sh
├── requirements.txt
└── .env.example
```

## Limitations and honest caveats

- SSH/CLI only — no NETCONF, RESTCONF, or gNMI in the current cut.
- In-memory session store in `web/app.py`; for multi-process
  deployment, swap in Redis or a DB.
- The `tools/agent_tools.py` action map covers a small set of common
  operational commands per platform; arbitrary commands are routed via
  `action="custom"`.
- Tool-call parsing is intentionally tolerant (extracts the first
  balanced `{...}` block); a strict JSON-only schema would be more
  robust at the cost of model flexibility.
- Tested on Cisco NX-OS (Nexus) and Cisco IOS-XR in lab. Other vendor
  CLIs (Junos, Arista EOS, Cumulus, SONiC, etc.) should work as long
  as they accept `terminal length 0` or tolerate it as a no-op.

## License

MIT — see `LICENSE`.
