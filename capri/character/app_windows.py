"""Finds other applications' windows on Windows and macOS (safe no-ops elsewhere).

Windows: Win32 reports physical pixels; Qt works in logical (DPI-scaled)
pixels. Each rectangle is converted using the monitor it is on, so docking
also works with display scaling and on multi-monitor setups with different
scales.

macOS: Quartz (``CGWindowListCopyWindowInfo``) reports window bounds in
points, with the origin at the top-left corner of the main display. That is
exactly Qt's global coordinate system on macOS, so no conversion is needed.
Window titles are only visible to apps with the Screen Recording permission;
without it the application name is used as the title instead.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass

from PySide6.QtGui import QGuiApplication

from .docking import AppWindow, Rect

log = logging.getLogger(__name__)

_IGNORED_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                    "NotifyIconOverflowWindow", "Windows.UI.Core.CoreWindow",
                    "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland"}
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14

# macOS: owners of on-screen windows that are part of the system UI, not apps.
_MAC_IGNORED_OWNERS = {"Window Server", "Dock", "Control Center", "Notification Center",
                       "SystemUIServer", "Spotlight", "Wallpaper", "WindowManager",
                       "Screenshot", "TextInputMenuAgent"}
_MAC_MIN_SIZE = 40  # smaller windows are helpers, status items or invisible panels

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")

    class _MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    _EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_EnumProc, wintypes.LPARAM]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsIconic.argtypes = [wintypes.HWND]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    _user32.MonitorFromRect.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFOEX)]
    _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                              wintypes.DWORD]

    def _frame(hwnd: int) -> "wintypes.RECT | None":
        """Visible frame (without the invisible resize borders) in physical pixels."""
        rect = wintypes.RECT()
        if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect),
                                         ctypes.sizeof(rect)) != 0:
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None
        return rect

    def _cloaked(hwnd: int) -> bool:
        """Hidden by DWM (other virtual desktop, suspended UWP app...)."""
        value = wintypes.DWORD()
        if _dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(value),
                                         ctypes.sizeof(value)) != 0:
            return False
        return value.value != 0

    def _to_logical(rect: "wintypes.RECT") -> Rect:
        """Convert a physical rectangle to Qt logical coordinates."""
        info = _MONITORINFOEX()
        info.cbSize = ctypes.sizeof(_MONITORINFOEX)
        monitor = _user32.MonitorFromRect(ctypes.byref(rect), 2)  # MONITOR_DEFAULTTONEAREST
        screen = None
        if monitor and _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            screen = next((s for s in QGuiApplication.screens() if s.name() == info.szDevice),
                          None)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
            scale = screen.devicePixelRatio() or 1.0
            return Rect(round(rect.left / scale), round(rect.top / scale),
                        round((rect.right - rect.left) / scale),
                        round((rect.bottom - rect.top) / scale))
        scale = screen.devicePixelRatio() or 1.0
        origin = screen.geometry().topLeft()
        m = info.rcMonitor
        return Rect(origin.x() + round((rect.left - m.left) / scale),
                    origin.y() + round((rect.top - m.top) / scale),
                    round((rect.right - rect.left) / scale),
                    round((rect.bottom - rect.top) / scale))

    def _usable(hwnd: int) -> bool:
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd) or _cloaked(hwnd):
            return False
        ex_style = _user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex_style & (_WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE):
            return False
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False
        name = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, name, 256)
        return name.value not in _IGNORED_CLASSES

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR,
                                                     ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _process_name(hwnd: int) -> str:
        """Executable name of the window's process, e.g. ``chrome``."""
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(520)
            buffer = ctypes.create_unicode_buffer(size.value)
            if _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.splitext(os.path.basename(buffer.value))[0]
            return ""
        finally:
            _kernel32.CloseHandle(handle)

    def _title(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value


if sys.platform == "darwin":
    try:
        import Quartz
    except ImportError:  # pyobjc-framework-Quartz not installed
        Quartz = None
        log.warning("pyobjc-framework-Quartz is missing: Capri can't see other windows. "
                    "Run 'pip install -r requirements.txt'.")

    def _mac_window_list(option: int, window_id: int = 0) -> list:
        """Raw window dictionaries from Quartz, front to back."""
        if Quartz is None:
            return []
        return list(Quartz.CGWindowListCopyWindowInfo(option, window_id) or [])

    def _mac_on_screen() -> list:
        """Windows on the current Space (minimized and hidden ones are excluded)."""
        if Quartz is None:
            return []
        return _mac_window_list(Quartz.kCGWindowListOptionOnScreenOnly
                                | Quartz.kCGWindowListExcludeDesktopElements,
                                Quartz.kCGNullWindowID)


# The Quartz dictionary keys are CFStrings whose values equal their names, so
# plain strings work and keep the parsing below testable on any platform.
def _mac_rect(entry) -> Rect | None:
    """Bounds of a Quartz window dictionary as a :class:`Rect` (Qt coordinates)."""
    bounds = entry.get("kCGWindowBounds")
    if not bounds:
        return None
    try:
        return Rect(round(float(bounds["X"])), round(float(bounds["Y"])),
                    round(float(bounds["Width"])), round(float(bounds["Height"])))
    except (KeyError, TypeError, ValueError):
        return None


def _mac_parse(entry, own_pid: int) -> tuple[int, str, str, Rect] | None:
    """``(handle, title, process, rect)`` for a usable app window, else ``None``."""
    try:
        if int(entry.get("kCGWindowLayer", -1)) != 0:  # 0 = normal app windows
            return None
        if int(entry.get("kCGWindowOwnerPID", -1)) == own_pid:
            return None
        if float(entry.get("kCGWindowAlpha", 1.0)) <= 0.01:
            return None
        handle = int(entry.get("kCGWindowNumber", 0))
    except (TypeError, ValueError):
        return None
    process = str(entry.get("kCGWindowOwnerName") or "").strip()
    if not handle or process in _MAC_IGNORED_OWNERS:
        return None
    rect = _mac_rect(entry)
    if rect is None or rect.width < _MAC_MIN_SIZE or rect.height < _MAC_MIN_SIZE:
        return None
    # Without the Screen Recording permission the title is missing: fall back
    # to the application name so the window can still be found and targeted.
    title = str(entry.get("kCGWindowName") or "").strip() or process
    if not title:
        return None
    return handle, title, process, rect


def unavailable_reason() -> str | None:
    """Why other windows can't be detected on this machine, or ``None`` if they can."""
    if sys.platform == "win32":
        return None
    if sys.platform == "darwin":
        if Quartz is None:
            return ("the pyobjc-framework-Quartz package is missing "
                    "(pip install -r requirements.txt)")
        return None
    return "window detection only works on Windows and macOS"


def titles_hidden() -> bool:
    """True on macOS when window titles are hidden for lack of Screen Recording access."""
    if sys.platform != "darwin" or Quartz is None:
        return False
    try:
        return not bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:  # very old macOS without the API: titles are always visible there
        return False


@dataclass(frozen=True)
class WindowInfo:
    """Title and program of an open window (no geometry, no Qt: safe in any thread)."""

    handle: int
    title: str
    process: str


def enumerate_titles() -> list[WindowInfo]:
    """Open windows of other applications, topmost first. Thread-safe (no Qt calls)."""
    if sys.platform == "darwin":
        try:
            own_pid = os.getpid()
            parsed = (_mac_parse(entry, own_pid) for entry in _mac_on_screen())
            return [WindowInfo(p[0], p[1], p[2]) for p in parsed if p is not None]
        except Exception:
            log.debug("Window title enumeration failed", exc_info=True)
            return []
    if sys.platform != "win32":
        return []
    found: list[WindowInfo] = []
    try:
        def callback(hwnd: int, _param: int) -> bool:
            if _usable(hwnd):
                title = _title(hwnd)
                if title:
                    found.append(WindowInfo(int(hwnd), title, _process_name(hwnd)))
            return True

        _user32.EnumWindows(_EnumProc(callback), 0)
    except Exception:
        log.debug("Window title enumeration failed", exc_info=True)
        return []
    return found


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^\w]+", text.lower()) if w]


def match_window(query: str, windows: list[WindowInfo]) -> WindowInfo | None:
    """Best window for a loose description such as ``"chrome"`` or ``"the excel file"``.

    Scores: exact title > title or program starts with the query > title or
    program contains it > all query words appear. Ties go to the topmost window.
    """
    q = " ".join(_words(query))
    if not q:
        return None
    stop = {"the", "a", "an", "window", "app", "application", "program", "my", "open"}
    words = [w for w in q.split() if w not in stop] or q.split()
    best: tuple[int, int] | None = None
    choice = None
    for rank, window in enumerate(windows):
        title = " ".join(_words(window.title))
        process = window.process.lower()
        haystack = f"{title} {process}"
        if title == q:
            score = 100
        elif title.startswith(q) or process == q or process.startswith(q):
            score = 80
        elif q in title or q in process:
            score = 60
        elif all(w in haystack for w in words):
            score = 40 + min(10, len(words))
        elif any(len(w) > 2 and w in haystack for w in words):
            score = 10 + sum(1 for w in words if w in haystack)
        else:
            continue
        key = (score, -rank)
        if best is None or key > best:
            best, choice = key, window
    return choice


def list_windows() -> list[AppWindow]:
    """Visible top-level windows of other applications, topmost first."""
    if sys.platform == "darwin":
        try:
            own_pid = os.getpid()
            parsed = (_mac_parse(entry, own_pid) for entry in _mac_on_screen())
            return [AppWindow(p[0], p[1], p[3]) for p in parsed if p is not None]
        except Exception:  # detection is a nice-to-have; never break Capri over it
            log.debug("Window enumeration failed", exc_info=True)
            return []
    if sys.platform != "win32":
        return []
    found: list[AppWindow] = []
    try:
        def callback(hwnd: int, _param: int) -> bool:
            if _usable(hwnd):
                title = _title(hwnd)
                frame = _frame(hwnd)
                if title and frame is not None:
                    rect = _to_logical(frame)
                    if rect.width > 0 and rect.height > 0:
                        found.append(AppWindow(int(hwnd), title, rect))
            return True

        _user32.EnumWindows(_EnumProc(callback), 0)
    except Exception:  # detection is a nice-to-have; never break Capri over it
        log.debug("Window enumeration failed", exc_info=True)
        return []
    return found


def window_rect(handle: int) -> Rect | None:
    """Current frame of a window, or ``None`` once it is closed, minimized or hidden."""
    if sys.platform == "darwin" and handle:
        try:
            if Quartz is None:
                return None
            entries = _mac_window_list(Quartz.kCGWindowListOptionIncludingWindow, int(handle))
            entry = next((e for e in entries
                          if int(e.get("kCGWindowNumber", 0)) == int(handle)), None)
            # Minimized windows, hidden apps and windows on another Space are
            # reported as not on screen.
            if entry is None or not entry.get("kCGWindowIsOnscreen"):
                return None
            return _mac_rect(entry)
        except Exception:
            log.debug("window_rect failed", exc_info=True)
            return None
    if sys.platform != "win32" or not handle:
        return None
    try:
        if not _user32.IsWindow(handle) or not _user32.IsWindowVisible(handle) \
                or _user32.IsIconic(handle) or _cloaked(handle):
            return None
        frame = _frame(handle)
        return _to_logical(frame) if frame is not None else None
    except Exception:
        log.debug("window_rect failed", exc_info=True)
        return None
