"""
Offline tests for Step 2 (geometry extraction) and Step 3 (WGS84 transform).
No network / browser. Fixtures are real captures under tests/fixtures/.

    python3 -m pytest gps_engine/tests -q
"""

import json
import math
from pathlib import Path

import pytest

from gps_engine.step2_geometry import extract_parcel_geometry
from gps_engine.step3_transform import transform_parcel, haversine_m

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


# ── Step 2 ──────────────────────────────────────────────────────────────────

def test_step2_wkt_multipolygon():
    raw = load("mh_akolakhed_survey3_getplotinfo.json")
    g = extract_parcel_geometry(raw, state="Maharashtra")
    assert g.found and g.status == "SUCCESS"
    assert g.method == "wkt"
    assert g.native_crs == "EPSG:32643"
    assert g.vertex_count >= 4
    assert g.geometry_native["type"] in ("Polygon", "MultiPolygon")


def test_step2_geojson_feature():
    raw = {"geojson": {"type": "Feature", "properties": {},
                       "geometry": {"type": "Polygon",
                                    "coordinates": [[[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]]}},
           "native_crs": "EPSG:32644"}
    g = extract_parcel_geometry(raw)
    assert g.found and g.method == "geojson" and g.vertex_count == 5


def test_step2_bbox_only_is_refer():
    g = extract_parcel_geometry({"bbox": [100, 200, 150, 260], "native_crs": "EPSG:32643"})
    assert g.found and g.status == "REFER" and g.reason == "geometry_from_bbox_only"


def test_step2_centroid_point_is_success():
    raw = load("up_nadini_gata106_bbox.json")
    g = extract_parcel_geometry(raw, state="Uttar Pradesh")
    assert g.found and g.status == "SUCCESS"
    assert g.method == "centroid_point"
    assert g.geometry_native["type"] == "Point"
    assert any("only a plot centroid" in w for w in g.warnings)


def test_step2_nothing_usable():
    g = extract_parcel_geometry({"native_crs": "EPSG:32643"})
    assert not g.found and g.reason == "geometry_unavailable"


def test_step2_degenerate_geometry():
    raw = {"geojson": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [0, 0]]]}}
    g = extract_parcel_geometry(raw)
    assert not g.found and g.reason == "degenerate_geometry"


# ── Step 3 ──────────────────────────────────────────────────────────────────

def test_step3_mh_golden_centroid_and_area():
    raw = load("mh_akolakhed_survey3_getplotinfo.json")
    g = extract_parcel_geometry(raw, state="Maharashtra")
    t = transform_parcel(g.geometry_native, payload_crs=g.native_crs, state="Maharashtra")
    assert t.status == "SUCCESS"
    exp = raw["expected_centroid"]
    d = haversine_m(t.centroid_wgs84["lat"], t.centroid_wgs84["lon"], exp["lat"], exp["lon"])
    assert d < 30, f"centroid off by {d:.0f} m"
    dev = abs(t.area_sqm - raw["raw_area"]) / raw["raw_area"] * 100
    assert dev < raw["expected_area_tolerance_pct"], f"area deviation {dev:.2f}%"


def test_step3_up_golden_record_within_brd_tolerance():
    """BRD section 10 golden record: Gata 106, Nadini, Khajani, Gorakhpur -> 26.619406N 83.199410E."""
    raw = load("up_nadini_gata106_bbox.json")
    g = extract_parcel_geometry(raw, state="Uttar Pradesh")
    t = transform_parcel(g.geometry_native, payload_crs=g.native_crs, state="Uttar Pradesh")
    assert t.status == "SUCCESS"
    exp = raw["brd_expected_centroid"]
    d = haversine_m(t.centroid_wgs84["lat"], t.centroid_wgs84["lon"], exp["lat"], exp["lon"])
    assert d < raw["brd_tolerance_m"], f"centroid {d:.0f} m from BRD golden record (tol {raw['brd_tolerance_m']} m)"


def test_step3_point_geometry_has_no_computed_area():
    raw = load("up_nadini_gata106_bbox.json")
    g = extract_parcel_geometry(raw, state="Uttar Pradesh")
    t = transform_parcel(g.geometry_native, payload_crs=g.native_crs, state="Uttar Pradesh")
    assert t.area_sqm is None            # no polygon -> pipeline falls back to raw_area
    assert t.geometry_wgs84["type"] == "Point"
    assert t.vertex_count == 1


def test_step3_roundtrip_precision():
    raw = load("mh_akolakhed_survey3_getplotinfo.json")
    g = extract_parcel_geometry(raw, state="Maharashtra")
    t = transform_parcel(g.geometry_native, payload_crs="EPSG:32643", state="Maharashtra")
    from pyproj import Transformer
    back = Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    lon = t.centroid_wgs84["lon"]; lat = t.centroid_wgs84["lat"]
    x, y = back.transform(lon, lat)
    # centroid of the native polygon
    from shapely.geometry import shape
    cx, cy = shape(g.geometry_native).centroid.x, shape(g.geometry_native).centroid.y
    assert math.hypot(x - cx, y - cy) < 0.5


def test_step3_wrong_crs_trips_state_gate():
    raw = load("mh_akolakhed_survey3_getplotinfo.json")
    g = extract_parcel_geometry(raw, state="Maharashtra")
    # force a wrong zone -> centroid lands outside Maharashtra
    t = transform_parcel(g.geometry_native, payload_crs="EPSG:32644", state="Maharashtra")
    assert t.status == "REFER" and t.reason == "centroid_outside_state"


def test_step3_no_crs_no_state():
    raw = load("mh_akolakhed_survey3_getplotinfo.json")
    g = extract_parcel_geometry(raw, state="Maharashtra")
    t = transform_parcel(g.geometry_native, payload_crs=None, state=None)
    assert t.status == "FAILED" and t.reason == "no_source_crs"


def test_step3_fallback_crs_flagged_low_confidence():
    raw = load("mh_akolakhed_survey3_getplotinfo.json")
    g = extract_parcel_geometry(raw, state="Maharashtra")
    t = transform_parcel(g.geometry_native, payload_crs=None, state="Maharashtra")
    assert t.source_crs_confidence == "low"
    assert any("fallback" in w for w in t.warnings)
