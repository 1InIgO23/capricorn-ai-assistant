"""Qt styling helpers derived from the theme."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

from ..config.theme import Theme


def qcolor(value: str, alpha: float | None = None) -> QColor:
    """QColor from a hex string, optionally with an alpha in 0..1."""
    color = QColor(value)
    if not color.isValid():
        color = QColor("#FF00FF")
    if alpha is not None:
        color.setAlphaF(max(0.0, min(1.0, alpha)))
    return color


def blend(a: QColor, b: QColor, t: float) -> QColor:
    """Linear blend from *a* to *b*."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
        int(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


def qfont(theme: Theme, kind: str = "ui", size: int | None = None, bold: bool = False) -> QFont:
    """QFont for ``ui`` / ``display`` / ``mono``."""
    font = QFont()
    font.setFamilies(theme.font_families(kind))
    size_key = {"mono": "mono_size", "hud": "hud_size"}.get(kind, "ui_size")
    if kind == "hud":
        font.setFamilies(theme.font_families("mono"))
    font.setPointSize(size or theme.font_size(size_key))
    font.setBold(bold)
    if kind in ("mono", "hud"):
        font.setStyleHint(QFont.Monospace)
    return font


def css_fonts(theme: Theme, kind: str) -> str:
    """CSS ``font-family`` value for a font kind."""
    return ", ".join(f'"{name}"' for name in theme.font_families(kind))


def pulse(theme: Theme) -> float:
    """Shared 0..1 pulse so Capri's glow and dialog borders beat together."""
    period = float(theme.fx("pulse_period", 1.1) or 1.1)
    return 0.5 + 0.5 * math.sin(time.monotonic() * 2 * math.pi / period)


def paint_glass(painter: QPainter, rect: QRectF, theme: Theme, alpha: float | None = None,
                border: QColor | None = None, radius: float | None = None,
                scanlines: bool = True) -> None:
    """Paint the translucent dark-glass panel background with a neon border."""
    radius = float(theme.fx("panel_radius", 10)) if radius is None else radius
    alpha = theme.glass_alpha() if alpha is None else alpha
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    box = rect.adjusted(1, 1, -1, -1)
    path.addRoundedRect(box, radius, radius)
    painter.fillPath(path, qcolor(theme.color("surface"), alpha))
    if scanlines:
        painter.setClipPath(path)
        line = qcolor(theme.color("text"), float(theme.fx("panel_scanline_alpha", 0.05)))
        y = box.top()
        while y < box.bottom():
            painter.fillRect(QRectF(box.left(), y, box.width(), 1), line)
            y += 3
        painter.setClipping(False)
    edge = border or qcolor(theme.color("border"))
    glow = QColor(edge)
    glow.setAlphaF(0.25)
    painter.setPen(QPen(glow, 3))
    painter.drawPath(path)
    painter.setPen(QPen(edge, 1.2))
    painter.drawPath(path)
    painter.restore()


def build_stylesheet(theme: Theme) -> str:
    """Application-wide Qt style sheet (menus, dialogs, inputs, scroll bars)."""
    c = theme.color
    ui, mono, display = css_fonts(theme, "ui"), css_fonts(theme, "mono"), css_fonts(theme, "display")
    size = theme.font_size("ui_size")
    return f"""
    QWidget {{ color: {c('text')}; font-family: {ui}; font-size: {size}pt; }}
    QDialog, QMessageBox, QInputDialog {{ background: {c('surface')}; }}
    QToolTip {{ background: {c('background')}; color: {c('text')}; border: 1px solid {c('border')};
               padding: 4px; }}
    QMenu {{ background: {c('background')}; border: 1px solid {c('border')}; padding: 4px; }}
    QMenu::item {{ padding: 6px 22px 6px 14px; font-family: {display}; }}
    QMenu::item:selected {{ background: {c('accent')}; color: {c('background')}; }}
    QMenu::item:disabled {{ color: {c('text_dim')}; }}
    QMenu::separator {{ height: 1px; background: {c('surface_alt')}; margin: 4px 8px; }}
    QMenu::indicator {{ width: 10px; height: 10px; margin-left: 6px; }}
    QMenu::indicator:checked {{ background: {c('secondary')}; }}
    QPushButton {{ background: {c('surface_alt')}; border: 1px solid {c('border')};
                  border-radius: 4px; padding: 6px 14px; font-family: {display}; }}
    QPushButton:hover {{ border-color: {c('accent')}; color: {c('accent_soft')}; }}
    QPushButton:pressed {{ background: {c('accent')}; color: {c('background')}; }}
    QPushButton:disabled {{ color: {c('text_dim')}; border-color: {c('surface_alt')}; }}
    QPushButton#primary {{ background: {c('accent')}; color: {c('background')};
                          border-color: {c('accent')}; font-weight: bold; }}
    QPushButton#primary:hover {{ background: {c('accent_soft')}; color: {c('background')}; }}
    QPushButton#flat {{ background: transparent; border: none; padding: 3px 7px;
                       color: {c('text_dim')}; }}
    QPushButton#flat:hover {{ color: {c('secondary')}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
        background: {c('background')}; border: 1px solid {c('surface_alt')}; border-radius: 4px;
        padding: 5px; selection-background-color: {c('accent')};
        selection-color: {c('background')}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border-color: {c('border')}; }}
    QComboBox QAbstractItemView {{ background: {c('background')}; border: 1px solid {c('border')};
        selection-background-color: {c('accent')}; selection-color: {c('background')}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {c('border')};
                           border-radius: 3px; background: {c('background')}; }}
    QCheckBox::indicator:checked {{ background: {c('secondary')}; }}
    QTabWidget::pane {{ border: 1px solid {c('surface_alt')}; top: -1px; }}
    QTabBar::tab {{ background: transparent; padding: 7px 16px; color: {c('text_dim')};
                   font-family: {display}; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {c('text')}; border-bottom: 2px solid {c('accent')}; }}
    QListWidget {{ background: transparent; border: none; outline: none; }}
    QListWidget::item {{ padding: 6px 6px; border-radius: 3px; color: {c('text_dim')}; }}
    QListWidget::item:selected {{ background: {c('surface_alt')}; color: {c('text')};
                                 border-left: 2px solid {c('accent')}; }}
    QListWidget::item:hover {{ color: {c('text')}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c('surface_alt')}; border-radius: 3px;
                                  min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {c('border')}; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c('surface_alt')}; border-radius: 3px;
                                    min-width: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QLabel#dim {{ color: {c('text_dim')}; }}
    QLabel#hint {{ color: {c('warning')}; font-size: {max(7, size - 2)}pt; }}
    QLabel#title {{ font-family: {display}; font-size: {size + 2}pt; font-weight: bold;
                   color: {c('text')}; }}
    QLabel#mono {{ font-family: {mono}; }}
    """
