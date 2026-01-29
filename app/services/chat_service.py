from __future__ import annotations

from typing import Any, Dict

from app.core.session_store import InMemorySessionStore

from app.conversation.models import ConversationState, Intent as IntentModel
from app.conversation.merge import merge_intent, apply_compare_prev_year
from app.conversation.clarify import needs_clarification
from app.conversation.suggest import build_suggestions

from app.llm.followup_detector import detect_followup
from app.llm.intent_extractor import sanitize_intent

# ✅ robust fallback intent (no LLM required)
from app.llm.fallback_intent import build_intent_fallback

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
    q_raw = (message or "").strip()

    prev: ConversationState = store.get(sid)

    # 0) Empty question -> ask user
    if not q_raw:
        store.set(sid, prev)
        return {
            "mode": "clarify",
            "answer": "Асуултаа бичнэ үү.",
            "meta": {
                "needs_clarification": True,
                "choices": [],
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

    # ---------------------------------------------------------
    # ✅ Clarify answer handling: ALWAYS merge with pending_question
    # ---------------------------------------------------------
    awaiting = bool(getattr(prev, "awaiting_clarification", False))
    pending_q = getattr(prev, "pending_question", None)

    # If we are awaiting clarification, treat this message as answer
    if awaiting and pending_q:
        q_final = f"{pending_q} {q_raw}".strip()
    else:
        q_final = q_raw

    # ✅ prev intent snapshot (for fallback context)
    prev_intent: Dict[str, Any] = {}
    try:
        prev_intent = prev.to_intent() if prev and hasattr(prev, "to_intent") else {}
    except Exception:
        prev_intent = {}

    # 1) intent (LLM schema dict) + fallback
    intent_dict: Dict[str, Any] = {}
    if extract_intent is not None:
        try:
            intent_dict = extract_intent(q_final) or {}
            intent_dict = sanitize_intent(intent_dict, q_final)
        except Exception:
            # ✅ LLM extractor failed -> fallback with prev_state
            intent_dict = build_intent_fallback(q_final, prev_state=prev_intent)
            intent_dict = sanitize_intent(intent_dict, q_final)
    else:
        # ✅ extractor not available -> always fallback with prev_state
        intent_dict = build_intent_fallback(q_final, prev_state=prev_intent)
        intent_dict = sanitize_intent(intent_dict, q_final)

    # 2) Follow-up overrides
    overrides: Dict[str, Any] = {}
    try:
        overrides = detect_followup(q_final) or {}
    except Exception:
        overrides = {}

    # 3) dict -> IntentModel (safe)
    try:
        intent_model = IntentModel.model_validate(intent_dict)
    except Exception:
        intent_model = IntentModel()

    # 4) merge state
    state = merge_intent(prev, intent_model, overrides)

    # 5) If we just consumed a pending clarify, clear pending flags NOW
    if awaiting:
        state.awaiting_clarification = False
        state.pending_question = None
        state.pending_clarify = None

    # 6) compare prev year
    if overrides.get("compare_prev_year"):
        state = apply_compare_prev_year(state)

    # 7) clarification?
    clar = needs_clarification(state)
    if clar:
        # ✅ Enter clarify mode: store what question we’re clarifying for
        # If we were already clarifying, keep the original pending question
        base_q = pending_q if awaiting and pending_q else q_raw

        state.awaiting_clarification = True
        state.pending_question = base_q
        state.pending_clarify = clar

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
                # ✅ debug helpers (remove later if you want)
                "pending_question": base_q,
                "q_final": q_final,
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
            # ✅ debug helpers
            "q_final": q_final,
        },
        "result": None,
        "state": state,
        "intent": intent_dict,
        "overrides": overrides,
    }