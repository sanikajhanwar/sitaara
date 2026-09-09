"""
GPS Engine — Pipeline Orchestrator (Steps 1 → 8)
================================================
Runs the full pipeline for one parcel.

    python3 -m gps_engine.cli run --state "Maharashtra" --district "..." \
        --tehsil "..." --village "..." --khasra "..." \
        [--tvr-lat 21.1522 --tvr-lon 77.0888]

Steps 1-3  -> government GPS centroid + parcel polygon (gps_engine.step{1,2,3}_*)
Step  4    -> satellite mosaic of the same ground        (step4_satellite)
Steps 5-7  -> parcel superimposed on the satellite       (step6_overlay)
Step  8    -> Haversine distance vs the field GPS verdict (step8_distance)

Output layout
-------------
Every run gets its own immutable folder — an audit trail, so results for many
loan applications are retained instead of one run overwriting the next:

    output/
      runs/
        20260909-143022_SGRL00037212/     <- this run
          step1.json  step2.json  ...  step8.json   (each step's full record)
          step1_metadata.json                        (raw Step 1 adapter output)
          bhunaksha.png  debug_*.png                  (portal screenshots)
          parcel_native.geojson  parcel_wgs84.geojson
          satellite.png  satellite.pgw  overlay.png
          gps_engine_result.json                      (the aggregate)
      cache/                                          (shared 24 h result cache)
      latest -> runs/20260909-143022_SGRL00037212     (symlink to the newest run)

`run_id` = timestamp, suffixed with the application_id when one is given.
"""

from __future__ import annotations

import json
import logging
import re
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
    base_dir = output_dir or OUTPUT_DIR
    cache_dir = base_dir / "cache"
    run_id = _make_run_id(application_id)
    run_dir = base_dir / "runs" / run_id
    n = 2
    while run_dir.exists() and any(run_dir.iterdir()):   # same-second collision
        run_dir = base_dir / "runs" / f"{run_id}~{n}"
        n += 1
    run_id = run_dir.name
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    timings: Dict[str, float] = {}

    ckey = (state, district, tehsil, village, khasra_no, extra_params)
    if use_cache:
        cached = _cache.load(cache_dir, *ckey)
        if cached is not None:
            # Step 8 is cheap and depends on the caller's TVR GPS — always recompute it.
            c = (cached.get("parcel") or {}).get("centroid_wgs84")
            if c:
                d = verify_distance(c["lat"], c["lon"], tvr_lat, tvr_lon)
                cached["distance"] = d.to_dict()
                _write_step(run_dir, "step8", d.to_dict())
                if d.status == "SUCCESS":
                    cached["parameter5_verdict"] = d.verdict
            cached["run_id"] = run_id
            cached["output_dir"] = str(run_dir)
            (run_dir / "gps_engine_result.json").write_text(
                json.dumps(cached, indent=2, ensure_ascii=False))
            _point_latest_at(run_dir, base_dir)
            logger.info("Pipeline served from cache (%s) -> %s", cached.get("status"), run_dir)
            return cached

    result: Dict[str, Any] = {
        "application_id": application_id,
        "run_id": run_id,
        "output_dir": str(run_dir),
        "status": "FAILED",
        "reason": None,
        "state": state,
        "nav_resolved": {"district": district, "tehsil": tehsil, "village": village, "khasra": khasra_no},
        "parcel": None,
        "image": None,
        "warnings": [],
        "timing_seconds": None,
        "step_timings_seconds": timings,
    }

    # ── STEP 1 ──────────────────────────────────────────────────────────────
    _t = time.perf_counter()
    try:
        step1 = Step1Capture(output_dir=run_dir).capture_plot(
            state=state, district=district, tehsil=tehsil, village=village,
            khasra_no=khasra_no, headless=headless, extra_params=extra_params,
        )
    except (NotImplementedError, ValueError) as e:
        timings["step1"] = round(time.perf_counter() - _t, 2)
        result["reason"] = "state_not_supported"
        result["warnings"].append(str(e))
        logger.error("%s", e)
        return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)
    timings["step1"] = round(time.perf_counter() - _t, 2)
    _write_step(run_dir, "step1", step1)

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
        return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)
    if step1.get("status") == "REFER":
        result["status"] = "REFER"
        result["reason"] = step1.get("reason") or "step1_refer"
        return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)

    parcel_raw = step1.get("parcel_raw")
    if not parcel_raw or parcel_raw.get("error"):
        result["status"] = "REFER"
        result["reason"] = "no_geometry_from_step1"
        result["warnings"].append(
            f"adapter for {state} did not return parcel_raw "
            f"({parcel_raw.get('error') if parcel_raw else 'missing'}); "
            "screenshot captured but Parameter 5 cannot be verified"
        )
        return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)

    # ── STEP 2 ──────────────────────────────────────────────────────────────
    _t = time.perf_counter()
    g = extract_parcel_geometry(parcel_raw, state=state)
    timings["step2"] = round(time.perf_counter() - _t, 2)
    _write_step(run_dir, "step2", g.to_dict())
    _log_block("2", "Extract parcel geometry", {
        "Method": g.method,
        "Native CRS": g.native_crs,
        "Vertices": g.vertex_count,
        "Portal area": f"{g.raw_area} m2" if g.raw_area is not None else None,
        "Status": f"{g.status} ({g.reason})" if g.reason else g.status,
    }, g.warnings)
    result["warnings"] += g.warnings
    if not g.found:
        result["status"] = "REFER"
        result["reason"] = g.reason
        return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)

    (run_dir / "parcel_native.geojson").write_text(json.dumps({
        "type": "Feature",
        "properties": {"crs": g.native_crs, "method": g.method, "raw_area": g.raw_area,
                       "khasra": khasra_no},
        "geometry": g.geometry_native,
    }, indent=2))

    # ── STEP 3 ──────────────────────────────────────────────────────────────
    _t = time.perf_counter()
    t = transform_parcel(
        g.geometry_native,
        payload_crs=g.native_crs,
        state=state,
        bbox_native=g.bbox_native,
    )
    timings["step3"] = round(time.perf_counter() - _t, 2)
    _write_step(run_dir, "step3", t.to_dict())
    _log_block("3", "Transform to WGS84 + centroid", {
        "Source CRS": f"{t.source_crs} ({t.source_crs_confidence} confidence)" if t.source_crs else None,
        "Centroid": f"{t.centroid_wgs84['lat']}, {t.centroid_wgs84['lon']}" if t.centroid_wgs84 else None,
        "Area": f"{t.area_sqm} m2" if t.area_sqm is not None else f"{g.raw_area} m2 (portal-reported, no polygon)",
        "Vertices": t.vertex_count,
        "Status": f"{t.status} ({t.reason})" if t.reason else t.status,
    }, t.warnings)
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
        (run_dir / "parcel_wgs84.geojson").write_text(json.dumps({
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
        return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)

    # ── STEP 4: satellite mosaic ───────────────────────────────────────────
    sat_bbox = None
    if superimpose:
        _t = time.perf_counter()
        sat = fetch_satellite(
            centroid["lat"], centroid["lon"], span_m=span_m,
            out_path=run_dir / "satellite.png",
        )
        timings["step4"] = round(time.perf_counter() - _t, 2)
        _write_step(run_dir, "step4", sat.to_dict())
        _log_block("4", "Fetch & stitch satellite imagery", {
            "Provider": sat.provider,
            "Zoom level": sat.zoom,
            "Tiles": f"{sat.tiles_downloaded} downloaded, {sat.tiles_failed} failed",
            "Image size": f"{sat.pixel_size[0]}x{sat.pixel_size[1]} px" if sat.pixel_size else None,
            "BBox (lon/lat)": sat.bbox_wgs84,
            "Status": f"{sat.status} ({sat.reason})" if sat.reason else sat.status,
        }, sat.warnings)
        result["satellite"] = sat.to_dict()
        result["warnings"] += sat.warnings
        if sat.status == "SUCCESS":
            sat_bbox = sat.bbox_wgs84
        else:
            result["warnings"].append(f"step4 satellite fetch failed: {sat.reason}")

    # ── STEPS 5-7: superimpose parcel on satellite ────────────────────────
    if superimpose and sat_bbox:
        _t = time.perf_counter()
        ov = superimpose_vector(
            run_dir / "satellite.png", sat_bbox,
            t.geometry_wgs84, centroid,
            out_path=run_dir / "overlay.png",
            parcel_bbox_wgs84=t.bbox_wgs84,
        )
        timings["step6"] = round(time.perf_counter() - _t, 2)
        _write_step(run_dir, "step6", ov.to_dict())
        _log_block("5-7", "Superimpose parcel on satellite", {
            "Alignment method": ov.alignment_method,
            "Centroid pixel": ov.centroid_pixel,
            "Parcel in frame": ov.parcel_in_frame,
            "Output": "overlay.png",
            "Status": f"{ov.status} ({ov.reason})" if ov.reason else ov.status,
        }, ov.warnings)
        result["overlay"] = ov.to_dict()
        result["warnings"] += ov.warnings

    # ── STEP 8: Haversine distance verdict ────────────────────────────────
    _t = time.perf_counter()
    dist = verify_distance(centroid["lat"], centroid["lon"], tvr_lat, tvr_lon)
    timings["step8"] = round(time.perf_counter() - _t, 2)
    _write_step(run_dir, "step8", dist.to_dict())
    _log_block("8", "GPS distance verdict", {
        "BhuNaksha GPS": f"{dist.bhu['lat']}, {dist.bhu['lon']}" if dist.bhu else None,
        "Field GPS (TVR)": f"{dist.tvr['lat']}, {dist.tvr['lon']}" if dist.tvr else "not supplied",
        "Distance": f"{dist.distance_m} m" if dist.distance_m is not None else None,
        "Verdict": f"{dist.verdict} ({dist.color})" if dist.verdict else None,
        "Status": dist.status,
    })
    result["distance"] = dist.to_dict()
    if dist.status == "SUCCESS":
        result["parameter5_verdict"] = dist.verdict
        if dist.verdict == "NEGATIVE" and result["status"] == "SUCCESS":
            result["status"] = "REFER"
            result["reason"] = "gps_distance_negative"
        result["warnings"].append(dist.note)

    return _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache)


# ── run-folder helpers ─────────────────────────────────────────────────────

def _make_run_id(application_id: Optional[str]) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    if application_id:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(application_id))[:60]
        return f"{ts}_{safe}"
    return ts


def _write_step(run_dir: Path, name: str, payload: Any) -> None:
    try:
        (run_dir / f"{name}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        )
    except Exception as e:  # never let audit-file I/O break the pipeline
        logger.warning("could not write %s.json: %s", name, e)


def _log_block(num: str, title: str, facts: Dict[str, Any], warnings=()) -> None:
    """One consistent, human-readable log block per step (same style as Step 1)."""
    head = (f"GPS ENGINE — RESULT: {title.upper()}" if num == "RESULT"
            else f"GPS ENGINE — STEP {num}: {title.upper()}")
    logger.info("=" * 70)
    logger.info(" %s", head)
    logger.info("=" * 70)
    for k, v in facts.items():
        logger.info("  %-16s : %s", k, "—" if v is None else v)
    for w in warnings or ():
        logger.info("  ! %s", w)


def _point_latest_at(run_dir: Path, base_dir: Path) -> None:
    """Repoint output/latest -> runs/<this run> so the newest run is always one hop away."""
    if run_dir.parent.parent != base_dir:
        return
    link = base_dir / "latest"
    try:
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            logger.warning("output/latest exists and is not a symlink — leaving it alone")
            return
        link.symlink_to(Path("runs") / run_dir.name, target_is_directory=True)
    except Exception as e:
        logger.warning("could not update output/latest link: %s", e)


# ── result finalisation ────────────────────────────────────────────────────

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


def _finish(result: Dict[str, Any], started: float, run_dir: Path, base_dir: Path) -> Dict[str, Any]:
    elapsed = round(time.time() - started, 1)
    result["timing_seconds"] = elapsed
    if elapsed > SLA_SECONDS and result["status"] == "SUCCESS":
        result["status"] = "REFER"
        result["reason"] = "sla_exceeded"
        result["warnings"].append(f"pipeline took {elapsed}s (> {SLA_SECONDS}s SLA)")
    _validate(result)
    (run_dir / "gps_engine_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    _point_latest_at(run_dir, base_dir)

    p = result.get("parcel") or {}
    c = p.get("centroid_wgs84") or {}
    d = result.get("distance") or {}
    _log_block("RESULT", result["status"], {
        "Reason": result.get("reason"),
        "Centroid": f"{c.get('lat')}, {c.get('lon')}" if c else None,
        "Area": f"{p.get('area_sqm')} m2" if p.get("area_sqm") is not None else None,
        "Parameter 5": (
            f"{d.get('distance_m')} m -> {d.get('verdict')} ({d.get('color')})"
            if d.get("verdict") else (d.get("status") if d else None)
        ),
        "Total time": f"{elapsed}s  ({', '.join(f'{k} {v}s' for k, v in (result.get('step_timings_seconds') or {}).items())})",
        "Run folder": run_dir,
    })
    return result


def _finish_and_cache(result, started, run_dir, base_dir, cache_dir, ckey, use_cache):
    out = _finish(result, started, run_dir, base_dir)
    if use_cache:
        _cache.store(cache_dir, out, *ckey)
    return out
