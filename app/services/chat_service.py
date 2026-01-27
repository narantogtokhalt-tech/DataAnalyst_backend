from __future__ import annotations

from typing import Any, Dict

from app.core.session_store import InMemorySessionStore

# conversation modules (folder: app/conversation/)
from app.conversation.models import ConversationState, Intent as IntentModel
from app.conversation.merge import merge_intent, apply_compare_prev_year
from app.conversation.clarify import needs_clarification
from app.conversation.suggest import build_suggestions

# llm helpers
from app.llm.followup_detector import detect_followup

# intent extractor (dict intent буцаана)
try:
    from app.llm.intent_extractor import extract_intent  # type: ignore
except Exception:
    extract_intent = None  # type: ignore


store = InMemorySessionStore()


def handle_chat(message: str, session_id: str) -> Dict[str, Any]:
    """
    Conversation layer only:
    - session_id -> state load/store
    - intent + follow-up overrides -> merge state
    - clarification decision
    - suggestions
    Энэ функц SQL ажиллуулахгүй.
    """
    sid = (session_id or "default").strip() or "default"
    q = (message or "").strip()

    prev: ConversationState = store.get(sid)

    # 0) Empty question -> ask user
    if not q:
        clar = {
            "question": "Асуултаа бичнэ үү.",
            "choices": [],
        }
        store.set(sid, prev)
        return {
            "mode": "clarify",
            "answer": clar["question"],
            "meta": {
                "needs_clarification": True,
                "choices": clar.get("choices", []),
                "suggestions": build_suggestions(prev),
                "state": prev.model_dump(),
                "intent": {},
                "overrides": {},
            },
            "result": None,
            "state": prev,
            "intent": {},
            "overrides": {},
        }

    # 1) intent (LLM schema dict) + follow-up overrides
    intent_dict: Dict[str, Any] = {}
    if extract_intent is not None:
        try:
            intent_dict = extract_intent(q) or {}
        except Exception:
            intent_dict = {}
    overrides: Dict[str, Any] = {}
    try:
        overrides = detect_followup(q) or {}
    except Exception:
        overrides = {}

    # 2) dict -> IntentModel (safe)
    try:
        intent_model = IntentModel.model_validate(intent_dict)
    except Exception:
        intent_model = IntentModel()

    # 3) merge state
    state = merge_intent(prev, intent_model, overrides)

    # 4) compare prev year
    if overrides.get("compare_prev_year"):
        state = apply_compare_prev_year(state)

    # 5) clarification?
    clar = needs_clarification(state)
    if clar:
        store.set(sid, state)
        return {
            "mode": "clarify",
            "answer": clar["question"],
            "meta": {
                "needs_clarification": True,
                "choices": clar.get("choices", []),
                "suggestions": build_suggestions(state),
                "state": state.model_dump(),
                "intent": intent_dict,
                "overrides": overrides,
            },
            "result": None,
            "state": state,
            "intent": intent_dict,
            "overrides": overrides,
        }

    # ✅ ready
    store.set(sid, state)
    return {
        "mode": "ready",
        "answer": "",
        "meta": {
            "needs_clarification": False,
            "suggestions": build_suggestions(state),
            "state": state.model_dump(),
            "intent": intent_dict,
            "overrides": overrides,
        },
        "result": None,
        "state": state,
        "intent": intent_dict,
        "overrides": overrides,
    }