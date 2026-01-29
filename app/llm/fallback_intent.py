from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# NOTE: Ideally import this from a single source of truth, e.g.
# from app.mapping.hscode import HS_CODE_MAP
HS_CODE_MAP = {
    "нүүрс": ["2701", "2702"],
    "зэс": ["2603"],
    "төмөр": ["2601"],
    "газрын тос": ["2709"],
}

# Category keywords -> which field to filter (for v_import_monthly_category)
# We keep values short (e.g. "Тамхи") and expect builder.py to use ILIKE '%...%'
CATEGORY_KEYWORDS: Dict[str, str] = {
    # sub3
    "тамхи": "sub3",
    "суудлын автомашин": "sub3",

    # sub2
    "хүнс": "sub2",
    "автобензин": "sub2",

    # sub1
    "түргэн эдэлгээтэй": "sub1",

    # purpose
    "хэрэглээний бүтээгдэхүүн": "purpose",
}


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def _find_year_month(q: str) -> tuple[Optional[int], Optional[int]]:
    """
    Returns (year, month)
    - "2025 оны 12 сар"
    - "2025 12"
    - "2025"  -> (2025, None)  ✅ clarify answer
    """
    # 2025 оны 12 сар / 2025 ... 12 ... сар
    m = re.search(r"(20\d{2})\D+(\d{1,2})\D*сар", q)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 2025 12 (month validation)
    m = re.search(r"\b(20\d{2})\D+(\d{1,2})\b", q)
    if m:
        y, mm = int(m.group(1)), int(m.group(2))
        if 1 <= mm <= 12:
            return y, mm

    # ✅ single year only (e.g. "2025")
    m = re.search(r"\b(20\d{2})\b", q)
    if m:
        return int(m.group(1)), None

    return None, None


def _find_years_list(q: str) -> Optional[List[int]]:
    """
    Find multi-year requests:
    - "2024, 2025"
    - "2024-2025" / "2024–2025"
    - If 2+ distinct years found, return sorted list.
    """
    qn = _norm(q)

    # range: 2024-2025 or 2024–2025
    m = re.search(r"\b(20\d{2})\s*[-–]\s*(20\d{2})\b", qn)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        if y1 > y2:
            y1, y2 = y2, y1
        return list(range(y1, y2 + 1))

    # list: pick all years
    years = [int(x) for x in re.findall(r"\b(20\d{2})\b", qn)]
    years = sorted(set(years))
    if len(years) >= 2:
        return years

    return None


def _infer_category_filters(question: str) -> Dict[str, str]:
    qn = _norm(question)
    out: Dict[str, str] = {}
    for kw, field in CATEGORY_KEYWORDS.items():
        if kw in qn:
            out[field] = kw
    return out


def _infer_hscode(question: str) -> Optional[List[str]]:
    qn = _norm(question)

    # user typed 4-digit codes; exclude year-like numbers (e.g., 2000–2030)
    m = re.findall(r"\b(\d{4})\b", qn)
    if m:
        hs: List[str] = []
        for s in m:
            n = int(s)
            if 2000 <= n <= 2030:
                continue
            hs.append(s)
        if hs:
            return hs

    # keyword mapping
    for k, v in HS_CODE_MAP.items():
        if k in qn:
            return v

    return None


def _get_prev_domain(prev_state: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(prev_state, dict):
        return None
    d = prev_state.get("domain")
    if d in ("import", "export"):
        return d
    return None


def build_intent_fallback(
    question: str, prev_state: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    q = _norm(question)
    prev_domain = _get_prev_domain(prev_state)

    # Category filters first (avoid HS over-broad grouping cases like 2710)
    filters: Dict[str, Any] = {}
    cat_filters = _infer_category_filters(question)
    if cat_filters:
        filters.update(cat_filters)

    # ✅ domain (robust)
    # 1) explicit keyword wins
    if "импорт" in q:
        domain = "import"
    elif "экспорт" in q:
        domain = "export"
    else:
        # 2) if category keyword matched → it is import-category vocabulary in your system
        #    (this avoids "2025" turning into export after clarification)
        if cat_filters:
            domain = "import"
        else:
            # 3) keep previous domain
            domain = prev_domain or "export"

    # metric + calc
    if "нэгж" in q or "нэгж үнэ" in q or "дундаж үнэ" in q or "unit price" in q:
        metric = "weighted_price"
        calc = "weighted_price"
    elif "тонн" in q or "тоо хэмжээ" in q or "хэмжээ" in q:
        metric = "quantity"
        calc = "month_value"
    else:
        metric = "amountUSD"
        calc = "month_value"

    # ✅ timeseries_year heuristic (only when explicit multi-year is present)
    years_list = _find_years_list(question)
    if years_list:
        calc = "timeseries_year"
        time: Any = {"years": years_list}
    else:
        # time (single month/year/latest)
        y, m = _find_year_month(question)
        if y and m:
            time = {"year": y, "month": m}
        elif y:
            time = {"year": y}
            # ✅ year-only: default to monthly series (more reliable expectation)
            if calc == "month_value":
                calc = "timeseries_month"
        else:
            time = "latest"

    # If no category filter matched, infer HS code
    if not cat_filters:
        hs = _infer_hscode(question)
        if hs:
            filters["hscode"] = hs

    return {
        "domain": domain,
        "calc": calc,
        "metric": metric,
        "time": time,
        "filters": filters,
        "topn": 50,
    }