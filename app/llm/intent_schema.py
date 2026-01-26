# app/llm/intent_schema.py

INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["domain", "calc", "metric", "time"],
    "properties": {
        # ------------------------
        # DOMAIN
        # ------------------------
        "domain": {
            "type": "string",
            "enum": ["export", "import"],
            "description": "Экспорт эсвэл импорт",
        },

        # ------------------------
        # CALCULATION TYPE
        # ------------------------
        "calc": {
            "type": "string",
            "enum": [
                "month_value",
                "year_total",
                "ytd",
                "timeseries_month",
                "timeseries_year",   # ✅ NEW
                "yoy",
                "avg_months",
                "avg_years",
                "weighted_price",
            ],
        },

        # ------------------------
        # METRIC
        # ------------------------
        "metric": {
            "type": "string",
            "enum": ["amountUSD", "quantity", "weighted_price"],
            "description": "Дүн, хэмжээ, жигнэсэн үнэ",
        },

        # ------------------------
        # TIME (NO ambiguity)
        # ------------------------
        "time": {
            "oneOf": [
                {
                    "type": "string",
                    "enum": ["latest"],
                    "description": "Хамгийн сүүлийн сар",
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["year", "month"],
                    "properties": {
                        "year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                        "month": {"type": "integer", "minimum": 1, "maximum": 12},
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["year"],
                    "properties": {
                        "year": {"type": "integer", "minimum": 1900, "maximum": 2100},
                    },
                    "not": {"required": ["month"]},
                },
                {
                    # ✅ NEW: Multi-year request (e.g., 2024, 2025)
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["years"],
                    "properties": {
                        "years": {
                            "type": "array",
                            "items": {"type": "integer", "minimum": 1900, "maximum": 2100},
                            "minItems": 1,
                        }
                    },
                },
            ]
        },

        # ------------------------
        # WINDOW (avg-д)
        # ------------------------
        "window": {
            "type": "integer",
            "minimum": 1,
            "maximum": 60,
            "default": 3,
            "description": "avg_months / avg_years үед ашиглана",
        },

        # ------------------------
        # FILTERS
        # ------------------------
        "filters": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "hscode": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                    ]
                },
                "company": {"type": "string"},
                "country": {"type": "string"},
                "senderReceiver": {"type": "string"},
                "customs": {"type": "string"},
            },
        },

        # ------------------------
        # TOP N
        # ------------------------
        "topn": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    },
}