"""Tool plumbing: definitions, registry, confirmation requests."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..llm.base import ToolSpec
from .safety import SafetyError, SafetyPolicy

log = logging.getLogger(__name__)


@dataclass
class ConfirmRequest:
    """Something the user must approve before it happens."""

    title: str
    summary: str
    details: list[str] = field(default_factory=list)
    code: str | None = None
    code_language: str = ""
    kind: str = "file"  # "file" | "script"
    approved: bool = False
    event: threading.Event = field(default_factory=threading.Event)


@dataclass
class ToolResult:
    """Outcome of a tool call."""

    ok: bool
    content: str            # what the model sees
    summary: str = ""       # short text for the collapsed log entry
    details: str = ""       # Markdown shown when the entry is expanded
    error_line: str = ""    # most relevant error line for the HUD


@dataclass
class ToolContext:
    """Everything a tool handler may need."""

    workspace: Path
    policy: SafetyPolicy
    script_timeout: int = 60
    confirm: Callable[[ConfirmRequest], bool] = lambda request: False
    progress: Callable[[dict[str, Any]], None] = lambda info: None
    cancel: threading.Event = field(default_factory=threading.Event)


@dataclass
class Tool:
    """A callable capability exposed to the model."""

    name: str
    label: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], ToolResult]
    state: str = "thinking"            # character state while it runs
    visual: str = ""                   # "search" | "files" | "script" | ""
    hud: Callable[[dict[str, Any]], str] = lambda args: "WORKING"
    chip: Callable[[dict[str, Any]], str] = lambda args: ""

    def spec(self) -> ToolSpec:
        return ToolSpec(self.name, self.description, self.parameters)


class ToolRegistry:
    """Holds the tools and executes them safely."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Run a tool; every failure becomes a ToolResult instead of an exception."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool '{name}'. Available: {', '.join(self._tools)}",
                              "unknown tool", error_line=f"unknown tool {name}")
        try:
            return tool.handler(arguments or {}, context)
        except SafetyError as exc:
            log.warning("Tool %s refused: %s", name, exc)
            return ToolResult(False, f"Refused: {exc}", "refused", details=str(exc),
                              error_line=str(exc))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Tool %s failed: %s", name, exc)
            text = f"{type(exc).__name__}: {exc}"
            return ToolResult(False, f"Error: {text}", "failed", details=text, error_line=text)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Tool %s crashed", name)
            text = f"{type(exc).__name__}: {exc}"
            return ToolResult(False, f"Error: {text}", "failed", details=text, error_line=text)


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    """Shorthand for a JSON-schema object."""
    return {"type": "object", "properties": properties, "required": required or []}
