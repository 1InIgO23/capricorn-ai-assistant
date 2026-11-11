"""Confirmation dialog and settings window."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..config.settings import EFFECT_LEVELS, Settings
from ..config.theme import Theme
from ..tools.base import ConfirmRequest
from .markdown import highlight_code
from .style import blend, css_fonts, paint_glass, pulse, qcolor

SCRIPT_NOTE = ("Local models are fine for simple scripts. For complex scripts a cloud model "
               "(Settings → Brain) is recommended. Always read a script before allowing it.")


class ConfirmDialog(QDialog):
    """Frameless always-on-top dialog whose border pulses with Capri's alert glow."""

    decided = Signal(bool)

    def __init__(self, request: ConfirmRequest, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self._theme = theme
        self._drag: QPoint | None = None
        self._done = False
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("Capri needs your OK")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        tag = QLabel("CAPRI REQUESTS PERMISSION")
        tag.setStyleSheet(
            f"color: {theme.color('alert')}; font-family: {css_fonts(theme, 'display')}; "
            "font-weight: bold; letter-spacing: 2px;")
        title = QLabel(request.title)
        title.setObjectName("title")
        summary = QLabel(request.summary)
        summary.setWordWrap(True)
        layout.addWidget(tag)
        layout.addWidget(title)
        layout.addWidget(summary)

        if request.details:
            details = QLabel("\n".join(request.details))
            details.setWordWrap(True)
            details.setTextInteractionFlags(Qt.TextSelectableByMouse)
            details.setStyleSheet(
                f"font-family: {css_fonts(theme, 'mono')}; background: {theme.color('background')};"
                f" border: 1px solid {theme.color('surface_alt')}; border-radius: 4px; "
                "padding: 8px;")
            layout.addWidget(details)

        if request.code is not None:
            caption = QLabel("FULL SCRIPT" if request.kind == "script" else "CONTENT TO WRITE")
            caption.setObjectName("dim")
            layout.addWidget(caption)
            view = QTextBrowser()
            view.setLineWrapMode(QTextBrowser.NoWrap)
            view.setHtml(highlight_code(request.code, request.code_language, theme))
            view.setMinimumHeight(180)
            view.setMaximumHeight(380)
            layout.addWidget(view, 1)
        if request.kind == "script":
            note = QLabel(SCRIPT_NOTE)
            note.setObjectName("hint")
            note.setWordWrap(True)
            layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        deny = QPushButton("Deny")
        deny.setDefault(True)
        deny.setAutoDefault(True)
        deny.clicked.connect(lambda: self._finish(False))
        allow = QPushButton("Allow")
        allow.setObjectName("primary")
        allow.setAutoDefault(False)
        allow.clicked.connect(lambda: self._finish(True))
        buttons.addWidget(deny)
        buttons.addWidget(allow)
        layout.addLayout(buttons)
        self.setMinimumWidth(480)
        self.setMaximumWidth(760)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.setInterval(40)
        self._pulse_timer.timeout.connect(self.update)
        self._pulse_timer.start()

    def _finish(self, approved: bool) -> None:
        if self._done:
            return
        self._done = True
        self._pulse_timer.stop()
        self.request.approved = approved
        self.request.event.set()
        self.decided.emit(approved)
        self.hide()
        self.deleteLater()

    def cancel(self) -> None:
        """Close as denied (used when the run is stopped)."""
        self._finish(False)

    def reject(self) -> None:  # Esc key
        self._finish(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._finish(False)
        event.accept()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        alert = qcolor(self._theme.color("alert"))
        border = blend(qcolor(self._theme.color("surface_alt")), alert, 0.35 + 0.65 * pulse(self._theme))
        paint_glass(painter, QRectF(self.rect()), self._theme, alpha=0.97, border=border)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = None


class SettingsDialog(QDialog):
    """The settings window: frameless glass panel in the same style as the chat.

    The AI backend, model and API key live in the separate Model window.
    """

    def __init__(self, settings: Settings, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme = theme
        self._drag: QPoint | None = None
        self.setWindowTitle("Capri — Settings")
        self.setWindowModality(Qt.ApplicationModal)  # before the flags: it resets them
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 16, 22, 18)
        layout.setSpacing(10)
        header = QHBoxLayout()
        tag = QLabel("SYSTEM CONFIG")
        tag.setStyleSheet(f"color: {theme.color('accent')}; "
                          f"font-family: {css_fonts(theme, 'display')}; "
                          "font-weight: bold; letter-spacing: 2px;")
        close = QPushButton("✕")
        close.setObjectName("flat")
        close.setToolTip("Close without saving")
        close.clicked.connect(self.reject)
        header.addWidget(tag)
        header.addStretch(1)
        header.addWidget(close)
        layout.addLayout(header)
        title = QLabel("Settings")
        title.setObjectName("title")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._body_tab(), "Character")
        tabs.addTab(self._tools_tab(), "Tools")
        layout.addWidget(tabs)
        model_note = QLabel("To change the AI model or an API key, click the model name in "
                            "the chat header (or AI model… in the menu).")
        model_note.setObjectName("dim")
        model_note.setWordWrap(True)
        layout.addWidget(model_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    # ---------------------------------------------------------------- tabs
    def _spin(self, key: str, low: int, high: int, suffix: str = "", step: int = 1) -> QSpinBox:
        box = QSpinBox()
        box.setRange(low, high)
        box.setSingleStep(step)
        box.setSuffix(suffix)
        try:
            box.setValue(int(self._settings.get(key, low)))
        except (TypeError, ValueError):
            box.setValue(low)
        return box

    def _body_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(8)
        self._size = self._spin("character_size", 120, 420, " px", 10)
        self._effects = QComboBox()
        for level in EFFECT_LEVELS:
            self._effects.addItem(level, level)
        self._effects.setCurrentIndex(EFFECT_LEVELS.index(self._settings.effects_level()))
        self._on_top = QCheckBox("Keep Capri above other windows")
        self._on_top.setChecked(bool(self._settings.get("always_on_top", True)))
        self._sleep = self._spin("sleep_minutes", 1, 240, " min")
        self._dock_windows = QCheckBox("Peek from behind other app windows")
        self._dock_windows.setToolTip("Drop Capri next to a window's side or top edge and she "
                                      "hides behind it and peeks out. When bored she may also "
                                      "do it on her own.")
        self._dock_windows.setChecked(bool(self._settings.get("dock_to_windows", True)))
        self._remarks = QCheckBox("Allow rare spontaneous remarks")
        self._remarks.setChecked(bool(self._settings.get("remarks_enabled", True)))
        self._remark_gap = self._spin("remarks_interval_minutes", 5, 240, " min")
        form.addRow("Character size", self._size)
        form.addRow("Effects intensity", self._effects)
        form.addRow("", self._on_top)
        form.addRow("Fall asleep after", self._sleep)
        form.addRow("", self._dock_windows)
        form.addRow("", self._remarks)
        form.addRow("At most one remark every", self._remark_gap)
        return page

    def _tools_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(8)
        self._workspace = QLineEdit(str(self._settings.workspace))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self._workspace, 1)
        row.addWidget(browse)
        self._timeout = self._spin("script_timeout", 5, 3600, " s", 5)
        form.addRow("Script workspace", row)
        form.addRow("Script timeout", self._timeout)
        note = QLabel("Scripts are saved in the workspace and only run after you have seen "
                      "the full script and clicked Allow. " + SCRIPT_NOTE)
        note.setObjectName("dim")
        note.setWordWrap(True)
        form.addRow(note)
        return page

    def _browse(self) -> None:
        dialog = QFileDialog(self, "Choose the script workspace", self._workspace.text())
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        if dialog.exec() and dialog.selectedFiles():
            self._workspace.setText(dialog.selectedFiles()[0])

    def _save(self) -> None:
        s = self._settings
        s.set("character_size", self._size.value())
        s.set("effects_intensity", self._effects.currentData())
        s.set("always_on_top", self._on_top.isChecked())
        s.set("sleep_minutes", self._sleep.value())
        s.set("dock_to_windows", self._dock_windows.isChecked())
        s.set("remarks_enabled", self._remarks.isChecked())
        s.set("remarks_interval_minutes", self._remark_gap.value())
        s.set("workspace", self._workspace.text().strip())
        s.set("script_timeout", self._timeout.value())
        s.save()
        self.accept()

    # ------------------------------------------------------- look and drag
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_glass(painter, QRectF(self.rect()), self._theme, alpha=0.97,
                    border=qcolor(self._theme.color("border")))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _event) -> None:  # noqa: N802
        self._drag = None
