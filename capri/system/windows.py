"""Windows-specific helpers (safe no-ops on other platforms)."""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                  "XamlExplorerHostIslandWindow", "Windows.UI.Core.CoreWindow"}


def foreground_is_fullscreen() -> bool:
    """True when another application's window covers a whole monitor exactly
    (games, videos, presentations). Maximized windows do not count."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW(hwnd, class_name, 256)
        if class_name.value in _SHELL_CLASSES:
            return False

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid():
            return False

        rect = wintypes.RECT()
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return (abs(rect.left - m.left) <= 1 and abs(rect.top - m.top) <= 1
                and abs(rect.right - m.right) <= 1 and abs(rect.bottom - m.bottom) <= 1)
    except Exception:  # never let a detection hiccup disturb the app
        log.debug("Fullscreen detection failed", exc_info=True)
        return False
