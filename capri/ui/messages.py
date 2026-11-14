"""Widgets that make up the conversation view: messages, code blocks, tool log entries."""
from __future__ import annotations

import random

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QTextOption
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..config.theme import Theme
from .markdown import highlight_code, md_to_html, split_segments
from ..osutil import font_pt
from .style import css_fonts

_GLYPHS = "01#$%&=+?/\\<>▚▞▓░"
_DECRYPT_MAX = 160


class AutoTextBrowser(QTextBrowser):
    """A read-only rich text view that grows to fit its content (no inner scrolling)."""

    def __init__(self, theme: Theme, wrap: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.setFrameShape(QFrame.NoFrame)
        self.setOpenExternalLinks(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff if wrap else Qt.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if wrap:
            self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        else:
            self.setLineWrapMode(QTextBrowser.NoWrap)
        self.document().setDocumentMargin(2)
        self.document().setDefaultStyleSheet(
            f"a {{ color: {theme.color('secondary')}; }} ")
        self.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; padding: 0; "
            f"color: {theme.color('text')}; }}")
        self.viewport().setAutoFillBackground(False)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_args: object) -> None:
        height = int(self.document().size().height()) + 2
        bar = self.horizontalScrollBar()
        if self.horizontalScrollBarPolicy() != Qt.ScrollBarAlwaysOff and bar.maximum() > 0:
            height += bar.sizeHint().height()
        height = max(16, height)
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._fit()
        QTimer.singleShot(0, self._fit)

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.ignore()  # let the conversation scroll area handle the wheel


class ElidingButton(QPushButton):
    """A flat button whose text is cut with "…" instead of widening the window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def _elide(self) -> None:
        width = max(10, self.width() - 12)
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, width))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._elide()

    def minimumSizeHint(self):  # noqa: N802
        hint = super().minimumSizeHint()
        hint.setWidth(40)
        return hint


class CodeBlock(QFrame):
    """A syntax-highlighted code block with a language label and a copy button."""

    def __init__(self, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._code = ""
        self._lang = ""
        self.setObjectName("codeblock")
        self.setStyleSheet(
            f"QFrame#codeblock {{ background: {theme.color('background')}; "
            f"border: 1px solid {theme.color('surface_alt')}; border-radius: 5px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 6)
        layout.setSpacing(2)
        header = QHBoxLayout()
        self._label = QLabel("code")
        self._label.setStyleSheet(
            f"color: {theme.color('text_dim')}; font-family: {css_fonts(theme, 'mono')}; "
            f"font-size: {font_pt(max(7, theme.font_size('mono_size') - 2))}pt;")
        self._copy = QPushButton("COPY")
        self._copy.setObjectName("flat")
        self._copy.setCursor(Qt.PointingHandCursor)
        self._copy.setToolTip("Copy this code to the clipboard")
        self._copy.clicked.connect(self._copy_code)
        header.addWidget(self._label)
        header.addStretch(1)
        header.addWidget(self._copy)
        layout.addLayout(header)
        self._view = AutoTextBrowser(theme, wrap=False)
        layout.addWidget(self._view)

    def set_code(self, code: str, language: str) -> None:
        if code == self._code and language == self._lang:
            return
        self._code, self._lang = code, language
        self._label.setText(language or "code")
        self._view.setHtml(highlight_code(code, language, self._theme))

    def _copy_code(self) -> None:
        QGuiApplication.clipboard().setText(self._code)
        self._copy.setText("COPIED")
        QTimer.singleShot(1200, lambda: self._copy.setText("COPY"))


class MessageWidget(QFrame):
    """One chat message (user, assistant or a notice)."""

    def __init__(self, role: str, theme: Theme, decrypt: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self.role = role
        self._text = ""
        self._final = False
        self._parts: list[tuple[str, QWidget]] = []
        self._shown: list[tuple[str, str, str]] = []
        self._decrypt = decrypt and role == "assistant"
        self._decrypt_pos = 0.0

        accent = {"user": theme.color("tertiary"), "assistant": theme.color("accent"),
                  "error": theme.color("error"), "notice": theme.color("warning")}.get(
                      role, theme.color("border"))
        self.setObjectName("message")
        tint = theme.color("surface_alt") if role == "user" else "transparent"
        self.setStyleSheet(
            f"QFrame#message {{ background: {tint}; border: none; "
            f"border-left: 2px solid {accent}; border-radius: 3px; }}")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(10, 5, 6, 5)
        self._layout.setSpacing(4)
        names = {"user": "YOU", "assistant": "CAPRI", "error": "SIGNAL LOST", "notice": "NOTE"}
        tag = QLabel(names.get(role, role.upper()))
        tag.setStyleSheet(
            f"color: {accent}; font-family: {css_fonts(theme, 'display')}; "
            f"font-size: {font_pt(max(7, theme.font_size('ui_size') - 3))}pt; font-weight: bold; "
            "letter-spacing: 1px;")
        self._layout.addWidget(tag)

        self._decrypt_timer = QTimer(self)
        self._decrypt_timer.setInterval(35)
        self._decrypt_timer.timeout.connect(self._advance_decrypt)
        if self._decrypt:
            self._decrypt_timer.start()

    # ------------------------------------------------------------- content
    @property
    def text(self) -> str:
        return self._text

    def set_text(self, text: str, final: bool = False) -> None:
        """Replace the message text; *final* marks the end of streaming."""
        self._text = text
        self._final = self._final or final
        self._render()

    def _first_line(self) -> str:
        return self._text.split("\n", 1)[0][:_DECRYPT_MAX]

    def _advance_decrypt(self) -> None:
        first = self._first_line()
        rate = float(self._theme.fx("decrypt_chars_per_second", 60) or 60)
        self._decrypt_pos += rate * 0.035
        line_done = "\n" in self._text or self._final
        if (line_done and self._decrypt_pos >= len(first)) or first.startswith("```"):
            self._decrypt = False
            self._decrypt_timer.stop()
        self._render()

    def _display_text(self) -> str:
        if not self._decrypt:
            return self._text
        first = self._first_line()
        settled = int(self._decrypt_pos)
        if settled >= len(first):
            return self._text
        scrambled = first[:settled] + "".join(
            ch if ch.isspace() else random.choice(_GLYPHS) for ch in first[settled:])
        return scrambled + self._text[len(first):]

    def _render(self) -> None:
        segments = split_segments(self._display_text())
        for index, segment in enumerate(segments):
            kind, content, lang = segment
            if index < len(self._parts) and self._parts[index][0] == kind:
                if index < len(self._shown) and self._shown[index] == segment:
                    continue
                widget = self._parts[index][1]
            else:
                self._drop_parts(index)
                widget = CodeBlock(self._theme) if kind == "code" else AutoTextBrowser(self._theme)
                self._layout.addWidget(widget)
                self._parts.append((kind, widget))
            if kind == "code":
                widget.set_code(content, lang)  # type: ignore[union-attr]
            else:
                widget.setHtml(md_to_html(content, self._theme))  # type: ignore[union-attr]
        self._drop_parts(len(segments))
        self._shown = segments

    def _drop_parts(self, start: int) -> None:
        while len(self._parts) > start:
            _kind, widget = self._parts.pop()
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._shown = self._shown[:start]


class ToolEntry(QFrame):
    """A compact, collapsible log line for one tool call."""

    def __init__(self, label: str, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = theme
        self._label = label
        self._summary = "running…"
        self._open = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(0)
        self._button = ElidingButton()
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.clicked.connect(self.toggle)
        layout.addWidget(self._button)
        self._details = AutoTextBrowser(theme)
        self._details.setVisible(False)
        self._details.setStyleSheet(
            f"QTextBrowser {{ background: transparent; border: none; "
            f"border-left: 1px dotted {theme.color('surface_alt')}; padding-left: 12px; "
            f"color: {theme.color('text_dim')}; }}")
        layout.addWidget(self._details)
        self._detail_parts: list[QWidget] = []
        self._color = theme.color("text_dim")
        self._refresh()

    def _refresh(self) -> None:
        arrow = "▾" if self._open else "▸"
        self._button.set_full_text(f"{arrow} {self._label} · {self._summary}")
        self._button.setStyleSheet(
            f"QPushButton {{ text-align: left; background: transparent; border: none; "
            f"padding: 2px 4px; color: {self._color}; "
            f"font-family: {css_fonts(self._theme, 'mono')}; "
            f"font-size: {font_pt(max(7, self._theme.font_size('mono_size') - 1))}pt; }}"
            f"QPushButton:hover {{ color: {self._theme.color('secondary')}; }}")
        self._button.ensurePolished()
        self._button._elide()

    def toggle(self) -> None:
        self._open = not self._open
        self._details.setVisible(self._open)
        for part in self._detail_parts:
            part.setVisible(self._open)
        self._refresh()

    def set_progress(self, summary: str) -> None:
        self._summary = summary
        self._refresh()

    def set_result(self, summary: str, details: str, ok: bool) -> None:
        """Fill in the outcome of the tool call."""
        self._summary = summary or ("done" if ok else "failed")
        self._color = self._theme.color("text_dim") if ok else self._theme.color("error")
        for part in self._detail_parts:
            self.layout().removeWidget(part)
            part.deleteLater()
        self._detail_parts = []
        text_parts = []
        for kind, content, lang in split_segments(details or "(no details)"):
            if kind == "code":
                block = CodeBlock(self._theme)
                block.set_code(content, lang)
                block.setVisible(self._open)
                self.layout().addWidget(block)
                self._detail_parts.append(block)
            else:
                text_parts.append(content)
        self._details.setHtml(md_to_html("\n\n".join(text_parts), self._theme))
        self._refresh()
