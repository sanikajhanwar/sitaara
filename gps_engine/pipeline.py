"""
GPS Engine — Pipeline Orchestrator (Steps 1 → 8)
================================================
Runs the full pipeline for one parcel and emits `output/gps_engine_result.json`.

    python3 -m gps_engine.cli run --state "Maharashtra" --district "..." \
        --tehsil "..." --village "..." --khasra "..." \
        [--tvr-lat 21.1522 --tvr-lon 77.0888]

Steps 1-3  -> government GPS centroid + parcel polygon (gps_engine.step{1,2,3}_*)
Step  4    -> satellite mosaic of the same ground        (step4_satellite)
Steps 5-7  -> parcel superimposed on the satellite       (step6_overlay)
Step  8    -> Haversine distance vs the field GPS verdict (step8_distance)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from gps_engine import cache as _cache
from gps_engine.config import OUTPUT_DIR, CADASTRAL_PORTALS
from gps_engine.step1_capture import Step1Capture
from gps_engine.step2_geometry import extract_parcel_geometry
from gps_engine.step3_transform import transform_parcel
from gps_engine.step4_satellite import fetch_satellite
from gps_engine.step6_overlay import superimpose_vector
from gps_engine.step8_distance import verify_distance

logger = logging.getLogger("GPS.Pipeline")

SLA_SECONDS = 180   # BRD 6.5


def run(
    state: str,
    district: str,
    tehsil: str,
    village: str,
    khasra_no: str,
    *,
    application_id: Optional[str] = None,
    headless: bool = True,
    extra_params: Optional[Dict[str, Any]] = None,
    output_dir: Optional[Path] = None,
    tvr_lat: Optional[float] = None,
    tvr_lon: Optional[float] = None,
    span_m: float = 300.0,
    superimpose: bool = True,
    use_cache: bool = True,
) -> Dict[str, Any]:
    output_dir = output_dir or OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    ckey = (state, district, tehsil, village, khasra_no, extra_params)
    if use_cache:
        cached = _cache.load(output_dir / "cache", *ckey)
        if cached is not None:
            # Step 8 is cheap and depends on the caller's TVR GPS — always recompute it.
            c = cached.get("parcel", {}).get("centroid_wgs84")
            if c:
                d = verify_distance(c["lat"], c["lon"], tvr_lat, tvr_lon)
                cached["distance"] = d.to_dict()
                if d.status == "SUCCESS":
                    cached["parameter5_verdict"] = d.verdict
            (output_dir / "gps_engine_result.json").write_text(
                json.dumps(cached, indent=2, ensure_ascii=False))
            logger.info("Pipeline served from cache (%s)", cached.get("status"))
            return cached

    result: Dict[str, Any] = {
        "application_id": application_id,
        "status": "FAILED",
        "reason": None,
        "state": state,
        "nav_resolved": {"district": district, "tehsil": tehsil, "village": village, "khasra": khasra_no},
        "parcel": None,
        "image": None,
        "warnings": [],
        "timing_seconds": None,
    }

    # ── STEP 1 ──────────────────────────────────────────────────────────────
    step1 = Step1Capture(output_dir=output_dir).capture_plot(
        state=state, district=district, tehsil=tehsil, village=village,
        khasra_no=khasra_no, headless=headless, extra_params=extra_params,
    )
    result["portal"] = {
        "name": CADASTRAL_PORTALS.get(state, {}).get("adapter"),
        "url": step1.get("portal_url"),
    }
    result["image"] = {
        "path": step1.get("image_path"),
        "map_meta": step1.get("map_meta"),
    }
    result["debug_screenshots"] = step1.get("debug_screenshots")
    result["warnings"] += step1.get("name_warnings") or []
    if (step1.get("parcel_raw") or {}).get("info"):
        result["portal_owner_info"] = step1["parcel_raw"]["info"]

    if step1.get("status") == "FAILED":
        result["reason"] = step1.get("error") or "step1_failed"
        return _finish_and_cache(result, started, output_dir, ckey, use_cache)
    if step1.get("status") == "REFER":
        result["status"] = "REFER"
        result["reason"] = step1.get("reason") or "step1_refer"
        return _finish_and_cache(result, started, output_dir, ckey, use_cache)

    parcel_raw = step1.get("parcel_raw")
    if not parcel_raw or parcel_raw.get("error"):
        result["status"] = "REFER"
        result["reason"] = "no_geometry_from_step1"
        result["warnings"].append(
            f"adapter for {state} did not return parcel_raw "
            f"({parcel_raw.get('error') if parcel_raw else 'missing'}); "
            "screenshot captured but Parameter 5 cannot be verified"
        )
        return _finish_and_cache(result, started, output_dir, ckey, use_cache)

    # ── STEP 2 ──────────────────────────────────────────────────────────────
    g = extract_parcel_geometry(parcel_raw, state=state)
    result["warnings"] += g.warnings
    if not g.found:
        result["status"] = "REFER"
        result["reason"] = g.reason
        return _finish_and_cache(result, started, output_dir, ckey, use_cache)

    (output_dir / "parcel_native.geojson").write_text(json.dumps({
        "type": "Feature",
        "properties": {"crs": g.native_crs, "method": g.method, "raw_area": g.raw_area,
                       "khasra": khasra_no},
        "geometry": g.geometry_native,
    }, indent=2))

    # ── STEP 3 ──────────────────────────────────────────────────────────────
    t = transform_parcel(
        g.geometry_native,
        payload_crs=g.native_crs,
        state=state,
        bbox_native=g.bbox_native,
    )
    result["warnings"] += t.warnings

    result["parcel"] = {
        "found": True,
        "source_crs": t.source_crs,
        "source_crs_confidence": t.source_crs_confidence,
        "extraction_method": g.method,
        "geometry_wgs84": t.geometry_wgs84,
        "bbox_wgs84": t.bbox_wgs84,
        "centroid_wgs84": t.centroid_wgs84,
        "area_sqm": t.area_sqm if t.area_sqm is not None else g.raw_area,
        "area_sqm_source": "computed_from_polygon" if t.area_sqm is not None else "portal_reported",
        "area_sqm_portal_reported": g.raw_area,
        "polygon_available": g.method not in ("centroid_point", "bbox"),
        "vertex_count": t.vertex_count,
    }

    if t.status == "SUCCESS":
        (output_dir / "parcel_wgs84.geojson").write_text(json.dumps({
            "type": "Feature",
            "properties": {"centroid": t.centroid_wgs84, "area_sqm": t.area_sqm},
            "geometry": t.geometry_wgs84,
        }, indent=2))
        result["status"] = "SUCCESS"
    else:
        result["status"] = "REFER"
        result["reason"] = t.reason

    centroid = t.centroid_wgs84
    if not centroid:
        return _finish_and_cache(result, started, output_dir, ckey, use_cache)

    # ── STEP 4: satellite mosaic ───────────────────────────────────────────
    sat_bbox = sat_size = None
    if superimpose:
        sat = fetch_satellite(
            centroid["lat"], centroid["lon"], span_m=span_m,
            out_path=output_dir / "satellite.png",
        )
        result["satellite"] = sat.to_dict()
        result["warnings"] += sat.warnings
        if sat.status == "SUCCESS":
            sat_bbox, sat_size = sat.bbox_wgs84, sat.pixel_size
        else:
            result["warnings"].append(f"step4 satellite fetch failed: {sat.reason}")

    # ── STEPS 5-7: superimpose parcel on satellite ────────────────────────
    if superimpose and sat_bbox:
        ov = superimpose_vector(
            output_dir / "satellite.png", sat_bbox,
            t.geometry_wgs84, centroid,
            out_path=output_dir / "overlay.png",
            parcel_bbox_wgs84=t.bbox_wgs84,
        )
        result["overlay"] = ov.to_dict()
        result["warnings"] += ov.warnings

    # ── STEP 8: Haversine distance verdict ────────────────────────────────
    dist = verify_distance(centroid["lat"], centroid["lon"], tvr_lat, tvr_lon)
    result["distance"] = dist.to_dict()
    if dist.status == "SUCCESS":
        result["parameter5_verdict"] = dist.verdict
        if dist.verdict == "NEGATIVE" and result["status"] == "SUCCESS":
            result["status"] = "REFER"
            result["reason"] = "gps_distance_negative"
        result["warnings"].append(dist.note)

    return _finish_and_cache(result, started, output_dir, ckey, use_cache)


_SCHEMA_PATH = Path(__file__).parent / "schema" / "gps_engine_result.schema.json"


def _validate(result: Dict[str, Any]) -> None:
    try:
        import jsonschema
        schema = json.loads(_SCHEMA_PATH.read_text())
        jsonschema.validate(result, schema)
    except ImportError:
        result.setdefault("warnings", []).append("jsonschema not installed — result not validated")
    except Exception as e:  # jsonschema.ValidationError
        result.setdefault("warnings", []).append(f"result failed schema validation: {e}")
        logger.warning("result JSON failed schema validation: %s", e)


def _finish(result: Dict[str, Any], started: float, output_dir: Path) -> Dict[str, Any]:
    elapsed = round(time.time() - started, 1)
    result["timing_seconds"] = elapsed
    if elapsed > SLA_SECONDS and result["status"] == "SUCCESS":
        result["status"] = "REFER"
        result["reason"] = "sla_exceeded"
        result["warnings"].append(f"pipeline took {elapsed}s (> {SLA_SECONDS}s SLA)")
    _validate(result)
    (output_dir / "gps_engine_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    logger.info("Pipeline %s (%s) in %ss", result["status"], result.get("reason"), elapsed)
    return result


def _finish_and_cache(result, started, output_dir, ckey, use_cache):
    out = _finish(result, started, output_dir)
    if use_cache:
        _cache.store(output_dir / "cache", out, *ckey)
    return out
