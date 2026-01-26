from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from jsonschema import validate, ValidationError

from app.core.config import settings
from app.core.database import get_db
from app.llm.client import llm_json, llm_text
from app.llm.prompt import build_intent_prompt
from app.llm.intent_schema import INTENT_SCHEMA
from app.sql.builder import build_sql
from app.models.intent import ChatRequest
from google.genai import errors as genai_errors
from app.llm.fallback_intent import build_intent_fallback

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

def _scale_info(metric: str) -> dict:
    # Chart дээр default scale
    if metric == "amountUSD":
        return {"scale": 1_000_000.0, "scale_label": "сая"}  # USD -> сая
    if metric == "quantity":
        return {"scale": 1_000.0, "scale_label": "мянга"}   # тонн -> мянга (хүсвэл 1 болгож болно)
    # weighted_price: scale хийхгүй
    return {"scale": 1.0, "scale_label": ""}

def _format_value(x: Any, metric: str) -> str:
    """
    Display formatting aligned with _scale_info()
    - amountUSD: "сая ам.доллар"
    - quantity: "мянга тонн" (эсвэл scale=1 бол "тонн")
    - weighted_price: "ам.доллар/тонн" (no scaling)
    """
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

    # label байвал "сая ам.доллар", "мянга тонн" гэх мэт
    if label:
        return f"{vv:,.2f} {label} {u}"
    return f"{vv:,.2f} {u}"

def _looks_analytic(q: str) -> bool:
    t = q.strip().casefold()
    keys = [
        "экспорт", "импорт", "дүн", "хэмжээ", "тонн", "usd", "ам.доллар",
        "өмнөх", "мөн үе", "өссөн", "сар", "он", "сар сараар", "дундаж", "yoy"
    ]
    return any(k in t for k in keys) or any(ch.isdigit() for ch in t)


def _infer_period(calc: str, time_field: Any) -> str:
    if calc in ("timeseries_month",):
        return "series_month"
    if calc in ("timeseries_year",):
        return "series_year"
    if calc in ("ytd", "year_total", "avg_years"):
        return "year"
    return "month"


def _normalize_value_result(calc: str, rows: list[Dict[str, Any]]) -> Tuple[Dict[str, Any], Optional[str]]:
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
        return {"series": [
            {
                "year": x.get("year"),
                "month": x.get("month"),
                "label": f"{x.get('year')}-{int(x.get('month') or 0):02d}",
                "value": x.get("value"),
            }
            for x in rows
        ]}, None

    if calc == "timeseries_year":
        return {"series": [
            {
                "year": x.get("year"),
                "label": str(x.get("year")),
                "value": x.get("value"),
            }
            for x in rows
        ]}, None

    return {"value": r0.get("value")}, None


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

    # 0) Smalltalk
    if not _looks_analytic(q):
        prompt = f"Та Монгол хэл дээр ярьдаг туслах. Найрсаг, товч хариул.\nАсуулт: {q}"
        return {"answer": llm_text(prompt), "meta": {"intent": None}, "result": None}

    # 1) Intent
    try:
        intent = llm_json(build_intent_prompt(q)) or {}
    except genai_errors.ClientError as e:
        # Gemini quota (429) үед fallback ашиглана
        if getattr(e, "status_code", None) == 429:
            intent = build_intent_fallback(q)
        else:
            raise
    # 2) Validate
    try:
        validate(instance=intent, schema=INTENT_SCHEMA)
    except ValidationError as e:
        return {
            "answer": "Ойлгоход мэдээлэл дутуу байна. Жишээ: “2025 оны 3 сард нүүрсний экспорт хэд вэ?”",
            "meta": {"intent": intent},
            "result": {"error": "invalid_intent", "detail": str(e)},
        }

    calc = intent.get("calc") or "month_value"
    metric = intent.get("metric") or "amountUSD"
    domain = intent.get("domain") or "export"

    # 3) SQL + execute
    sql, params, sql_meta = build_sql(intent, q)
    r = await db.execute(sql, params)
    rows = [dict(x) for x in r.mappings().all()][:500]

    # 4) Normalize
    normalized, err_code = _normalize_value_result(calc, rows)

    unit = _unit(metric)
    period = _infer_period(calc, intent.get("time"))

    # display (UI)
    if calc == "yoy":
        display = {
            "current": _format_value(normalized.get("current"), metric),
            "previous": _format_value(normalized.get("previous"), metric),
            "pct": "—" if normalized.get("pct") is None else f"{float(normalized['pct']):.2f}%",
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
        **scale_meta,  # ✅ scale, scale_label
    }

    if err_code:
        result_contract["warning"] = err_code

    # ✅ add scaled values for frontend charts/tables
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

    # 5) Base answer
    if calc == "yoy":
        pct = normalized.get("pct")
        trend = "—"
        if pct is not None:
            trend = "өссөн" if pct > 0 else ("буурсан" if pct < 0 else "өөрчлөлтгүй")

        base_answer = (
            f"{domain} • өмнөх оны мөн үе: "
            f"Одоогийн={display['current']}, "
            f"Өмнөх={display['previous']}, "
            f"Өөрчлөлт={display['pct']} ({trend})"
        )
    elif calc == "timeseries_month":
        base_answer = f"{domain} • {metric} • сар сараар цуваа гаргалаа."
    elif calc == "timeseries_year":
        base_answer = f"{domain} • {metric} • жил жилээр хүснэгт/цуваа гаргалаа."
    else:
        base_answer = f"{domain} • {calc} • {metric} = {display}"

    # 6) LLM explanation (safe json)
    explain_payload = {
        "question": q,
        "intent": intent,
        "sql_meta": sql_meta,
        "result": result_contract,
        "rows_preview": rows[:20],
    }

    explain_prompt = f"""
Та экспорт/импортын monthly өгөгдөл тайлбарладаг Монгол хэлний туслах.
Доорх JSON дээр үндэслэн 3–6 өгүүлбэрээр ойлгомжтой тайлбар бич.
- Тоог таслалтайгаар бич
- Он/сар, бүтээгдэхүүн (HS) байвал дурд
- YoY бол өсөлт/бууралтыг тайлбарла
- Хэт урт бүү болго

JSON:
{json.dumps(explain_payload, ensure_ascii=False, default=str)}
""".strip()

    explanation = llm_text(explain_prompt).strip()
    answer = explanation if explanation else base_answer

    return {
        "answer": answer,
        "meta": {"intent": intent, "sql_meta": sql_meta},
        "result": result_contract,
    }