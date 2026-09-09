"""
GPS Engine — STEPS 5-7: Superimpose the Cadastral Parcel on the Satellite Image
=============================================================================
Two alignment paths:

  A. vector_projection  (used when Step 2/3 produced a real polygon + CRS, e.g. Maharashtra)
     Every parcel vertex has a known WGS84 lat/lon, and the satellite mosaic (Step 4) has a
     known geographic bbox. Projecting vertex -> pixel is exact; there is nothing to align,
     so Steps 5 (GCP) and 7 (homography) collapse to a direct draw.

  B. gcp_homography     (fallback for screenshot-only states — NOT used for MH)
     Take the cadastral screenshot, pick >=4 matching landmarks (ponds, road junctions) on
     both images, cv2.findHomography, warp the cadastral layer onto the satellite.
     Left as a documented hook: `align_by_gcp()` raises NotImplementedError until a
     screenshot-only state needs it.

Output: output/overlay.png  — satellite underneath, parcel boundary (45% fill + solid edge)
        on top, a pin on the verified centroid.

Reference: docs/CONTEXT.md §3 (Steps 5-7).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from gps_engine.step4_satellite import lonlat_to_pixel

logger = logging.getLogger("GPS.Step6")

CADASTRAL_ALPHA = 0.45   # doc: 45% cadastral, 55% satellite
EDGE_RGB = (255, 60, 60)
FILL_RGBA = (255, 60, 60, int(255 * CADASTRAL_ALPHA))
PIN_RGB = (255, 214, 0)


@dataclass
class OverlayResult:
    status: str
    reason: Optional[str] = None
    path: Optional[str] = None
    alignment_method: Optional[str] = None       # vector_projection | gcp_homography
    parcel_pixels: Optional[List[List[float]]] = None
    centroid_pixel: Optional[List[float]] = None
    parcel_in_frame: Optional[bool] = None       # is the whole parcel inside the image?
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "reason": self.reason, "path": self.path,
            "alignment_method": self.alignment_method,
            "centroid_pixel": self.centroid_pixel,
            "parcel_in_frame": self.parcel_in_frame,
            "warnings": self.warnings,
        }


def _dashed_line(draw: ImageDraw.ImageDraw, p0, p1, color, width=2, dash=8):
    x0, y0 = p0
    x1, y1 = p1
    length = max(1.0, ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)
    steps = int(length / dash)
    for i in range(0, steps, 2):
        a = i / steps
        b = min(1.0, (i + 1) / steps)
        draw.line([x0 + (x1 - x0) * a, y0 + (y1 - y0) * a,
                   x0 + (x1 - x0) * b, y0 + (y1 - y0) * b], fill=color, width=width)


def _rings(geometry_wgs84: Dict[str, Any]) -> List[List[Tuple[float, float]]]:
    t = geometry_wgs84["type"]
    c = geometry_wgs84["coordinates"]
    if t == "Point":
        return []
    if t == "Polygon":
        return [ring for ring in c]
    if t == "MultiPolygon":
        return [ring for poly in c for ring in poly]
    return []


def superimpose_vector(
    satellite_png: Path,
    satellite_bbox_wgs84: List[float],
    geometry_wgs84: Dict[str, Any],
    centroid_wgs84: Dict[str, float],
    *,
    out_path: Path,
    parcel_bbox_wgs84: Optional[List[float]] = None,
) -> OverlayResult:
    """
    Path A — direct projection. Exact when the parcel geometry is a real polygon.
    When it is only a centroid Point, `parcel_bbox_wgs84` (the plot's bounding box, from
    the portal) is drawn as a dashed rectangle so the report still shows the plot footprint.
    """
    res = OverlayResult(status="FAILED", alignment_method="vector_projection")

    try:
        base = Image.open(satellite_png).convert("RGBA")
    except Exception as e:
        res.reason = f"cannot_open_satellite: {e}"
        return res
    size = base.size

    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    all_px: List[List[float]] = []
    in_frame = True
    for ring in _rings(geometry_wgs84):
        pts = []
        for lon, lat in ring:
            px, py = lonlat_to_pixel(lon, lat, satellite_bbox_wgs84, size)
            pts.append((px, py))
            if not (0 <= px <= size[0] and 0 <= py <= size[1]):
                in_frame = False
        if len(pts) >= 3:
            draw.polygon(pts, fill=FILL_RGBA, outline=EDGE_RGB, width=3)
            all_px.append([[round(x, 1), round(y, 1)] for x, y in pts])

    # centroid-only: draw the portal bounding box as a dashed footprint
    if not all_px and parcel_bbox_wgs84 and len(parcel_bbox_wgs84) == 4:
        lo0, la0, lo1, la1 = parcel_bbox_wgs84
        corners = [(lo0, la1), (lo1, la1), (lo1, la0), (lo0, la0), (lo0, la1)]
        bpx = [lonlat_to_pixel(lo, la, satellite_bbox_wgs84, size) for lo, la in corners]
        for i in range(len(bpx) - 1):
            _dashed_line(draw, bpx[i], bpx[i + 1], EDGE_RGB, width=3, dash=9)
        res.warnings.append("no polygon from portal — drew the plot bounding box (dashed) + centroid pin")
        res.parcel_pixels = [[[round(x, 1), round(y, 1)] for x, y in bpx]]

    # centroid pin
    cx, cy = lonlat_to_pixel(centroid_wgs84["lon"], centroid_wgs84["lat"], satellite_bbox_wgs84, size)
    r = 7
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=PIN_RGB, outline=(0, 0, 0), width=2)
    draw.line([cx - r * 2, cy, cx + r * 2, cy], fill=(0, 0, 0), width=1)
    draw.line([cx, cy - r * 2, cx, cy + r * 2], fill=(0, 0, 0), width=1)

    is_point = geometry_wgs84.get("type") == "Point"
    if not is_point and not all_px:
        res.reason = "no_polygon_rings_to_draw"
        return res
    if is_point and not (parcel_bbox_wgs84 and len(parcel_bbox_wgs84) == 4):
        res.warnings.append("parcel is a centroid point only, and no bounding box — drew just the pin")

    composite = Image.alpha_composite(base, overlay).convert("RGB")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    composite.save(out_path, "PNG")

    res.status = "SUCCESS"
    res.path = str(out_path)
    res.parcel_pixels = all_px or None
    res.centroid_pixel = [round(cx, 1), round(cy, 1)]
    res.parcel_in_frame = in_frame if all_px else None
    if all_px and not in_frame:
        res.warnings.append("parcel extends outside the satellite frame — increase span_m in Step 4")
    logger.debug("Step 6: overlay -> %s (%s)", out_path.name, res.alignment_method)
    return res


def align_by_gcp(*args, **kwargs) -> OverlayResult:
    """
    Path B — GCP + homography, for screenshot-only states (no vector coordinates).
    Not needed for Maharashtra. Implement when the first such state is wired:
      1. render the cadastral screenshot with a transparent (keyed-out) background
      2. collect >= 4 (cadastral_px, satellite_px) landmark pairs
      3. H, _ = cv2.findHomography(src, dst, cv2.RANSAC)
      4. cv2.warpPerspective the cadastral layer, then alpha-composite at CADASTRAL_ALPHA
    """
    raise NotImplementedError(
        "gcp_homography path is not implemented — only needed for screenshot-only states"
    )
