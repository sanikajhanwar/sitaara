"""
GPS Engine — STEP 2: Capture the Plot Boundary as Vector Data
=============================================================
Turns whatever the state portal handed back (during the Step 1 browser run) into a
single normalised parcel geometry in the portal's *native* projection.

No browser here. Step 1 adapters are responsible for collecting the raw material
(`parcel_raw`) and handing it to `extract_parcel_geometry()`.

Supported raw shapes (in priority order):
  1. `wkt`        — a WKT POLYGON / MULTIPOLYGON string  (modern NIC `getPlotInfo.the_geom`)
  2. `geojson`    — a GeoJSON geometry or Feature/FeatureCollection (GeoServer WFS `outputFormat=json`)
  3. `gml`        — a GML <gml:posList> / <gml:coordinates> block (classic WFS GetFeature)
  4. `ol_coords`  — raw OpenLayers getCoordinates() nested arrays + the map projection
  5. `bbox`       — last-resort: [xmin,ymin,xmax,ymax]; yields a rectangle + a warning

Reference: docs/CONTEXT.md §3 (Step 2).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shapely.geometry import shape, box, mapping, Polygon, MultiPolygon
from shapely import wkt as shapely_wkt

logger = logging.getLogger("GPS.Step2")


@dataclass
class GeometryResult:
    found: bool
    status: str                                   # SUCCESS | REFER | FAILED
    reason: Optional[str] = None
    method: Optional[str] = None                   # wkt | geojson | gml | ol_coords | bbox
    native_crs: Optional[str] = None               # SRS string from the payload if present
    geometry_native: Optional[Dict[str, Any]] = None   # GeoJSON geometry, native coords
    raw_area: Optional[float] = None               # area value the portal itself reported
    bbox_native: Optional[List[float]] = None
    vertex_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "found": self.found,
            "status": self.status,
            "reason": self.reason,
            "method": self.method,
            "native_crs": self.native_crs,
            "geometry_native": self.geometry_native,
            "raw_area": self.raw_area,
            "bbox_native": self.bbox_native,
            "vertex_count": self.vertex_count,
            "warnings": self.warnings,
        }


# EPSG code for a few OpenLayers projection identifiers
_OL_PROJ_MAP = {
    "EPSG:3857": "EPSG:3857",
    "EPSG:900913": "EPSG:3857",
    "EPSG:102100": "EPSG:3857",
    "EPSG:4326": "EPSG:4326",
}


def _poly_from_shapely(geom) -> Optional[Dict[str, Any]]:
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        # e.g. GeometryCollection — take the largest polygonal part
        polys = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")]
        if not polys:
            return None
        geom = max(polys, key=lambda g: g.area)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return mapping(geom)


def _count_vertices(geojson_geom: Dict[str, Any]) -> int:
    try:
        g = shape(geojson_geom)
        if g.geom_type == "Polygon":
            return len(g.exterior.coords)
        if g.geom_type == "MultiPolygon":
            return sum(len(p.exterior.coords) for p in g.geoms)
    except Exception:
        pass
    return 0


def _from_wkt(raw: str) -> Optional[Dict[str, Any]]:
    try:
        return _poly_from_shapely(shapely_wkt.loads(raw.strip()))
    except Exception as e:
        logger.warning("WKT parse failed: %s", e)
        return None


def _from_geojson(raw: Any) -> Optional[Dict[str, Any]]:
    try:
        if isinstance(raw, str):
            import json
            raw = json.loads(raw)
        if raw.get("type") == "FeatureCollection":
            feats = raw.get("features", [])
            if not feats:
                return None
            geoms = [shape(f["geometry"]) for f in feats if f.get("geometry")]
            merged = geoms[0]
            for g in geoms[1:]:
                merged = merged.union(g)
            return _poly_from_shapely(merged)
        if raw.get("type") == "Feature":
            return _poly_from_shapely(shape(raw["geometry"]))
        return _poly_from_shapely(shape(raw))
    except Exception as e:
        logger.warning("GeoJSON parse failed: %s", e)
        return None


def _from_gml(raw: str) -> Optional[Dict[str, Any]]:
    """Handle <gml:posList> (space-separated lat lon lat lon ...) and <gml:coordinates> (x,y x,y)."""
    try:
        m = re.search(r"<gml:posList[^>]*>([\s\d.\-eE]+)</gml:posList>", raw)
        if m:
            nums = [float(x) for x in m.group(1).split()]
            # posList is typically lat lon (axis order EPSG:4326) OR x y — caller must set CRS.
            pts = list(zip(nums[0::2], nums[1::2]))
            return _poly_from_shapely(Polygon(pts))
        m = re.search(r"<gml:coordinates[^>]*>([\s\d.,\-eE]+)</gml:coordinates>", raw)
        if m:
            pts = [tuple(float(v) for v in pair.split(",")) for pair in m.group(1).split()]
            return _poly_from_shapely(Polygon(pts))
    except Exception as e:
        logger.warning("GML parse failed: %s", e)
    return None


def _from_ol_coords(coords: Any) -> Optional[Dict[str, Any]]:
    try:
        # OL Polygon coords: [ [ [x,y], ... ] ]   ; MultiPolygon: [ [ [ [x,y],... ] ] ]
        def depth(a):
            d = 0
            while isinstance(a, (list, tuple)) and a:
                d += 1
                a = a[0]
            return d
        dp = depth(coords)
        if dp == 3:
            return _poly_from_shapely(Polygon(coords[0], coords[1:]))
        if dp == 4:
            return _poly_from_shapely(MultiPolygon([(p[0], p[1:]) for p in coords]))
    except Exception as e:
        logger.warning("OL coords parse failed: %s", e)
    return None


def extract_parcel_geometry(parcel_raw: Dict[str, Any], *, state: Optional[str] = None) -> GeometryResult:
    """
    parcel_raw keys (any subset; tried in priority order):
        wkt: str
        geojson: dict|str
        gml: str
        ol_coords: nested list
        ol_projection: str            (required when ol_coords is used)
        centroid_native: [x, y]       (classic NIC OP=5 center_x/center_y — no polygon available)
        bbox: [xmin, ymin, xmax, ymax]
        native_crs: str               (SRS from the payload, e.g. "EPSG:32643")
        raw_area: float               (area the portal displayed)
    """
    native_crs = parcel_raw.get("native_crs")
    raw_area = parcel_raw.get("raw_area")
    bbox = parcel_raw.get("bbox")

    geom = None
    method = None

    if parcel_raw.get("wkt"):
        geom = _from_wkt(parcel_raw["wkt"])
        method = "wkt"
    if geom is None and parcel_raw.get("geojson") is not None:
        geom = _from_geojson(parcel_raw["geojson"])
        method = "geojson"
    if geom is None and parcel_raw.get("gml"):
        geom = _from_gml(parcel_raw["gml"])
        method = "gml"
    if geom is None and parcel_raw.get("ol_coords") is not None:
        geom = _from_ol_coords(parcel_raw["ol_coords"])
        method = "ol_coords"
        native_crs = native_crs or _OL_PROJ_MAP.get(parcel_raw.get("ol_projection", ""), parcel_raw.get("ol_projection"))

    if geom is not None:
        vc = _count_vertices(geom)
        if vc < 4:  # a closed ring needs >= 4 points (first == last)
            return GeometryResult(
                found=False, status="REFER", reason="degenerate_geometry",
                method=method, native_crs=native_crs, raw_area=raw_area,
                warnings=[f"parsed geometry has only {vc} vertices"],
            )
        g = shape(geom)
        return GeometryResult(
            found=True, status="SUCCESS", method=method, native_crs=native_crs,
            geometry_native=geom, raw_area=raw_area,
            bbox_native=list(g.bounds), vertex_count=vc,
        )

    # ── centroid-only (classic NIC ScalarDatahandler OP=5 gives center_x/center_y
    #    + bbox but no polygon — the parcel is server-rendered by WMS) ──────────
    cn = parcel_raw.get("centroid_native")
    if cn and len(cn) == 2:
        from shapely.geometry import Point
        return GeometryResult(
            found=True, status="SUCCESS", reason="centroid_only_no_polygon",
            method="centroid_point", native_crs=native_crs,
            geometry_native=mapping(Point(cn[0], cn[1])), raw_area=raw_area,
            bbox_native=list(bbox) if bbox and len(bbox) == 4 else None,
            vertex_count=1,
            warnings=["portal exposes only a plot centroid + bbox (no polygon); "
                      "centroid is authoritative for Parameter 5, overlay will use the bbox"],
        )

    # ── last resort: rectangle from bbox ──────────────────────────────────
    if bbox and len(bbox) == 4:
        g = box(*bbox)
        return GeometryResult(
            found=True, status="REFER", reason="geometry_from_bbox_only",
            method="bbox", native_crs=native_crs,
            geometry_native=mapping(g), raw_area=raw_area,
            bbox_native=list(bbox), vertex_count=5,
            warnings=["no polygon available; using the plot bounding box as an approximation"],
        )

    return GeometryResult(
        found=False, status="REFER", reason="geometry_unavailable",
        native_crs=native_crs, raw_area=raw_area,
        warnings=["portal returned no vector geometry, GeoJSON, GML, OL feature, bbox, or centroid"],
    )
