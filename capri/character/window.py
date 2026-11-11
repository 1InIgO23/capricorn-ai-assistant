"""Capri's desktop presence: a frameless, transparent, always-on-top window.

The character image (or the runtime placeholder) is only one layer. A
procedural effects pipeline adds the floating bob, breathing, glow outline,
RGB split, scanlines, glitch slices and the dissolve/reassemble "re-render"
used whenever she moves. Tool visuals and the activity HUD are drawn in the
same window so you can tell what she is doing without opening the chat.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
import random
import time
from collections import deque
from typing import Callable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QFontMetricsF, QGuiApplication, QImage, QPainter,
                           QPen, QRadialGradient, QRegion)
from PySide6.QtWidgets import QWidget

from ..config.settings import Settings
from ..config.theme import Theme
from ..ui.style import blend, pulse, qcolor, qfont
from . import app_windows
from .docking import DockTarget, Rect, dock_position, find_dock, window_edges
from .mood import MoodEngine
from .placeholder import draw_construct
from .sprites import SpriteLibrary

log = logging.getLogger(__name__)

TICK_MS = 33
WINDOW_POLL_MS = 250
_FRAGMENTS = ("01", "//", "{}", "#", "@", "::", "</>", "?", "10", "~")


def _rect(rect: QRect) -> Rect:
    """Convert a QRect to the docking module's Rect."""
    return Rect(rect.x(), rect.y(), rect.width(), rect.height())


class CharacterWindow(QWidget):
    """The animated construct on the desktop."""

    clicked = Signal()
    menu_requested = Signal(QPoint)
    moved = Signal()
    woke = Signal()
    interaction = Signal(str)       # "click" | "hover" | "drag"
    click_reaction = Signal(str)    # "playful" | "annoyed"

    def __init__(self, settings: Settings, theme: Theme, sprites: SpriteLibrary,
                 mood: MoodEngine) -> None:
        super().__init__(None)
        self.settings = settings
        self.theme = theme
        self.sprites = sprites
        self.mood = mood

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self._apply_flags()

        now = time.monotonic()
        self._t = 0.0
        self._last_tick = now
        self._tick_count = 0
        # state
        self._activity: str | None = None
        self._alert = False
        self._state = "idle"
        self._state_since = 0.0
        self._happy_until = 0.0
        self._error_until = 0.0
        self._blink_until = 0.0
        self._next_blink = now + random.uniform(3, 6)
        self._sleeping = False
        self._last_interaction = now
        self._pinned = False
        # pose
        self._face = 1.0
        self._face_target = 1.0
        self._tilt = 0.0
        self._hovered = False
        self._last_hover = 0.0
        self._shake_until = 0.0
        # effects
        self._glitch_until = 0.0
        self._burst_until = 0.0
        self._next_glitch = now + random.uniform(3, 8)
        self._slices: list[tuple[int, int, float]] = []
        self._flash_color: QColor | None = None
        self._flash_until = 0.0
        self._dissolve = 0.0
        self._rerender: dict | None = None
        self._strip_rand = [(random.random(), random.random()) for _ in range(400)]
        self._pix_rand = [(random.random(), random.random(), random.random()) for _ in range(40)]
        # hud & tool visuals
        self._hud_text = ""
        self._hud_alpha = 0.0
        self._hud_hold_until = 0.0
        self._hud_color: QColor | None = None
        self._hud_override: tuple[str, QColor | None] = ("", None)
        self._ticker: list[str] = []
        self._visual = ""
        self._chip_text = ""
        self._chip_icon = None
        # drag / dock
        self._press_pos: QPoint | None = None
        self._press_offset = QPoint()
        self._dragging = False
        self._dock: DockTarget | None = None
        self._peek_hidden = False
        self._dock_timer = QTimer(self)
        self._dock_timer.setInterval(WINDOW_POLL_MS)
        self._dock_timer.timeout.connect(self._follow_window)
        self._clicks: deque[float] = deque(maxlen=12)
        self._next_wander = now + random.uniform(360, 600)

        self._layout()
        self._restore_position()

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.reassemble()

    # ------------------------------------------------------------ geometry
    def _apply_flags(self) -> None:
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        if self.settings.get("always_on_top", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _layout(self) -> None:
        size = int(self.settings.get("character_size", 220) or 220)
        self.S = max(80, min(size, 600))
        self.F = int(self.S * 1.4)
        self.win_w = int(self.S * 2.2)
        self.win_h = int(self.S * 1.45) + 36
        self.char_rect = QRectF((self.win_w - self.S) / 2, self.S * 0.3, self.S, self.S)
        self.setFixedSize(self.win_w, self.win_h)

    def apply_settings(self) -> None:
        """Re-read size and window flags after the settings changed."""
        anchor = self.char_pos()
        visible = self.isVisible()
        if int(self.settings.get("character_size", 220)) != self.S:
            self._layout()
            self.sprites.reload(self.S)
        self._apply_flags()
        self.move_char_to(anchor)
        if visible:
            self.show()

    def char_rect_global(self) -> QRect:
        """The character's square in global screen coordinates."""
        return QRect(self.x() + int(self.char_rect.x()), self.y() + int(self.char_rect.y()),
                     self.S, self.S)

    def char_pos(self) -> QPoint:
        return self.char_rect_global().topLeft()

    def move_char_to(self, top_left: QPoint) -> None:
        """Move the window so the character's square starts at *top_left*."""
        self.move(top_left.x() - int(self.char_rect.x()), top_left.y() - int(self.char_rect.y()))
        self.moved.emit()

    def current_screen_rect(self) -> QRect:
        """Available geometry of the screen Capri is on."""
        screen = QGuiApplication.screenAt(self.char_rect_global().center()) \
            or QGuiApplication.primaryScreen()
        return screen.availableGeometry()

    def _restore_position(self) -> None:
        x, y = self.settings.get("window.x"), self.settings.get("window.y")
        primary = QGuiApplication.primaryScreen().availableGeometry()
        default = QPoint(primary.right() - self.S - 60, primary.bottom() - self.S - 70)
        if isinstance(x, int) and isinstance(y, int):
            target = QPoint(x, y)
            center = target + QPoint(self.S // 2, self.S // 2)
            if not any(s.geometry().adjusted(-self.S, -self.S, self.S, self.S).contains(center)
                       for s in QGuiApplication.screens()):
                target = default
        else:
            target = default
        self.move_char_to(target)
        dock = self.settings.get("window.dock")
        if dock in ("left", "right", "top", "bottom"):
            screen = _rect(self.current_screen_rect())
            along = target.y() - screen.top if dock in ("left", "right") else \
                target.x() - screen.left
            self.set_dock(DockTarget("screen", dock, screen, along), hidden=True, burst=False)

    def save_position(self) -> None:
        """Remember where she is (the fully visible position when docked).

        Only screen docks are restored at the next start; the window she peeked
        from may not exist any more.
        """
        pos = self.dock_shown_pos() if self._dock else self.char_pos()
        self.settings.set("window.x", int(pos.x()))
        self.settings.set("window.y", int(pos.y()))
        self.settings.set("window.dock",
                          self._dock.edge if self._dock and self._dock.kind == "screen" else None)

    # ------------------------------------------------------- public control
    @property
    def state(self) -> str:
        return self._state

    @property
    def asleep(self) -> bool:
        return self._sleeping

    def set_pinned(self, pinned: bool) -> None:
        """Keep her fully visible (not peeking) while the chat is open."""
        self._pinned = pinned

    def set_activity(self, state: str | None, hud: str | None = None, visual: str = "",
                     chip: str = "") -> None:
        """Set what she is doing: character state, HUD line and tool visual."""
        self._activity = state
        self._visual = visual
        self._ticker = []
        if state is not None:
            self.wake()
        if hud:
            self.set_hud(hud)
        else:
            self.clear_hud()
        self.set_chip(chip)

    def set_hud(self, text: str, color: QColor | None = None, hold: float = 0.0) -> None:
        """Show a HUD line. *hold* keeps it for that many seconds, then fades."""
        if hold:
            self._hud_override = (text, color)
            self._hud_hold_until = time.monotonic() + hold
        else:
            self._hud_text = text
            self._hud_color = color

    def clear_hud(self) -> None:
        self._hud_text = ""

    def _hud_current(self) -> tuple[str, QColor | None]:
        if self._hud_hold_until and time.monotonic() < self._hud_hold_until:
            return self._hud_override
        return self._hud_text, self._hud_color

    def add_ticker(self, item: str) -> None:
        """Add a domain that flickers past in the HUD during a web search."""
        if item and item not in self._ticker:
            self._ticker.append(item)

    def set_chip(self, name: str) -> None:
        """Show (or hide with an empty name) the floating file chip."""
        self._chip_text = name
        self._chip_icon = None
        if name:
            try:
                from PySide6.QtCore import QFileInfo
                from PySide6.QtWidgets import QFileIconProvider

                icon = QFileIconProvider().icon(QFileInfo(name))
                if not icon.isNull():
                    self._chip_icon = icon.pixmap(16, 16)
            except Exception:  # icon lookup is purely cosmetic
                self._chip_icon = None

    def set_alert(self, alert: bool) -> None:
        """Pulse in the alert color while a confirmation is waiting."""
        self._alert = alert
        if alert:
            self.wake()
            self._hud_hold_until = 0.0
            self.set_hud("WAITING FOR YOUR OK")
        else:
            self.clear_hud()

    def flash(self, kind: str, message: str = "") -> None:
        """``success``: short green flash. ``error``: red glitch burst + HUD line.
        ``happy``: brief happy state."""
        now = time.monotonic()
        if kind == "success":
            self._flash_color = qcolor(self.theme.color("success"))
            self._flash_until = now + 0.55
        elif kind == "error":
            self._flash_color = qcolor(self.theme.color("error"))
            self._flash_until = now + 0.7
            self._burst_until = now + 0.7
            self._error_until = now + 2.2
            if message:
                self.set_hud("ERROR " + message, qcolor(self.theme.color("error")), hold=7.0)
        elif kind == "happy":
            self._happy_until = now + 2.2

    def glitch_burst(self, seconds: float = 0.35) -> None:
        self._burst_until = time.monotonic() + seconds

    def nudge(self) -> None:
        """One small shake to draw attention."""
        self._shake_until = time.monotonic() + 0.5
        self.glitch_burst(0.25)

    def touch(self) -> None:
        """Register user activity (keeps her awake)."""
        self._last_interaction = time.monotonic()

    def wake(self) -> None:
        """Wake from sleep with a quick reassemble."""
        self._last_interaction = time.monotonic()
        if self._sleeping:
            self._sleeping = False
            self.reassemble()
            self.woke.emit()

    def reassemble(self) -> None:
        """Play only the second half of the re-render effect, in place."""
        self._dissolve = 1.0
        self._rerender = {"phase": "in", "start": time.monotonic(), "target": None, "done": None}

    def rerender_to(self, top_left: QPoint, done: Callable[[], None] | None = None,
                    keep_dock: bool = False) -> None:
        """Dissolve here and reassemble with the character at *top_left*."""
        if not keep_dock:
            self.clear_dock()
        if self._intensity() <= 0 or not self.isVisible():
            self.move_char_to(top_left)
            if done:
                done()
            return
        self._rerender = {"phase": "out", "start": time.monotonic(), "target": QPoint(top_left),
                          "done": done}

    def reload_assets(self) -> None:
        self.sprites.reload(self.S)
        self.glitch_burst(0.4)

    # -------------------------------------------------------------- helpers
    def _intensity(self) -> float:
        return self.theme.intensity(self.settings.effects_level())

    def _night(self) -> bool:
        hour = _dt.datetime.now().hour
        return hour >= 23 or hour < 6

    def _resolve_state(self, now: float) -> str:
        if self._dragging:
            return "dragging"
        if now < self._error_until:
            return "error"
        if self._alert:
            return "alert"
        if self._activity:
            return self._activity
        if now < self._happy_until:
            return "happy"
        if self._sleeping:
            return "sleeping"
        if self._dock and self._peek_hidden:
            return "peek"
        if now < self._blink_until:
            return "blink"
        return "idle"

    def _glow_color(self) -> QColor:
        now = time.monotonic()
        if self._flash_color is not None and now < self._flash_until:
            return QColor(self._flash_color)
        if self._visual == "script" and not self._alert:
            return qcolor(self.theme.color("warning"))
        base = qcolor(self.theme.state_color(self._state))
        if self._state in ("idle", "blink", "peek", "happy"):
            base = blend(base, qcolor(self.theme.mood_tint(self.mood.mood)), 0.3)
        return base

    def _busy(self) -> bool:
        return bool(self._activity or self._alert or self._rerender)

    # ----------------------------------------------------------------- tick
    def _tick(self) -> None:
        now = time.monotonic()
        dt = min(0.25, now - self._last_tick)
        self._last_tick = now
        self._tick_count += 1
        mood = self.mood.mood
        speed = {"bored": 0.8, "content": 1.0, "excited": 1.35, "tired": 0.65}.get(mood, 1.0)
        if self._sleeping:
            speed = 0.4
        self._t += dt * speed

        cursor = QCursor.pos()
        rect = self.char_rect_global()
        hovered = rect.contains(cursor) and self._dissolve < 0.5
        if hovered:
            self._last_hover = now
            if not self._hovered:
                self.interaction.emit("hover")
                self.wake()
            self._last_interaction = now
        self._hovered = hovered

        # awareness: face the mouse and tilt slightly
        radius = float(self.theme.fx("awareness_radius", 280))
        center = rect.center()
        dx, dy = cursor.x() - center.x(), cursor.y() - center.y()
        tilt_target = 0.0
        if not self.asleep and math.hypot(dx, dy) < radius:
            if abs(dx) > 12:
                self._face_target = 1.0 if dx > 0 else -1.0
            tilt_target = (max(-1.0, min(1.0, dy / radius))
                           * float(self.theme.fx("tilt_degrees", 6)) * self._face_target)
        self._face += (self._face_target - self._face) * min(1.0, dt * 14)
        self._tilt += (tilt_target - self._tilt) * min(1.0, dt * 7)

        # rest
        idle_for = now - self._last_interaction
        sleep_after = float(self.settings.get("sleep_minutes", 10) or 10) * 60
        if self._night():
            sleep_after *= 0.5
        if not self._busy() and not self._pinned:
            if not self._sleeping and idle_for > sleep_after:
                self._sleeping = True

        # state bookkeeping
        state = self._resolve_state(now)
        if state != self._state:
            self._state, self._state_since = state, self._t

        # blink
        if now >= self._next_blink:
            own = self.sprites.duration("blink")
            self._blink_until = now + (own if own > 0 else 0.14)
            self._next_blink = now + random.uniform(2.5, 6.5) / max(0.5, speed)


        self._update_rerender(now)
        self._update_glitch(now, speed)
        self._update_dock(now)
        self._update_wander(now, mood)

        # HUD fade
        if self._hud_hold_until and now > self._hud_hold_until:
            self._hud_hold_until = 0.0
        hud_target = 1.0 if self._hud_current()[0] else 0.0
        self._hud_alpha += (hud_target - self._hud_alpha) * min(1.0, dt * (9 if hud_target else 3))
        self.update()

    def _update_rerender(self, now: float) -> None:
        if not self._rerender:
            if self._dissolve > 0:
                self._dissolve = 0.0
            return
        duration = max(0.08, float(self.theme.fx("rerender_ms", 380)) / 1000.0)
        progress = (now - self._rerender["start"]) / duration
        if self._rerender["phase"] == "out":
            self._dissolve = min(1.0, progress)
            if progress >= 1.0:
                target = self._rerender["target"]
                if target is not None:
                    self.move_char_to(target)
                self._rerender["phase"] = "in"
                self._rerender["start"] = now
                self._strip_rand = [(random.random(), random.random()) for _ in range(400)]
        else:
            self._dissolve = max(0.0, 1.0 - progress)
            if progress >= 1.0:
                done = self._rerender.get("done")
                self._rerender = None
                self._dissolve = 0.0
                if done:
                    done()

    def _make_slices(self, count: int, max_offset: float) -> list[tuple[int, int, float]]:
        slices, y = [], 0
        for _ in range(count):
            y += random.randint(6, max(8, self.F // (count + 1)))
            height = random.randint(4, max(6, self.F // 12))
            if y + height >= self.F:
                break
            slices.append((y, height, random.uniform(-max_offset, max_offset)))
            y += height
        return slices

    def _update_glitch(self, now: float, speed: float) -> None:
        intensity = self._intensity()
        if intensity <= 0:
            self._slices = []
            return
        max_offset = float(self.theme.fx("glitch_max_offset", 18)) * intensity
        if now < self._burst_until:
            self._slices = self._make_slices(random.randint(6, 9), max_offset * 1.8)
            return
        if now >= self._next_glitch and not self.asleep:
            low, high = self.theme.fx("glitch_interval", [4.0, 11.0])
            self._glitch_until = now + float(self.theme.fx("glitch_duration", 0.18))
            self._next_glitch = now + random.uniform(float(low), float(high)) / speed / max(
                0.4, intensity)
        if now < self._glitch_until:
            if not self._slices or random.random() < 0.5:
                low, high = self.theme.fx("glitch_slices", [2, 4])
                self._slices = self._make_slices(random.randint(int(low), int(high)), max_offset)
        else:
            self._slices = []

    # ------------------------------------------------------------- docking
    def _docks_to_windows(self) -> bool:
        return bool(self.settings.get("dock_to_windows", True))

    @property
    def dock(self) -> DockTarget | None:
        """Where she is docked (``None`` when free)."""
        return self._dock

    def dock_shown_pos(self) -> QPoint:
        """Top-left of her square when fully shown at the current dock."""
        if self._dock is None:
            return self.char_pos()
        x, y = dock_position(self._dock, self.S, hidden=False)
        return QPoint(x, y)

    def set_dock(self, target: DockTarget | None, hidden: bool = True,
                 burst: bool = True) -> None:
        """Dock at *target* (screen or window edge) and peek, or undock with ``None``."""
        self._dock = target
        if target is None:
            self.clear_dock()
            return
        self._last_hover = time.monotonic() if not hidden else 0.0
        self._apply_peek(hidden=hidden, burst=burst)
        if target.kind == "window":
            self._dock_timer.start()
            log.info("Docked to the %s edge of window '%s'", target.edge, target.title[:60])
        else:
            self._dock_timer.stop()

    def clear_dock(self) -> None:
        self._dock = None
        self._peek_hidden = False
        self._dock_timer.stop()
        self.clearMask()
        self.update()

    def _apply_peek(self, hidden: bool, burst: bool = True) -> None:
        if self._dock is None:
            return
        x, y = dock_position(self._dock, self.S, hidden)
        self._peek_hidden = hidden
        self.move_char_to(QPoint(x, y))
        self._update_window_mask()
        if burst:
            self.glitch_burst(0.18)

    def _update_window_mask(self) -> None:
        """While peeking from behind a window, the part of her overlapping that
        window is masked out: it is not drawn and clicks there reach the window."""
        if not (self._dock and self._dock.kind == "window" and self._peek_hidden):
            self.clearMask()
            return
        r = self._dock.rect
        behind = QRect(r.left - self.x(), r.top - self.y(), r.width, r.height)
        self.setMask(QRegion(self.rect()).subtracted(QRegion(behind)))

    def _check_dock(self) -> None:
        """After a drop: dock to a screen edge or to the edge of another app's window."""
        windows = app_windows.list_windows() if self._docks_to_windows() else []
        target = find_dock(_rect(self.char_rect_global()), _rect(self.current_screen_rect()),
                           windows, use_windows=self._docks_to_windows())
        if target is None:
            self.clear_dock()
        else:
            self.set_dock(target, hidden=True)

    def _follow_window(self) -> None:
        """Keep up with the window she peeks from; step out when it goes away."""
        dock = self._dock
        if dock is None or dock.kind != "window":
            self._dock_timer.stop()
            return
        if self._dragging or self._rerender:
            return
        rect = app_windows.window_rect(dock.handle)
        if rect == dock.rect:
            return
        if not self._peek_hidden:
            # She is standing fully outside (hovered, chat open, busy...): the window
            # moving or closing must not drag her along. She stays and is free again.
            log.info("Window '%s' moved while Capri was out; undocking.", dock.title[:60])
            self.clear_dock()
            return
        if rect is None:  # closed, minimized or hidden: re-render fully visible beside it
            shown = self.dock_shown_pos()
            self.clear_dock()
            screen = self.current_screen_rect()
            shown.setX(max(screen.left(), min(shown.x(), screen.right() - self.S)))
            shown.setY(max(screen.top(), min(shown.y(), screen.bottom() - self.S)))
            self.rerender_to(shown)
            return
        self._dock = dock.with_rect(rect)  # peeking: follow the window
        self._apply_peek(True, burst=False)

    def _update_dock(self, now: float) -> None:
        if not self._dock or self._dragging or self._rerender:
            return
        want_shown = (self._hovered or self._pinned or bool(self._activity) or self._alert
                      or now - self._last_hover < 1.4)
        if want_shown and self._peek_hidden:
            self._apply_peek(hidden=False)
        elif not want_shown and not self._peek_hidden:
            self._apply_peek(hidden=True)

    def _peek_from_window(self) -> bool:
        """Bored: re-render to peek from behind a nearby app window. False if none."""
        screen = _rect(self.current_screen_rect())
        edges = window_edges(app_windows.list_windows()[:8], screen, self.S)
        if not edges:
            return False
        cx, cy = _rect(self.char_rect_global()).center

        def distance(t: DockTarget) -> float:
            x, y = dock_position(t, self.S, hidden=True)
            return abs(x - cx) + abs(y - cy)

        target = random.choice(sorted(edges, key=distance)[:3])
        x, y = dock_position(target, self.S, hidden=True)
        self.rerender_to(QPoint(x, y), done=lambda: self.set_dock(target, hidden=True,
                                                                    burst=False))
        return True

    def _update_wander(self, now: float, mood: str) -> None:
        """When bored she occasionally re-renders somewhere nearby."""
        if now < self._next_wander:
            return
        self._next_wander = now + random.uniform(420, 780)
        if (mood != "bored" or self._busy() or self.asleep or self._dock or self._pinned
                or self._dragging):
            return
        if self._docks_to_windows() and random.random() < 0.5 and self._peek_from_window():
            return
        screen = self.current_screen_rect()
        pos = self.char_pos()
        x = max(screen.left() + 10, min(pos.x() + random.randint(-260, 260),
                                        screen.right() - self.S - 10))
        y = max(screen.top() + 10, min(pos.y() + random.randint(-120, 120),
                                       screen.bottom() - self.S - 40))
        self.rerender_to(QPoint(x, y))

    # ---------------------------------------------------------------- mouse
    def _on_character(self, pos: QPointF) -> bool:
        return self.char_rect.adjusted(-6, -6, 6, 6).contains(pos)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._on_character(event.position()):
            event.ignore()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_offset = self._press_pos - self.pos()
            self._dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            return
        current = event.globalPosition().toPoint()
        if not self._dragging and (current - self._press_pos).manhattanLength() > 5:
            self._dragging = True
            self.wake()
            self.clear_dock()
            self._rerender = None
            self.interaction.emit("drag")
        if self._dragging:
            self.move(current - self._press_offset)
            self.moved.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            if self._on_character(event.position()):
                self.wake()
                self.menu_requested.emit(event.globalPosition().toPoint())
            return
        if event.button() != Qt.LeftButton or self._press_pos is None:
            return
        self._press_pos = None
        self.wake()
        if self._dragging:
            self._dragging = False
            self._check_dock()
            self.save_position()
            return
        now = time.monotonic()
        self._clicks.append(now)
        recent = sum(1 for stamp in self._clicks if now - stamp < 5.0)
        self.interaction.emit("click")
        if recent >= 7:
            self._clicks.clear()
            self.glitch_burst(0.5)
            self._error_until = now + 1.0
            self.click_reaction.emit("annoyed")
        elif recent == 4:
            self.flash("happy")
            self.click_reaction.emit("playful")
        elif recent < 4:
            self.clicked.emit()

    # ---------------------------------------------------------------- paint
    def _tint(self, image: QImage, color: QColor) -> QImage:
        out = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.drawImage(0, 0, image)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(out.rect(), color)
        painter.end()
        return out

    def _compose(self, intensity: float, glow: QColor) -> QImage:
        """Render the character layer plus glow, RGB split and scanlines."""
        F, S = self.F, self.S
        body = QImage(F, F, QImage.Format_ARGB32_Premultiplied)
        body.fill(Qt.transparent)
        painter = QPainter(body)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        painter.translate(F / 2, F / 2)
        breath = 1.0 + float(self.theme.fx("breath_scale", 0.02)) * min(1.0, intensity * 2) * \
            math.sin(self._t * 2 * math.pi / float(self.theme.fx("breath_period", 4.6)))
        state = self._state
        pixmap = self.sprites.frame(state, self._t - self._state_since)
        if pixmap is not None:
            face = self._face if abs(self._face) > 0.08 else (0.08 if self._face >= 0 else -0.08)
            painter.rotate(self._tilt)
            painter.scale(face * breath, breath)
            painter.drawPixmap(QPointF(-pixmap.width() / 2, -pixmap.height() / 2), pixmap)
        else:
            painter.rotate(self._tilt * 0.5)
            painter.scale(breath, breath)
            draw_construct(painter, QRectF(-S / 2, -S / 2, S, S), state, self._t, glow,
                           qcolor(self.theme.color("secondary")), qfont(self.theme, "mono"),
                           motion=1.0)
        painter.end()
        if intensity <= 0:
            return body

        out = QImage(F, F, QImage.Format_ARGB32_Premultiplied)
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        beat = 0.45 + 0.55 * pulse(self.theme) if (self._alert or self._visual == "script") else 1.0
        glow_alpha = float(self.theme.fx("glow_alpha", 0.8)) * intensity * beat
        silhouette = self._tint(body, glow)
        small = silhouette.scaled(max(8, F // 10), max(8, F // 10), Qt.IgnoreAspectRatio,
                                  Qt.SmoothTransformation)
        radius = float(self.theme.fx("glow_radius", 12))
        painter.setOpacity(min(1.0, glow_alpha))
        painter.drawImage(QRectF(-radius, -radius, F + 2 * radius, F + 2 * radius), small)
        painter.setOpacity(min(1.0, glow_alpha * 0.7))
        painter.drawImage(QRectF(0, 0, F, F), small)

        glitching = bool(self._slices)
        split = float(self.theme.fx("rgb_split", 2.0)) * intensity * (3.0 if glitching else 1.0)
        if split >= 0.5:
            painter.setOpacity(0.55)
            painter.drawImage(QPointF(-split, 0),
                              self._tint(body, qcolor(self.theme.color("tertiary"))))
            painter.drawImage(QPointF(split, 0),
                              self._tint(body, qcolor(self.theme.color("accent"))))
        painter.setOpacity(1.0)
        painter.drawImage(0, 0, body)

        spacing = max(2, int(self.theme.fx("scanline_spacing", 3)))
        flicker = 0.75 + 0.25 * math.sin(self._t * 23.0)
        alpha = float(self.theme.fx("scanline_alpha", 0.16)) * intensity * flicker
        painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
        line = QColor(0, 0, 0, int(255 * max(0.0, min(1.0, alpha))))
        for y in range(int(self._t * 14) % spacing, F, spacing):
            painter.fillRect(0, y, F, 1, line)
        painter.end()
        return out

    def _draw_bands(self, painter: QPainter, image: QImage, x0: float, y0: float,
                    glow: QColor) -> None:
        F = self.F
        dissolve = self._dissolve
        if dissolve > 0.001:
            height = 3
            for index, y in enumerate(range(0, F, height)):
                r1, r2 = self._strip_rand[index % len(self._strip_rand)]
                if r1 < dissolve:
                    continue
                dx = (r2 - 0.5) * 2.0 * dissolve * F * 0.55
                painter.drawImage(QPointF(x0 + dx, y0 + y), image, QRectF(0, y, F, height))
            strength = math.sin(math.pi * dissolve)
            for rx, ry, rs in self._pix_rand:
                color = QColor(glow)
                color.setAlphaF(min(1.0, strength * (0.4 + 0.6 * rs)))
                side = 2 + rs * 4
                painter.fillRect(QRectF(x0 + F * (0.15 + rx * 0.7),
                                        y0 + F * (0.15 + ry * 0.7) - dissolve * 40 * rs,
                                        side, side), color)
            return
        if not self._slices:
            painter.drawImage(QPointF(x0, y0), image)
            return
        y = 0
        for start, height, dx in self._slices:
            if start > y:
                painter.drawImage(QPointF(x0, y0 + y), image, QRectF(0, y, F, start - y))
            painter.drawImage(QPointF(x0 + dx, y0 + start), image, QRectF(0, start, F, height))
            y = start + height
        if y < F:
            painter.drawImage(QPointF(x0, y0 + y), image, QRectF(0, y, F, F - y))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        now = time.monotonic()
        intensity = self._intensity()
        glow = self._glow_color()
        motion = min(1.0, intensity * 2)
        amplitude = float(self.theme.fx("bob_amplitude", 6.0)) * motion
        bob = amplitude * math.sin(self._t * 2 * math.pi / float(self.theme.fx("bob_period", 3.4)))
        shake = 0.0
        if now < self._shake_until:
            shake = math.sin(now * 60) * 5 * (self._shake_until - now) / 0.5
        cx = self.char_rect.center().x() + shake
        cy = self.char_rect.center().y() + bob

        opacity = 1.0
        if self._night() and not self._busy():
            opacity *= float(self.theme.fx("night_dim", 0.8))
        if self._sleeping:
            opacity *= 0.7

        active = self._dissolve < 0.6
        if self._visual == "script" and active and not self._alert:
            self._draw_script_pulse(painter, cx, cy, glow)
        if self._visual == "search" and active:
            self._draw_search_ring(painter, cx, cy, glow, front=False)

        image = self._compose(intensity, glow)
        painter.setOpacity(opacity)
        self._draw_bands(painter, image, cx - self.F / 2, cy - self.F / 2, glow)
        painter.setOpacity(1.0)

        if self._visual == "search" and active:
            self._draw_search_ring(painter, cx, cy, glow, front=True)
        if self._flash_color is not None and now < self._flash_until and active:
            self._draw_flash(painter, cx, cy, (self._flash_until - now) / 0.6)
        if self._chip_text and active and self._visual in ("files", "script"):
            self._draw_file_link(painter, cx, cy)
        if self._sleeping:
            self._draw_sleep_glyphs(painter, cx, cy)
        self._draw_hud(painter, glow)

    # ---- tool visuals
    def _draw_script_pulse(self, painter: QPainter, cx: float, cy: float, glow: QColor) -> None:
        painter.save()
        painter.setBrush(Qt.NoBrush)
        for index in range(3):
            phase = (self._t * 0.7 + index / 3.0) % 1.0
            color = QColor(glow)
            color.setAlphaF(0.55 * (1.0 - phase))
            painter.setPen(QPen(color, 2.0))
            radius = self.S * (0.25 + 0.45 * phase)
            painter.drawEllipse(QPointF(cx, cy), radius, radius)
        painter.restore()

    def _draw_search_ring(self, painter: QPainter, cx: float, cy: float, glow: QColor,
                          front: bool) -> None:
        painter.save()
        font = qfont(self.theme, "mono", max(6, int(self.S * 0.035)))
        painter.setFont(font)
        count = len(_FRAGMENTS)
        rx, ry = self.S * 0.68, self.S * 0.2
        secondary = qcolor(self.theme.color("secondary"))
        for index, fragment in enumerate(_FRAGMENTS):
            angle = self._t * 1.6 + index * 2 * math.pi / count
            depth = math.sin(angle)
            if (depth > 0) != front:
                continue
            x = cx + math.cos(angle) * rx
            y = cy + depth * ry + self.S * 0.05
            color = QColor(glow if index % 2 else secondary)
            color.setAlphaF(0.35 + 0.6 * (depth + 1) / 2)
            painter.setPen(color)
            painter.drawText(QPointF(x - 6, y), fragment)
            painter.fillRect(QRectF(x - 8, y + 3, 14 + 6 * (index % 3), 1.5), color)
        painter.restore()

    def _draw_flash(self, painter: QPainter, cx: float, cy: float, strength: float) -> None:
        strength = max(0.0, min(1.0, strength))
        gradient = QRadialGradient(QPointF(cx, cy), self.S * 0.7)
        inner = QColor(self._flash_color)
        inner.setAlphaF(0.55 * strength)
        outer = QColor(self._flash_color)
        outer.setAlphaF(0.0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, outer)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(QPointF(cx, cy), self.S * 0.7, self.S * 0.7)
        painter.restore()

    def _draw_file_link(self, painter: QPainter, cx: float, cy: float) -> None:
        painter.save()
        color = qcolor(self.theme.color("secondary"))
        font = qfont(self.theme, "mono", max(7, self.theme.font_size("hud_size") - 1))
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        max_text = self.S * 0.8
        text = metrics.elidedText(self._chip_text, Qt.ElideMiddle, max_text)
        chip_w = metrics.horizontalAdvance(text) + 34
        chip_h = 22.0
        side = 1 if self._face_target >= 0 else -1
        chip_cx = cx + side * (self.win_w / 2 - chip_w / 2 - 6)
        chip_cy = self.S * 0.16 + 3 * math.sin(self._t * 2.1)
        chip = QRectF(chip_cx - chip_w / 2, chip_cy - chip_h / 2, chip_w, chip_h)
        start = QPointF(cx + side * self.S * 0.22, cy - self.S * 0.2)
        end = QPointF(chip.center().x(), chip.bottom())
        pen = QPen(color, 1.3, Qt.CustomDashLine)
        pen.setDashPattern([4, 4])
        pen.setDashOffset(-self._t * 24)
        painter.setPen(pen)
        painter.drawLine(start, end)
        phase = (self._t * 1.4) % 1.0
        dot = QPointF(start.x() + (end.x() - start.x()) * phase,
                      start.y() + (end.y() - start.y()) * phase)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(dot, 2.4, 2.4)
        painter.setBrush(qcolor(self.theme.color("background"), 0.9))
        painter.setPen(QPen(color, 1.0))
        painter.drawRoundedRect(chip, 4, 4)
        icon_rect = QRectF(chip.left() + 6, chip.center().y() - 8, 16, 16)
        if self._chip_icon is not None:
            painter.drawPixmap(icon_rect.topLeft(), self._chip_icon)
        else:
            painter.setBrush(Qt.NoBrush)
            page = icon_rect.adjusted(3, 1, -3, -1)
            painter.drawRect(page)
            painter.drawLine(QPointF(page.left() + 2, page.top() + 5),
                             QPointF(page.right() - 2, page.top() + 5))
            painter.drawLine(QPointF(page.left() + 2, page.top() + 9),
                             QPointF(page.right() - 2, page.top() + 9))
        painter.setPen(qcolor(self.theme.color("text")))
        painter.drawText(QRectF(chip.left() + 26, chip.top(), chip_w - 30, chip_h),
                         Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()

    def _draw_sleep_glyphs(self, painter: QPainter, cx: float, cy: float) -> None:
        painter.save()
        painter.setFont(qfont(self.theme, "mono", max(7, int(self.S * 0.05))))
        for index in range(2):
            phase = (self._t * 0.35 + index * 0.5) % 1.0
            color = qcolor(self.theme.state_color("sleeping"), math.sin(math.pi * phase) * 0.9)
            painter.setPen(color)
            painter.drawText(QPointF(cx + self.S * 0.28 + phase * 14 + index * 8,
                                     cy - self.S * 0.2 - phase * self.S * 0.25), "z")
        painter.restore()

    def _hud_display(self) -> str:
        text = self._hud_current()[0]
        if self._ticker and self._visual == "search" and not self._hud_hold_until:
            text += " · " + self._ticker[int(time.monotonic() * 4) % len(self._ticker)]
        return text

    def _draw_hud(self, painter: QPainter, glow: QColor) -> None:
        if self._hud_alpha <= 0.02:
            return
        text = self._hud_display() or " "
        font = qfont(self.theme, "hud")
        metrics = QFontMetricsF(font)
        text = metrics.elidedText("▸ " + text, Qt.ElideRight, self.win_w - 30)
        width = metrics.horizontalAdvance(text) + 18
        height = metrics.height() + 8
        x = (self.win_w - width) / 2
        y = self.char_rect.bottom() + self.S * 0.1
        color = QColor(self._hud_current()[1] or glow)
        flicker = 0.86 + 0.14 * math.sin(time.monotonic() * 31.0)
        painter.save()
        painter.setOpacity(self._hud_alpha * flicker)
        painter.setPen(QPen(qcolor(color.name(), 0.5), 1.0))
        painter.drawLine(QPointF(self.win_w / 2, y - 6), QPointF(self.win_w / 2, y))
        painter.setBrush(qcolor(self.theme.color("background"), 0.72))
        painter.drawRoundedRect(QRectF(x, y, width, height), 3, 3)
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QRectF(x, y, width, height), Qt.AlignCenter, text)
        painter.restore()
