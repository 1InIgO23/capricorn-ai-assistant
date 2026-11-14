"""Operating system helpers shared by the whole app (Windows and macOS).

Everything that depends on the platform and is needed in more than one place
lives here, so the rest of the code can stay platform neutral. Nothing in this
module imports Qt at import time, so it is safe to use from worker threads and
from the unit tests.
"""
from __future__ import annotations

import logging
import platform
import sys
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Qt measures font points at 96 DPI on Windows but at 72 DPI on macOS, so the
# same point size is drawn about 25% smaller on a Mac. Every font size goes
# through this factor to keep the proportions of the original design.
FONT_SCALE = 96 / 72 if IS_MAC else 1.0

# NSWindowCollectionBehavior flags (AppKit), used to keep Capri on every Space.
_NS_CAN_JOIN_ALL_SPACES = 1 << 0
_NS_MOVE_TO_ACTIVE_SPACE = 1 << 1
_NS_FULL_SCREEN_AUXILIARY = 1 << 8
# NSApplicationActivationPolicy values.
_NS_POLICY_REGULAR = 0
_NS_POLICY_ACCESSORY = 1


# ----------------------------------------------------------------- wording
def os_display_name() -> str:
    """Human friendly OS name for the system prompt, e.g. ``macOS 15.1 (Apple Silicon)``."""
    if IS_MAC:
        version = platform.mac_ver()[0] or platform.release()
        chip = "Apple Silicon" if platform.machine() == "arm64" else "Intel"
        return f"macOS {version} ({chip})"
    return f"{platform.system()} {platform.release()}"


def machine_word() -> str:
    """How the user's computer is called in UI texts ("your PC" / "your Mac")."""
    if IS_WINDOWS:
        return "PC"
    if IS_MAC:
        return "Mac"
    return "computer"


def trash_name() -> str:
    """Name of the place deleted files go to."""
    return "Trash" if IS_MAC else "Recycle Bin"


def tray_name() -> str:
    """Name of the area that holds Capri's status icon."""
    return "menu bar" if IS_MAC else "system tray"


def font_pt(points: float) -> int:
    """Platform corrected point size for Qt style sheets (``font-size: Npt``)."""
    return max(1, round(points * FONT_SCALE))


# ---------------------------------------------------------- macOS windows
def _ns_window(widget: Any) -> Any:
    """The NSWindow behind a top-level QWidget, or ``None`` (needs PyObjC)."""
    import objc  # noqa: PLC0415 (only available on macOS)

    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def _keep_tool_window_visible(widget: Any) -> None:
    """Stop a ``Qt.Tool`` window from vanishing when another app is focused.

    On macOS, Qt turns tool windows into NSPanels, which by default hide while
    the app is in the background. Capri must stay on the desktop all the time
    (as she does on Windows), and she should also follow the user to every
    Space (virtual desktop) and over full-screen apps.
    """
    from PySide6.QtCore import Qt  # noqa: PLC0415

    widget.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)
    try:
        window = _ns_window(widget)
        if window is None:
            return
        window.setHidesOnDeactivate_(False)
        behavior = int(window.collectionBehavior())
        behavior &= ~_NS_MOVE_TO_ACTIVE_SPACE  # mutually exclusive with "all Spaces"
        behavior |= _NS_CAN_JOIN_ALL_SPACES | _NS_FULL_SCREEN_AUXILIARY
        window.setCollectionBehavior_(behavior)
    except Exception:  # PyObjC missing or an unexpected native error: not fatal
        log.debug("Could not adjust the native tool window", exc_info=True)


def _make_tool_window_filter() -> Any:
    """Event filter that applies :func:`_keep_tool_window_visible` to every tool window."""
    from PySide6.QtCore import QEvent, QObject, Qt  # noqa: PLC0415
    from PySide6.QtWidgets import QWidget  # noqa: PLC0415

    class ToolWindowFilter(QObject):
        def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
            # Show is sent each time a window appears, including after
            # setWindowFlags() recreated its native window, so the fix is
            # re-applied whenever it might have been lost.
            if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() \
                    and (obj.windowFlags() & Qt.WindowType_Mask) == Qt.Tool:
                _keep_tool_window_visible(obj)
            return False

    return ToolWindowFilter()


def set_dock_icon_visible(visible: bool) -> None:
    """Show or hide the Dock icon (macOS). Without it Capri lives in the menu bar."""
    if not IS_MAC:
        return
    try:
        import AppKit  # noqa: PLC0415

        policy = _NS_POLICY_REGULAR if visible else _NS_POLICY_ACCESSORY
        AppKit.NSApplication.sharedApplication().setActivationPolicy_(policy)
    except Exception as exc:
        log.warning("Could not change the Dock icon (is pyobjc-framework-Cocoa installed?): %s",
                    exc)


def configure_application(app: Any, show_dock_icon: bool = False) -> None:
    """Platform specific tweaks for the QApplication. Call before showing any window."""
    if not IS_MAC:
        return
    window_filter = _make_tool_window_filter()
    window_filter.setParent(app)  # keeps the filter alive as long as the app
    app.installEventFilter(window_filter)
    set_dock_icon_visible(show_dock_icon)
