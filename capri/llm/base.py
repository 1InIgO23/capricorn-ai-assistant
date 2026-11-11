"""Backend-agnostic LLM interface.

To add a provider, drop one module into ``capri/llm/providers`` containing a
subclass of :class:`LLMBackend` decorated with :func:`register_backend`.
It is discovered automatically and appears in the settings window.
"""
from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Iterator


class BackendError(Exception):
    """An error with a message that is safe and useful to show in the chat."""


class ToolsNotSupported(BackendError):
    """Raised when the selected model cannot do native tool calling."""


@dataclass
class ToolSpec:
    """Provider-neutral description of a tool (JSON-schema parameters)."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]

    @staticmethod
    def new_id() -> str:
        return "call_" + uuid.uuid4().hex[:16]


@dataclass
class Message:
    """One conversation message in Capri's internal, provider-neutral format."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool name for role == "tool"
    meta: dict[str, Any] = field(default_factory=dict)  # UI-only data, never sent

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": c.id, "name": c.name, "arguments": c.arguments} for c in self.tool_calls
            ]
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name:
            data["name"] = self.name
        if self.meta:
            data["meta"] = self.meta
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        calls = [
            ToolCall(str(c.get("id") or ToolCall.new_id()), str(c.get("name", "")),
                     c.get("arguments") if isinstance(c.get("arguments"), dict) else {})
            for c in data.get("tool_calls") or []
        ]
        return cls(
            role=str(data.get("role", "user")),
            content=str(data.get("content") or ""),
            tool_calls=calls,
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
            meta=data.get("meta") if isinstance(data.get("meta"), dict) else {},
        )


@dataclass
class StreamEvent:
    """An item produced while streaming a reply."""

    kind: str  # "text" | "tool_call"
    text: str = ""
    tool_call: ToolCall | None = None


class LLMBackend(ABC):
    """Base class of every provider."""

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    default_base_url: ClassVar[str] = ""
    default_model: ClassVar[str] = ""
    needs_api_key: ClassVar[bool] = False
    is_local: ClassVar[bool] = False
    help_text: ClassVar[str] = ""

    def __init__(self, base_url: str = "", model: str = "", api_key: str = "") -> None:
        self.base_url = (base_url or self.default_base_url).strip().rstrip("/")
        self.model = (model or self.default_model).strip()
        self.api_key = (api_key or "").strip()

    @abstractmethod
    def stream_chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        cancel: threading.Event,
    ) -> Iterator[StreamEvent]:
        """Stream one assistant turn.

        Yields ``text`` events as tokens arrive and ``tool_call`` events for
        requested tool invocations. Must stop promptly when *cancel* is set and
        raise :class:`BackendError` with a friendly message on failure.
        """

    def list_models(self) -> list[str]:
        """Names of the models this backend offers (used by the Model window).

        Also serves as a cheap connection test: it must raise
        :class:`BackendError` with a friendly message when the server is
        unreachable or the API key is rejected. Providers that cannot list
        models may return an empty list.
        """
        return []


_REGISTRY: dict[str, type[LLMBackend]] = {}


def register_backend(cls: type[LLMBackend]) -> type[LLMBackend]:
    """Class decorator that makes a backend selectable in the settings."""
    if not cls.key:
        raise ValueError("Backend classes need a non-empty 'key'.")
    _REGISTRY[cls.key] = cls
    return cls


def available_backends() -> dict[str, type[LLMBackend]]:
    """All registered backends by key (providers are imported on first use)."""
    from . import providers  # noqa: F401  (import triggers registration)

    return dict(_REGISTRY)


def create_backend(key: str, config: dict[str, str]) -> LLMBackend:
    """Instantiate the backend *key* with its stored configuration."""
    backends = available_backends()
    if key not in backends:
        raise BackendError(f"Unknown backend '{key}'. Pick one in Settings.")
    cls = backends[key]
    backend = cls(config.get("base_url", ""), config.get("model", ""), config.get("api_key", ""))
    if cls.needs_api_key and not backend.api_key:
        raise BackendError(
            f"No API key is set for {cls.label}. Open Settings (right-click me) and paste one in."
        )
    if not backend.model:
        raise BackendError(f"No model name is set for {cls.label}. Add one in Settings.")
    return backend
