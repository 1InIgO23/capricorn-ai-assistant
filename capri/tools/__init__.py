"""Tools Capri can use: web, files, scripts."""
from __future__ import annotations

from .base import ConfirmRequest, Tool, ToolContext, ToolRegistry, ToolResult
from .safety import SafetyError, SafetyPolicy, default_policy

__all__ = ["ConfirmRequest", "Tool", "ToolContext", "ToolRegistry", "ToolResult",
           "SafetyError", "SafetyPolicy", "default_policy", "build_registry"]


def build_registry() -> ToolRegistry:
    """Create the registry with every built-in tool."""
    from .desktop import desktop_tools
    from .files import file_tools
    from .scripts import script_tools
    from .web import web_tools

    registry = ToolRegistry()
    for tool in (*web_tools(), *file_tools(), *script_tools(), *desktop_tools()):
        registry.register(tool)
    return registry
