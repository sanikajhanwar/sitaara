"""
Offline tests for Steps 4 (tile math), 6 (projection), 8 (Haversine verdict).
Step 4's network download and Step 6's compositing are exercised by the live
`cli run` against Maharashtra, not here.
"""

import math
from pathlib import Path

from PIL import Image

from gps_engine.step4_satellite import (
    _lonlat_to_tilexy, _tilexy_to_lonlat, _pick_zoom, meters_per_pixel, lonlat_to_pixel,
)
from gps_engine.step6_overlay import superimpose_vector
from gps_engine.step8_distance import verify_distance


# ── Step 4 ──────────────────────────────────────────────────────────────────

def test_tilexy_roundtrip():
    lon, lat, z = 77.0888, 21.1522, 18
    x, y = _lonlat_to_tilexy(lon, lat, z)
    lon2, lat2 = _tilexy_to_lonlat(x, y, z)
    assert abs(lon - lon2) < 1e-9 and abs(lat - lat2) < 1e-9


def test_pick_zoom_reasonable():
    assert _pick_zoom(21.15, 300) in (17, 18, 19)
    assert _pick_zoom(21.15, 5000) < _pick_zoom(21.15, 300)


def test_meters_per_pixel_shrinks_with_zoom():
    assert meters_per_pixel(21.15, 19) < meters_per_pixel(21.15, 18) < meters_per_pixel(21.15, 17)


def test_lonlat_to_pixel_corners_and_centre():
    bbox = [77.0, 21.0, 77.01, 21.01]   # lon_min, lat_min, lon_max, lat_max
    size = (1000, 1000)
    # NW corner -> (0, 0)
    px, py = lonlat_to_pixel(77.0, 21.01, bbox, size)
    assert abs(px) < 1 and abs(py) < 1
    # SE corner -> (w, h)
    px, py = lonlat_to_pixel(77.01, 21.0, bbox, size)
    assert abs(px - 1000) < 1 and abs(py - 1000) < 2
    # centre -> ~middle
    px, py = lonlat_to_pixel(77.005, 21.005, bbox, size)
    assert 480 < px < 520 and 480 < py < 520


# ── Step 6 ──────────────────────────────────────────────────────────────────

def test_superimpose_vector_draws_and_reports(tmp_path):
    sat = tmp_path / "sat.png"
    Image.new("RGB", (400, 400), (30, 60, 30)).save(sat)
    bbox = [77.0, 21.0, 77.01, 21.01]
    geom = {"type": "Polygon", "coordinates": [[
        [77.004, 21.004], [77.006, 21.004], [77.006, 21.006], [77.004, 21.006], [77.004, 21.004]]]}
    centroid = {"lat": 21.005, "lon": 77.005}
    r = superimpose_vector(sat, bbox, geom, centroid, out_path=tmp_path / "ov.png")
    assert r.status == "SUCCESS"
    assert r.alignment_method == "vector_projection"
    assert r.parcel_in_frame is True
    assert Path(r.path).exists()
    assert 190 < r.centroid_pixel[0] < 210


def test_superimpose_flags_parcel_outside_frame(tmp_path):
    sat = tmp_path / "sat.png"
    Image.new("RGB", (400, 400), (0, 0, 0)).save(sat)
    bbox = [77.0, 21.0, 77.01, 21.01]
    geom = {"type": "Polygon", "coordinates": [[
        [77.02, 21.02], [77.03, 21.02], [77.03, 21.03], [77.02, 21.03], [77.02, 21.02]]]}
    r = superimpose_vector(sat, bbox, geom, {"lat": 21.025, "lon": 77.025}, out_path=tmp_path / "ov.png")
    assert r.status == "SUCCESS" and r.parcel_in_frame is False
    assert any("outside the satellite frame" in w for w in r.warnings)


# ── Step 8 ──────────────────────────────────────────────────────────────────

def test_distance_match_green():
    r = verify_distance(21.15220, 77.08880, 21.15238, 77.08875)   # ~21 m
    assert r.status == "SUCCESS" and r.verdict == "MATCH" and r.color == "GREEN"
    assert r.distance_m < 50


def test_distance_refer_amber():
    r = verify_distance(21.15220, 77.08880, 21.15320, 77.08880)   # ~110 m
    assert r.verdict == "REFER" and r.color == "AMBER"


def test_distance_negative_red():
    r = verify_distance(21.15220, 77.08880, 21.16000, 77.08880)   # ~870 m
    assert r.verdict == "NEGATIVE" and r.color == "RED"


def test_distance_skipped_without_tvr():
    r = verify_distance(21.15220, 77.08880, None, None)
    assert r.status == "SKIPPED" and r.verdict is None
    assert "no Technical Valuation Report GPS" in r.note
