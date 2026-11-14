"""Tests for the cross-platform parts: macOS window parsing, scripts and file checks."""
import os
import sys
import threading
from pathlib import Path

import pytest

from capri import osutil
from capri.character import app_windows
from capri.character.docking import Rect
from capri.tools import files, scripts
from capri.tools.base import ToolContext
from capri.tools.safety import SafetyPolicy


def _entry(**overrides):
    """A Quartz window dictionary as returned by CGWindowListCopyWindowInfo."""
    entry = {"kCGWindowLayer": 0, "kCGWindowOwnerPID": 4242, "kCGWindowNumber": 77,
             "kCGWindowOwnerName": "Safari", "kCGWindowName": "Apple",
             "kCGWindowAlpha": 1.0,
             "kCGWindowBounds": {"X": 100.0, "Y": 50.5, "Width": 800.0, "Height": 600.0}}
    entry.update(overrides)
    return entry


def test_mac_window_entry_is_parsed_in_qt_coordinates() -> None:
    parsed = app_windows._mac_parse(_entry(), own_pid=1)
    assert parsed == (77, "Apple", "Safari", Rect(100, 50, 800, 600))


def test_mac_window_without_title_uses_the_app_name() -> None:
    # Without the Screen Recording permission macOS leaves the title out.
    entry = _entry()
    del entry["kCGWindowName"]
    assert app_windows._mac_parse(entry, own_pid=1)[1] == "Safari"


@pytest.mark.parametrize("overrides", [
    {"kCGWindowLayer": 25},                 # menu bar / status items
    {"kCGWindowOwnerPID": 1},               # Capri's own windows
    {"kCGWindowAlpha": 0.0},                # invisible
    {"kCGWindowOwnerName": "Dock"},         # system UI
    {"kCGWindowBounds": {"X": 0, "Y": 0, "Width": 20, "Height": 20}},  # tiny helper
    {"kCGWindowBounds": None},
    {"kCGWindowNumber": 0},
])
def test_mac_window_entries_that_are_not_app_windows_are_skipped(overrides) -> None:
    assert app_windows._mac_parse(_entry(**overrides), own_pid=1) is None


def test_mac_windows_can_be_matched_by_app_name() -> None:
    windows = [app_windows.WindowInfo(1, "Budget.xlsx", "Microsoft Excel"),
               app_windows.WindowInfo(2, "Downloads", "Finder")]
    assert app_windows.match_window("excel", windows).handle == 1
    assert app_windows.match_window("finder", windows).handle == 2


def test_platform_wording() -> None:
    assert osutil.trash_name() == ("Trash" if sys.platform == "darwin" else "Recycle Bin")
    assert osutil.font_pt(10) == (13 if sys.platform == "darwin" else 10)
    assert osutil.os_display_name()


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    work = tmp_path / "workspace"
    work.mkdir()
    return ToolContext(workspace=work, policy=SafetyPolicy(), confirm=lambda request: True,
                       cancel=threading.Event())


def test_bundles_and_executables_count_as_code(tmp_path: Path) -> None:
    bundle = tmp_path / "Tool.app"
    bundle.mkdir()  # macOS app bundles are folders
    assert files.can_run_code(bundle)
    assert files.can_run_code(tmp_path / "setup.command")
    plain = tmp_path / "notes.txt"
    plain.write_text("x")
    assert not files.can_run_code(plain)
    if sys.platform != "win32":
        binary = tmp_path / "tool"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        assert files.can_run_code(binary)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell scripts")
def test_shell_scripts_run_with_lf_line_endings(ctx: ToolContext) -> None:
    with pytest.raises(ValueError):
        scripts.script_path("tool.ps1", ctx)  # PowerShell / batch are Windows only
    assert scripts.save_script({"filename": "hello.sh",
                                "content": "echo \"hi $1\"\r\nexit 0\r\n"}, ctx).ok
    assert b"\r" not in (ctx.workspace / "hello.sh").read_bytes()
    result = scripts.run_script({"filename": "hello.sh", "args": ["mac"]}, ctx)
    assert result.ok and "hi mac" in result.content


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_timeout_also_kills_child_processes(ctx: ToolContext, tmp_path: Path) -> None:
    ctx.script_timeout = 1
    marker = tmp_path / "child_survived"
    (ctx.workspace / "spawn.sh").write_text(f"(sleep 3; touch '{marker}') &\nsleep 30\n")
    result = scripts.run_script({"filename": "spawn.sh"}, ctx)
    assert not result.ok and "timed out" in result.summary
    import time
    time.sleep(3.5)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows script types")
def test_windows_script_types_are_kept(ctx: ToolContext) -> None:
    for name in ("a.ps1", "b.bat", "c.cmd", "d.py"):
        assert scripts.script_path(name, ctx).name == name
    assert os.path.basename(scripts.build_command(Path("x.bat"), [])[0]) == "cmd"
