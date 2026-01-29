# D:\DataAnalystBot\app\api\chat.py
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db

from app.llm.client import llm_text

from app.sql.builder import build_sql
from app.models.intent import ChatRequest

# ✅ conversation pre-processor (state merge + clarify + suggestions)
from app.services.chat_service import handle_chat
from app.analytics.query_log import log_query


router = APIRouter()


async def require_key(x_api_key: Optional[str] = Header(None)) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _unit(metric: str) -> str:
    if metric == "amountUSD":
        return "ам.доллар"
    if metric == "quantity":
        return "тонн"
    return "ам.доллар/тонн"

def _metric_label(metric: str) -> str:
    if metric == "amountUSD":
        return "үнийн дүн"
    if metric == "quantity":
        return "тоо хэмжээ"
    return "нэгж үнэ"


def _domain_label(domain: str) -> str:
    return "импорт" if domain == "import" else "экспорт"


def _filters_summary(intent: Dict[str, Any]) -> str:
    filters = (intent or {}).get("filters") or {}
    parts = []

    # HS
    hs = filters.get("hscode")
    if isinstance(hs, list) and hs:
        parts.append(f"HS {', '.join(map(str, hs[:6]))}" + ("…" if len(hs) > 6 else ""))
    elif isinstance(hs, str) and hs:
        parts.append(f"HS {hs}")

    # category fields
    for k, label in [("purpose", "purpose"), ("sub1", "sub1"), ("sub2", "sub2"), ("sub3", "sub3")]:
        v = filters.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(f"{label}~{v.strip()}")

    # country/senderReceiver/customs/company (optional)
    for k in ("country", "senderReceiver", "customs", "company"):
        v = filters.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(f"{k}~{v.strip()}")

    return " • " + ", ".join(parts) if parts else ""

def _scale_info(metric: str) -> dict:
    # Chart/Table дээр default scale
    if metric == "amountUSD":
        return {"scale": 1_000_000.0, "scale_label": "сая"}  # USD -> сая
    if metric == "quantity":
        return {"scale": 1_000.0, "scale_label": "мянга"}  # тонн -> мянга (toggle-оор сольж болно)
    return {"scale": 1.0, "scale_label": ""}  # weighted_price: scale хийхгүй


def _format_value(x: Any, metric: str) -> str:
    if x is None:
        return "—"

    try:
        v = float(x)
    except Exception:
        return str(x)

    u = _unit(metric)

    # weighted_price: no scaling, show 2 decimals
    if metric == "weighted_price":
        return f"{v:,.2f} {u}"

    scale_meta = _scale_info(metric)
    sc = float(scale_meta.get("scale", 1.0) or 1.0)
    label = scale_meta.get("scale_label", "")

    vv = v / sc if sc else v
    if label:
        return f"{vv:,.2f} {label} {u}"
    return f"{vv:,.2f} {u}"


def _looks_analytic(q: str) -> bool:
    t = q.strip().casefold()
    keys = [
        "экспорт", "импорт", "дүн", "хэмжээ", "тонн", "usd", "ам.доллар",
        "өмнөх", "мөн үе", "өссөн", "сар", "он", "сар сараар", "дундаж", "yoy",
    ]
    return any(k in t for k in keys) or any(ch.isdigit() for ch in t)

def _infer_domain_from_text(q: str) -> Optional[str]:
    t = (q or "").strip().casefold()
    # хамгийн тод keyword-ууд
    if "импорт" in t:
        return "import"
    if "экспорт" in t:
        return "export"
    return None


def canonicalize_intent(intent: Dict[str, Any], state: Any, q: str) -> Dict[str, Any]:
    """
    ✅ intent/state/асуултын текст 3-аас хамгийн итгэлтэйг нь сонгож domain-оо тогтооно.
    - Хэрвээ user асуултанд импорт/экспорт ил байвал тэр нь ялана.
    - Үгүй бол state.domain
    - Үгүй бол intent.domain
    """
    out = dict(intent or {})

    q_domain = _infer_domain_from_text(q)
    state_domain = getattr(state, "domain", None) if state is not None else None
    intent_domain = out.get("domain")

    domain = q_domain or state_domain or intent_domain or "export"
    out["domain"] = domain

    # metric fallback (optional)
    if getattr(state, "metric", None) and not out.get("metric"):
        out["metric"] = state.metric

    return out

def _infer_period(calc: str, time_field: Any) -> str:
    if calc in ("timeseries_month",):
        return "series_month"
    if calc in ("timeseries_year",):
        return "series_year"
    if calc in ("ytd", "year_total", "avg_years"):
        return "year"
    return "month"


def _normalize_value_result(
    calc: str, rows: list[Dict[str, Any]]
) -> Tuple[Dict[str, Any], Optional[str]]:
    if not rows:
        return {"value": None}, "no_data"

    r0 = rows[0]

    if calc == "yoy":
        return {
            "current": r0.get("current"),
            "previous": r0.get("previous"),
            "pct": r0.get("pct"),
        }, None

    if calc == "timeseries_month":
        series = []
        for x in rows:
            try:
                yy = int(x.get("year")) if x.get("year") is not None else None
            except Exception:
                yy = None
            try:
                mm = int(x.get("month")) if x.get("month") is not None else None
            except Exception:
                mm = None

            y_str = str(yy) if yy is not None else ""
            m_str = f"{mm:02d}" if mm is not None else ""

            series.append(
                {
                    # ✅ string болгосон (front хүснэгтэнд 2024.00 болохгүй)
                    "year": y_str,
                    "month": m_str,
                    "label": f"{y_str}-{m_str}" if y_str and m_str else (x.get("label") or ""),
                    "value": x.get("value"),
                }
            )

        return {"series": series}, None

    if calc == "timeseries_year":
        series = []
        for x in rows:
            try:
                yy = int(x.get("year")) if x.get("year") is not None else None
            except Exception:
                yy = None
            y_str = str(yy) if yy is not None else ""

            series.append(
                {
                    # ✅ string болгосон
                    "year": y_str,
                    "label": y_str or str(x.get("year") or ""),
                    "value": x.get("value"),
                }
            )
        return {"series": series}, None

    return {"value": r0.get("value")}, None


def sync_intent_from_state(intent: dict, state: Any) -> dict:
    """
    ✅ Single source of truth:
    SQL intent always comes from ConversationState.to_intent()
    """
    if state and hasattr(state, "to_intent"):
        return state.to_intent()
    return intent or {}

@router.get("/health")
async def health():
    return {"ok": True}


@router.post("/chat")
async def chat(
    body: ChatRequest,
    dep: None = Depends(require_key),
    db: AsyncSession = Depends(get_db),
):
    q = (body.message or "").strip()
    if not q:
        return {"answer": "Асуултаа бичнэ үү.", "meta": {}, "result": None}

    # 0) Smalltalk / General knowledge
    if not _looks_analytic(q):
        prompt = f"Та Монгол хэл дээр ярьдаг туслах. Найрсаг, товч хариул.\nАсуулт: {q}"
        return {"answer": llm_text(prompt), "meta": {"intent": None}, "result": None}

    session_id = getattr(body, "session_id", None) or "default"

    # ✅ 1) Conversation layer (state merge + clarify + suggestions)
    convo = handle_chat(q, session_id)

    if convo.get("mode") == "clarify":
        return {
            "answer": convo.get("answer"),
            "meta": convo.get("meta"),
            "result": None,
        }

    state = convo.get("state")
    overrides = convo.get("overrides") or {}
    raw_intent = convo.get("intent") or {}  # debug only

    # ✅ SINGLE SOURCE OF TRUTH
    intent = state.to_intent() if state else {}
    intent = canonicalize_intent(intent, state, q)

    # 1) SQL build + execute (✅ once)
    sql, params, sql_meta = build_sql(intent, q)
    r = await db.execute(sql, params)
    rows = [dict(x) for x in r.mappings().all()][:500]

    # ✅ IMPORTANT: use sql_meta overrides
    calc = sql_meta.get("calc") or intent.get("calc") or "month_value"
    metric = sql_meta.get("metric") or intent.get("metric") or "amountUSD"
    domain = sql_meta.get("domain") or intent.get("domain") or "export"

    # 3) Normalize
    normalized, err_code = _normalize_value_result(calc, rows)

    # ✅ LOG HERE (rows + err_code бэлэн болсон яг энэ цэг)
    log_query({
        "question": q,
        "intent": intent,
        "view": sql_meta.get("view"),
        "view_type": sql_meta.get("view_type"),
        "calc": sql_meta.get("calc"),
        "row_count": len(rows),
        "status": ("no_data" if err_code == "no_data" else "success"),
    })

    unit = _unit(metric)
    period = _infer_period(calc, intent.get("time"))

    # display (UI)
    if calc == "yoy":
        display = {
            "current": _format_value(normalized.get("current"), metric),
            "previous": _format_value(normalized.get("previous"), metric),
            "pct": "—"
            if normalized.get("pct") is None
            else f"{float(normalized['pct']):.2f}%",
        }
    elif calc in ("timeseries_month", "timeseries_year"):
        display = None
    else:
        display = _format_value(normalized.get("value"), metric)

    scale_meta = _scale_info(metric)

    result_contract: Dict[str, Any] = {
        **normalized,
        "display": display,
        "unit": unit,
        "period": period,
        **scale_meta,
    }

    # ✅ If no data, return a clean answer + extra suggestions (LLM explanation skip)
    if err_code == "no_data":
        meta = convo.get("meta", {}) or {}
        # нэмэлт UX suggestions
        extra = [
            {"label": "Хугацаагаа өөрчлөх", "prompt": "2024, 2025 оныг жилээр хүснэгтээр"},
            {"label": "Сараар харах", "prompt": "2025 оны сар бүрээр хүснэгтээр"},
            {"label": "Шүүлт сулруулах", "prompt": "HS код/ангилалгүйгээр нийт дүнг харуул"},
        ]
        # merge suggestions (хэрвээ meta.suggestions байхгүй бол)
        existing = meta.get("suggestions") or []

        # label+prompt-оор unique болгоно
        seen = {(s.get("label"), s.get("prompt")) for s in existing}
        for s in extra:
            key = (s["label"], s["prompt"])
            if key not in seen:
                existing.append(s)
                seen.add(key)

        meta["suggestions"] = existing

        meta.update(
            {
                "intent": intent,  # ✅ final intent used by SQL
                "intent_raw": raw_intent,  # ✅ optional debug
                "sql_meta": sql_meta,
                "overrides": overrides,
            }
        )

        return {
            "answer": "Өгөгдөл олдсонгүй. Хугацаа/ангилал/шүүлтээ өөрчлөөд дахин оролдоорой.",
            "meta": meta,
            "result": result_contract,
        }

    if err_code:
        result_contract["warning"] = err_code

    # ✅ add scaled values (for charts/tables)
    try:
        sc = float(result_contract.get("scale", 1.0) or 1.0)
    except Exception:
        sc = 1.0

    if "value" in result_contract and result_contract["value"] is not None:
        try:
            result_contract["value_scaled"] = float(result_contract["value"]) / sc
        except Exception:
            result_contract["value_scaled"] = None

    if "series" in result_contract and isinstance(result_contract["series"], list):
        for p in result_contract["series"]:
            if p.get("value") is None:
                p["value_scaled"] = None
            else:
                try:
                    p["value_scaled"] = float(p["value"]) / sc
                except Exception:
                    p["value_scaled"] = None

    # 4) LLM explanation (optional, safe json)
    explain_payload = {
        "question": q,
        "intent": intent,
        "overrides": overrides,
        "sql_meta": sql_meta,
        "result": result_contract,
        "rows_preview": rows[:20],
        "state": (state.model_dump() if hasattr(state, "model_dump") else None),
    }
    domain_label = _domain_label(domain)
    metric_label = _metric_label(metric)
    filters_summary = _filters_summary(intent)

    explain_prompt = f"""
    Та Монгол хэлээр хариулна. Доорх JSON-д байгаа тоо, огноо, шүүлтээс ӨӨР ЮМ БҮҮ ЗОХИО.
    Зөвхөн JSON-д байгаа мэдээлэл дээр тулгуурлан 2–5 өгүүлбэрээр тайлбарла.

    Шаардлага:
    - domain: "{domain_label}" гэдгийг ашигла
    - metric: "{metric_label}" гэдгийг ашигла
    - Хэрвээ result.warning == "no_data" бол: "Өгөгдөл олдсонгүй" гэж нэг өгүүлбэр бичээд зогс.
    - timeseries (series) бол: "Хүснэгт/цуваа гаргалаа" + хамгийн эхний ба сүүлийн утгыг л дурд (байвал)
    - single value бол: display-г нэг өгүүлбэрт тодорхой хэл
    - Шүүлтүүд байвал нэг мөрөөр {filters_summary} байдлаар дурд
    - Тоог таслалтай, 2 орны нарийвчлалтай бич (display байгаа бол display-г тэр чигт нь ашигла)

    JSON:
    {json.dumps(explain_payload, ensure_ascii=False, default=str)}
    """.strip()

    explanation = llm_text(explain_prompt).strip()

    # fallback base answer
    if not explanation:
        flt = _filters_summary(intent)
        dom = _domain_label(domain)
        met = _metric_label(metric)

        if err_code == "no_data":
            explanation = "Өгөгдөл олдсонгүй. Хугацаа/ангилал/шүүлтээ өөрчлөөд дахин оролдоорой."
        elif calc == "yoy":
            pct = normalized.get("pct")
            trend = "—"
            if pct is not None:
                trend = "өссөн" if pct > 0 else ("буурсан" if pct < 0 else "өөрчлөлтгүй")
            explanation = (
                f"{dom} • {met}{flt}: "
                f"Одоогийн={display['current']}, Өмнөх={display['previous']}, "
                f"Өөрчлөлт={display['pct']} ({trend})"
            )
        elif calc in ("timeseries_month", "timeseries_year"):
            series = normalized.get("series") or []
            if series:
                first = series[0]
                last = series[-1]
                # display байхгүй үед raw value дээр scale ашиглан format хийхгүй, богино үлдээнэ
                explanation = (
                    f"{dom} • {met}{flt}: хүснэгт/цуваа гаргалаа. "
                    f"Эхлэл {first.get('label')}: {first.get('value_scaled')}, "
                    f"Сүүл {last.get('label')}: {last.get('value_scaled')}."
                )
            else:
                explanation = f"{dom} • {met}{flt}: хүснэгт/цуваа гаргалаа."
        else:
            explanation = f"{dom} • {met}{flt}: {display}"

    # suggestions/state meta from convo
    meta = convo.get("meta", {}) or {}
    meta.update({
        "intent": intent,  # ✅ FINAL SQL INTENT
        "intent_raw": raw_intent,  # debug
        "sql_meta": sql_meta,
        "overrides": overrides,
    })

    return {
        "answer": explanation,
        "meta": meta,
        "result": result_contract,
    }

