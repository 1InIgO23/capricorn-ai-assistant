"""The chat: content widget, the holographic panel that projects out of Capri,
and the detached window used for long sessions."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QRect, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QRegion
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..config.theme import Theme
from .messages import MessageWidget, ToolEntry
from .style import paint_glass, qcolor


def _on_top(dialog: QWidget) -> QWidget:
    """Make a dialog stay above Capri and the chat panel (both are always-on-top windows)."""
    dialog.setWindowModality(Qt.ApplicationModal)  # must come first: it resets the flags
    dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    return dialog


def ask_text(parent: QWidget, title: str, label: str, text: str = "") -> tuple[str, bool]:
    """Text prompt that is never hidden behind Capri's windows."""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    _on_top(dialog)
    dialog.raise_()
    ok = dialog.exec() == QInputDialog.Accepted
    return dialog.textValue(), ok


def ask_yes_no(parent: QWidget, title: str, text: str) -> bool:
    """Yes/No question that is never hidden behind Capri's windows."""
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.No, parent)
    box.setDefaultButton(QMessageBox.No)
    _on_top(box)
    box.raise_()
    return box.exec() == QMessageBox.Yes


class ElidingLabel(QLabel):
    """A label that cuts its text with "…" instead of widening the window."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def _elide(self) -> None:
        self.setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight,
                                                   max(10, self.width())))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:  # noqa: N802 (font changes from the style sheet)
        super().changeEvent(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange):
            self._elide()


class ChatInput(QPlainTextEdit):
    """Multi-line input: Enter sends, Shift+Enter inserts a new line."""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (
                event.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


class ChatContent(QWidget):
    """All chat controls. It is hosted either by the holo panel or the detached window."""

    send_requested = Signal(str)
    stop_requested = Signal()
    detach_requested = Signal()
    close_requested = Signal()
    conversation_selected = Signal(str)
    new_conversation_requested = Signal()
    rename_requested = Signal(str, str)
    delete_requested = Signal(str)
    model_requested = Signal()
    settings_requested = Signal()
    drawer_toggled = Signal(bool)

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._busy = False
        self._stick = True
        self._current: MessageWidget | None = None
        self._pending = ""
        self._tool_entries: dict[str, ToolEntry] = {}
        self._build()
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(45)
        self._flush_timer.timeout.connect(self._flush)

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        theme = self._theme
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # --- side drawer with the conversations
        self._drawer = QFrame()
        self._drawer.setFixedWidth(168)
        self._drawer.setVisible(False)
        self._drawer.setObjectName("drawer")
        self._drawer.setStyleSheet(
            f"QFrame#drawer {{ border-right: 1px solid {theme.color('surface_alt')}; }}")
        drawer_layout = QVBoxLayout(self._drawer)
        drawer_layout.setContentsMargins(0, 0, 6, 0)
        heading = QLabel("CONVERSATIONS")
        heading.setObjectName("dim")
        new_button = QPushButton("+ New")
        new_button.clicked.connect(self.new_conversation_requested)
        self._list = QListWidget()
        self._list.setToolTip("Double-click to rename")
        self._list.itemClicked.connect(self._on_item_clicked)
        self._list.itemDoubleClicked.connect(lambda _item: self._rename_current())
        row = QHBoxLayout()
        rename = QPushButton("Rename")
        rename.setObjectName("flat")
        rename.clicked.connect(self._rename_current)
        delete = QPushButton("Delete")
        delete.setObjectName("flat")
        delete.clicked.connect(self._delete_current)
        row.addWidget(rename)
        row.addWidget(delete)
        drawer_layout.addWidget(heading)
        drawer_layout.addWidget(new_button)
        drawer_layout.addWidget(self._list, 1)
        drawer_layout.addLayout(row)
        root.addWidget(self._drawer)

        # --- main column
        column = QVBoxLayout()
        column.setSpacing(6)
        top = QHBoxLayout()
        self._drawer_button = self._flat("☰", "Conversations")
        self._drawer_button.clicked.connect(self._toggle_drawer)
        self._title = ElidingLabel("CAPRI")
        self._title.setObjectName("title")
        self._title.setToolTip("Drag to move the chat. Double-click to put it back next to Capri.")
        self._badge = QPushButton("")
        self._badge.setObjectName("flat")
        self._badge.setCursor(Qt.PointingHandCursor)
        self._badge.setToolTip("Change the AI model or set an API key")
        self._badge.clicked.connect(self.model_requested)
        settings_button = self._flat("⚙", "Settings")
        settings_button.clicked.connect(self.settings_requested)
        self._detach_button = self._flat("⧉", "Detach into a normal window")
        self._detach_button.clicked.connect(self.detach_requested)
        close_button = self._flat("✕", "Close the chat")
        close_button.clicked.connect(self.close_requested)
        top.addWidget(self._drawer_button)
        top.addWidget(self._title, 1)
        top.addWidget(self._badge)
        top.addWidget(settings_button)
        top.addWidget(self._detach_button)
        top.addWidget(close_button)
        column.addLayout(top)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.viewport().setAutoFillBackground(False)
        holder = QWidget()
        holder.setAutoFillBackground(False)
        holder.setStyleSheet("background: transparent;")
        self._messages = QVBoxLayout(holder)
        self._messages.setContentsMargins(2, 2, 8, 2)
        self._messages.setSpacing(8)
        self._messages.addStretch(1)
        self._scroll.setWidget(holder)
        bar = self._scroll.verticalScrollBar()
        bar.rangeChanged.connect(self._on_range_changed)
        bar.valueChanged.connect(self._on_scrolled)
        column.addWidget(self._scroll, 1)

        self._hint = QLabel("")
        self._hint.setObjectName("hint")
        self._hint.setWordWrap(True)
        column.addWidget(self._hint)

        bottom = QHBoxLayout()
        self._input = ChatInput()
        self._input.setPlaceholderText("Talk to Capri…  (Enter to send, Shift+Enter for a new line)")
        self._input.setFixedHeight(62)
        self._input.submitted.connect(self._send)
        self._send_button = QPushButton("SEND")
        self._send_button.setObjectName("primary")
        self._send_button.setFixedWidth(74)
        self._send_button.clicked.connect(self._send_or_stop)
        bottom.addWidget(self._input, 1)
        bottom.addWidget(self._send_button)
        column.addLayout(bottom)
        root.addLayout(column, 1)

    DRAWER_WIDTH = 174  # drawer width plus spacing

    def drawer_open(self) -> bool:
        return self._drawer.isVisible()

    def _toggle_drawer(self) -> None:
        opened = not self._drawer.isVisible()
        self.drawer_toggled.emit(opened)  # before the change, so the panel can record its size
        self._drawer.setVisible(opened)

    def drag_handles(self) -> list[QWidget]:
        """Widgets that can be used to drag the panel around (the title bar)."""
        return [self._title]

    def _flat(self, text: str, tip: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("flat")
        button.setToolTip(tip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedWidth(30)
        return button

    # ------------------------------------------------------------ scrolling
    def _on_scrolled(self, value: int) -> None:
        self._stick = value >= self._scroll.verticalScrollBar().maximum() - 40

    def _on_range_changed(self, _minimum: int, maximum: int) -> None:
        if self._stick:
            self._scroll.verticalScrollBar().setValue(maximum)

    def _add(self, widget: QWidget) -> None:
        self._messages.insertWidget(self._messages.count() - 1, widget)
        self._stick = True

    # --------------------------------------------------------------- public
    def focus_input(self) -> None:
        self._input.setFocus()

    def set_detached(self, detached: bool) -> None:
        self._detach_button.setText("⇲" if detached else "⧉")
        self._detach_button.setToolTip(
            "Attach the chat back to Capri" if detached else "Detach into a normal window")

    def set_title(self, title: str) -> None:
        self._title.set_full_text(title.upper() if title else "CAPRI")
        hint = "Drag to move the chat. Double-click to put it back next to Capri."
        self._title.setToolTip(f"{title}\n{hint}" if title else hint)

    def set_backend_info(self, badge: str, hint: str) -> None:
        """Show the active model and the local/cloud capability hint."""
        self._badge.setText(f"{badge}  ▾")
        self._hint.setText(hint)
        self._hint.setVisible(bool(hint))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._send_button.setText("STOP" if busy else "SEND")
        self._send_button.setToolTip("Stop generating" if busy else "Send")

    def set_conversations(self, items: list[tuple[str, str, float]], current: str) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for conv_id, title, _updated in items:
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, conv_id)
            item.setToolTip(title)
            self._list.addItem(item)
            if conv_id == current:
                self._list.setCurrentItem(item)
        self._list.blockSignals(False)

    def clear_messages(self) -> None:
        self._flush_timer.stop()
        self._current, self._pending = None, ""
        self._tool_entries.clear()
        while self._messages.count() > 1:
            item = self._messages.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_user_message(self, text: str) -> None:
        widget = MessageWidget("user", self._theme)
        widget.set_text(text, final=True)
        self._add(widget)

    def add_assistant_message(self, text: str) -> None:
        """Add a finished assistant message (used when reopening a conversation)."""
        widget = MessageWidget("assistant", self._theme)
        widget.set_text(text, final=True)
        self._add(widget)

    def add_notice(self, text: str, kind: str = "error") -> None:
        widget = MessageWidget(kind, self._theme)
        widget.set_text(text, final=True)
        self._add(widget)

    def append_assistant_text(self, delta: str) -> None:
        """Stream text into the current assistant message (created on demand)."""
        if self._current is None:
            self._current = MessageWidget("assistant", self._theme, decrypt=True)
            self._add(self._current)
            self._pending = ""
        self._pending += delta
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def _flush(self) -> None:
        if self._current is not None and self._pending != self._current.text:
            self._current.set_text(self._pending)
        else:
            self._flush_timer.stop()

    def end_assistant_message(self) -> None:
        """Finish the message that is currently streaming, if any."""
        self._flush_timer.stop()
        if self._current is not None:
            self._current.set_text(self._pending.strip(), final=True)
        self._current, self._pending = None, ""

    def add_tool_entry(self, call_id: str, label: str) -> ToolEntry:
        entry = ToolEntry(label, self._theme)
        self._tool_entries[call_id] = entry
        self._add(entry)
        return entry

    def tool_entry(self, call_id: str) -> ToolEntry | None:
        return self._tool_entries.get(call_id)

    # -------------------------------------------------------------- actions
    def _send_or_stop(self) -> None:
        if self._busy:
            self.stop_requested.emit()
        else:
            self._send()

    def _send(self) -> None:
        if self._busy:
            return
        text = self._input.toPlainText().strip()
        if text:
            self._input.clear()
            self.send_requested.emit(text)

    def _selected_id(self) -> str:
        item = self._list.currentItem()
        return str(item.data(Qt.UserRole)) if item else ""

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.conversation_selected.emit(str(item.data(Qt.UserRole)))

    def _rename_current(self) -> None:
        conv_id = self._selected_id()
        if not conv_id:
            return
        current = self._list.currentItem().text()
        title, ok = ask_text(self.window(), "Rename conversation", "New name:", current)
        if ok and title.strip():
            self.rename_requested.emit(conv_id, title.strip())

    def _delete_current(self) -> None:
        conv_id = self._selected_id()
        if not conv_id:
            return
        if ask_yes_no(self.window(), "Delete conversation",
                      f"Delete “{self._list.currentItem().text()}”? This cannot be undone."):
            self.delete_requested.emit(conv_id)


class _ScanOverlay(QWidget):
    """Draws the scanline sweep while the holo panel unfolds."""

    def __init__(self, panel: "HoloPanel") -> None:
        super().__init__(panel)
        self._panel = panel
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

    def paintEvent(self, _event) -> None:  # noqa: N802
        reveal = self._panel.reveal
        if reveal >= 0.999:
            return
        painter = QPainter(self)
        theme = self._panel.theme
        strength = 1.0 - reveal
        line = qcolor(theme.color("secondary"), 0.35 * strength)
        for y in range(0, self.height(), 4):
            painter.fillRect(0, y, self.width(), 1, line)
        painter.fillRect(self.rect(), qcolor(theme.color("accent"), 0.18 * strength))
        edge_x = int(self.width() * reveal) if self._panel.side == "right" else int(
            self.width() * (1 - reveal))
        painter.fillRect(edge_x - 2, 0, 4, self.height(), qcolor(theme.color("text"), 0.8))


class HoloPanel(QWidget):
    """Frameless translucent panel that unfolds from Capri's side.

    It follows Capri until the user drags it by its title bar (or its border);
    from then on it stays where it was put. Double-clicking the title bar
    snaps it back next to Capri.
    """

    snap_back_requested = Signal()

    PANEL_W, PANEL_H = 490, 560

    def __init__(self, theme: Theme, always_on_top: bool = True) -> None:
        super().__init__(None)
        self.theme = theme
        self.side = "right"
        self.reveal = 1.0
        self._closing = False
        self.user_placed = False
        self._drag_offset: QPoint | None = None
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint
        if always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(self.PANEL_W, self.PANEL_H)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._overlay = _ScanOverlay(self)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(240)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_reveal)
        self._anim.finished.connect(self._animation_done)

    def set_content(self, content: QWidget) -> None:
        self._layout.addWidget(content)
        content.show()
        self._overlay.raise_()
        for handle in getattr(content, "drag_handles", lambda: [])():
            handle.installEventFilter(self)
            handle.setCursor(Qt.SizeAllCursor)

    # ------------------------------------------------------------ dragging
    def _drag_start(self, global_pos: QPoint) -> None:
        self._drag_offset = global_pos - self.pos()

    def _drag_move(self, global_pos: QPoint) -> None:
        if self._drag_offset is not None:
            self.user_placed = True
            self.move(global_pos - self._drag_offset)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
            self._drag_offset = None
            self.snap_back_requested.emit()
            return True
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._drag_start(event.globalPosition().toPoint())
            return True
        if kind == QEvent.MouseMove and self._drag_offset is not None:
            self._drag_move(event.globalPosition().toPoint())
            return True
        if kind == QEvent.MouseButtonRelease and self._drag_offset is not None:
            self._drag_offset = None
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:  # the margins around the content act as a border
            self._drag_start(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._drag_move(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag_offset = None

    def set_drawer(self, opened: bool) -> None:
        """Grow for the conversation drawer, and shrink back when it closes.

        Must be called *before* the drawer is shown or hidden: Qt grows the window
        on its own as soon as the drawer appears, so the closed size is recorded
        first. The edge next to Capri stays put, so the panel grows away from her.
        """
        right = self.x() + self.width()
        if opened:
            self._closed_width = self.width()
            target = self._closed_width + ChatContent.DRAWER_WIDTH
        else:
            target = getattr(self, "_closed_width", self.PANEL_W)

        def apply() -> None:
            # process pending layout updates first, or the old (wider) minimum size of the
            # nested layouts still blocks the shrink
            QApplication.sendPostedEvents(None, QEvent.LayoutRequest)
            self._layout.activate()
            self.resize(target, self.height())
            if self.side == "left":
                self.move(right - self.width(), self.y())
            self.keep_on_screen()

        QTimer.singleShot(0, apply)

    def keep_on_screen(self) -> None:
        """Pull the panel back inside the nearest screen (after a resolution change etc.)."""
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.screenAt(self.geometry().center()) or \
            QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = max(area.left(), min(self.x(), area.right() - self.width()))
        y = max(area.top(), min(self.y(), area.bottom() - self.height()))
        self.move(x, y)

    def set_always_on_top(self, enabled: bool) -> None:
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        if visible:
            self.show()

    def place_beside(self, character: QRect, screen: QRect) -> None:
        """Position the panel next to the character rectangle, inside *screen*."""
        gap = 6
        right_space = screen.right() - character.right()
        left_space = character.left() - screen.left()
        self.side = "right" if right_space >= self.width() + gap or right_space >= left_space \
            else "left"
        x = character.right() + gap if self.side == "right" else character.left() - gap - self.width()
        y = character.bottom() - self.height() + 20
        x = max(screen.left() + 4, min(x, screen.right() - self.width() - 4))
        y = max(screen.top() + 4, min(y, screen.bottom() - self.height() - 4))
        self.move(QPoint(x, y))

    def unfold(self) -> None:
        """Show with the scanline unfold animation."""
        self._closing = False
        self._anim.stop()
        self._set_reveal(0.02)
        self.show()
        self.raise_()
        self.activateWindow()
        self._anim.setStartValue(0.02)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def fold(self) -> None:
        """Hide with the reverse animation."""
        if not self.isVisible():
            return
        self._closing = True
        self._anim.stop()
        self._anim.setStartValue(float(self.reveal))
        self._anim.setEndValue(0.02)
        self._anim.start()

    def _set_reveal(self, value: float) -> None:
        self.reveal = float(value)
        if self.reveal >= 0.999:
            self.clearMask()
        else:
            width = max(2, int(self.width() * self.reveal))
            rect = QRect(0, 0, width, self.height()) if self.side == "right" else QRect(
                self.width() - width, 0, width, self.height())
            self.setMask(QRegion(rect))
        self._overlay.update()

    def _animation_done(self) -> None:
        if self._closing:
            self.hide()
            self.clearMask()
            self.reveal = 1.0
            self._closing = False
        else:
            self._set_reveal(1.0)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_glass(painter, QRectF(self.rect()), self.theme)


class DetachedWindow(QWidget):
    """A normal, resizable window hosting the chat for long sessions."""

    closed = Signal()

    def __init__(self, theme: Theme) -> None:
        super().__init__(None)
        self.theme = theme
        self.setWindowTitle("Capri")
        self.resize(760, 680)
        self.setMinimumSize(420, 360)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)

    def set_content(self, content: QWidget) -> None:
        self._layout.addWidget(content)
        content.show()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(self.theme.color("background")))
        paint_glass(painter, QRectF(self.rect()), self.theme, alpha=1.0, radius=0)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.accept()
        self.closed.emit()
