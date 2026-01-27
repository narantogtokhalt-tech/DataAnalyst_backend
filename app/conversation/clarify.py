from typing import Optional, Dict, Any
from .models import ConversationState


def needs_clarification(s: ConversationState) -> Optional[Dict[str, Any]]:
    """
    AI өөрөө асуулт асуух эсэхийг шийднэ
    """
    if not s.metric:
        return {
            "question": "Ямар үзүүлэлтээр авах вэ?",
            "choices": [
                {"label": "Үнийн дүн", "prompt": "үнийн дүнгээр"},
                {"label": "Тоо хэмжээ", "prompt": "тоо хэмжээгээр"},
            ],
        }

    if not (s.time.year or s.time.years):
        return {
            "question": "Аль оны мэдээлэл вэ?",
            "choices": [
                {"label": "2025 он", "prompt": "2025 он"},
                {"label": "2024 он", "prompt": "2024 он"},
                {"label": "Харьцуулах", "prompt": "2024, 2025 харьцуул"},
            ],
        }

    return None