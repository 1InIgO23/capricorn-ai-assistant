"""Conversations stored as one JSON file each in the app data folder."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..llm.base import Message

log = logging.getLogger(__name__)


@dataclass
class Conversation:
    """A persistent chat."""

    id: str
    title: str = "New conversation"
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    messages: list[Message] = field(default_factory=list)

    def auto_title(self) -> None:
        """Name the conversation after its first user message."""
        if self.title != "New conversation":
            return
        for message in self.messages:
            if message.role == "user" and message.content.strip():
                text = " ".join(message.content.split())
                self.title = text[:42] + ("…" if len(text) > 42 else "")
                return


class ConversationStore:
    """Create, list, load, save, rename and delete conversations."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, conv_id: str) -> Path:
        safe = "".join(ch for ch in conv_id if ch.isalnum())
        return self.directory / f"{safe}.json"

    def create(self) -> Conversation:
        return Conversation(id=uuid.uuid4().hex)

    def list(self) -> list[tuple[str, str, float]]:
        """``(id, title, updated)`` tuples, most recent first."""
        out = []
        for file in self.directory.glob("*.json"):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                out.append((str(data["id"]), str(data.get("title") or "Untitled"),
                            float(data.get("updated", 0))))
            except (OSError, ValueError, KeyError):
                log.warning("Skipping unreadable conversation file %s", file.name)
        return sorted(out, key=lambda item: item[2], reverse=True)

    def load(self, conv_id: str) -> Conversation | None:
        path = self._path(conv_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Conversation(
                id=str(data["id"]), title=str(data.get("title") or "Untitled"),
                created=float(data.get("created", time.time())),
                updated=float(data.get("updated", time.time())),
                messages=[Message.from_dict(m) for m in data.get("messages", [])])
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Could not load conversation %s: %s", conv_id, exc)
            return None

    def save(self, conversation: Conversation) -> None:
        conversation.updated = time.time()
        data = {"id": conversation.id, "title": conversation.title,
                "created": conversation.created, "updated": conversation.updated,
                "messages": [m.to_dict() for m in conversation.messages]}
        path = self._path(conversation.id)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.error("Could not save conversation: %s", exc)

    def rename(self, conv_id: str, title: str) -> None:
        conversation = self.load(conv_id)
        if conversation and title.strip():
            conversation.title = title.strip()[:80]
            updated = conversation.updated
            self.save(conversation)
            conversation.updated = updated

    def delete(self, conv_id: str) -> None:
        try:
            self._path(conv_id).unlink(missing_ok=True)
        except OSError as exc:
            log.error("Could not delete conversation: %s", exc)
