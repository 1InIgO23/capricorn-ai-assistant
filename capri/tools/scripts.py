"""Script tools: save scripts in the workspace and run them after confirmation."""
from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..osutil import machine_word
from .base import ConfirmRequest, Tool, ToolContext, ToolResult, obj

# Script types Capri may write and run, mapped to their code-fence language.
if sys.platform == "win32":
    SCRIPT_TYPES = {".py": "python", ".ps1": "powershell", ".bat": "batch", ".cmd": "batch"}
    SCRIPT_KINDS = "Python (.py), PowerShell (.ps1) or batch (.bat/.cmd)"
else:
    SCRIPT_TYPES = {".py": "python", ".sh": "bash", ".zsh": "zsh"}
    SCRIPT_KINDS = "Python (.py), Bash (.sh) or Zsh (.zsh)"
# Windows interpreters expect CRLF line endings; everything else uses LF.
_CRLF_TYPES = {".ps1", ".bat", ".cmd"}
MAX_OUTPUT = 8000
_NAME_RE = re.compile(r"^[\w][\w .\-]{0,80}$")


def _suffix_list() -> str:
    """Allowed script extensions as text, e.g. ``.py, .sh or .zsh``."""
    suffixes = list(SCRIPT_TYPES)
    return ", ".join(suffixes[:-1]) + " or " + suffixes[-1]


def script_path(filename: Any, ctx: ToolContext) -> Path:
    """Validate a script file name and return its path inside the workspace."""
    name = re.split(r"[\\/]", str(filename or "").strip().strip('"'))[-1]
    if not name or not _NAME_RE.match(name):
        raise ValueError("Use a simple script file name such as cleanup.py")
    if Path(name).suffix.lower() not in SCRIPT_TYPES:
        raise ValueError("Scripts must end in " + _suffix_list())
    return ctx.workspace / name


def build_command(path: Path, args: list[str]) -> list[str]:
    """Interpreter command line for a script."""
    suffix = path.suffix.lower()
    if suffix == ".py":
        python = sys.executable or "python"
        if os.path.basename(python).lower() == "pythonw.exe":
            candidate = os.path.join(os.path.dirname(python), "python.exe")
            python = candidate if os.path.exists(candidate) else python
        return [python, "-u", str(path), *args]
    if suffix == ".ps1":
        return ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(path), *args]
    if suffix == ".sh":
        return ["/bin/bash", str(path), *args]
    if suffix == ".zsh":
        return ["/bin/zsh", str(path), *args]
    return ["cmd", "/c", str(path), *args]


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],  # noqa: S603,S607
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            # The script runs in its own session (see run_script), so killing
            # the process group also stops any child processes it started.
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


def _clip(text: str) -> str:
    text = text.strip()
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return text[:half] + "\n[...output truncated...]\n" + text[-half:]


def relevant_error_line(stderr: str, stdout: str = "") -> str:
    """The most useful single line of an error output (last non-empty line)."""
    for stream in (stderr, stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1][:160]
    return "script failed"


def save_script(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = script_path(args.get("filename"), ctx)
    content = str(args.get("content") or "").replace("\r\n", "\n")
    if not content.strip():
        raise ValueError("The script content is empty.")
    ctx.policy.check_modify(path)
    if path.exists():
        request = ConfirmRequest(
            title="Overwrite a script",
            summary="Capri wants to replace an existing script in the workspace.",
            details=[f"OVERWRITE: {path}"], code=content,
            code_language=SCRIPT_TYPES[path.suffix.lower()], kind="script")
        if not ctx.confirm(request):
            return ToolResult(False, "The user denied overwriting the script.", "denied by user")
    path.parent.mkdir(parents=True, exist_ok=True)
    newline = "\r\n" if path.suffix.lower() in _CRLF_TYPES else "\n"
    path.write_text(content, encoding="utf-8", newline=newline)
    fence = SCRIPT_TYPES[path.suffix.lower()]
    return ToolResult(True, f"Saved script to {path}. Run it with run_script.",
                      f"saved {path.name}", details=f"`{path}`\n```{fence}\n{content}\n```")


def run_script(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    path = script_path(args.get("filename"), ctx)
    if not path.is_file():
        raise ValueError(f"No script named {path.name} in the workspace. Save it first.")
    extra = [str(a) for a in (args.get("args") or [])] if isinstance(
        args.get("args"), (list, tuple)) else []
    source = path.read_text(encoding="utf-8", errors="replace")
    digest = hashlib.sha256(source.encode("utf-8", "replace")).hexdigest()
    command = build_command(path, extra)
    request = ConfirmRequest(
        title="Run a script",
        summary=f"Capri wants to run this script on your {machine_word()}. "
                "Read it before allowing.",
        details=[f"Script: {path}",
                 f"Working directory: {ctx.workspace}",
                 f"Arguments: {' '.join(extra) if extra else '(none)'}",
                 f"Timeout: {ctx.script_timeout} seconds"],
        code=source, code_language=SCRIPT_TYPES[path.suffix.lower()], kind="script")
    if not ctx.confirm(request):
        return ToolResult(False, "The user denied running the script. Do not retry.",
                          "denied by user")
    current = path.read_text(encoding="utf-8", errors="replace")
    if hashlib.sha256(current.encode("utf-8", "replace")).hexdigest() != digest:
        raise ValueError("The script changed after it was approved; not running it.")

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603
            command, cwd=str(ctx.workspace), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            start_new_session=sys.platform != "win32")
    except OSError as exc:
        raise ValueError(f"Could not start the interpreter: {exc}") from exc

    output: dict[str, str] = {"out": "", "err": ""}

    def _collect() -> None:
        out, err = proc.communicate()
        output["out"], output["err"] = out or "", err or ""

    reader = threading.Thread(target=_collect, daemon=True)
    reader.start()
    status = "done"
    last_tick = -1
    while reader.is_alive():
        reader.join(0.2)
        elapsed = time.monotonic() - started
        if int(elapsed) != last_tick:
            last_tick = int(elapsed)
            ctx.progress({"hud": f"RUNNING {path.name} · {last_tick}s", "elapsed": last_tick})
        if ctx.cancel.is_set():
            status = "cancelled"
            _kill_tree(proc)
            break
        if elapsed > ctx.script_timeout:
            status = "timeout"
            _kill_tree(proc)
            break
    reader.join(5)
    elapsed = time.monotonic() - started
    stdout, stderr = _clip(output["out"]), _clip(output["err"])
    code = proc.returncode

    report = f"Script: {path.name}\nDuration: {elapsed:.1f}s\n"
    details = f"`{path.name}` · {elapsed:.1f}s"
    if status == "timeout":
        report += f"Result: KILLED after the {ctx.script_timeout}s timeout.\n"
    elif status == "cancelled":
        report += "Result: stopped by the user.\n"
    else:
        report += f"Exit code: {code}\n"
        details += f" · exit code {code}"
    report += f"--- stdout ---\n{stdout or '(empty)'}\n--- stderr ---\n{stderr or '(empty)'}"
    if stdout:
        details += f"\n\n**stdout**\n```\n{stdout}\n```"
    if stderr:
        details += f"\n\n**stderr**\n```\n{stderr}\n```"

    if status == "timeout":
        line = f"timed out after {ctx.script_timeout}s"
        return ToolResult(False, report, "timed out", details, error_line=line)
    if status == "cancelled":
        return ToolResult(False, report, "stopped", details, error_line="stopped")
    if code != 0:
        return ToolResult(False, report, f"failed (exit {code})", details,
                          error_line=relevant_error_line(stderr, stdout))
    return ToolResult(True, report, f"ok · {elapsed:.1f}s", details)


def script_tools() -> list[Tool]:
    """Script saving and running tools."""
    return [
        Tool("save_script", "save script",
             f"Save a {SCRIPT_KINDS} script into the workspace folder. Give only a file "
             "name, not a path.",
             obj({"filename": {"type": "string", "description": "e.g. cleanup.py"},
                  "content": {"type": "string", "description": "Full script source"}},
                 ["filename", "content"]),
             save_script, "coding", "files",
             lambda a: f"WRITING {os.path.basename(str(a.get('filename') or 'script'))}",
             lambda a: os.path.basename(str(a.get("filename") or ""))),
        Tool("run_script", "run script",
             "Run a script previously saved in the workspace. The user sees the full script "
             "and must confirm. Returns exit code, stdout and stderr.",
             obj({"filename": {"type": "string"},
                  "args": {"type": "array", "items": {"type": "string"},
                           "description": "Optional command line arguments"}}, ["filename"]),
             run_script, "coding", "script",
             lambda a: f"RUNNING {os.path.basename(str(a.get('filename') or 'script'))}"),
    ]
