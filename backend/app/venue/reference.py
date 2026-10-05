from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class ReferenceBand:
    min_ratio: float
    max_ratio: float
    min_percent: float


class PublicBDMReference:
    def __init__(self, profile_path: Path):
        raw = json.loads(profile_path.read_text(encoding="utf-8"))
        self.profile_id = raw["profile_id"]
        self.bands = [
            ReferenceBand(
                min_ratio=float(x["min_ratio"]),
                max_ratio=float(x["max_ratio"]),
                min_percent=float(x["min_element_percent"]),
            ) for x in raw["table"]
        ]

    def required_percent(self, viewing_ratio: float) -> float | None:
        if viewing_ratio < self.bands[0].min_ratio or viewing_ratio > self.bands[-1].max_ratio:
            return None
        # Conservative shared-boundary policy: among all matching intervals,
        # choose the higher minimum element target.
        matches = [
            b.min_percent for b in self.bands
            if b.min_ratio <= viewing_ratio <= b.max_ratio
        ]
        return max(matches) if matches else None
