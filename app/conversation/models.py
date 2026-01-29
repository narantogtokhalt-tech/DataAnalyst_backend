# D:\DataAnalystBot\app\conversation\models.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, model_validator

Domain = Literal["export", "import"]
Metric = Literal["amountUSD", "quantity", "weighted_price"]
Granularity = Literal["month", "year"]
ScaleLabel = Literal["сая", "мянга"]


class TimeSpec(BaseModel):
    year: Optional[int] = None
    years: Optional[List[int]] = None
    granularity: Optional[str] = None  # "month" | "year"
    latest: bool = False  # ✅ add (байхгүй бол нэм)

    @model_validator(mode="after")
    def _normalize_time(self) -> "TimeSpec":
        # years өгөгдвөл year-г цэвэрлэнэ
        if self.years:
            self.years = sorted(set(int(x) for x in self.years if x is not None))
            if self.years:
                self.year = None
            else:
                self.years = None

        # year өгөгдвөл years-г цэвэрлэнэ
        if self.year is not None:
            try:
                self.year = int(self.year)
            except Exception:
                self.year = None
            if self.year is not None:
                self.years = None

        # latest бол year/years байх ёсгүй
        if self.latest:
            self.year = None
            self.years = None

        return self


class Commodity(BaseModel):
    label: Optional[str] = None
    hscode: Optional[List[str]] = None


class Intent(BaseModel):
    domain: Optional[Domain] = None               # export | import
    metric: Optional[Metric] = None               # amountUSD | quantity | weighted_price
    calc: Optional[str] = None
    time: Optional[Dict[str, Any]] = None
    filters: Optional[Dict[str, Any]] = None


class ConversationState(BaseModel):
    domain: Optional[Domain] = None
    metric: Optional[Metric] = None
    unit: Optional[str] = None
    awaiting_clarification: bool = False
    pending_question: Optional[str] = None
    pending_clarify: Optional[Dict[str, Any]] = None

    time: TimeSpec = Field(default_factory=TimeSpec)
    commodity: Optional[Commodity] = None

    scale_label: Optional[ScaleLabel] = None      # "сая" | "мянга"

    def to_intent(self) -> Dict[str, Any]:
        """
        Single source of truth → SQL intent
        """
        intent: Dict[str, Any] = {
            "domain": self.domain,
            "metric": self.metric,
            "time": {},
            "filters": {},
        }

        # -------- time --------
        if self.time.latest:
            intent["time"] = "latest"
        else:
            if self.time.years:
                intent["time"]["years"] = self.time.years
            elif self.time.year:
                intent["time"]["year"] = self.time.year

        # -------- filters --------
        if self.commodity and self.commodity.hscode:
            intent["filters"]["hscode"] = self.commodity.hscode

        # -------- calc from granularity --------
        if self.time.granularity == "month":
            intent["calc"] = "timeseries_month"
        elif self.time.granularity == "year":
            intent["calc"] = "timeseries_year"

        return intent