"""The agent loop: one background thread per user turn.

The thread streams the model's reply, executes requested tools and loops
until the model answers without asking for a tool. Everything the UI needs is
reported through Qt signals, so the interface never blocks.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..tools.base import ConfirmRequest, ToolContext, ToolRegistry, ToolResult
from .base import BackendError, LLMBackend, Message, ToolCall, ToolsNotSupported
from .text_tools import StreamFilter, parse_tool_calls, to_text_protocol

log = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 60
MAX_TOOL_RESULT_CHARS = 14_000


def trim_history(messages: list[Message], limit: int = MAX_HISTORY_MESSAGES) -> list[Message]:
    """Keep the most recent messages, cutting only at a user-message boundary
    so tool calls are never separated from their results."""
    if len(messages) <= limit:
        return list(messages)
    start = len(messages) - limit
    while start < len(messages) and messages[start].role != "user":
        start += 1
    return list(messages[start:]) if start < len(messages) else list(messages[-1:])


class AgentRun(QThread):
    """Runs one user turn to completion."""

    phase_changed = Signal(str)            # "thinking" | "talking"
    text_delta = Signal(str)               # streamed assistant text
    message_ready = Signal(object)         # Message to append to the conversation
    tool_started = Signal(str, str, dict)  # call id, tool name, arguments
    tool_progress = Signal(str, dict)      # call id, progress info
    tool_finished = Signal(str, str, object)  # call id, tool name, ToolResult
    confirm_requested = Signal(object)     # ConfirmRequest
    failed = Signal(str)                   # user-facing error text
    completed = Signal(str)                # final assistant text of the turn

    def __init__(self, backend: LLMBackend, system_prompt: str, history: list[Message],
                 registry: ToolRegistry, context: ToolContext, max_rounds: int = 12,
                 parent: Any = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._system = system_prompt
        self._messages = trim_history(history)
        self._registry = registry
        self._context = context
        self._max_rounds = max(1, int(max_rounds))
        self.cancel = threading.Event()
        self._context.cancel = self.cancel
        self._context.confirm = self._confirm
        self._pending_confirm: ConfirmRequest | None = None
        self._text_tools = False

    # ------------------------------------------------------------- control
    def stop(self) -> None:
        """Ask the run to stop as soon as possible."""
        self.cancel.set()
        pending = self._pending_confirm
        if pending is not None:
            pending.approved = False
            pending.event.set()

    def _confirm(self, request: ConfirmRequest) -> bool:
        """Called from tools: ask the UI and block until the user decides."""
        if self.cancel.is_set():
            return False
        self._pending_confirm = request
        self.confirm_requested.emit(request)
        while not request.event.wait(0.2):
            if self.cancel.is_set():
                request.approved = False
                break
        self._pending_confirm = None
        return bool(request.approved)

    # ---------------------------------------------------------------- loop
    def run(self) -> None:  # noqa: D102 (QThread entry point)
        final_text = ""
        try:
            for _round in range(self._max_rounds):
                if self.cancel.is_set():
                    break
                self.phase_changed.emit("thinking")
                text, calls = self._one_round()
                if self.cancel.is_set() and not text and not calls:
                    break
                if not text and not calls and not self.cancel.is_set():
                    self.failed.emit("The model sent back an empty reply. Try again, or try "
                                     "another model in Settings.")
                    return
                assistant = Message("assistant", text, tool_calls=calls)
                if text or calls:
                    self._messages.append(assistant)
                    self.message_ready.emit(assistant)
                final_text = text or final_text
                if not calls or self.cancel.is_set():
                    break
                for call in calls:
                    self._run_tool(call)
            else:
                note = "I hit my step limit for one request, so I stopped here."
                self.text_delta.emit("\n\n" + note)
                self.message_ready.emit(Message("assistant", note))
        except BackendError as exc:
            log.warning("Backend error: %s", exc)
            self.failed.emit(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Unexpected agent failure")
            self.failed.emit(f"Something broke on my side: {type(exc).__name__}: {exc}")
            return
        self.completed.emit(final_text)

    def _one_round(self) -> tuple[str, list[ToolCall]]:
        """Stream one model turn, falling back to text-protocol tools if needed."""
        specs = self._registry.specs()
        system = Message("system", self._system)
        if not self._text_tools:
            try:
                return self._stream([system, *self._messages], specs, None)
            except ToolsNotSupported:
                log.info("Model has no native tool calling; using the text protocol.")
                self._text_tools = True
        converted = to_text_protocol([system, *self._messages], specs)
        return self._stream(converted, None, StreamFilter())

    def _stream(self, messages: list[Message], specs: Any,
                text_filter: StreamFilter | None) -> tuple[str, list[ToolCall]]:
        parts: list[str] = []
        calls: list[ToolCall] = []
        talking = False
        for event in self._backend.stream_chat(messages, specs, self.cancel):
            if self.cancel.is_set():
                break
            if event.kind == "text" and event.text:
                shown = text_filter.feed(event.text) if text_filter else event.text
                if shown:
                    if not talking:
                        talking = True
                        self.phase_changed.emit("talking")
                    parts.append(shown)
                    self.text_delta.emit(shown)
            elif event.kind == "tool_call" and event.tool_call is not None:
                calls.append(event.tool_call)
        if text_filter is not None:
            tail = text_filter.finish()
            if tail:
                parts.append(tail)
                self.text_delta.emit(tail)
            _clean, parsed = parse_tool_calls(text_filter.full_text)
            calls.extend(parsed[:1])  # the text protocol is one call at a time
        return "".join(parts).strip(), calls

    def _run_tool(self, call: ToolCall) -> None:
        """Execute one tool call and append its result message."""
        self.tool_started.emit(call.id, call.name, dict(call.arguments))
        self._context.progress = lambda info, _id=call.id: self.tool_progress.emit(_id, dict(info))
        if self.cancel.is_set():
            result = ToolResult(False, "Cancelled by the user.", "cancelled")
        else:
            result = self._registry.execute(call.name, call.arguments, self._context)
        content = result.content
        if len(content) > MAX_TOOL_RESULT_CHARS:
            content = content[:MAX_TOOL_RESULT_CHARS] + "\n[...truncated...]"
        message = Message(
            "tool", content, tool_call_id=call.id, name=call.name,
            meta={"ok": result.ok, "summary": result.summary, "details": result.details},
        )
        self._messages.append(message)
        self.message_ready.emit(message)
        self.tool_finished.emit(call.id, call.name, result)
