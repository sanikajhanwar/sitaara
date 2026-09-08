"""
GPS Engine — STEP 3: Projection Transform → WGS84 + Centroid
============================================================
Takes a parcel polygon in a state's native projection (from Step 2) and produces:
  - the same polygon in WGS84 (EPSG:4326)
  - the authoritative government GPS centroid (bhu_lat, bhu_lon)
  - the parcel area in m2 (computed on an equal-area local projection)
  - sanity-gate verdicts

Pure geometry/projection logic — no browser, no network. Fully unit-testable.

Reference: docs/CONTEXT.md §3 (Step 3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pyproj import Transformer
from shapely.geometry import Polygon, MultiPolygon, shape, mapping
from shapely.ops import transform as shp_transform

logger = logging.getLogger("GPS.Step3")

WGS84 = "EPSG:4326"

# ── State → default UTM zone fallback (used only when Step 2 gives no CRS) ────
# Authoritative source is always the SRS name in the portal payload. This table
# is a low-confidence fallback and callers should flag when it is used.
STATE_UTM_FALLBACK: Dict[str, str] = {
    "Rajasthan":        "EPSG:32643",   # zone 43N (west); east RJ is 43N too mostly
    "Gujarat":          "EPSG:32643",
    "Maharashtra":      "EPSG:32643",   # west; Vidarbha/east ~ 44N — verify per district
    "Uttar Pradesh":    "EPSG:32644",
    "Madhya Pradesh":   "EPSG:32644",
    "Chhattisgarh":     "EPSG:32644",
    "Uttarakhand":      "EPSG:32644",
    "Haryana":          "EPSG:32643",
    "Delhi":            "EPSG:32643",
    "Bihar":            "EPSG:32645",   # east Bihar 45N; west ~ 44N
}

# Rough state bounding boxes (lon_min, lat_min, lon_max, lat_max) for the
# "centroid falls inside the state" sanity gate. Generous margins.
STATE_BBOX: Dict[str, Tuple[float, float, float, float]] = {
    "Uttar Pradesh":  (77.0, 23.8, 84.7, 30.4),
    "Rajasthan":      (69.4, 23.0, 78.3, 30.2),
    "Maharashtra":    (72.6, 15.6, 80.9, 22.1),
    "Madhya Pradesh": (74.0, 21.0, 82.8, 26.9),
    "Chhattisgarh":   (80.2, 17.7, 84.4, 24.1),
    "Uttarakhand":    (77.5, 28.7, 81.1, 31.5),
    "Bihar":          (83.2, 24.2, 88.2, 27.6),
    "Gujarat":        (68.1, 20.0, 74.5, 24.8),
    "Haryana":        (74.4, 27.6, 77.6, 30.9),
    "Delhi":          (76.8, 28.4, 77.4, 28.9),
}

MIN_PARCEL_SQM = 1.0
MAX_PARCEL_SQM = 10_000_000.0   # 10 km2


@dataclass
class TransformResult:
    status: str                       # SUCCESS | REFER | FAILED
    reason: Optional[str] = None
    source_crs: Optional[str] = None
    source_crs_confidence: str = "high"   # high (from payload) | low (from fallback table)
    centroid_wgs84: Optional[Dict[str, float]] = None   # {"lat":.., "lon":..}
    geometry_wgs84: Optional[Dict[str, Any]] = None     # GeoJSON geometry
    bbox_wgs84: Optional[List[float]] = None            # [lon_min, lat_min, lon_max, lat_max]
    area_sqm: Optional[float] = None
    vertex_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "source_crs": self.source_crs,
            "source_crs_confidence": self.source_crs_confidence,
            "centroid_wgs84": self.centroid_wgs84,
            "geometry_wgs84": self.geometry_wgs84,
            "bbox_wgs84": self.bbox_wgs84,
            "area_sqm": self.area_sqm,
            "vertex_count": self.vertex_count,
            "warnings": self.warnings,
        }


def _resolve_crs(
    payload_crs: Optional[str],
    state: Optional[str],
) -> Tuple[Optional[str], str]:
    """Return (crs, confidence)."""
    if payload_crs:
        crs = payload_crs.strip()
        # normalise a few common SRS name spellings
        if crs.upper().startswith("URN:OGC:DEF:CRS:EPSG"):
            crs = "EPSG:" + crs.rstrip(":").split(":")[-1]
        if crs.isdigit():
            crs = f"EPSG:{crs}"
        return crs, "high"
    if state and state in STATE_UTM_FALLBACK:
        return STATE_UTM_FALLBACK[state], "low"
    return None, "low"


def _local_utm_for(lat: float, lon: float) -> str:
    zone = int((lon + 180) / 6) + 1
    return f"EPSG:{32600 + zone}" if lat >= 0 else f"EPSG:{32700 + zone}"


def _count_vertices(geom) -> int:
    if geom.geom_type == "Polygon":
        return len(geom.exterior.coords)
    if geom.geom_type == "MultiPolygon":
        return sum(len(p.exterior.coords) for p in geom.geoms)
    return 0


def transform_parcel(
    native_geometry: Dict[str, Any],
    *,
    payload_crs: Optional[str] = None,
    state: Optional[str] = None,
    cross_check_latlon: Optional[Tuple[float, float]] = None,
    bbox_native: Optional[List[float]] = None,
) -> TransformResult:
    """
    Args:
        native_geometry:  GeoJSON geometry (Polygon/MultiPolygon) in the portal's native CRS
        payload_crs:      SRS string straight from the portal response (authoritative)
        state:            state name — enables fallback CRS + bbox sanity gate
        cross_check_latlon: (lat, lon) independently obtained (e.g. from a map URL) to sanity-check

    Returns:
        TransformResult
    """
    res = TransformResult(status="FAILED")

    try:
        native = shape(native_geometry)
    except Exception as e:
        res.reason = f"unparseable_geometry: {e}"
        return res

    point_only = native.geom_type == "Point"
    if native.is_empty or native.geom_type not in ("Polygon", "MultiPolygon", "Point"):
        res.reason = f"geometry_not_polygonal: {native.geom_type}"
        return res
    if not point_only and not native.is_valid:
        native = native.buffer(0)
        res.warnings.append("native geometry was invalid; repaired with buffer(0)")
    if point_only:
        res.warnings.append("input is a centroid point only — no polygon; area taken from portal-reported value")

    crs, confidence = _resolve_crs(payload_crs, state)
    res.source_crs = crs
    res.source_crs_confidence = confidence
    if crs is None:
        res.reason = "no_source_crs"
        return res
    if confidence == "low":
        res.warnings.append(
            f"source CRS not in portal payload; assumed {crs} from state fallback table"
        )

    # ── native → WGS84 ──────────────────────────────────────────────────────
    try:
        fwd = Transformer.from_crs(crs, WGS84, always_xy=True)
        wgs = shp_transform(lambda xs, ys, zs=None: fwd.transform(xs, ys), native)
        if bbox_native and len(bbox_native) == 4:
            x0, y0, x1, y1 = bbox_native
            (lo0, la0), (lo1, la1) = fwd.transform(x0, y0), fwd.transform(x1, y1)
            res.bbox_wgs84 = [min(lo0, lo1), min(la0, la1), max(lo0, lo1), max(la0, la1)]
    except Exception as e:
        res.reason = f"projection_failed: {e}"
        return res

    if wgs.is_empty:
        res.reason = "projection_produced_empty_geometry"
        return res

    centroid = wgs.centroid            # area-weighted; correct for concave parcels
    lat, lon = centroid.y, centroid.x

    # ── area on a local equal-distance UTM ──────────────────────────────────
    if point_only:
        area_sqm = None   # no polygon to measure; pipeline keeps the portal-reported value
    else:
        try:
            local = _local_utm_for(lat, lon)
            to_local = Transformer.from_crs(WGS84, local, always_xy=True)
            wgs_local = shp_transform(lambda xs, ys, zs=None: to_local.transform(xs, ys), wgs)
            area_sqm = abs(wgs_local.area)
        except Exception as e:
            area_sqm = None
            res.warnings.append(f"area computation failed: {e}")

    res.geometry_wgs84 = mapping(wgs)
    res.centroid_wgs84 = {"lat": round(lat, 8), "lon": round(lon, 8)}
    res.area_sqm = round(area_sqm, 2) if area_sqm is not None else None
    res.vertex_count = 1 if point_only else _count_vertices(wgs)

    # ── sanity gates ───────────────────────────────────────────────────────
    verdict = "SUCCESS"

    if state and state in STATE_BBOX:
        x0, y0, x1, y1 = STATE_BBOX[state]
        if not (x0 <= lon <= x1 and y0 <= lat <= y1):
            res.status = "REFER"
            res.reason = "centroid_outside_state"
            res.warnings.append(
                f"centroid ({lat:.5f},{lon:.5f}) outside {state} bbox — likely wrong CRS"
            )
            return res

    if area_sqm is not None and not (MIN_PARCEL_SQM <= area_sqm <= MAX_PARCEL_SQM):
        res.status = "REFER"
        res.reason = "implausible_parcel_area"
        res.warnings.append(f"parcel area {area_sqm:.1f} m2 outside plausible range")
        return res

    if cross_check_latlon:
        clat, clon = cross_check_latlon
        d = _haversine_m(lat, lon, clat, clon)
        if d > 300:
            verdict = "REFER"
            res.reason = "centroid_disagrees_with_map_url"
            res.warnings.append(
                f"derived centroid is {d:.0f} m from the portal map-URL lat/lon"
            )

    res.status = verdict
    return res


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import radians, sin, cos, asin, sqrt
    r = 6_371_000.0
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


# Re-export for the Haversine step (Step 8) and tests
haversine_m = _haversine_m
