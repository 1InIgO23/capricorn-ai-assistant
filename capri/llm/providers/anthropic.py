"""Anthropic Messages API (Claude models)."""
from __future__ import annotations

import threading
from typing import Any, Iterator

from ..base import (BackendError, LLMBackend, Message, StreamEvent, ToolCall, ToolSpec,
                    register_backend)
from ._http import error_text, get_json, iter_sse, loads, post_stream

API_VERSION = "2023-06-01"
MAX_TOKENS = 8192


@register_backend
class AnthropicBackend(LLMBackend):
    """Claude through ``/v1/messages`` with streaming and native tool use."""

    key = "anthropic"
    label = "Anthropic API (Claude)"
    default_base_url = "https://api.anthropic.com"
    default_model = "claude-sonnet-5-5"
    needs_api_key = True
    is_local = False
    help_text = "Recommended for complex coding tasks. Needs an Anthropic API key."

    @staticmethod
    def _convert(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Split out the system prompt and build alternating content blocks."""
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue
            blocks: list[dict[str, Any]] = []
            if m.role == "tool":
                role = "user"
                blocks.append({"type": "tool_result", "tool_use_id": m.tool_call_id or "",
                               "content": m.content or "(no output)"})
            elif m.role == "assistant":
                role = "assistant"
                if m.content.strip():
                    blocks.append({"type": "text", "text": m.content})
                for c in m.tool_calls:
                    blocks.append({"type": "tool_use", "id": c.id, "name": c.name,
                                   "input": c.arguments})
            else:
                role = "user"
                blocks.append({"type": "text", "text": m.content or "(empty)"})
            if not blocks:
                continue
            if out and out[-1]["role"] == role:
                # tool results must precede text inside a merged user turn
                if role == "user" and blocks[0]["type"] == "tool_result":
                    idx = sum(1 for b in out[-1]["content"] if b["type"] == "tool_result")
                    out[-1]["content"][idx:idx] = blocks
                else:
                    out[-1]["content"].extend(blocks)
            else:
                out.append({"role": role, "content": blocks})
        return "\n\n".join(system_parts), out

    def list_models(self) -> list[str]:
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION}
        data = get_json(f"{self.base_url}/v1/models?limit=100", headers,
                        unreachable="I can't reach the Anthropic API. Check your connection.")
        return [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]

    def stream_chat(self, messages: list[Message], tools: list[ToolSpec] | None,
                    cancel: threading.Event) -> Iterator[StreamEvent]:
        system, converted = self._convert(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "messages": converted,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION}
        response = post_stream(
            f"{self.base_url}/v1/messages", headers, payload,
            unreachable="I can't reach the Anthropic API. Check your internet connection.",
        )
        if response.status_code != 200:
            detail = error_text(response)
            status = response.status_code
            response.close()
            if status in (401, 403):
                raise BackendError("Anthropic rejected the API key. Check it in Settings.")
            if status == 404:
                raise BackendError(
                    f"Anthropic doesn't know the model '{self.model}'. Fix the name in Settings.")
            if status == 429:
                raise BackendError(f"Anthropic rate limit reached. ({detail})")
            if status == 529:
                raise BackendError("Anthropic is overloaded right now. Try again in a moment.")
            raise BackendError(f"Anthropic returned an error ({status}): {detail}")

        blocks: dict[int, dict[str, str]] = {}
        try:
            for _event, data in iter_sse(response):
                if cancel.is_set():
                    return
                chunk = loads(data)
                kind = chunk.get("type")
                if kind == "content_block_start":
                    block = chunk.get("content_block") or {}
                    if block.get("type") == "tool_use":
                        blocks[int(chunk.get("index", 0))] = {
                            "id": str(block.get("id", "")), "name": str(block.get("name", "")),
                            "json": ""}
                elif kind == "content_block_delta":
                    delta = chunk.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield StreamEvent("text", text=delta["text"])
                    elif delta.get("type") == "input_json_delta":
                        slot = blocks.get(int(chunk.get("index", 0)))
                        if slot is not None:
                            slot["json"] += delta.get("partial_json") or ""
                elif kind == "content_block_stop":
                    slot = blocks.pop(int(chunk.get("index", 0)), None)
                    if slot is not None:
                        args = loads(slot["json"]) if slot["json"].strip() else {}
                        yield StreamEvent("tool_call", tool_call=ToolCall(
                            slot["id"] or ToolCall.new_id(), slot["name"], args))
                elif kind == "error":
                    err = chunk.get("error") or {}
                    raise BackendError(f"Anthropic error: {str(err.get('message') or err)[:300]}")
                elif kind == "message_stop":
                    break
        finally:
            response.close()
