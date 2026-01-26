# app/llm/prompt.py
from __future__ import annotations

import datetime
import pytz

from app.core.config import settings

TZ = pytz.timezone(settings.timezone)

# Prompt-д зөвхөн "заавар" хэлбэрээр ашиглана.
# Сервер талдаа бодит fallback/validation-оо builder.py дээр хийж байгаа (сайн).
HS_CODE_MAP = {
    "нүүрс": ["2701", "2702"],
    "зэс": ["2603"],
    "төмөр": ["2601"],
    "газрын тос": ["2709"],
}


def build_intent_prompt(question: str) -> str:
    today = datetime.datetime.now(TZ).date().isoformat()

    return f"""
ЧИ МОНГОЛ ХЭЛ ДЭЭРХ АСУУЛТЫГ "intent JSON" БОЛГОЖ ХӨРВҮҮЛНЭ.
ЧИ ЗӨВХӨН НЭГ ШИРХЭГ JSON ОБЪЕКТ БУЦААНА. ӨӨР ТЕКСТ БИЧИХГҮЙ.

JSON бүтэц:
{{
  "domain": "export" | "import",
  "calc": "month_value" | "ytd" | "yoy" | "timeseries_month" | "timeseries_year" | "year_total" | "weighted_price" | "avg_months" | "avg_years",
  "metric": "amountUSD" | "quantity" | "weighted_price",
  "time":
    "latest"
    | {{"year": 2025, "month": 3}}
    | {{"year": 2025}}
    | {{"years": [2024, 2025]}},
  "filters": {{
     "hscode": "2701" | ["2701","2702"],
     "country": "China",
     "senderReceiver": "CN",
     "company": "Эрдэнэс",
     "customs": "...",
     "purpose": "...",
     "sub1": "...",
     "sub2": "...",
     "sub3": "..."
  }},
  "window": 3,
  "topn": 50
}}

ДҮРЭМ (заавал мөрдөнө):

1) DOMAIN
- "импорт" гэж байвал domain="import"
- бусад үед domain="export"

2) TIME
- "өнөөдрийн байдлаар", "сүүлийн сар", "хамгийн сүүлийн" гэвэл time="latest"
- "YYYY оны M сар" гэвэл time={{"year":YYYY,"month":M}}
- "YYYY онд" гэвэл time={{"year":YYYY}}
- "YYYY, YYYY" (ж: "2024, 2025") эсвэл "YYYY-YYYY" (ж: "2024-2025") эсвэл "хоёр жил", "2 жил" гэвэл:
  time={{"years":[YYYY,YYYY]}} хэлбэрээр тавь
- огноо/он/сар дурдагдаагүй бол time="latest"

3) CATEGORY (АНГИЛАЛ) — HS-ЭЭС ӨӨР
Хэрвээ асуулт нь "тамхи", "хүнс", "түргэн эдэлгээтэй", "хэрэглээний бүтээгдэхүүн" зэрэг ангиллын үгтэй байвал:
- HS код (filters.hscode) БҮҮ таамагла.
- Доорх filter-үүдийг ашигла:
  - "хэрэглээний бүтээгдэхүүн" -> filters.purpose = "Хэрэглээний бүтээгдэхүүн"
  - "түргэн эдэлгээтэй" -> filters.sub1 = "Түргэн эдэлгээтэй"
  - "хүнс" -> filters.sub2 = "Хүнс"
  - "тамхи" -> filters.sub3 = "Тамхи"
ЖИЧ: filters.sub* / purpose дээр exact биш, түлхүүр үг ("Тамхи" гэх мэт) тавихад болно.

4) HS CODE / PRODUCT MAPPING (HS4)
- "нийт экспорт", "нийт импорт", "бүх экспорт", "нийт дүн" гэвэл filters.hscode БИТГИЙ тавь
- Бүтээгдэхүүн mapping (HS4):
  - нүүрс -> ["2701","2702"]
  - зэс -> ["2603"]
  - төмөр -> ["2601"]
  - газрын тос -> ["2709"]
- Хэрэглэгч 4 оронтой HS код (ж: 2701) бичвэл filters.hscode болгож тавь.
- ⚠️ 2000–2030 хоорондын 4 оронтой тоо (ж: 2025) ихэвчлэн "он" тул HS гэж БҮҮ үз.

5) METRIC
- "дүн", "USD", "ам.доллар", "үнийн дүн" гэвэл metric="amountUSD"
- "тоо хэмжээ", "хэмжээ", "тонн" гэвэл metric="quantity"
- "жигнэсэн дундаж үнэ", "average price", "дундаж үнэ" гэвэл:
  metric="weighted_price" ба calc="weighted_price"
- "нэгж үнэ", "тонн тутмын үнэ", "unit price" гэвэл:
  metric="weighted_price" ба calc="weighted_price"

6) CALC
- "өссөн дүн", "он эхнээс", "YTD" гэвэл calc="ytd"
- "өмнөх оны мөн үе" гэвэл calc="yoy"
- "сар сараар", "явц", "timeline" гэвэл calc="timeseries_month" ба time={{"year":YYYY}} хэлбэрийг сонго
- "жилээр", "жилийн", "2024, 2025", "2024-2025", "хоёр жил", "2 жил", "хүснэгтээр" (жилүүдийг харьцуулж) гэвэл:
  calc="timeseries_year" ба time={{"years":[...]}}

- "YYYY онд ... нийт" гэвэл calc="year_total" + time={{"year":YYYY}}
- "YYYY оны M сар" бол calc="month_value"

7) AVG
- "сүүлийн N сар(ын) дундаж" -> calc="avg_months", window=N
- "сүүлийн N жил(ийн) дундаж" -> calc="avg_years", window=N
- N тодорхойгүй "дундаж" -> window=3
- AVG calc дээр metric нь amountUSD эсвэл quantity байх ёстой (weighted_price асуусан бол calc="weighted_price")

8) FILTERS (хэрвээ асуултад байвал)
- компани нэр байвал filters.company-д оруул (fuzzy)
- улс нэр байвал filters.country-д оруул (fuzzy)
- senderReceiver 2 үсэг (CN, RU гэх мэт) байвал filters.senderReceiver-д оруул
- гааль/customs дурдагдвал filters.customs-д оруул

9) OTHER
- topn дурдаагүй бол 50
- window дурдаагүй бол 3

Өнөөдөр: {today}

АСУУЛТ:
{question}

ЗӨВХӨН JSON-ОО БУЦАА.
""".strip()