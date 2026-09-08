"""
GPS Engine — STEP 8: GPS Distance Verification (Haversine)
=========================================================
Compares the authoritative BhuNaksha centroid (Step 3) against the field engineer's
mobile GPS from the Technical Valuation Report, and returns the Parameter-5 verdict.

Thresholds (see docs/CONTEXT.md §3):
    d <= 50 m         MATCH     GREEN   — same plot, GPS confirmed
    50 m < d <= 200 m REFER     AMBER   — adjacent-plot / boundary ambiguity, manual review
    d > 200 m         NEGATIVE  RED     — location mismatch, possible collateral fraud
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from gps_engine.config import DISTANCE_THRESHOLD_MATCH_METERS, DISTANCE_THRESHOLD_REFER_METERS
from gps_engine.step3_transform import haversine_m


@dataclass
class DistanceResult:
    status: str                     # SUCCESS | SKIPPED
    distance_m: Optional[float] = None
    verdict: Optional[str] = None   # MATCH | REFER | NEGATIVE
    color: Optional[str] = None     # GREEN | AMBER | RED
    bhu: Optional[Dict[str, float]] = None
    tvr: Optional[Dict[str, float]] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "distance_m": self.distance_m,
            "verdict": self.verdict,
            "color": self.color,
            "bhu_centroid": self.bhu,
            "tvr_gps": self.tvr,
            "note": self.note,
        }


def verify_distance(
    bhu_lat: float,
    bhu_lon: float,
    tvr_lat: Optional[float],
    tvr_lon: Optional[float],
) -> DistanceResult:
    bhu = {"lat": round(bhu_lat, 8), "lon": round(bhu_lon, 8)}

    if tvr_lat is None or tvr_lon is None:
        return DistanceResult(
            status="SKIPPED", bhu=bhu,
            note="no Technical Valuation Report GPS supplied; Parameter 5 = 'GPS derived from "
                 "BhuNaksha only' (BRD col 5). Provide --tvr-lat/--tvr-lon to compute the verdict.",
        )

    d = round(haversine_m(bhu_lat, bhu_lon, tvr_lat, tvr_lon), 1)
    if d <= DISTANCE_THRESHOLD_MATCH_METERS:
        verdict, color = "MATCH", "GREEN"
    elif d <= DISTANCE_THRESHOLD_REFER_METERS:
        verdict, color = "REFER", "AMBER"
    else:
        verdict, color = "NEGATIVE", "RED"

    return DistanceResult(
        status="SUCCESS", distance_m=d, verdict=verdict, color=color,
        bhu=bhu, tvr={"lat": round(tvr_lat, 8), "lon": round(tvr_lon, 8)},
        note=f"BhuNaksha centroid vs field GPS = {d} m -> {verdict}",
    )
