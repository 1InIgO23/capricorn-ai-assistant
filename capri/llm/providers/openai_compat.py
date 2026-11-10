"""Any OpenAI-compatible chat completions API (OpenAI, OpenRouter, LM Studio...)."""
from __future__ import annotations

import json
import threading
from typing import Any, Iterator

from ..base import (BackendError, LLMBackend, Message, StreamEvent, ToolCall, ToolSpec,
                    ToolsNotSupported, register_backend)
from ._http import error_text, iter_sse, loads, post_stream


@register_backend
class OpenAICompatBackend(LLMBackend):
    """OpenAI-style ``/chat/completions`` endpoint with streaming and tools."""

    key = "openai"
    label = "OpenAI-compatible API"
    default_base_url = "https://api.openai.com/v1"
    default_model = "gpt-4o-mini"
    needs_api_key = True
    is_local = False
    help_text = ("Works with any service that speaks the OpenAI chat completions protocol. "
                 "Set the base URL, a model name your provider offers, and your API key.")

    @staticmethod
    def _convert(message: Message) -> dict[str, Any]:
        if message.role == "tool":
            return {"role": "tool", "tool_call_id": message.tool_call_id or "",
                    "content": message.content}
        out: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            out["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in message.tool_calls
            ]
        return out

    def stream_chat(self, messages: list[Message], tools: list[ToolSpec] | None,
                    cancel: threading.Event) -> Iterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._convert(m) for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.parameters}}
                for t in tools
            ]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        response = post_stream(
            f"{self.base_url}/chat/completions", headers, payload,
            unreachable=f"I can't reach {self.base_url}. Check the base URL and your connection.",
        )
        if response.status_code != 200:
            detail = error_text(response)
            status = response.status_code
            response.close()
            if status in (401, 403):
                raise BackendError("The API key was rejected. Check it in Settings.")
            if status == 404:
                raise BackendError(
                    f"The API says '{self.model}' or the endpoint doesn't exist. "
                    f"Check the model name and base URL in Settings. ({detail})")
            if status == 429:
                raise BackendError(f"Rate limit or quota reached on the API. ({detail})")
            if "tool" in detail.lower() and "support" in detail.lower():
                raise ToolsNotSupported(detail)
            raise BackendError(f"The API returned an error ({status}): {detail}")

        pending: dict[int, dict[str, str]] = {}
        try:
            for _event, data in iter_sse(response):
                if cancel.is_set():
                    return
                if data == "[DONE]":
                    break
                chunk = loads(data)
                if chunk.get("error"):
                    raise BackendError(f"API error: {str(chunk['error'])[:300]}")
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield StreamEvent("text", text=delta["content"])
                    for part in delta.get("tool_calls") or []:
                        slot = pending.setdefault(int(part.get("index", 0)),
                                                  {"id": "", "name": "", "args": ""})
                        fn = part.get("function") or {}
                        slot["id"] = part.get("id") or slot["id"]
                        slot["name"] += fn.get("name") or ""
                        slot["args"] += fn.get("arguments") or ""
        finally:
            response.close()
        for index in sorted(pending):
            slot = pending[index]
            args = loads(slot["args"]) if slot["args"].strip() else {}
            yield StreamEvent("tool_call", tool_call=ToolCall(
                slot["id"] or ToolCall.new_id(), slot["name"], args))
