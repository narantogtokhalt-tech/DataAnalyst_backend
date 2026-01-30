from __future__ import annotations

import re
from typing import Any, Dict, Tuple, Optional, List

from sqlalchemy import text
from app.sql.templates import resolve_view

HS_CODE_MAP = {
    "нүүрс": ["2701", "2702"],
    "зэс": ["2603"],
    "төмөр": ["2601"],
    "газрын тос": ["2709"],
}

CATEGORY_KEYWORDS: Dict[str, str] = {
    "тамхи": "sub3",
    "суудлын автомашин": "sub3",
    "хүнс": "sub2",
    "автобензин": "sub2",
    "түргэн эдэлгээтэй": "sub1",
    "хэрэглээний бүтээгдэхүүн": "purpose",
}

def _infer_category_filters(question: str) -> Dict[str, str]:
    qn = _norm(question)
    out: Dict[str, str] = {}
    for kw, field in CATEGORY_KEYWORDS.items():
        if kw in qn:
            out[field] = kw
    return out


def _norm(s: Any) -> str:
    return str(s).strip().casefold()


def _infer_hscode(question: str) -> Optional[List[str]]:
    qn = _norm(question)

    # user typed explicit HS codes
    m = re.findall(r"\b(\d{4})\b", qn)
    if m:
        hs = []
        for s in m:
            n = int(s)
            # 2000–2030 бол "он" гэж үзээд HS-ээс хасна
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


def _time_parts(intent_time: Any) -> Tuple[Optional[int], Optional[int], bool]:
    """
    Returns: (year, month, is_latest)
    """
    if isinstance(intent_time, str) and intent_time == "latest":
        return None, None, True

    if isinstance(intent_time, dict):
        y = intent_time.get("year")
        m = intent_time.get("month")
        if y is not None and m is not None:
            return int(y), int(m), False
        if y is not None:
            return int(y), None, False

    # fallback latest
    return None, None, True

def _time_years(intent_time: Any) -> Optional[List[int]]:
    """
    Returns list of years if time is {"years":[...]} else None
    """
    if isinstance(intent_time, dict) and isinstance(intent_time.get("years"), list):
        years = []
        for y in intent_time.get("years", []):
            try:
                years.append(int(y))
            except Exception:
                continue
        years = sorted(set(years))
        return years if years else None
    return None

def _where_filters(filters: Dict[str, Any], params: Dict[str, Any], need_company: bool) -> str:
    clauses = []

    # hscode: string эсвэл list
    if filters.get("hscode"):
        hscodes = filters["hscode"]
        if isinstance(hscodes, list):
            params["hscodes"] = [str(x).strip() for x in hscodes]
            clauses.append("hscode = ANY(CAST(:hscodes AS text[]))")
        else:
            params["hscode"] = str(hscodes).strip()
            clauses.append("hscode = :hscode")

    # Prefer "country" (full name) – fuzzy match
    if filters.get("country"):
        params["country"] = f"%{str(filters['country']).strip()}%"
        clauses.append("country ILIKE :country")

    if filters.get("senderReceiver"):
        params["senderReceiver"] = str(filters["senderReceiver"]).strip()
        clauses.append("senderReceiver = :senderReceiver")

    if filters.get("customs"):
        params["customs"] = f"%{str(filters['customs']).strip()}%"
        clauses.append("customs ILIKE :customs")

        # --- Category filters (for v_*_monthly_category) ---
    if filters.get("purpose"):
        params["purpose"] = f"%{str(filters['purpose']).strip()}%"
        clauses.append("purpose ILIKE :purpose")

    if filters.get("sub1"):
        params["sub1"] = f"%{str(filters['sub1']).strip()}%"
        clauses.append("sub1 ILIKE :sub1")

    if filters.get("sub2"):
        params["sub2"] = f"%{str(filters['sub2']).strip()}%"
        clauses.append("sub2 ILIKE :sub2")

    if filters.get("sub3"):
        params["sub3"] = f"%{str(filters['sub3']).strip()}%"
        clauses.append("sub3 ILIKE :sub3")

    # export company view only
    if need_company and filters.get("company"):
        params["company"] = f"%{str(filters['company']).strip()}%"
        clauses.append("(companyName ILIKE :company OR companyRegnum ILIKE :company)")

    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


def build_sql(intent: Dict[str, Any], question: str) -> Tuple[Any, Dict[str, Any], Dict[str, Any]]:
    domain = intent.get("domain", "export")
    calc = intent.get("calc", "month_value")
    metric = intent.get("metric", "amountUSD")

    raw_filters = intent.get("filters") or {}
    filters: Dict[str, Any] = raw_filters if isinstance(raw_filters, dict) else {}

    topn = int(intent.get("topn", 50) or 50)

    # avg window
    window = int(intent.get("window", 3) or 3)
    if window <= 0:
        window = 3

    qn = _norm(question)

    # -------------------------------------------------
    # ✅ 1) Category fallback (always wins; never mix HS)
    # -------------------------------------------------
    cat_filters = _infer_category_filters(question)
    if cat_filters:
        filters.update(cat_filters)
        filters.pop("hscode", None)

    has_category = any(filters.get(k) for k in ("purpose", "sub1", "sub2", "sub3"))

    # -------------------------------------------------
    # ✅ 2) HS fallback (only if NOT category and NOT "нийт")
    # -------------------------------------------------
    if (not has_category) and (not filters.get("hscode")) and ("нийт" not in qn):
        hs = _infer_hscode(question)
        if hs:
            filters["hscode"] = hs

    # -------------------------------------------------
    # ✅ 3) Time parse + HARD RULE for multi-year
    # -------------------------------------------------
    year, month, is_latest = _time_parts(intent.get("time", "latest"))
    years_list = _time_years(intent.get("time"))

    # ✅ HARD RULE: multi-year => timeseries_year only
    if years_list:
        calc = "timeseries_year"

    # -------------------------------------------------
    # ✅ 4) Rule-based calc override (single-year only)
    # -------------------------------------------------
    wants_total = any(k in qn for k in ("нийт", "нийлбэр", "total"))
    asking_amount = any(k in qn for k in ("хэд", "хэчнээн", "дүн", "утга", "value"))

    # "2025 оны нийт ... хэд вэ" -> year_total
    # ⚠️ multi-year үед ажиллуулахгүй (HARD RULE дарна)
    if (not years_list) and (not is_latest) and (year is not None) and (month is None) and wants_total and asking_amount:
        calc = "year_total"

    # -------------------------------------------------
    # ✅ 5) Resolve view AFTER filters are stable
    # -------------------------------------------------
    need_company = bool(filters.get("company")) and domain == "export"
    view, view_type = resolve_view(domain, need_company, filters)

    # -------------------------------------------------
    # ✅ 6) Params + where
    # -------------------------------------------------
    params: Dict[str, Any] = {"topn": topn, "window": window}
    w = _where_filters(filters, params, need_company)

    # metric expr (aggregate level)
    if metric == "amountUSD":
        metric_expr = "SUM(COALESCE(amountUSD,0))"
    elif metric == "quantity":
        metric_expr = "SUM(COALESCE(quantity,0))"
    else:  # weighted_price
        metric_expr = (
            "SUM(COALESCE(amountUSD,0)) "
            "/ NULLIF(SUM(COALESCE(quantity,0)) / 1000, 0)"
        )

    meta = {
        "view": view,
        "view_type": view_type,
        "domain": domain,
        "need_company": need_company,
        "calc": calc,
        "metric": metric,
        "window": window,
        "is_timeseries": calc.startswith("timeseries"),
        "granularity": (
            "year" if calc == "timeseries_year"
            else "month" if calc == "timeseries_month"
            else "single"
        ),
    }

    # latest month CTE body (no leading WITH)
    latest_cte = f"""
latest AS (
  SELECT MAX(make_date(year::int, month::int, 1)) AS dt
  FROM {view}
),
latest_parts AS (
  SELECT EXTRACT(YEAR FROM dt)::int AS y, EXTRACT(MONTH FROM dt)::int AS m, dt
  FROM latest
)
""".strip()

    def _with_prefix(sql_body: str) -> str:
        if is_latest:
            return "WITH " + latest_cte + "\n" + sql_body.lstrip()
        return sql_body

    def _ref_month_start_sql() -> str:
        if is_latest:
            return "(SELECT dt FROM latest_parts)"
        if year is not None and month is not None:
            params["year"] = year
            params["month"] = month
            return "make_date(CAST(:year AS int), CAST(:month AS int), 1)"
        if year is not None:
            params["year"] = year
            params["month"] = 1
            return "make_date(CAST(:year AS int), 1, 1)"
        return "(SELECT dt FROM latest_parts)"

    def _append_time_month(where_sql: str) -> str:
        """
        For month-level queries: append (year,month) filter.
        """
        if is_latest:
            time_clause = (
                "year = (SELECT y FROM latest_parts) "
                "AND month = (SELECT m FROM latest_parts)"
            )
            return (where_sql + " AND " + time_clause) if where_sql else ("WHERE " + time_clause)

        # ✅ Explicit year+month
        if year is not None and month is not None:
            params["year"] = int(year)
            params["month"] = int(month)
            time_clause = "year = :year AND month = :month"
            return (where_sql + " AND " + time_clause) if where_sql else ("WHERE " + time_clause)

        # ✅ No year → treat as latest year (month-level queries still need a year)
        if year is None:
            time_clause = "year = (SELECT y FROM latest_parts)"
            return (where_sql + " AND " + time_clause) if where_sql else ("WHERE " + time_clause)

        # ✅ Year only
        params["year"] = int(year)
        time_clause = "year = :year"
        return (where_sql + " AND " + time_clause) if where_sql else ("WHERE " + time_clause)

    # -------------------
    # calc cases
    # -------------------

    if calc == "month_value":
            # ✅ If only year is provided, month_value is ambiguous → treat as monthly timeseries
            if (not is_latest) and (year is not None) and (month is None):
                params["year"] = int(year)
                base = w + (" AND year = :year" if w else "WHERE year = :year")

                sql_body = f"""
    SELECT year, month, {metric_expr} AS value
    FROM {view}
    {base}
    GROUP BY year, month
    ORDER BY year, month
    """
                # ✅ meta-г зөв болгож өгнө
                meta["calc"] = "timeseries_month"
                meta["is_timeseries"] = True
                meta["granularity"] = "month"
                return text(sql_body), params, meta

            where2 = _append_time_month(w)

            if is_latest:
                sql_body = f"""
    SELECT
      (SELECT y FROM latest_parts) AS year,
      (SELECT m FROM latest_parts) AS month,
      {metric_expr} AS value
    FROM {view}
    {where2}
    """
                return text(_with_prefix(sql_body)), params, meta

            # explicit time (year+month)
            sql_body = f"""
    SELECT
      CAST(:year AS int) AS year,
      CAST(:month AS int) AS month,
      {metric_expr} AS value
    FROM {view}
    {where2}
    """
            return text(sql_body), params, meta

    if calc == "year_total":
        if is_latest:
            base = w.replace("WHERE ", "")
            extra = f" AND {base}" if base else ""
            sql_body = f"""
SELECT
  (SELECT y FROM latest_parts) AS year,
  NULL::int AS month,
  {metric_expr} AS value
FROM {view}
WHERE year = (SELECT y FROM latest_parts){extra}
"""
            return text(_with_prefix(sql_body)), params, meta

        if year is None:
            year = 0
        params["year"] = year
        base_where = w + (" AND year = :year" if w else "WHERE year = :year")
        sql_body = f"""
SELECT
  CAST(:year AS int) AS year,
  NULL::int AS month,
  {metric_expr} AS value
FROM {view}
{base_where}
"""
        return text(sql_body), params, meta

    if calc == "ytd":
        if is_latest:
            base = w.replace("WHERE ", "")
            extra = f" AND {base}" if base else ""
            sql_body = f"""
SELECT
  (SELECT y FROM latest_parts) AS year,
  (SELECT m FROM latest_parts) AS month,
  {metric_expr} AS value
FROM {view}
WHERE year = (SELECT y FROM latest_parts)
  AND month <= (SELECT m FROM latest_parts){extra}
"""
            return text(_with_prefix(sql_body)), params, meta

        if year is None:
            year = 0
        params["year"] = year
        params["mmax"] = int(month or 12)
        base = w + (" AND year = :year AND month <= :mmax" if w else "WHERE year = :year AND month <= :mmax")
        sql_body = f"""
SELECT
  CAST(:year AS int) AS year,
  CAST(:mmax AS int) AS month,
  {metric_expr} AS value
FROM {view}
{base}
"""
        return text(sql_body), params, meta

    if calc == "timeseries_month":
        if is_latest:
            base = w.replace("WHERE ", "")
            extra = f" AND {base}" if base else ""
            sql_body = f"""
SELECT year, month, {metric_expr} AS value
FROM {view}
WHERE year = (SELECT y FROM latest_parts){extra}
GROUP BY year, month
ORDER BY year, month
"""
            return text(_with_prefix(sql_body)), params, meta

        if year is None:
            year = 0
        params["year"] = year
        base = w + (" AND year = :year" if w else "WHERE year = :year")
        sql_body = f"""
SELECT year, month, {metric_expr} AS value
FROM {view}
{base}
GROUP BY year, month
ORDER BY year, month
"""
        return text(sql_body), params, meta

    if calc == "timeseries_year":
        # years_list байх ёстой
        if not years_list:
            # is_latest үед: latest year total-г (1 мөр) буцаана
            if is_latest:
                base = w.replace("WHERE ", "")
                extra = f" AND {base}" if base else ""
                sql_body = f"""
    SELECT
      (SELECT y FROM latest_parts) AS year,
      {metric_expr} AS value
    FROM {view}
    WHERE year = (SELECT y FROM latest_parts){extra}
    GROUP BY 1
    ORDER BY 1
    """
                return text(_with_prefix(sql_body)), params, meta

            # explicit year өгөгдсөн бол тэр жилээр total (1 мөр)
            if year is None:
                year = 0
            params["year"] = year
            base_where = w + (" AND year = :year" if w else "WHERE year = :year")
            sql_body = f"""
    SELECT
      CAST(:year AS int) AS year,
      {metric_expr} AS value
    FROM {view}
    {base_where}
    GROUP BY 1
    ORDER BY 1
    """
            return text(sql_body), params, meta

        # normal multi-year
        params["years"] = years_list
        base_where = w + (" AND year = ANY(CAST(:years AS int[]))" if w else "WHERE year = ANY(CAST(:years AS int[]))")
        sql_body = f"""
    SELECT
      year::int AS year,
      {metric_expr} AS value
    FROM {view}
    {base_where}
    GROUP BY 1
    ORDER BY 1
    """
        return text(sql_body), params, meta

    if calc == "yoy":
        if is_latest:
            base = w.replace("WHERE ", "")
            extra = f" AND {base}" if base else ""

            sql_body = f"""
    cur AS (
      SELECT {metric_expr} AS v
      FROM {view}
      WHERE year = (SELECT y FROM latest_parts)
        AND month = (SELECT m FROM latest_parts){extra}
    ),
    prev AS (
      SELECT {metric_expr} AS v
      FROM {view}
      WHERE year = (SELECT y FROM latest_parts) - 1
        AND month = (SELECT m FROM latest_parts){extra}
    )
    SELECT
      (SELECT y FROM latest_parts) AS year,
      (SELECT m FROM latest_parts) AS month,
      (SELECT v FROM cur) AS current,
      (SELECT v FROM prev) AS previous,
      CASE
        WHEN (SELECT v FROM prev) IS NULL OR (SELECT v FROM prev) = 0 THEN NULL
        ELSE ((SELECT v FROM cur) - (SELECT v FROM prev)) / (SELECT v FROM prev) * 100.0
      END AS pct
    """.strip()

            # ✅ IMPORTANT: latest_cte + ",\n" + sql_body
            return text("WITH " + latest_cte + ",\n" + sql_body), params, meta

        if year is None or month is None:
            if year is None:
                year = 0
            params["year"] = year
            base_where = w + (" AND year = :year" if w else "WHERE year = :year")
            sql_body = f"""
SELECT
  CAST(:year AS int) AS year,
  NULL::int AS month,
  {metric_expr} AS value
FROM {view}
{base_where}
"""
            return text(sql_body), params, meta

        params["year"] = year
        params["month"] = month
        params["prev_year"] = year - 1

        base = w.replace("WHERE ", "")
        extra = f" AND {base}" if base else ""
        sql_body = f"""
WITH cur AS (
  SELECT {metric_expr} AS v
  FROM {view}
  WHERE year = :year AND month = :month{extra}
),
prev AS (
  SELECT {metric_expr} AS v
  FROM {view}
  WHERE year = :prev_year AND month = :month{extra}
)
SELECT
  CAST(:year AS int) AS year,
  CAST(:month AS int) AS month,
  (SELECT v FROM cur) AS current,
  (SELECT v FROM prev) AS previous,
  CASE
    WHEN (SELECT v FROM prev) IS NULL OR (SELECT v FROM prev) = 0 THEN NULL
    ELSE ((SELECT v FROM cur) - (SELECT v FROM prev)) / (SELECT v FROM prev) * 100.0
  END AS pct
"""
        return text(sql_body), params, meta

    if calc == "weighted_price":
        where2 = _append_time_month(w)
        metric_expr2 = (
            "SUM(COALESCE(amountUSD,0)) "
            "/ NULLIF(SUM(COALESCE(quantity,0)) / 1000, 0)"
        )

        if is_latest:
            sql_body = f"""
SELECT
  (SELECT y FROM latest_parts) AS year,
  (SELECT m FROM latest_parts) AS month,
  {metric_expr2} AS value
FROM {view}
{where2}
"""
            return text(_with_prefix(sql_body)), params, meta

        sql_body = f"""
SELECT
  CAST(:year AS int) AS year,
  CAST(:month AS int) AS month,
  {metric_expr2} AS value
FROM {view}
{where2}
"""
        return text(sql_body), params, meta

    # ✅ avg_months: average of last N months (from ref month)
    if calc == "avg_months":
        ref_dt = _ref_month_start_sql()
        params["window"] = window

        base = w.replace("WHERE ", "")
        extra = f"WHERE {base}" if base else ""

        sql_body = f"""
monthly AS (
  SELECT
    make_date(year::int, month::int, 1) AS dt,
    year::int AS y,
    month::int AS m,
    {metric_expr} AS v
  FROM {view}
  {extra}
  GROUP BY 1,2,3
),
win AS (
  SELECT *
  FROM monthly
  WHERE dt <= {ref_dt}
    AND dt >= ({ref_dt} - ((CAST(:window AS int) - 1) * INTERVAL '1 month'))
)
SELECT
  EXTRACT(YEAR FROM {ref_dt})::int AS year,
  EXTRACT(MONTH FROM {ref_dt})::int AS month,
  AVG(v) AS value
FROM win
""".strip()

        if is_latest:
            sql2 = "WITH " + latest_cte + ",\n" + sql_body
            return text(sql2), params, meta

        sql2 = "WITH " + sql_body
        return text(sql2), params, meta

    # ✅ avg_years: average of last N years (year totals)
    if calc == "avg_years":
        if is_latest:
            ref_year_sql = "(SELECT y FROM latest_parts)"
        else:
            if year is None:
                year = 0
            params["year"] = year
            ref_year_sql = "CAST(:year AS int)"

        params["window"] = window
        base = w.replace("WHERE ", "")
        extra = f"WHERE {base}" if base else ""

        sql_body = f"""
yearly AS (
  SELECT
    year::int AS y,
    {metric_expr} AS v
  FROM {view}
  {extra}
  GROUP BY 1
),
win AS (
  SELECT *
  FROM yearly
  WHERE y <= {ref_year_sql}
    AND y >= ({ref_year_sql} - (CAST(:window AS int) - 1))
)
SELECT
  {ref_year_sql} AS year,
  NULL::int AS month,
  AVG(v) AS value
FROM win
""".strip()

        if is_latest:
            sql2 = "WITH " + latest_cte + ",\n" + sql_body
            return text(sql2), params, meta

        sql2 = "WITH " + sql_body
        return text(sql2), params, meta

    # fallback -> month_value
    where2 = _append_time_month(w)
    if is_latest:
        sql_body = f"""
SELECT
  (SELECT y FROM latest_parts) AS year,
  (SELECT m FROM latest_parts) AS month,
  {metric_expr} AS value
FROM {view}
{where2}
"""
        return text(_with_prefix(sql_body)), params, meta

    sql_body = f"""
SELECT
  CAST(:year AS int) AS year,
  CAST(:month AS int) AS month,
  {metric_expr} AS value
FROM {view}
{where2}
"""
    return text(sql_body), params, meta