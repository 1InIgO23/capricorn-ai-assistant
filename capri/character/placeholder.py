"""Runtime placeholder: an abstract "construct" drawn with QPainter.

Used whenever a state has no image. It is deliberately abstract (a glowing
core, orbiting rings and drifting glyphs) and reacts to the current state.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF, QRadialGradient

# state: (ring spin, core pulse Hz, core scale, brightness, particles, glyph set)
_PARAMS: dict[str, tuple[float, float, float, float, int, str]] = {
    "idle": (0.6, 0.35, 1.0, 1.0, 6, "01+×▪◆"),
    "blink": (0.6, 0.35, 1.0, 1.0, 6, "01+×▪◆"),
    "peek": (0.5, 0.35, 0.95, 0.95, 4, "01+×▪◆"),
    "happy": (1.3, 1.1, 1.15, 1.25, 10, "+✦◆×"),
    "talking": (0.9, 2.6, 1.05, 1.1, 6, "01▪◆"),
    "thinking": (3.2, 0.9, 0.95, 1.0, 8, "01?∴▪"),
    "searching": (4.2, 1.4, 0.95, 1.05, 9, "?@#/."),
    "files": (1.8, 0.8, 1.0, 1.0, 7, "▤▥▦▧"),
    "coding": (2.4, 1.8, 1.0, 1.1, 9, "{};<>/="),
    "alert": (1.0, 2.2, 1.12, 1.3, 4, "!▲"),
    "error": (0.25, 0.0, 0.9, 1.0, 5, "!×#"),
    "sleeping": (0.12, 0.15, 0.85, 0.5, 2, "zZ"),
    "dragging": (2.6, 3.2, 0.92, 1.15, 8, "~≈*"),
    "pointing": (1.6, 1.6, 1.08, 1.25, 6, "»>▸"),
}


def _hash01(value: float) -> float:
    """Cheap deterministic pseudo-random number in 0..1."""
    return (math.sin(value * 12.9898) * 43758.5453) % 1.0


def _with_alpha(color: QColor, alpha: float) -> QColor:
    out = QColor(color)
    out.setAlphaF(max(0.0, min(1.0, alpha)))
    return out


def draw_construct(painter: QPainter, rect: QRectF, state: str, t: float, primary: QColor,
                   secondary: QColor, mono_font: QFont, motion: float = 1.0) -> None:
    """Draw the placeholder construct for *state* at time *t* inside *rect*.

    *motion* (0..1) scales the animation speed (effects intensity).
    """
    spin, pulse_hz, scale, bright, particles, glyphs = _PARAMS.get(state, _PARAMS["idle"])
    tm = t * (0.25 + 0.75 * motion)
    center = rect.center()
    radius = rect.width() / 2.0
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)

    flicker = 1.0
    if state == "error":
        flicker = 0.25 + 0.75 * (1.0 if _hash01(math.floor(t * 18)) > 0.45 else 0.0)
    pulse = 1.0 + 0.08 * math.sin(tm * 2 * math.pi * pulse_hz) if pulse_hz else 1.0

    # soft halo
    halo = QRadialGradient(center, radius * 0.95)
    halo.setColorAt(0.0, _with_alpha(primary, 0.34 * bright * flicker))
    halo.setColorAt(0.55, _with_alpha(primary, 0.10 * bright * flicker))
    halo.setColorAt(1.0, _with_alpha(primary, 0.0))
    painter.setPen(Qt.NoPen)
    painter.setBrush(halo)
    painter.drawEllipse(center, radius * 0.95, radius * 0.95)

    # orbiting rings
    if spin > 0:
        rings = (
            (radius * 0.80, radius * 0.27, tm * spin * 40.0, primary, 2.0),
            (radius * 0.60, radius * 0.46, -tm * spin * 27.0 + 60.0, secondary, 1.4),
        )
        for index, (rx, ry, angle, color, width) in enumerate(rings):
            painter.save()
            painter.translate(center)
            painter.rotate(angle)
            pen = QPen(_with_alpha(color, 0.85 * bright * flicker), width)
            pen.setCapStyle(Qt.FlatCap)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            box = QRectF(-rx, -ry, rx * 2, ry * 2)
            for start, span in ((10, 110), (140, 70), (230, 95)):
                painter.drawArc(box, int(start * 16), int(span * 16))
            node_angle = tm * spin * (2.2 + index) + index * 2.0
            node = QPointF(math.cos(node_angle) * rx, math.sin(node_angle) * ry)
            painter.setPen(Qt.NoPen)
            painter.setBrush(_with_alpha(color, bright * flicker))
            painter.drawEllipse(node, 3.2, 3.2)
            painter.restore()

    # angular core
    core_r = radius * 0.24 * scale * pulse
    squash = 0.12 if state == "blink" else 1.0
    points = []
    for index in range(8):
        angle = index * math.pi / 4 + math.pi / 8 + tm * 0.2 * spin
        reach = core_r * (1.0 if index % 2 == 0 else 0.62)
        points.append(QPointF(center.x() + math.cos(angle) * reach,
                              center.y() + math.sin(angle) * reach * squash))
    core = QRadialGradient(center, core_r)
    core.setColorAt(0.0, _with_alpha(QColor("#FFFFFF"), 0.95 * flicker * min(1.0, bright)))
    core.setColorAt(0.45, _with_alpha(primary, 0.95 * flicker * min(1.0, bright)))
    core.setColorAt(1.0, _with_alpha(primary, 0.55 * flicker * min(1.0, bright)))
    painter.setBrush(core)
    painter.setPen(QPen(_with_alpha(secondary, 0.8 * flicker * min(1.0, bright)), 1.2))
    painter.drawPolygon(QPolygonF(points))

    # sparkle specks inside the core
    painter.setPen(Qt.NoPen)
    for index in range(7):
        twinkle = 0.5 + 0.5 * math.sin(tm * 3.0 + index * 1.7)
        px = center.x() + (_hash01(index + 1.0) - 0.5) * core_r * 1.2
        py = center.y() + (_hash01(index + 7.0) - 0.5) * core_r * 1.2 * squash
        painter.setBrush(_with_alpha(QColor("#FFFFFF"), 0.7 * twinkle * flicker * min(1.0, bright)))
        painter.drawEllipse(QPointF(px, py), 1.1, 1.1)

    # drifting glyph particles
    if particles and glyphs:
        font = QFont(mono_font)
        font.setPixelSize(max(8, int(radius * 0.11)))
        painter.setFont(font)
        speed = 0.10 + 0.05 * spin
        for index in range(particles):
            phase = (tm * speed + index / particles + _hash01(index) * 0.3) % 1.0
            x = center.x() + math.sin(index * 2.4 + tm * 0.35) * radius * 0.78
            y = center.y() + radius * 0.85 - phase * radius * 1.7
            alpha = math.sin(math.pi * phase) * 0.8 * bright * flicker
            color = primary if index % 3 else secondary
            painter.setPen(_with_alpha(color, alpha))
            painter.drawText(QPointF(x, y), glyphs[index % len(glyphs)])
    painter.restore()
