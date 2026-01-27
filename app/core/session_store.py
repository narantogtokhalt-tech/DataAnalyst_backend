from __future__ import annotations

import time
from typing import Dict, Tuple

from app.conversation.models import ConversationState


class InMemorySessionStore:
    def __init__(self, ttl_seconds: int = 6 * 60 * 60):
        self.ttl = ttl_seconds
        self._data: Dict[str, Tuple[float, ConversationState]] = {}

    def get(self, session_id: str) -> ConversationState:
        session_id = session_id or "default"
        now = time.time()

        item = self._data.get(session_id)
        if not item:
            return ConversationState()

        ts, state = item
        if now - ts > self.ttl:
            self._data.pop(session_id, None)
            return ConversationState()

        return state

    def set(self, session_id: str, state: ConversationState) -> None:
        session_id = session_id or "default"
        self._data[session_id] = (time.time(), state)