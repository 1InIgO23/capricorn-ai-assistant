"""Finds other applications' windows on Windows (safe no-ops elsewhere).

Win32 reports physical pixels; Qt works in logical (DPI-scaled) pixels. Each
rectangle is converted using the monitor it is on, so docking also works with
display scaling and on multi-monitor setups with different scales.
"""
from __future__ import annotations

import logging
import os
import sys

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

    def _title(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value


def list_windows() -> list[AppWindow]:
    """Visible top-level windows of other applications, topmost first."""
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
