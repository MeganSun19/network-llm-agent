"""Basic conversational agent — no tool calling, just multi-turn chat.

Useful as a smoke test for the LLM client and as a reference for the
minimal integration pattern.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Allow `python examples/basic_agent.py` from project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import LLMClient


class BasicAgent:
    """Minimal multi-turn chat agent, no tool calling."""

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client or LLMClient()
        self.history: List[Dict[str, str]] = []
        self.system_prompt = (
            "You are a helpful assistant. Keep replies concise and ask "
            "for clarification when the request is ambiguous."
        )
        self.history.append({"role": "system", "content": self.system_prompt})

    def chat(self, user_input: str) -> str:
        self.history.append({"role": "user", "content": user_input})
        resp = self.client.chat_completion(self.history, stop=["<|im_end|>"])
        reply = resp["choices"][0]["message"]["content"]
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def clear(self) -> None:
        self.history = [self.history[0]]

    def save(self, filename: str) -> None:
        Path(filename).write_text(
            json.dumps(self.history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def main() -> None:
    agent = BasicAgent()
    print("=== Basic Chat Agent ===")
    print("Commands: quit | clear | save <filename>\n")

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
            name = rest[0] if rest else "conversation_basic.json"
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
