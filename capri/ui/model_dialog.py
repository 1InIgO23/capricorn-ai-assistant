"""The Model window: pick the AI backend, the model and the API key."""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRectF, Qt, QThread, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..config.settings import Settings
from ..config.theme import Theme
from ..llm.base import BackendError, LLMBackend, available_backends
from .style import css_fonts, paint_glass, qcolor

log = logging.getLogger(__name__)


class _Worker(QThread):
    """Runs one blocking call off the UI thread."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, job: Callable[[], Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._job = job

    def run(self) -> None:  # noqa: D102
        try:
            self.succeeded.emit(self._job())
        except BackendError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Model window job failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ModelDialog(QDialog):
    """Frameless glass window, in the same style as Capri's other panels."""

    def __init__(self, settings: Settings, theme: Theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme = theme
        self._backends = available_backends()
        self._drafts: dict[str, dict[str, str]] = {
            key: {"base_url": cfg.get("base_url") or cls.default_base_url,
                  "model": cfg.get("model") or cls.default_model,
                  "api_key": cfg.get("api_key", "")}
            for key, cls in self._backends.items()
            for cfg in [settings.backend_config(key)]
        }
        self._current = ""
        self._worker: _Worker | None = None
        self._drag: QPoint | None = None

        self.setWindowTitle("Capri — Model")
        self.setWindowModality(Qt.ApplicationModal)  # before the flags: it resets them
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumWidth(500)
        self._build()
        start = str(settings.get("backend", "ollama"))
        index = max(0, self._provider.findData(start))
        self._provider.setCurrentIndex(index)
        self._load(self._provider.currentData())

    # ------------------------------------------------------------------ ui
    def _build(self) -> None:
        t = self._theme
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 18)
        root.setSpacing(10)

        header = QHBoxLayout()
        tag = QLabel("NEURAL LINK")
        tag.setStyleSheet(f"color: {t.color('accent')}; font-family: {css_fonts(t, 'display')}; "
                          "font-weight: bold; letter-spacing: 2px;")
        close = QPushButton("✕")
        close.setObjectName("flat")
        close.setToolTip("Close without saving")
        close.clicked.connect(self.reject)
        header.addWidget(tag)
        header.addStretch(1)
        header.addWidget(close)
        root.addLayout(header)
        title = QLabel("Choose Capri's brain")
        title.setObjectName("title")
        root.addWidget(title)

        form = QFormLayout()
        form.setSpacing(8)
        self._provider = QComboBox()
        for key, cls in self._backends.items():
            self._provider.addItem(cls.label, key)
        self._provider.currentIndexChanged.connect(
            lambda _i: self._load(self._provider.currentData()))
        form.addRow("Provider", self._provider)

        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setObjectName("dim")
        form.addRow("", self._info)

        self._url = QLineEdit()
        form.addRow("Base URL", self._url)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.setInsertPolicy(QComboBox.NoInsert)
        self._model.lineEdit().setPlaceholderText("model name")
        self._fetch = QPushButton("↻ Fetch")
        self._fetch.setToolTip("Ask the server which models are available")
        self._fetch.clicked.connect(self._fetch_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self._model, 1)
        model_row.addWidget(self._fetch)
        form.addRow("Model", model_row)

        self._key_label = QLabel("API key")
        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.Password)
        self._key.setPlaceholderText("paste your API key")
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self._key.setEchoMode(
            QLineEdit.Normal if on else QLineEdit.Password))
        self._key_row = QWidget()
        key_layout = QHBoxLayout(self._key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self._key, 1)
        key_layout.addWidget(show)
        form.addRow(self._key_label, self._key_row)
        root.addLayout(form)

        self._kind = QLabel()
        self._kind.setObjectName("hint")
        self._kind.setWordWrap(True)
        root.addWidget(self._kind)

        self._status = QLabel(" ")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(f"font-family: {css_fonts(t, 'mono')};")
        root.addWidget(self._status)

        note = QLabel("API keys are stored only in %APPDATA%\\Capri\\settings.json and are never "
                      "written to the logs.")
        note.setObjectName("dim")
        note.setWordWrap(True)
        root.addWidget(note)

        buttons = QHBoxLayout()
        self._test = QPushButton("Test connection")
        self._test.clicked.connect(self._test_connection)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(self._test)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    # ---------------------------------------------------------- provider switch
    def _store(self) -> None:
        """Keep what was typed for the provider being left."""
        if self._current:
            self._drafts[self._current] = {"base_url": self._url.text().strip(),
                                           "model": self._model.currentText().strip(),
                                           "api_key": self._key.text().strip()}

    def _load(self, key: str) -> None:
        self._store()
        self._current = key
        cls = self._backends[key]
        draft = self._drafts[key]
        self._info.setText(cls.help_text)
        self._url.setText(draft["base_url"])
        self._url.setPlaceholderText(cls.default_base_url)
        self._model.clear()
        self._model.setEditText(draft["model"])
        self._key.setText(draft["api_key"])
        self._key_label.setVisible(cls.needs_api_key)
        self._key_row.setVisible(cls.needs_api_key)
        self._kind.setText(
            "Local model: runs on your PC, free and private. Fine for chat, web lookups, file "
            "chores and simple scripts." if cls.is_local else
            "Cloud model: needs an API key and may cost money per use. Recommended for complex "
            "coding tasks.")
        self._set_status("")
        self.adjustSize()

    # ------------------------------------------------------------- actions
    def _backend(self) -> LLMBackend:
        cls = self._backends[self._current]
        return cls(self._url.text().strip(), self._model.currentText().strip(),
                   self._key.text().strip())

    def _set_status(self, text: str, kind: str = "dim") -> None:
        color = {"ok": "success", "error": "error", "busy": "warning"}.get(kind, "text_dim")
        self._status.setStyleSheet(f"color: {self._theme.color(color)}; "
                                   f"font-family: {css_fonts(self._theme, 'mono')};")
        self._status.setText(text or " ")

    def _run(self, job: Callable[[], Any], on_ok: Callable[[Any], None], busy: str) -> None:
        if self._worker is not None:
            return
        cls = self._backends[self._current]
        if cls.needs_api_key and not self._key.text().strip():
            self._set_status("▸ Paste an API key first.", "error")
            return
        self._set_status(f"▸ {busy}", "busy")
        self._fetch.setEnabled(False)
        self._test.setEnabled(False)
        worker = _Worker(job, self)
        worker.succeeded.connect(on_ok)
        worker.failed.connect(lambda message: self._set_status(f"▸ {message}", "error"))
        worker.finished.connect(self._job_done)
        self._worker = worker
        worker.start()

    def _job_done(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self._fetch.setEnabled(True)
        self._test.setEnabled(True)

    def _fill_models(self, models: list[str]) -> None:
        typed = self._model.currentText().strip()
        self._model.clear()
        self._model.addItems(models)
        self._model.setEditText(typed or (models[0] if models else ""))
        return None

    def _fetch_models(self) -> None:
        def done(models: list[str]) -> None:
            self._fill_models(models)
            if models:
                self._set_status(f"▸ {len(models)} models found. Pick one from the list.", "ok")
                self._model.showPopup()
            else:
                self._set_status("▸ Connected, but no models were listed. Type the name.", "ok")
        self._run(self._backend().list_models, done, "FETCHING MODELS…")

    def _test_connection(self) -> None:
        wanted = self._model.currentText().strip()

        def done(models: list[str]) -> None:
            self._fill_models(models)
            if not wanted:
                self._set_status("▸ Connected. Now choose a model.", "ok")
            elif not models or wanted in models or f"{wanted}:latest" in models:
                self._set_status(f"▸ LINK ESTABLISHED · {wanted}", "ok")
            elif self._backends[self._current].is_local:
                self._set_status(f"▸ Connected, but '{wanted}' is not installed. "
                                 f"Run: ollama pull {wanted}", "error")
            else:
                self._set_status(f"▸ Connected, but '{wanted}' is not in the provider's "
                                 "model list. Check the name.", "error")
        self._run(self._backend().list_models, done, "CONNECTING…")

    def _save(self) -> None:
        self._store()
        draft = self._drafts[self._current]
        cls = self._backends[self._current]
        if not draft["model"]:
            self._set_status("▸ Enter or fetch a model name first.", "error")
            return
        if cls.needs_api_key and not draft["api_key"]:
            self._set_status("▸ This provider needs an API key.", "error")
            return
        self._settings.set("backend", self._current)
        for key, values in self._drafts.items():
            self._settings.set(f"backends.{key}", dict(values))
        self._settings.save()
        self.accept()

    # ------------------------------------------------------------ painting
    def done(self, result: int) -> None:  # noqa: D102 (wait for a running job)
        if self._worker is not None:
            self._worker.wait(3000)
        super().done(result)

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
