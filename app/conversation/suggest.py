from typing import List, Dict
from .models import ConversationState


def build_suggestions(s: ConversationState) -> List[Dict[str, str]]:
    """
    UI дээр харуулах suggested follow-ups
    """
    out: List[Dict[str, str]] = []

    if s.time.granularity != "month":
        out.append({"label": "Сар бүр", "prompt": "сар бүрээр"})

    if s.time.granularity != "year":
        out.append({"label": "Жилээр", "prompt": "жилээр"})

    if s.time.year and not s.time.years:
        out.append({
            "label": "Өмнөх онтой харьцуулах",
            "prompt": "өмнөх онтой харьцуул",
        })

    if s.scale_label != "сая":
        out.append({"label": "Сая нэгж", "prompt": "сая нэгжээр"})

    if s.scale_label != "мянга":
        out.append({"label": "Мянга нэгж", "prompt": "мянга нэгжээр"})

    return out