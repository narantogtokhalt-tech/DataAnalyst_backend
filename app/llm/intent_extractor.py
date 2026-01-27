from __future__ import annotations
from typing import Any, Dict

from app.llm.client import llm_json
from app.llm.prompt import build_intent_prompt

def extract_intent(q: str) -> Dict[str, Any]:
    try:
        return llm_json(build_intent_prompt(q)) or {}
    except Exception:
        return {}