"""Application controller: wires the character, chat, agent, tray and settings together."""
from __future__ import annotations

import datetime as _dt
import json
import logging
import random
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QPoint, QStandardPaths, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import APP_NAME, __version__
from .character.bubble import SpeechBubble
from .character.mood import MoodEngine
from .character.sprites import SpriteLibrary
from .character.window import CharacterWindow
from .config import paths
from .config.logging_setup import register_secret, setup_logging
from .config.settings import Settings
from .config.theme import Theme
from .llm import persona
from .llm.agent import AgentRun
from .llm.base import BackendError, Message, available_backends, create_backend
from .storage.conversations import Conversation, ConversationStore
from .system.windows import foreground_is_fullscreen
from .tools import ToolContext, build_registry, default_policy
from .tools.base import ConfirmRequest, ToolResult
from .ui.chat import ChatContent, DetachedWindow, HoloPanel
from .ui.dialogs import ConfirmDialog, SettingsDialog
from .ui.model_dialog import ModelDialog
from .ui.style import build_stylesheet

log = logging.getLogger(__name__)

LOCAL_HINT = ("Local model: fine for chat, web lookups, file chores and simple scripts. "
              "For complex coding, set up a cloud model in Settings.")


def make_icon(theme: Theme) -> QIcon:
    """Use ``assets/icon.ico`` when present, otherwise draw a small construct icon."""
    icon_path = paths.assets_dir() / "icon.ico"
    if icon_path.is_file():
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(theme.color("background")))
    painter.drawRoundedRect(2, 2, 60, 60, 12, 12)
    painter.setBrush(QColor(theme.color("accent")))
    painter.drawPolygon(QPolygonF([QPointF(32, 8), QPointF(54, 32), QPointF(32, 56),
                                   QPointF(10, 32)]))
    painter.setBrush(QColor(theme.color("secondary")))
    painter.drawPolygon(QPolygonF([QPointF(32, 22), QPointF(42, 32), QPointF(32, 42),
                                   QPointF(22, 32)]))
    painter.end()
    return QIcon(pixmap)


class CapriApp(QObject):
    """Owns every window and all application state."""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.settings = Settings()
        for secret in self.settings.secrets():
            register_secret(secret)
        self.theme = Theme.load(paths.theme_file())
        app.setStyleSheet(build_stylesheet(self.theme))
        self.icon = make_icon(self.theme)
        app.setWindowIcon(self.icon)

        self.state = self._load_state()
        self.mood = MoodEngine(self.state.get("mood"))
        self.started = time.time()
        self.last_remark = float(self.state.get("last_remark", 0.0))

        paths.character_assets_dir().mkdir(parents=True, exist_ok=True)
        self.sprites = SpriteLibrary(paths.character_assets_dir(),
                                     int(self.settings.get("character_size", 220)))
        self.character = CharacterWindow(self.settings, self.theme, self.sprites, self.mood)
        self.bubble = SpeechBubble(self.theme)

        self.store = ConversationStore(paths.conversations_dir())
        self.registry = build_registry()
        self.conversation: Conversation = self._initial_conversation()
        self.run: AgentRun | None = None
        self.confirm_dialog: ConfirmDialog | None = None
        self._return_pos: QPoint | None = None
        self._return_dock: str | None = None
        self._streamed_any = False
        self._auto_quiet = False
        self._detached = False

        self.content = ChatContent(self.theme)
        self.holo = HoloPanel(self.theme, bool(self.settings.get("always_on_top", True)))
        self.detached = DetachedWindow(self.theme)
        self.detached.setWindowIcon(self.icon)
        self.holo.set_content(self.content)

        self.nudge_timer = QTimer(self)
        self.nudge_timer.setSingleShot(True)
        self.nudge_timer.setInterval(30_000)
        self.nudge_timer.timeout.connect(self._nudge)

        self._build_menu()
        self._build_tray()
        self._connect()
        self._show_conversation()
        self._refresh_backend_info()
        self._apply_quiet()

        self.minute_timer = QTimer(self)
        self.minute_timer.setInterval(60_000)
        self.minute_timer.timeout.connect(self._every_minute)
        self.minute_timer.start()
        self.fullscreen_timer = QTimer(self)
        self.fullscreen_timer.setInterval(2500)
        self.fullscreen_timer.timeout.connect(self._check_fullscreen)
        self.fullscreen_timer.start()

        app.aboutToQuit.connect(self._shutdown)
        self.character.show()
        log.info("Capri %s started. Backend: %s. Workspace: %s", __version__,
                 self.settings.get("backend"), self.settings.workspace)

    # ----------------------------------------------------------- persistence
    def _load_state(self) -> dict:
        try:
            data = json.loads(paths.state_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            paths.state_file().write_text(json.dumps(
                {"mood": self.mood.to_dict(), "last_remark": self.last_remark}), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save state: %s", exc)

    def _initial_conversation(self) -> Conversation:
        last = str(self.settings.get("last_conversation") or "")
        conversation = self.store.load(last) if last else None
        return conversation or self.store.create()

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        self.menu = QMenu()
        self.act_chat = QAction("Open chat", self.menu)
        self.act_chat.triggered.connect(self.open_chat)
        self.act_quiet = QAction("Quiet mode", self.menu)
        self.act_quiet.setCheckable(True)
        self.act_quiet.setChecked(bool(self.settings.get("quiet_mode", False)))
        self.act_quiet.toggled.connect(self._toggle_quiet)
        act_model = QAction("AI model…", self.menu)
        act_model.triggered.connect(self.open_model_dialog)
        act_settings = QAction("Settings…", self.menu)
        act_settings.triggered.connect(self.open_settings)
        act_reload = QAction("Reload assets", self.menu)
        act_reload.triggered.connect(self.reload_assets)
        act_folder = QAction("Open assets folder", self.menu)
        act_folder.triggered.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(paths.character_assets_dir()))))
        act_quit = QAction("Quit", self.menu)
        act_quit.triggered.connect(self.app.quit)
        self.menu.addAction(self.act_chat)
        self.menu.addAction(self.act_quiet)
        self.menu.addSeparator()
        self.menu.addAction(act_model)
        self.menu.addAction(act_settings)
        self.menu.addAction(act_reload)
        self.menu.addAction(act_folder)
        self.menu.addSeparator()
        self.menu.addAction(act_quit)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip("Capri")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        else:
            log.warning("No system tray available; use Capri's right-click menu instead.")

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_chat()

    def _connect(self) -> None:
        ch = self.character
        ch.clicked.connect(self.toggle_chat)
        ch.menu_requested.connect(lambda pos: self.menu.popup(pos))
        ch.moved.connect(self._follow_character)
        ch.interaction.connect(self.mood.note_interaction)
        ch.click_reaction.connect(self._click_reaction)
        self.bubble.clicked.connect(self._bubble_clicked)
        c = self.content
        c.send_requested.connect(self.send)
        c.stop_requested.connect(self.stop)
        c.close_requested.connect(self.close_chat)
        c.detach_requested.connect(self.toggle_detach)
        c.conversation_selected.connect(self._select_conversation)
        c.new_conversation_requested.connect(self._new_conversation)
        c.rename_requested.connect(self._rename_conversation)
        c.delete_requested.connect(self._delete_conversation)
        c.model_requested.connect(self.open_model_dialog)
        self.detached.closed.connect(self._detached_closed)
        self.holo.snap_back_requested.connect(self._snap_chat_back)

    # ------------------------------------------------------------------ chat
    def chat_visible(self) -> bool:
        return self.detached.isVisible() if self._detached else self.holo.isVisible()

    def toggle_chat(self) -> None:
        if self._detached:
            if self.detached.isVisible() and self.detached.isActiveWindow():
                self.detached.hide()
            else:
                self.open_chat()
        elif self.holo.isVisible():
            self.close_chat()
        else:
            self.open_chat()

    def open_chat(self) -> None:
        self.bubble.hide()
        self.character.wake()
        if self._detached:
            self.detached.show()
            self.detached.raise_()
            self.detached.activateWindow()
        else:
            self.character.set_pinned(True)
            QTimer.singleShot(60, self._unfold)
        self.content.focus_input()

    def _unfold(self) -> None:
        if self._detached or self.holo.isVisible():
            return
        if self.holo.user_placed:
            self.holo.keep_on_screen()
        else:
            self.holo.place_beside(self.character.char_rect_global(),
                                   self.character.current_screen_rect())
        self.holo.unfold()
        self.content.focus_input()

    def close_chat(self) -> None:
        if self._detached:
            self.detached.hide()
        else:
            self.holo.fold()
            self.character.set_pinned(False)

    def toggle_detach(self) -> None:
        """Move the chat between the holo panel and a normal resizable window."""
        if self._detached:
            self._detached = False
            self.detached.hide()
            self.holo.set_content(self.content)
            self.content.set_detached(False)
            self.open_chat()
        else:
            self._detached = True
            self.holo.hide()
            self.character.set_pinned(False)
            self.detached.set_content(self.content)
            self.content.set_detached(True)
            self.open_chat()

    def _detached_closed(self) -> None:
        """Closing the detached window re-attaches the chat to Capri (hidden)."""
        if self._detached:
            self._detached = False
            self.holo.set_content(self.content)
            self.content.set_detached(False)

    def _snap_chat_back(self) -> None:
        """Put the chat panel back next to Capri; it follows her again."""
        self.holo.user_placed = False
        self.holo.place_beside(self.character.char_rect_global(),
                               self.character.current_screen_rect())

    def _follow_character(self) -> None:
        if not self._detached and self.holo.isVisible() and not self.holo.user_placed:
            self.holo.place_beside(self.character.char_rect_global(),
                                   self.character.current_screen_rect())
        if self.bubble.isVisible():
            self.bubble.hide()

    # --------------------------------------------------------- conversations
    def _show_conversation(self) -> None:
        """Rebuild the message view from the current conversation."""
        c = self.content
        c.clear_messages()
        for message in self.conversation.messages:
            if message.role == "user":
                c.add_user_message(message.content)
            elif message.role == "assistant" and message.content.strip():
                c.add_assistant_message(message.content)
            elif message.role == "tool":
                tool = self.registry.get(message.name or "")
                entry = c.add_tool_entry(message.tool_call_id or message.name or "tool",
                                         tool.label if tool else (message.name or "tool"))
                entry.set_result(str(message.meta.get("summary", "")),
                                 str(message.meta.get("details", "")),
                                 bool(message.meta.get("ok", True)))
        c.set_title(self.conversation.title)
        self._refresh_list()

    def _refresh_list(self) -> None:
        items = self.store.list()
        if not any(item[0] == self.conversation.id for item in items):
            items.insert(0, (self.conversation.id, self.conversation.title, time.time()))
        self.content.set_conversations(items, self.conversation.id)

    def _save_conversation(self) -> None:
        self.conversation.auto_title()
        self.store.save(self.conversation)
        self.settings.set("last_conversation", self.conversation.id)
        self.content.set_title(self.conversation.title)

    def _busy_guard(self) -> bool:
        if self.run is not None:
            self.content.add_notice("Let me finish (or press STOP) before switching.", "notice")
            return True
        return False

    def _select_conversation(self, conv_id: str) -> None:
        if conv_id == self.conversation.id or self._busy_guard():
            self._refresh_list()
            return
        loaded = self.store.load(conv_id)
        if loaded:
            self.conversation = loaded
            self.settings.set("last_conversation", loaded.id)
            self._show_conversation()

    def _new_conversation(self) -> None:
        if self._busy_guard():
            return
        self.conversation = self.store.create()
        self._show_conversation()
        self.content.focus_input()

    def _rename_conversation(self, conv_id: str, title: str) -> None:
        if conv_id == self.conversation.id:
            self.conversation.title = title[:80]
            self.store.save(self.conversation)
            self.content.set_title(title)
        else:
            self.store.rename(conv_id, title)
        self._refresh_list()

    def _delete_conversation(self, conv_id: str) -> None:
        if conv_id == self.conversation.id:
            if self._busy_guard():
                return
            self.store.delete(conv_id)
            self.conversation = self.store.create()
            self._show_conversation()
        else:
            self.store.delete(conv_id)
            self._refresh_list()

    # ----------------------------------------------------------------- agent
    def _known_folders(self) -> dict[str, str]:
        out = {}
        for name, location in (("Desktop", QStandardPaths.DesktopLocation),
                               ("Documents", QStandardPaths.DocumentsLocation),
                               ("Downloads", QStandardPaths.DownloadLocation),
                               ("Pictures", QStandardPaths.PicturesLocation)):
            path = QStandardPaths.writableLocation(location)
            if path:
                out[name] = str(Path(path))
        return out

    def _refresh_backend_info(self) -> None:
        key = str(self.settings.get("backend", "ollama"))
        cls = available_backends().get(key)
        if cls is None:
            self.content.set_backend_info("no backend", "Pick a backend in Settings.")
            return
        cfg = self.settings.backend_config(key)
        model = cfg.get("model") or cls.default_model
        self.content.set_backend_info(f"{'local' if cls.is_local else 'cloud'} · {model}",
                                      LOCAL_HINT if cls.is_local else "")

    def send(self, text: str) -> None:
        """Handle a message typed by the user."""
        if self.run is not None:
            return
        self.bubble.hide()
        self.mood.note_interaction("message")
        self.character.wake()
        self.conversation.messages.append(Message("user", text))
        self.content.add_user_message(text)
        self._save_conversation()
        self._refresh_list()

        key = str(self.settings.get("backend", "ollama"))
        try:
            backend = create_backend(key, self.settings.backend_config(key))
        except BackendError as exc:
            self.content.add_notice(str(exc))
            self.character.flash("error", "no backend")
            return
        register_secret(backend.api_key)
        workspace = self.settings.workspace
        context = ToolContext(
            workspace=workspace,
            policy=default_policy(extra_forbidden=[paths.app_data_dir()],
                                  readonly=[paths.project_root()]),
            script_timeout=int(self.settings.get("script_timeout", 60) or 60))
        system = persona.build_system_prompt(workspace, self._known_folders(),
                                             backend.is_local, self.mood.mood)
        run = AgentRun(backend, system, list(self.conversation.messages), self.registry, context,
                       int(self.settings.get("max_tool_rounds", 12) or 12), parent=self)
        run.phase_changed.connect(self._on_phase)
        run.text_delta.connect(self._on_text)
        run.message_ready.connect(self._on_message)
        run.tool_started.connect(self._on_tool_started)
        run.tool_progress.connect(self._on_tool_progress)
        run.tool_finished.connect(self._on_tool_finished)
        run.confirm_requested.connect(self._on_confirm)
        run.failed.connect(self._on_failed)
        run.completed.connect(self._on_completed)
        run.finished.connect(self._on_thread_finished)
        self.run = run
        self._streamed_any = False
        self.content.set_busy(True)
        self.character.set_activity("thinking", "THINKING")
        run.start()

    def stop(self) -> None:
        """Stop generating."""
        if self.run is not None:
            self.run.stop()
            if self.confirm_dialog is not None:
                self.confirm_dialog.cancel()

    def _on_phase(self, phase: str) -> None:
        if phase == "thinking":
            self.character.set_activity("thinking", "THINKING")
        else:
            self.character.set_activity("talking")

    def _on_text(self, delta: str) -> None:
        self._streamed_any = True
        self.content.append_assistant_text(delta)

    def _on_message(self, message: Message) -> None:
        self.conversation.messages.append(message)
        if message.role == "assistant":
            self.content.end_assistant_message()
        self._save_conversation()

    def _on_tool_started(self, call_id: str, name: str, args: dict) -> None:
        self.content.end_assistant_message()
        tool = self.registry.get(name)
        label = tool.label if tool else name
        self.content.add_tool_entry(call_id, label)
        if tool:
            try:
                hud, chip = tool.hud(args), tool.chip(args)
            except Exception:  # malformed arguments from the model
                hud, chip = "WORKING", ""
            self.character.set_activity(tool.state, hud, tool.visual, chip)
        else:
            self.character.set_activity("thinking", "WORKING")

    def _on_tool_progress(self, call_id: str, info: dict) -> None:
        if info.get("domain"):
            self.character.add_ticker(str(info["domain"]))
        if info.get("hud"):
            self.character.set_hud(str(info["hud"]))
        entry = self.content.tool_entry(call_id)
        if entry and info.get("elapsed") is not None:
            entry.set_progress(f"running · {info['elapsed']}s")

    def _on_tool_finished(self, call_id: str, name: str, result: ToolResult) -> None:
        entry = self.content.tool_entry(call_id)
        if entry:
            entry.set_result(result.summary, result.details, result.ok)
        tool = self.registry.get(name)
        denied = "denied" in result.summary
        if not result.ok and not denied:
            self.character.flash("error", (result.error_line or result.summary)[:90])
        elif result.ok and tool and tool.visual == "script":
            self.character.flash("success")
        self.mood.note_interaction("task")
        self.character.set_activity("thinking", "THINKING")

    def _on_failed(self, message: str) -> None:
        self.content.end_assistant_message()
        self.content.add_notice(message)
        self.character.flash("error", message.split(".")[0][:80])
        if not self.chat_visible():
            self._bubble(message[:140], payload="open", color=self.theme.color("error"))

    def _on_completed(self, final_text: str) -> None:
        self.content.end_assistant_message()
        if self.run is not None and self.run.cancel.is_set():
            self.content.add_notice("Stopped.", "notice")
        elif final_text:
            self.character.flash("happy")
            if not self.chat_visible():
                preview = " ".join(final_text.split())
                preview = preview[:150] + ("…" if len(preview) > 150 else "")
                self._bubble(preview, payload="open", timeout=12_000)

    def _on_thread_finished(self) -> None:
        run, self.run = self.run, None
        if run is not None:
            run.deleteLater()
        self.content.set_busy(False)
        self.character.set_activity(None)
        self._save_conversation()
        self._refresh_list()

    # ---------------------------------------------------------- confirmations
    def _on_confirm(self, request: ConfirmRequest) -> None:
        """Show the confirmation dialog and bring Capri next to it."""
        dialog = ConfirmDialog(request, self.theme)
        self.confirm_dialog = dialog
        screen = self.character.current_screen_rect()
        dialog.adjustSize()
        size = dialog.sizeHint().expandedTo(dialog.minimumSize())
        dialog.resize(min(size.width(), 760), min(size.height(), screen.height() - 80))
        dialog.move(screen.center().x() - dialog.width() // 2 + self.character.S // 2,
                    screen.center().y() - dialog.height() // 2)
        dialog.decided.connect(self._confirm_decided)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

        self._return_pos = self.character.char_pos()
        self._return_dock = self.character._dock_edge
        if self._return_dock:
            self._return_pos = self.character._dock_position(False)
        beside = QPoint(dialog.x() - self.character.S - 12,
                        dialog.y() + dialog.height() // 2 - self.character.S // 2)
        beside.setX(max(screen.left() + 4, beside.x()))
        self.character.set_alert(True)
        self.character.rerender_to(beside)
        self.nudge_timer.start()

    def _confirm_decided(self, _approved: bool) -> None:
        self.nudge_timer.stop()
        self.confirm_dialog = None
        self.character.set_alert(False)
        if self._return_pos is not None:
            dock, pos = self._return_dock, self._return_pos

            def restore() -> None:
                if dock:
                    self.character._dock_edge = dock
                    self.character._peek_hidden = False

            self.character.rerender_to(pos, done=restore)
            self._return_pos = None

    def _nudge(self) -> None:
        """One small reminder after 30 seconds; never repeated."""
        if self.confirm_dialog is None:
            return
        self.character.nudge()
        self.confirm_dialog.raise_()
        self._bubble(random.choice(persona.NUDGES), color=self.theme.color("alert"), force=True)

    # --------------------------------------------------------------- bubbles
    def _bubble(self, text: str, payload: str = "", timeout: int = 7000,
                color: str | None = None, force: bool = False) -> None:
        if self.character.quiet and not force and not payload:
            return
        self.bubble.show_text(text, self.character.char_rect_global(),
                              self.character.current_screen_rect(), timeout, payload, color)

    def _bubble_clicked(self, payload: str) -> None:
        if payload == "open":
            self.open_chat()

    def _click_reaction(self, kind: str) -> None:
        lines = persona.CLICK_ANNOYED if kind == "annoyed" else persona.CLICK_PLAYFUL
        color = self.theme.color("error" if kind == "annoyed" else "accent_soft")
        self._bubble(random.choice(lines), timeout=4000, color=color)

    # ------------------------------------------------------------ quiet/mood
    def _toggle_quiet(self, enabled: bool) -> None:
        self.settings.set("quiet_mode", bool(enabled))
        self.settings.save()
        self._apply_quiet()

    def _apply_quiet(self) -> None:
        quiet = bool(self.settings.get("quiet_mode", False)) or self._auto_quiet
        self.character.set_quiet(quiet)
        if quiet:
            self.bubble.hide()

    def _check_fullscreen(self) -> None:
        enabled = bool(self.settings.get("auto_quiet_fullscreen", True))
        fullscreen = enabled and foreground_is_fullscreen()
        if fullscreen != self._auto_quiet:
            self._auto_quiet = fullscreen
            log.info("Fullscreen app %s: automatic quiet mode %s",
                     "detected" if fullscreen else "gone", "on" if fullscreen else "off")
            self._apply_quiet()

    def _every_minute(self) -> None:
        hour = _dt.datetime.now().hour
        session_minutes = (time.time() - self.started) / 60.0
        self.mood.tick(60.0, hour, session_minutes)
        self._save_state()
        self._maybe_remark()

    def _maybe_remark(self) -> None:
        if not self.settings.get("remarks_enabled", True) or self.character.quiet:
            return
        if self.run is not None or self.character.asleep or self.confirm_dialog is not None:
            return
        gap = max(5.0, float(self.settings.get("remarks_interval_minutes", 20) or 20)) * 60
        if time.time() - self.last_remark < gap or random.random() > 0.35:
            return
        self.last_remark = time.time()
        lines = persona.REMARKS.get(self.mood.mood) or persona.REMARKS["content"]
        self._bubble(random.choice(lines), timeout=8000,
                     color=self.theme.mood_tint(self.mood.mood))

    # ----------------------------------------------------------------- misc
    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.theme)
        dialog.setWindowIcon(self.icon)
        if dialog.exec():
            for secret in self.settings.secrets():
                register_secret(secret)
            self.character.apply_settings()
            self.holo.set_always_on_top(bool(self.settings.get("always_on_top", True)))
            self._refresh_backend_info()
            self._apply_quiet()
            log.info("Settings saved. Backend: %s", self.settings.get("backend"))

    def open_model_dialog(self) -> None:
        """Open the Model window; changes apply from the next message."""
        dialog = ModelDialog(self.settings, self.theme)
        dialog.setWindowIcon(self.icon)
        anchor = self.holo.geometry() if self.holo.isVisible() else \
            self.character.char_rect_global()
        dialog.adjustSize()
        screen = self.character.current_screen_rect()
        x = anchor.center().x() - dialog.width() // 2
        y = anchor.center().y() - dialog.height() // 2
        dialog.move(max(screen.left() + 8, min(x, screen.right() - dialog.width() - 8)),
                    max(screen.top() + 8, min(y, screen.bottom() - dialog.height() - 8)))
        if dialog.exec():
            for secret in self.settings.secrets():
                register_secret(secret)
            self._refresh_backend_info()
            key = str(self.settings.get("backend"))
            model = self.settings.backend_config(key).get("model", "")
            log.info("AI model changed: %s / %s", key, model)
            self._bubble(f"New brain online: {model}.", timeout=4000, force=True)
            if self.run is not None:
                self.content.add_notice("The new model will be used from your next message.",
                                        "notice")

    def reload_assets(self) -> None:
        self.character.reload_assets()
        found = len(self.sprites.index.found)
        self._bubble(f"Assets reloaded: {found} state(s) with images."
                     if found else "Assets reloaded. No images yet, so I stay a construct.",
                     timeout=4000, force=True)

    def _shutdown(self) -> None:
        if self.run is not None:
            self.run.stop()
            self.run.wait(3000)
        self.character.save_position()
        self.settings.save()
        self._save_state()
        self.tray.hide()
        log.info("Capri stopped.")


def main() -> int:
    """Program entry point."""
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    lock = QLockFile(str(paths.app_data_dir() / "capri.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Capri", "Capri is already running. Look for her on your "
                                               "desktop or in the system tray.")
        return 0
    try:
        controller = CapriApp(app)  # noqa: F841 (keeps everything alive)
        return app.exec()
    except Exception:
        log.exception("Fatal error")
        raise
    finally:
        lock.unlock()
