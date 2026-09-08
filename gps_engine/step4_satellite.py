"""
GPS Engine — STEP 4: Fetch & Stitch High-Resolution Satellite Imagery
====================================================================
Given the WGS84 centroid (Step 3) and a ground span, download the covering grid of
XYZ satellite tiles, stitch them into one image, and record the image's exact
geographic bounding box so later steps can place things on it to the pixel.

Provider: ESRI "World Imagery" (free, no API key). Swap SOURCES['esri'] for a keyed
provider (Google Static, Mapbox) if licensing requires it.

Reference: docs/CONTEXT.md §3 (Step 4).
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image

logger = logging.getLogger("GPS.Step4")

TILE = 256
USER_AGENT = "SitaaraGPSEngine/1.0 (property verification)"

SOURCES = {
    # {z}/{y}/{x}
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
}


@dataclass
class SatelliteResult:
    status: str                              # SUCCESS | FAILED
    reason: Optional[str] = None
    path: Optional[str] = None               # stitched image
    zoom: Optional[int] = None
    provider: str = "esri"
    pixel_size: Optional[Tuple[int, int]] = None
    bbox_wgs84: Optional[List[float]] = None      # [lon_min, lat_min, lon_max, lat_max]
    tiles_downloaded: int = 0
    tiles_failed: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "status": self.status, "reason": self.reason, "path": self.path,
            "zoom": self.zoom, "provider": self.provider, "pixel_size": self.pixel_size,
            "bbox_wgs84": self.bbox_wgs84, "tiles_downloaded": self.tiles_downloaded,
            "tiles_failed": self.tiles_failed, "warnings": self.warnings,
        }


# ── Web-Mercator tile math ──────────────────────────────────────────────────

def _lonlat_to_tilexy(lon: float, lat: float, z: int) -> Tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _tilexy_to_lonlat(x: float, y: float, z: int) -> Tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def _pick_zoom(lat: float, span_m: float, target_px: int = 1024, max_zoom: int = 19) -> int:
    """Largest zoom whose `span_m` of ground fits within ~target_px pixels."""
    for z in range(max_zoom, 10, -1):
        # metres per pixel at this lat/zoom (Web Mercator)
        mpp = 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)
        if span_m / mpp <= target_px:
            return z
    return 16


def meters_per_pixel(lat: float, z: int) -> float:
    return 156543.03392 * math.cos(math.radians(lat)) / (2 ** z)


# ── main ────────────────────────────────────────────────────────────────────

def fetch_satellite(
    centroid_lat: float,
    centroid_lon: float,
    *,
    span_m: float = 300.0,
    out_path: Path,
    provider: str = "esri",
    zoom: Optional[int] = None,
    session: Optional[requests.Session] = None,
    timeout: int = 20,
) -> SatelliteResult:
    """
    Args:
        span_m:  side length of the ground area to cover, centred on the centroid.
                 300 m comfortably frames a rural/peri-urban parcel + surroundings.
        zoom:    force a zoom level; otherwise auto-picked for ~1024 px.
    """
    res = SatelliteResult(status="FAILED", provider=provider)
    url_tmpl = SOURCES.get(provider)
    if not url_tmpl:
        res.reason = f"unknown_provider: {provider}"
        return res

    z = zoom or _pick_zoom(centroid_lat, span_m * 1.6)  # a little padding
    res.zoom = z

    # ground bbox around the centroid
    dlat = (span_m / 2) / 111_320.0
    dlon = (span_m / 2) / (111_320.0 * math.cos(math.radians(centroid_lat)))
    lon_min, lon_max = centroid_lon - dlon, centroid_lon + dlon
    lat_min, lat_max = centroid_lat - dlat, centroid_lat + dlat

    x0f, y0f = _lonlat_to_tilexy(lon_min, lat_max, z)   # NW corner
    x1f, y1f = _lonlat_to_tilexy(lon_max, lat_min, z)   # SE corner
    x0, y0 = math.floor(x0f), math.floor(y0f)
    x1, y1 = math.floor(x1f), math.floor(y1f)
    n = 2 ** z
    x0, x1 = max(0, x0), min(n - 1, x1)
    y0, y1 = max(0, y0), min(n - 1, y1)

    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    if cols * rows > 100:
        res.reason = f"tile_grid_too_large ({cols}x{rows}); reduce span_m or zoom"
        return res

    sess = session or requests.Session()
    sess.headers.setdefault("User-Agent", USER_AGENT)
    canvas = Image.new("RGB", (cols * TILE, rows * TILE))

    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            url = url_tmpl.format(z=z, x=tx, y=ty)
            try:
                r = sess.get(url, timeout=timeout)
                r.raise_for_status()
                tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                canvas.paste(tile, ((tx - x0) * TILE, (ty - y0) * TILE))
                res.tiles_downloaded += 1
            except Exception as e:
                res.tiles_failed += 1
                res.warnings.append(f"tile {z}/{tx}/{ty} failed: {e}")

    if res.tiles_downloaded == 0:
        res.reason = "all_tiles_failed"
        return res
    if res.tiles_failed:
        res.warnings.append(f"{res.tiles_failed} of {cols*rows} tiles missing (gaps in mosaic)")

    # the stitched image spans exactly tiles [x0..x1+1] x [y0..y1+1]
    nw_lon, nw_lat = _tilexy_to_lonlat(x0, y0, z)
    se_lon, se_lat = _tilexy_to_lonlat(x1 + 1, y1 + 1, z)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")

    res.status = "SUCCESS"
    res.path = str(out_path)
    res.pixel_size = canvas.size
    res.bbox_wgs84 = [nw_lon, se_lat, se_lon, nw_lat]   # [lon_min, lat_min, lon_max, lat_max]

    # sidecar world file for GIS tools
    _write_worldfile(out_path, res.bbox_wgs84, canvas.size)
    logger.info("Step 4: %s tiles @ z%s -> %s %s", res.tiles_downloaded, z, canvas.size, out_path.name)
    return res


def lonlat_to_pixel(lon: float, lat: float, bbox_wgs84: List[float], size: Tuple[int, int]) -> Tuple[float, float]:
    """Map a WGS84 point to a pixel in an image with the given geographic bbox.
    Uses Web-Mercator y so it matches the tile mosaic exactly."""
    lon_min, lat_min, lon_max, lat_max = bbox_wgs84
    w, h = size

    def merc_y(l):
        return math.asinh(math.tan(math.radians(l)))

    px = (lon - lon_min) / (lon_max - lon_min) * w
    y_top, y_bot = merc_y(lat_max), merc_y(lat_min)
    py = (y_top - merc_y(lat)) / (y_top - y_bot) * h
    return px, py


def _write_worldfile(img_path: Path, bbox: List[float], size: Tuple[int, int]) -> None:
    lon_min, lat_min, lon_max, lat_max = bbox
    w, h = size
    a = (lon_max - lon_min) / w
    e = -(lat_max - lat_min) / h
    pgw = img_path.with_suffix(".pgw")
    pgw.write_text(f"{a}\n0.0\n0.0\n{e}\n{lon_min + a/2}\n{lat_max + e/2}\n")
