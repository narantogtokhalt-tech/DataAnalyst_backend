from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

class Commodity(BaseModel):
    label: str
    hscode: List[str] = Field(default_factory=list)

class TimeState(BaseModel):
    year: Optional[int] = None
    years: Optional[List[int]] = None
    granularity: Optional[str] = None  # "month" | "year"

class ConversationState(BaseModel):
    domain: Optional[str] = None        # "export" | "import"
    metric: Optional[str] = None        # "amountUSD" | "quantity" | "weighted_price"
    unit: Optional[str] = None          # "ам.доллар" | "тонн" | "ам.доллар/тонн"
    scale_label: Optional[str] = None   # "мянга" | "сая" | ""
    filters: Dict[str, Any] = Field(default_factory=dict)
    commodity: Optional[Commodity] = None
    time: TimeState = Field(default_factory=TimeState)