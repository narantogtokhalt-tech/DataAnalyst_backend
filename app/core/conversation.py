from __future__ import annotations
from typing import Any, Dict, Optional, List
from app.models.conversation import ConversationState, Commodity

def merge_state(prev: ConversationState, intent: Dict[str, Any], overrides: Dict[str, Any]) -> ConversationState:
    s = prev.model_copy(deep=True)

    # intent базууд
    if intent.get("domain"):
        s.domain = intent["domain"]
    if intent.get("metric"):
        s.metric = intent["metric"]

    # time merge
    time = intent.get("time") or {}
    if isinstance(time, dict):
        if time.get("year") is not None:
            s.time.year = int(time["year"])
        if time.get("years"):
            s.time.years = [int(y) for y in time["years"]]

    # filters merge
    filters = intent.get("filters") or {}
    if isinstance(filters, dict) and filters:
        s.filters.update(filters)

    # commodity (hscode)
    hs = filters.get("hscode")
    if isinstance(hs, list) and hs:
        hs = [str(x) for x in hs]
        # label mapping-ийг дараа mapping/ дээрээс татаж болно
        s.commodity = Commodity(label="HS " + ", ".join(hs), hscode=hs)

    # overrides (follow-up)
    if overrides.get("granularity"):
        s.time.granularity = overrides["granularity"]
    if overrides.get("scale_label"):
        s.scale_label = overrides["scale_label"]
    if overrides.get("metric"):
        s.metric = overrides["metric"]

    return s

def apply_compare_prev_year(s: ConversationState) -> ConversationState:
    out = s.model_copy(deep=True)
    if out.time.year and not out.time.years:
        out.time.years = [out.time.year - 1, out.time.year]
    return out

def needs_clarification(s: ConversationState) -> Optional[Dict[str, Any]]:
    # metric дутуу
    if not s.metric:
        return {
            "question": "Ямар үзүүлэлтээр авах вэ?",
            "choices": [
                {"label": "Үнийн дүн (ам.доллар)", "prompt": "үнийн дүнгээр нь"},
                {"label": "Тоо хэмжээ (тонн)", "prompt": "тоо хэмжээгээр нь"},
            ],
        }

    # time дутуу (year/years)
    if not (s.time.year or s.time.years):
        return {
            "question": "Аль оны мэдээлэл авах вэ?",
            "choices": [
                {"label": "2025", "prompt": "2025 он"},
                {"label": "2024", "prompt": "2024 он"},
                {"label": "2024 vs 2025", "prompt": "2024, 2025-ыг харьцуул"},
            ],
        }

    # domain дутуу
    if not s.domain:
        return {
            "question": "Экспорт уу, импорт уу?",
            "choices": [
                {"label": "Экспорт", "prompt": "экспорт"},
                {"label": "Импорт", "prompt": "импорт"},
            ],
        }

    return None

def build_suggestions(s: ConversationState) -> List[Dict[str, str]]:
    sug: List[Dict[str, str]] = []

    if s.time.granularity != "month":
        sug.append({"label": "Сар бүрээр", "prompt": "сар бүрээр нь"})
    if s.time.granularity != "year":
        sug.append({"label": "Жилээр", "prompt": "жилээр нь"})

    if s.time.year and not s.time.years:
        sug.append({"label": "Өмнөх онтой харьцуулах", "prompt": "өмнөх онтой харьцуул"})

    # scale toggle suggestions
    if s.scale_label != "сая":
        sug.append({"label": "Сая нэгжээр", "prompt": "сая нэгжээр"})
    if s.scale_label != "мянга":
        sug.append({"label": "Мянга нэгжээр", "prompt": "мянга нэгжээр"})

    # metric switch
    if s.metric != "amountUSD":
        sug.append({"label": "Үнийн дүн (USD)", "prompt": "үнийн дүнгээр нь"})
    if s.metric != "quantity":
        sug.append({"label": "Тоо хэмжээ (тонн)", "prompt": "тоо хэмжээгээр нь"})

    return sug[:6]