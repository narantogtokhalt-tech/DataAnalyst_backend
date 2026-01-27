from __future__ import annotations
import re
from typing import Dict, Any

def detect_followup(text: str) -> Dict[str, Any]:
    """
    Follow-up intent detector.
    Previous conversation state дээр override хийх утгуудыг буцаана.
    """
    t = (text or "").strip().casefold()
    out: Dict[str, Any] = {}

    # -------- granularity --------
    if re.search(r"(сар\s*бүр|сараар|month)", t):
        out["granularity"] = "month"
    elif re.search(r"(жилээр|он\s*бүр|year)", t):
        out["granularity"] = "year"

    # -------- scale --------
    if re.search(r"\bсая\b", t):
        out["scale_label"] = "сая"
    elif re.search(r"\bмянга\b|\bмянган\b", t):
        out["scale_label"] = "мянга"

    # -------- metric --------
    if re.search(r"(тоо\s*хэмжээ|хэмжээ|тонн|kg|кг)", t):
        out["metric"] = "quantity"
    elif re.search(r"(дүн|үнэ|ам\.?доллар|usd|\$)", t):
        out["metric"] = "amountUSD"

    # -------- compare --------
    if re.search(r"(харьцуул|compare|өмнөх\s+он|өнгөрсөн\s+он)", t):
        out["compare_prev_year"] = True

    return out