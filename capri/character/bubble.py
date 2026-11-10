"""A small self-dismissing speech bubble that floats next to Capri."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from ..config.theme import Theme
from ..ui.style import paint_glass, qcolor, qfont


class SpeechBubble(QWidget):
    """Frameless translucent bubble; emits :attr:`clicked` with its payload."""

    clicked = Signal(str)

    MAX_WIDTH = 280

    def __init__(self, theme: Theme) -> None:
        super().__init__(None)
        self._theme = theme
        self._text = ""
        self._payload = ""
        self._color = theme.color("border")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setCursor(Qt.PointingHandCursor)
        self._font = qfont(theme, "ui")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, anchor: QRect, screen: QRect, timeout_ms: int = 7000,
                  payload: str = "", color: str | None = None) -> None:
        """Show *text* above the *anchor* rectangle (global coordinates)."""
        self._text = text.strip()
        self._payload = payload
        self._color = color or self._theme.color("border")
        metrics = QFontMetrics(self._font)
        box = metrics.boundingRect(QRect(0, 0, self.MAX_WIDTH - 28, 400),
                                   Qt.TextWordWrap, self._text)
        self.resize(max(90, box.width() + 30), box.height() + 24)
        x = anchor.center().x() - self.width() // 2
        y = anchor.top() - self.height() - 2
        if y < screen.top() + 4:
            y = anchor.bottom() + 6
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        self.move(QPoint(x, y))
        self.show()
        self.raise_()
        self.update()
        self._timer.start(timeout_ms)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_glass(painter, QRectF(self.rect()), self._theme, alpha=0.94,
                    border=qcolor(self._color), radius=8, scanlines=False)
        painter.setFont(self._font)
        painter.setPen(qcolor(self._theme.color("text")))
        painter.drawText(self.rect().adjusted(14, 10, -14, -10), Qt.TextWordWrap, self._text)

    def mousePressEvent(self, _event) -> None:  # noqa: N802
        self.hide()
        self._timer.stop()
        self.clicked.emit(self._payload)
