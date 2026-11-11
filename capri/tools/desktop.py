"""Desktop tools: list the open windows and point at one of them."""
from __future__ import annotations

from typing import Any

from ..character.app_windows import enumerate_titles, match_window
from .base import Tool, ToolContext, ToolResult, obj

MAX_LISTED = 25


def _listing(windows) -> str:
    lines = [f"- {w.title}" + (f"  ({w.process})" if w.process else "") for w in
             windows[:MAX_LISTED]]
    if len(windows) > MAX_LISTED:
        lines.append(f"- ...and {len(windows) - MAX_LISTED} more")
    return "\n".join(lines)


def list_open_windows(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    windows = enumerate_titles()
    if not windows:
        return ToolResult(True, "I can't see any open application windows (minimized windows "
                                "are not counted, and this only works on Windows).",
                          "none found")
    body = _listing(windows)
    return ToolResult(True, f"Open windows (topmost first):\n{body}",
                      f"{len(windows)} windows", details=body)


def point_at_window(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    query = str(args.get("window") or "").strip()
    if not query:
        raise ValueError("Say which window to point at (part of its title or the program).")
    windows = enumerate_titles()
    if not windows:
        return ToolResult(True, "There are no visible application windows to point at "
                                "(minimized windows don't count).", "no windows")
    match = match_window(query, windows)
    if match is None:
        body = _listing(windows)
        return ToolResult(True, f"No open window matches '{query}'. Open windows:\n{body}\n"
                                "Ask the user which one they mean, or point at the right one.",
                          "no match", details=f"No match for `{query}`.\n{body}")
    ctx.progress({"point": match.handle, "title": match.title})
    program = f" ({match.process})" if match.process else ""
    return ToolResult(True, f"Now pointing at the window '{match.title}'{program}. "
                            "Tell the user briefly which window it is.",
                      f"→ {match.title[:48]}", details=f"`{match.title}`{program}")


def desktop_tools() -> list[Tool]:
    """Window listing and pointing tools."""
    return [
        Tool("list_open_windows", "open windows",
             "List the titles and programs of the application windows currently open on the "
             "desktop (topmost first).",
             obj({}), list_open_windows, "thinking", "",
             lambda a: "SCANNING WINDOWS"),
        Tool("point_at_window", "point",
             "Visually point at an open window on the user's screen: Capri points at it and a "
             "beam links her hand to the window. Use it whenever the user asks where a window "
             "is or asks you to show/point at one. 'window' is part of the title or the program "
             "name, e.g. 'chrome', 'Excel', 'Downloads'.",
             obj({"window": {"type": "string",
                             "description": "Part of the window title or program name"}},
                 ["window"]),
             point_at_window, "pointing", "",
             lambda a: f'TARGETING "{str(a.get("window") or "")[:40]}"'),
    ]
