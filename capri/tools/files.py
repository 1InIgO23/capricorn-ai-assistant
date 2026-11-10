"""File management tools.

Read-only operations run immediately. Anything that writes, moves, renames or
deletes first asks the user through a confirmation dialog that states exactly
what will happen. Deleting always goes to the Recycle Bin.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .base import ConfirmRequest, Tool, ToolContext, ToolResult, obj
from .safety import SafetyError

MAX_LIST = 200
MAX_READ_CHARS = 12_000
MAX_WALK = 60_000
EXECUTABLE_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".vbs", ".vbe", ".js",
                         ".jse", ".wsf", ".scr", ".lnk", ".reg", ".py", ".pyw", ".hta", ".cpl",
                         ".jar", ".msc"}


def resolve_path(raw: Any, context: ToolContext) -> Path:
    """Expand ``~`` and environment variables; relative paths live in the workspace."""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise ValueError("A path is required.")
    text = os.path.expandvars(os.path.expanduser(text))
    path = Path(text)
    if not path.is_absolute():
        path = context.workspace / path
    return Path(os.path.abspath(path))


def _size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)] if value and str(value).strip() else []


def _denied() -> ToolResult:
    return ToolResult(False, "The user denied this action. Do not retry it.", "denied by user")


# ------------------------------------------------------------------ read-only
def list_directory(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = resolve_path(args.get("path") or ".", ctx)
    ctx.policy.check_read(path)
    if not path.is_dir():
        raise ValueError(f"Not a folder: {path}")
    rows = []
    entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    for entry in entries[:MAX_LIST]:
        try:
            stat = entry.stat()
            stamp = _dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            kind = "DIR " if entry.is_dir() else "FILE"
            size = "" if entry.is_dir() else _size(stat.st_size)
            rows.append(f"{kind}  {entry.name}  {size}  {stamp}".rstrip())
        except OSError:
            rows.append(f"?     {entry.name}")
    extra = f"\n(+{len(entries) - MAX_LIST} more not shown)" if len(entries) > MAX_LIST else ""
    body = "\n".join(rows) or "(empty folder)"
    return ToolResult(True, f"Contents of {path}:\n{body}{extra}", f"{len(entries)} items",
                      details=f"`{path}`\n```\n{body}{extra}\n```")


def search_files(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    root = resolve_path(args.get("root") or ".", ctx)
    ctx.policy.check_read(root)
    pattern = str(args.get("pattern") or "*").strip()
    contains = str(args.get("contains") or "").strip().lower()
    limit = max(1, min(int(args.get("max_results") or 50), 200))
    if not any(ch in pattern for ch in "*?["):
        pattern = f"*{pattern}*"
    pattern = pattern.lower()
    hits: list[str] = []
    visited = 0
    for folder, dirs, files in os.walk(root):
        if ctx.cancel.is_set():
            break
        dirs[:] = [d for d in dirs if not ctx.policy.is_forbidden(os.path.join(folder, d))
                   and not d.startswith("$")]
        for name in dirs + files:
            visited += 1
            if not fnmatch.fnmatch(name.lower(), pattern):
                continue
            full = os.path.join(folder, name)
            if contains:
                try:
                    if os.path.isdir(full) or os.path.getsize(full) > 2_000_000:
                        continue
                    with open(full, "r", encoding="utf-8", errors="ignore") as handle:
                        if contains not in handle.read().lower():
                            continue
                except OSError:
                    continue
            hits.append(full)
            if len(hits) >= limit:
                break
        if len(hits) >= limit or visited > MAX_WALK:
            break
    body = "\n".join(hits) or "(no matches)"
    note = " (search stopped early: very large folder)" if visited > MAX_WALK else ""
    return ToolResult(True, f"Matches under {root}{note}:\n{body}", f"{len(hits)} found",
                      details=f"pattern `{pattern}` in `{root}`{note}\n```\n{body}\n```")


def _read_pdf(path: Path, limit: int) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("Reading PDFs needs the 'pypdf' package.") from exc
    reader = PdfReader(str(path))
    parts, total = [], 0
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
        total += len(text)
        if total >= limit:
            break
    return "\n".join(parts)


def read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = resolve_path(args.get("path"), ctx)
    ctx.policy.check_read(path)
    if not path.is_file():
        raise ValueError(f"Not a file: {path}")
    limit = max(200, min(int(args.get("max_chars") or MAX_READ_CHARS), 40_000))
    if path.suffix.lower() == ".pdf":
        text = _read_pdf(path, limit)
    else:
        with open(path, "rb") as handle:
            raw = handle.read(limit * 4 + 4)
        if b"\x00" in raw[:4096]:
            raise ValueError("That looks like a binary file; I can only read text and PDF files.")
        text = raw.decode("utf-8-sig", errors="replace")
    truncated = len(text) > limit
    text = text[:limit]
    note = "\n[...truncated...]" if truncated else ""
    return ToolResult(True, f"Content of {path}:\n{text}{note}",
                      f"{path.name} · {len(text):,} chars",
                      details=f"`{path}`" + (" (truncated)" if truncated else ""))


def open_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = resolve_path(args.get("path"), ctx)
    ctx.policy.check_read(path)
    if not path.exists():
        raise ValueError(f"Not found: {path}")
    if path.is_file() and path.suffix.lower() in EXECUTABLE_EXTENSIONS:
        request = ConfirmRequest(
            title="Open a program or script",
            summary="Capri wants to open a file that can run code on this PC.",
            details=[f"Open with its default app: {path}"])
        if not ctx.confirm(request):
            return _denied()
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603,S607
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
    return ToolResult(True, f"Opened {path}", f"opened {path.name}", details=f"`{path}`")


# ------------------------------------------------------------------ modifying
def write_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = resolve_path(args.get("path"), ctx)
    content = str(args.get("content") or "")
    overwrite = bool(args.get("overwrite"))
    ctx.policy.check_modify(path)
    if path.is_dir():
        raise ValueError(f"{path} is a folder.")
    exists = path.exists()
    if exists and not overwrite:
        raise ValueError(f"{path} already exists. Pass overwrite=true to replace it.")
    action = "OVERWRITE the existing file" if exists else "Create a new file"
    request = ConfirmRequest(
        title="Overwrite a file" if exists else "Write a file",
        summary=f"Capri wants to {'replace' if exists else 'create'} a file.",
        details=[f"{action}: {path}", f"Size: {len(content):,} characters"],
        code=content, code_language=path.suffix.lstrip("."))
    if not ctx.confirm(request):
        return _denied()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")
    return ToolResult(True, f"Wrote {len(content)} characters to {path}",
                      f"wrote {path.name}", details=f"`{path}` · {len(content):,} chars")


def create_folder(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = resolve_path(args.get("path"), ctx)
    ctx.policy.check_read(path)
    if path.exists():
        return ToolResult(True, f"Folder already exists: {path}", "already exists",
                          details=f"`{path}`")
    for parent in [path, *path.parents]:
        if parent.exists():
            break
        ctx.policy.check_modify(parent)
    request = ConfirmRequest(title="Create a folder", summary="Capri wants to create a folder.",
                             details=[f"Create folder: {path}"])
    if not ctx.confirm(request):
        return _denied()
    path.mkdir(parents=True, exist_ok=True)
    return ToolResult(True, f"Created folder {path}", f"created {path.name}", details=f"`{path}`")


def _target_for(source: Path, destination: Path) -> Path:
    return destination / source.name if destination.is_dir() else destination


def _plan_transfers(args: dict[str, Any], ctx: ToolContext, modify_source: bool
                    ) -> list[tuple[Path, Path]]:
    sources = [resolve_path(s, ctx) for s in _as_list(args.get("sources") or args.get("source"))]
    if not sources:
        raise ValueError("At least one source path is required.")
    destination = resolve_path(args.get("destination"), ctx)
    if len(sources) > 1 and not destination.is_dir():
        raise ValueError("With several sources the destination must be an existing folder.")
    plan = []
    for source in sources:
        if not source.exists():
            raise ValueError(f"Not found: {source}")
        if modify_source:
            ctx.policy.check_modify(source)
        else:
            ctx.policy.check_read(source)
        target = _target_for(source, destination)
        ctx.policy.check_modify(target)
        if target.exists():
            raise ValueError(f"{target} already exists; I won't overwrite it.")
        if source.is_dir() and str(target).lower().startswith(str(source).lower() + os.sep):
            raise ValueError("A folder cannot be placed inside itself.")
        plan.append((source, target))
    return plan


def copy_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    plan = _plan_transfers(args, ctx, modify_source=False)
    request = ConfirmRequest(
        title="Copy", summary=f"Capri wants to copy {len(plan)} item(s).",
        details=[f"Copy: {src}  →  {dst}" for src, dst in plan])
    if not ctx.confirm(request):
        return _denied()
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    lines = "\n".join(f"- `{s}` → `{d}`" for s, d in plan)
    return ToolResult(True, "Copied:\n" + "\n".join(f"{s} -> {d}" for s, d in plan),
                      f"copied {len(plan)} item(s)", details=lines)


def move_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    plan = _plan_transfers(args, ctx, modify_source=True)
    request = ConfirmRequest(
        title="Move", summary=f"Capri wants to move {len(plan)} item(s).",
        details=[f"Move: {src}  →  {dst}" for src, dst in plan])
    if not ctx.confirm(request):
        return _denied()
    for src, dst in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    lines = "\n".join(f"- `{s}` → `{d}`" for s, d in plan)
    return ToolResult(True, "Moved:\n" + "\n".join(f"{s} -> {d}" for s, d in plan),
                      f"moved {len(plan)} item(s)", details=lines)


def rename_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = resolve_path(args.get("path"), ctx)
    new_name = str(args.get("new_name") or "").strip()
    if not new_name or any(ch in new_name for ch in '\\/:*?"<>|') or new_name in (".", ".."):
        raise ValueError("new_name must be a plain file or folder name without path separators.")
    if not path.exists():
        raise ValueError(f"Not found: {path}")
    target = path.with_name(new_name)
    ctx.policy.check_modify(path)
    ctx.policy.check_modify(target)
    if target.exists():
        raise ValueError(f"{target} already exists.")
    request = ConfirmRequest(title="Rename", summary="Capri wants to rename an item.",
                             details=[f"Rename: {path}  →  {target.name}"])
    if not ctx.confirm(request):
        return _denied()
    path.rename(target)
    return ToolResult(True, f"Renamed {path} to {target}", f"renamed to {target.name}",
                      details=f"`{path}` → `{target}`")


def delete_path(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    paths = [resolve_path(p, ctx) for p in _as_list(args.get("paths") or args.get("path"))]
    if not paths:
        raise ValueError("At least one path is required.")
    for path in paths:
        if not path.exists():
            raise ValueError(f"Not found: {path}")
        ctx.policy.check_modify(path)
    try:
        from send2trash import send2trash
    except ImportError as exc:
        raise SafetyError("The 'send2trash' package is missing, and I never delete "
                          "permanently. Install the requirements first.") from exc
    request = ConfirmRequest(
        title="Send to Recycle Bin",
        summary=f"Capri wants to send {len(paths)} item(s) to the Recycle Bin.",
        details=[f"Recycle: {p}" + ("  (folder and everything in it)" if p.is_dir() else "")
                 for p in paths])
    if not ctx.confirm(request):
        return _denied()
    for path in paths:
        send2trash(str(path))
    lines = "\n".join(f"- `{p}`" for p in paths)
    return ToolResult(True, "Sent to the Recycle Bin:\n" + "\n".join(map(str, paths)),
                      f"recycled {len(paths)} item(s)", details=lines)


# --------------------------------------------------------------- registration
def _name(args: dict[str, Any], key: str = "path") -> str:
    value = args.get(key)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return os.path.basename(str(value or "").rstrip("\\/")) or str(value or "")


def _count(args: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if args.get(key):
            return len(_as_list(args[key]))
    return 0


def _count_hud(verb: str, *keys: str):
    def hud(args: dict[str, Any]) -> str:
        n = _count(args, *keys)
        if n == 1:
            return f"{verb} {_name(args, next(k for k in keys if args.get(k)))}"
        return f"{verb} {n} files"
    return hud


def _first(*keys: str):
    def chip(args: dict[str, Any]) -> str:
        for key in keys:
            if args.get(key):
                return _name(args, key)
        return ""
    return chip


_PATH = {"type": "string", "description": "File or folder path. Relative paths are inside the "
                                          "workspace; ~ and %VARS% are expanded."}
_PATHS = {"type": "array", "items": {"type": "string"}, "description": "One or more paths."}


def file_tools() -> list[Tool]:
    """All file management tools."""
    return [
        Tool("list_directory", "list folder", "List the files and folders inside a folder.",
             obj({"path": _PATH}, ["path"]), list_directory, "files", "files",
             lambda a: f"LISTING {_name(a) or 'workspace'}", _first("path")),
        Tool("search_files", "search files",
             "Find files or folders by name (wildcards * and ? allowed) under a root folder, "
             "optionally only text files containing a phrase.",
             obj({"root": _PATH,
                  "pattern": {"type": "string", "description": "Name pattern, e.g. *.pdf or report"},
                  "contains": {"type": "string", "description": "Optional text the file must contain"},
                  "max_results": {"type": "integer"}}, ["root", "pattern"]),
             search_files, "files", "files",
             lambda a: f"SCANNING {_name(a, 'root') or 'workspace'}", _first("pattern")),
        Tool("read_file", "read file", "Read a text or PDF file and return its content.",
             obj({"path": _PATH, "max_chars": {"type": "integer"}}, ["path"]),
             read_file, "files", "files", lambda a: f"READING {_name(a)}", _first("path")),
        Tool("open_path", "open", "Open a file or folder with its default Windows app.",
             obj({"path": _PATH}, ["path"]), open_path, "files", "files",
             lambda a: f"OPENING {_name(a)}", _first("path")),
        Tool("write_file", "write file",
             "Create a text file with the given content (asks the user to confirm). "
             "Set overwrite=true to replace an existing file.",
             obj({"path": _PATH, "content": {"type": "string"},
                  "overwrite": {"type": "boolean"}}, ["path", "content"]),
             write_file, "files", "files", lambda a: f"WRITING {_name(a)}", _first("path")),
        Tool("create_folder", "create folder", "Create a folder (asks the user to confirm).",
             obj({"path": _PATH}, ["path"]), create_folder, "files", "files",
             lambda a: f"CREATING {_name(a)}", _first("path")),
        Tool("copy_path", "copy",
             "Copy files or folders to a destination (asks the user to confirm). "
             "Never overwrites.",
             obj({"sources": _PATHS, "destination": _PATH}, ["sources", "destination"]),
             copy_path, "files", "files", _count_hud("COPYING", "sources", "source"),
             _first("sources", "source")),
        Tool("move_path", "move",
             "Move files or folders to a destination (asks the user to confirm). "
             "Never overwrites.",
             obj({"sources": _PATHS, "destination": _PATH}, ["sources", "destination"]),
             move_path, "files", "files", _count_hud("MOVING", "sources", "source"),
             _first("sources", "source")),
        Tool("rename_path", "rename",
             "Rename a file or folder in place (asks the user to confirm).",
             obj({"path": _PATH, "new_name": {"type": "string"}}, ["path", "new_name"]),
             rename_path, "files", "files", lambda a: f"RENAMING {_name(a)}", _first("path")),
        Tool("delete_path", "recycle",
             "Send files or folders to the Recycle Bin (asks the user to confirm). "
             "Nothing is ever deleted permanently.",
             obj({"paths": _PATHS}, ["paths"]), delete_path, "files", "files",
             _count_hud("RECYCLING", "paths", "path"), _first("paths", "path")),
    ]
