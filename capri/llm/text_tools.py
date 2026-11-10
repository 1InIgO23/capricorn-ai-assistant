"""Prompt-based tool calling for models without native tool support.

The model is told to emit ``<tool_call>{"name": ..., "arguments": {...}}</tool_call>``
blocks; these helpers convert the conversation to that protocol, hide the
blocks from the streamed text and parse them afterwards.
"""
from __future__ import annotations

import json
import re

from .base import Message, ToolCall, ToolSpec

OPEN_TAG = "<tool_call>"
CLOSE_TAG = "</tool_call>"
_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL)


def tool_instructions(tools: list[ToolSpec]) -> str:
    """System prompt section describing the tools and the call format."""
    lines = [
        "# Tools",
        "You can use tools. To call one, write exactly this and then stop writing:",
        '<tool_call>{"name": "<tool name>", "arguments": {<JSON arguments>}}</tool_call>',
        "Call one tool at a time and wait for its result before continuing.",
        "Available tools (JSON schema of the arguments):",
    ]
    for tool in tools:
        lines.append(f"- {tool.name}: {tool.description}\n  arguments: "
                     f"{json.dumps(tool.parameters, separators=(',', ':'))}")
    return "\n".join(lines)


def to_text_protocol(messages: list[Message], tools: list[ToolSpec]) -> list[Message]:
    """Rewrite native tool messages into plain text the model can follow."""
    out: list[Message] = []
    for index, m in enumerate(messages):
        if m.role == "system" and index == 0:
            out.append(Message("system", m.content + "\n\n" + tool_instructions(tools)))
        elif m.role == "assistant" and m.tool_calls:
            calls = "\n".join(
                OPEN_TAG + json.dumps({"name": c.name, "arguments": c.arguments}) + CLOSE_TAG
                for c in m.tool_calls)
            out.append(Message("assistant", (m.content + "\n" + calls).strip()))
        elif m.role == "tool":
            out.append(Message("user", f"[Result of tool {m.name}]\n{m.content}"))
        else:
            out.append(Message(m.role, m.content))
    return out


def parse_tool_calls(text: str) -> tuple[str, list[ToolCall]]:
    """Return ``(text without tool blocks, parsed tool calls)``."""
    calls: list[ToolCall] = []
    for raw in _BLOCK_RE.findall(text):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("name"):
            args = data.get("arguments")
            calls.append(ToolCall(ToolCall.new_id(), str(data["name"]),
                                  args if isinstance(args, dict) else {}))
    clean = _BLOCK_RE.sub("", text).strip()
    return clean, calls


class StreamFilter:
    """Passes streamed text through while withholding ``<tool_call>`` blocks."""

    def __init__(self) -> None:
        self._buffer = ""
        self._emitted = 0
        self._blocked = False

    def feed(self, delta: str) -> str:
        """Add *delta*; returns the part that is safe to display now."""
        self._buffer += delta
        if self._blocked:
            return ""
        idx = self._buffer.find(OPEN_TAG, self._emitted)
        if idx >= 0:
            out = self._buffer[self._emitted:idx]
            self._emitted = len(self._buffer)
            self._blocked = True
            return out
        # hold back a tail that could be the start of the tag
        safe_end = len(self._buffer)
        for size in range(min(len(OPEN_TAG) - 1, len(self._buffer) - self._emitted), 0, -1):
            if OPEN_TAG.startswith(self._buffer[-size:]):
                safe_end = len(self._buffer) - size
                break
        out = self._buffer[self._emitted:safe_end]
        self._emitted = safe_end
        return out

    def finish(self) -> str:
        """Flush any withheld tail once the stream is over."""
        if self._blocked:
            return ""
        out = self._buffer[self._emitted:]
        self._emitted = len(self._buffer)
        return out

    @property
    def full_text(self) -> str:
        return self._buffer
