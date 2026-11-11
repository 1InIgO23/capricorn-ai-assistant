"""The "neural tether": a beam from Capri's hand to a window she points at.

Drawn in a transparent, click-through overlay that covers just the area
between her and the target window. Sequence (theme ``pointer.duration``):

1. the beam shoots out from her hand to the window edge (0.4 s);
2. targeting brackets collapse onto the window frame and lock (0.4 s);
3. hold: jittering beam, data packets travelling along it, a scan band
   sweeping the window, a "TARGET LOCKED" tag;
4. fade out (0.6 s).
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import QWidget

from ..character import app_windows
from ..config.theme import Theme
from .style import qcolor, qfont

EXTEND, LOCK, FADE = 0.4, 0.4, 0.6
FRAME_MS = 33
TRACK_MS = 250


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _alpha(color: QColor, alpha: float) -> QColor:
    out = QColor(color)
    out.setAlphaF(max(0.0, min(1.0, alpha)))
    return out


def border_point(rect: QRectF, origin: QPointF) -> QPointF:
    """Where the line from *origin* to the rect's center crosses the rect's border."""
    center = rect.center()
    dx, dy = origin.x() - center.x(), origin.y() - center.y()
    if dx == 0 and dy == 0:
        return center
    scale_x = (rect.width() / 2) / abs(dx) if dx else math.inf
    scale_y = (rect.height() / 2) / abs(dy) if dy else math.inf
    scale = min(scale_x, scale_y, 1.0)
    return QPointF(center.x() + dx * scale, center.y() + dy * scale)


class PointerOverlay(QWidget):
    """Transparent, click-through window that draws the tether and target lock."""

    def __init__(self, theme: Theme, character) -> None:
        super().__init__(None)
        self._theme = theme
        self._character = character
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.NoDropShadowWindowHint | Qt.WindowTransparentForInput
                            | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._handle = 0
        self._title = ""
        self._target = QRect()
        self._start = 0.0
        self._last_track = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------- control
    @property
    def duration(self) -> float:
        try:
            return max(2.0, float(self._theme.pointer("duration", 6.0)))
        except (TypeError, ValueError):
            return 6.0

    @property
    def active(self) -> bool:
        return self._timer.isActive()

    def start(self, handle: int, title: str, target: QRect) -> None:
        """Begin pointing at the window *handle* whose frame is *target* (global)."""
        self._handle, self._title, self._target = handle, title, QRect(target)
        self._start = time.monotonic()
        self._last_track = self._start
        self._fit()
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    # ---------------------------------------------------------------- tick
    def _fit(self) -> None:
        """Cover only Capri plus the target (large layered windows are expensive)."""
        area = self._character.char_rect_global().adjusted(-30, -30, 30, 30)
        area = area.united(self._target.adjusted(-60, -70, 60, 50))
        desktop = QGuiApplication.primaryScreen().virtualGeometry()
        area = area.intersected(desktop)
        if area != self.geometry():
            self.setGeometry(area)

    def _tick(self) -> None:
        now = time.monotonic()
        if now - self._start > self.duration or not self._character.isVisible():
            self.stop()
            return
        if now - self._last_track > TRACK_MS / 1000:
            self._last_track = now
            rect = app_windows.window_rect(self._handle)
            if rect is None:  # the window disappeared: end early
                self._start = min(self._start, now - self.duration + FADE)
            else:
                self._target = QRect(rect.left, rect.top, rect.width, rect.height)
                self._character.update_point_target(self._target.center())
            self._fit()
        self.update()

    # --------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:  # noqa: N802
        elapsed = time.monotonic() - self._start
        total = self.duration
        fade = 1.0 if elapsed < total - FADE else max(0.0, (total - elapsed) / FADE)
        extend = _ease(elapsed / EXTEND)
        lock = _ease((elapsed - EXTEND) / LOCK)
        t = self._theme
        accent = qcolor(t.color("accent"))
        soft = qcolor(t.color("accent_soft"))
        green = qcolor(t.color("secondary"))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setOpacity(fade)
        offset = QPointF(self.x(), self.y())
        hand = self._character.hand_point() - offset
        target = QRectF(self._target.translated(-self.x(), -self.y()))
        contact = border_point(target, hand)
        tip = hand + (contact - hand) * extend

        self._paint_target(painter, target, lock, elapsed, accent, green)
        self._paint_beam(painter, hand, tip, elapsed, accent, soft, green)
        self._paint_origin(painter, hand, elapsed, accent)
        if extend >= 1.0:
            self._paint_impact(painter, contact, elapsed, soft, green)
        if lock > 0:
            self._paint_tag(painter, target, lock, accent)

    def _paint_beam(self, painter: QPainter, a: QPointF, b: QPointF, elapsed: float,
                    accent: QColor, soft: QColor, green: QColor) -> None:
        length = math.hypot(b.x() - a.x(), b.y() - a.y())
        if length < 2:
            return
        ux, uy = (b.x() - a.x()) / length, (b.y() - a.y()) / length
        nx, ny = -uy, ux
        glitch = (elapsed % 1.7) < 0.12  # a short surge every couple of seconds
        jitter = float(self._theme.pointer("beam_jitter", 9.0)) * (2.2 if glitch else 1.0)
        rng = random.Random(int(elapsed / 0.07))
        segments = max(6, int(length / 36))
        path = QPainterPath(a)
        for i in range(1, segments):
            u = i / segments
            amp = (rng.random() - 0.5) * 2 * jitter * math.sin(math.pi * u)
            path.lineTo(a.x() + ux * length * u + nx * amp, a.y() + uy * length * u + ny * amp)
        path.lineTo(b)

        glow_w = float(self._theme.pointer("glow_width", 9.0))
        core_w = float(self._theme.pointer("beam_width", 2.5))
        painter.setBrush(Qt.NoBrush)
        for width, color in ((glow_w, _alpha(accent, 0.22)), (glow_w * 0.5, _alpha(accent, 0.4))):
            painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPath(path)
        split = 3.0 if glitch else 1.6  # RGB-split ghost in the secondary color
        painter.setPen(QPen(_alpha(green, 0.55), 1.2))
        painter.drawPath(path.translated(nx * split, ny * split))
        painter.setPen(QPen(soft, core_w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.drawPath(path)
        painter.setPen(QPen(_alpha(QColor("#FFFFFF"), 0.85), 1.0))
        painter.drawPath(path)

        # data packets travelling from Capri to the window, with a short trail
        count = int(self._theme.pointer("packets", 4) or 0)
        painter.setPen(Qt.NoPen)
        for j in range(count):
            phase = (elapsed * 0.85 + j / max(1, count)) % 1.0
            for trail in range(4):
                p = phase - trail * 0.018
                if p < 0:
                    continue
                x, y = a.x() + ux * length * p, a.y() + uy * length * p
                size = 4.5 - trail
                painter.setBrush(_alpha(green, 0.95 - trail * 0.25))
                painter.drawPolygon(QPolygonF([QPointF(x, y - size), QPointF(x + size, y),
                                               QPointF(x, y + size), QPointF(x - size, y)]))

    def _paint_origin(self, painter: QPainter, hand: QPointF, elapsed: float,
                      accent: QColor) -> None:
        beat = 0.5 + 0.5 * math.sin(elapsed * 9)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_alpha(accent, 0.8), 1.5))
        radius = 6 + 3 * beat
        painter.drawEllipse(hand, radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(QColor("#FFFFFF"), 0.9))
        painter.drawEllipse(hand, 2.2, 2.2)

    def _paint_impact(self, painter: QPainter, point: QPointF, elapsed: float, soft: QColor,
                      green: QColor) -> None:
        painter.save()
        painter.translate(point)
        painter.rotate(elapsed * 140)
        painter.setPen(QPen(soft, 1.6))
        painter.setBrush(Qt.NoBrush)
        hexagon = QPolygonF([QPointF(9 * math.cos(k * math.pi / 3), 9 * math.sin(k * math.pi / 3))
                             for k in range(6)])
        painter.drawPolygon(hexagon)
        painter.restore()
        rng = random.Random(int(elapsed / 0.07) + 7)
        painter.setPen(QPen(_alpha(green, 0.85), 1.2))
        for _ in range(6):
            angle = rng.random() * 2 * math.pi
            inner, outer = 11 + rng.random() * 3, 16 + rng.random() * 8
            painter.drawLine(QPointF(point.x() + math.cos(angle) * inner,
                                     point.y() + math.sin(angle) * inner),
                             QPointF(point.x() + math.cos(angle) * outer,
                                     point.y() + math.sin(angle) * outer))

    def _paint_target(self, painter: QPainter, rect: QRectF, lock: float, elapsed: float,
                      accent: QColor, green: QColor) -> None:
        if lock <= 0:
            return
        scale = 1.0 + 0.35 * (1.0 - lock)
        c = rect.center()
        box = QRectF(c.x() - rect.width() * scale / 2, c.y() - rect.height() * scale / 2,
                     rect.width() * scale, rect.height() * scale)
        locked = lock >= 1.0
        pulse = 0.55 + 0.45 * math.sin(elapsed * 6) if locked else 1.0

        if locked:  # faint tint and a scan band sweeping down the window
            painter.fillRect(rect, _alpha(accent, 0.04))
            band_y = rect.top() + ((elapsed * 0.55) % 1.0) * rect.height()
            gradient = QLinearGradient(0, band_y - 16, 0, band_y + 2)
            gradient.setColorAt(0.0, _alpha(green, 0.0))
            gradient.setColorAt(1.0, _alpha(green, 0.16))
            painter.fillRect(QRectF(rect.left(), band_y - 16, rect.width(), 18), gradient)
            painter.fillRect(QRectF(rect.left(), band_y, rect.width(), 1), _alpha(green, 0.45))

        arm = min(34.0, box.width() / 5, box.height() / 5)
        corners = ((box.topLeft(), 1, 1), (box.topRight(), -1, 1),
                   (box.bottomLeft(), 1, -1), (box.bottomRight(), -1, -1))
        painter.setBrush(Qt.NoBrush)
        for width, color in ((7.0, _alpha(accent, 0.25 * lock)),
                             (2.2, _alpha(accent, lock * pulse))):
            painter.setPen(QPen(color, width, Qt.SolidLine, Qt.SquareCap, Qt.MiterJoin))
            for corner, sx, sy in corners:
                path = QPainterPath(QPointF(corner.x() + sx * arm, corner.y()))
                path.lineTo(corner)
                path.lineTo(corner.x(), corner.y() + sy * arm)
                painter.drawPath(path)
        if locked:  # tick marks halfway along each side
            painter.setPen(QPen(_alpha(accent, 0.7 * pulse), 1.4))
            for x, y, dx, dy in ((box.center().x(), box.top(), 0, 8),
                                 (box.center().x(), box.bottom(), 0, -8),
                                 (box.left(), box.center().y(), 8, 0),
                                 (box.right(), box.center().y(), -8, 0)):
                painter.drawLine(QPointF(x, y), QPointF(x + dx, y + dy))

    def _paint_tag(self, painter: QPainter, rect: QRectF, lock: float, accent: QColor) -> None:
        font = qfont(self._theme, "hud")
        metrics = QFontMetricsF(font)
        label = "TARGET LOCKED" if lock >= 1.0 else "ACQUIRING"
        text = metrics.elidedText(f"▸ {label} · {self._title}", Qt.ElideRight,
                                  max(120.0, min(420.0, rect.width())))
        width = metrics.horizontalAdvance(text) + 16
        height = metrics.height() + 8
        y = rect.top() - height - 6
        if y < 2:
            y = rect.top() + 6
        box = QRectF(rect.left(), y, width, height)
        painter.setOpacity(painter.opacity() * lock)
        painter.setPen(QPen(accent, 1.0))
        painter.setBrush(qcolor(self._theme.color("background"), 0.85))
        painter.drawRoundedRect(box, 3, 3)
        painter.setFont(font)
        painter.setPen(qcolor(self._theme.color("text")))
        painter.drawText(box, Qt.AlignCenter, text)
