from .models import ConversationState, Intent, Commodity


HS_LABEL_MAP = {
    "2701": "нүүрс",
    "2702": "лигнит",
}


def merge_intent(
    prev: ConversationState,
    intent: Intent,
    overrides: dict,
) -> ConversationState:
    """
    Previous state + шинэ intent + follow-up override-уудыг нэгтгэнэ
    """
    s = prev.model_copy(deep=True)

    # --- base intent ---
    if intent.domain:
        s.domain = intent.domain

    if intent.metric:
        s.metric = intent.metric

    if intent.time:
        if "year" in intent.time:
            s.time.year = intent.time["year"]
        if "years" in intent.time:
            s.time.years = intent.time["years"]

    # commodity (HS → label)
    hs = (intent.filters or {}).get("hscode")
    if hs:
        label = HS_LABEL_MAP.get(hs[0])
        s.commodity = Commodity(label=label, hscode=hs)

    # --- follow-up overrides ---
    if overrides.get("granularity"):
        s.time.granularity = overrides["granularity"]

    if overrides.get("scale_label"):
        s.scale_label = overrides["scale_label"]

    if overrides.get("metric"):
        s.metric = overrides["metric"]

    if overrides.get("unit"):
        s.unit = overrides["unit"]

    return s


def apply_compare_prev_year(s: ConversationState) -> ConversationState:
    """
    “өмнөх онтой харьцуулах” гэвэл
    """
    out = s.model_copy(deep=True)

    if out.time.year and not out.time.years:
        out.time.years = [out.time.year - 1, out.time.year]

    return out