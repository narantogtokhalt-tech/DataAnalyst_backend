from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class TimeSpec(BaseModel):
    year: Optional[int] = None
    years: Optional[List[int]] = None
    granularity: Optional[str] = None  # "month" | "year"


class Commodity(BaseModel):
    label: Optional[str] = None
    hscode: Optional[List[str]] = None


class Intent(BaseModel):
    domain: Optional[str] = None          # export | import
    metric: Optional[str] = None          # amountUSD | quantity | weighted_price
    calc: Optional[str] = None
    time: Optional[Dict[str, Any]] = None
    filters: Optional[Dict[str, Any]] = None


class ConversationState(BaseModel):
    domain: Optional[str] = None
    metric: Optional[str] = None
    unit: Optional[str] = None

    time: TimeSpec = Field(default_factory=TimeSpec)
    commodity: Optional[Commodity] = None

    scale_label: Optional[str] = None     # "сая" | "мянга"

    def to_intent(self) -> Dict[str, Any]:
        """
        SQL / analytics intent руу хөрвүүлэхэд ашиглана
        """
        intent: Dict[str, Any] = {
            "domain": self.domain,
            "metric": self.metric,
            "time": {},
            "filters": {},
        }

        if self.time.year:
            intent["time"]["year"] = self.time.year
        if self.time.years:
            intent["time"]["years"] = self.time.years

        if self.commodity and self.commodity.hscode:
            intent["filters"]["hscode"] = self.commodity.hscode

        if self.time.granularity == "month":
            intent["calc"] = "timeseries_month"
        elif self.time.granularity == "year":
            intent["calc"] = "timeseries_year"

        return intent