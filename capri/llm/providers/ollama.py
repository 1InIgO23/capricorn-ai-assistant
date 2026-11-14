"""Local models through Ollama's native chat API."""
from __future__ import annotations

import json
import threading
from typing import Any, Iterator

import requests

from ..base import (BackendError, LLMBackend, Message, StreamEvent, ToolCall, ToolSpec,
                    ToolsNotSupported, register_backend)
from ...osutil import machine_word
from ._http import error_text, get_json, post_stream


@register_backend
class OllamaBackend(LLMBackend):
    """Ollama running on this machine (default backend)."""

    key = "ollama"
    label = "Ollama (local)"
    default_base_url = "http://localhost:11434"
    default_model = "qwen2.5:7b"
    needs_api_key = False
    is_local = True
    help_text = (f"Runs on your own {machine_word()}. Fine for chatting, web lookups, file chores "
                 "and simple scripts. Pick a model that supports tool calling.")

    @staticmethod
    def _convert(message: Message) -> dict[str, Any]:
        if message.role == "tool":
            return {"role": "tool", "content": message.content, "tool_name": message.name or ""}
        out: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            out["tool_calls"] = [
                {"function": {"name": c.name, "arguments": c.arguments}} for c in message.tool_calls
            ]
        return out

    def list_models(self) -> list[str]:
        data = get_json(f"{self.base_url}/api/tags", {},
                        unreachable=f"I can't reach Ollama at {self.base_url}. Is it running?")
        return sorted(str(m.get("name")) for m in data.get("models") or [] if m.get("name"))

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
        response = post_stream(
            f"{self.base_url}/api/chat", {}, payload,
            unreachable=(f"I can't reach Ollama at {self.base_url}. Is it running? Start the "
                         "Ollama app (or run `ollama serve`) and try again."),
        )
        if response.status_code != 200:
            detail = error_text(response)
            response.close()
            if "does not support tools" in detail.lower():
                raise ToolsNotSupported(detail)
            if response.status_code == 404 or "not found" in detail.lower():
                raise BackendError(
                    f"Ollama doesn't have the model '{self.model}'. Install it with "
                    f"`ollama pull {self.model}` or change the model name in Settings."
                )
            raise BackendError(f"Ollama returned an error ({response.status_code}): {detail}")
        try:
            for raw in response.iter_lines():
                if cancel.is_set():
                    return
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except ValueError:
                    continue
                if data.get("error"):
                    raise BackendError(f"Ollama error: {data['error']}")
                message = data.get("message") or {}
                if message.get("content"):
                    yield StreamEvent("text", text=message["content"])
                for call in message.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except ValueError:
                            args = {}
                    yield StreamEvent("tool_call", tool_call=ToolCall(
                        ToolCall.new_id(), str(fn.get("name", "")),
                        args if isinstance(args, dict) else {}))
                if data.get("done"):
                    return
        except requests.exceptions.RequestException as exc:
            raise BackendError("The connection to Ollama dropped mid-answer.") from exc
        finally:
            response.close()
