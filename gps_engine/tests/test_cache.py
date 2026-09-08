"""Offline tests for the 24h result cache."""

import time

from gps_engine import cache


ARGS = ("Maharashtra", "Akola", "Akot", "Akolkhed", "3", None)


def test_store_and_load_roundtrip(tmp_path):
    result = {"status": "SUCCESS", "parcel": {"centroid_wgs84": {"lat": 21.15, "lon": 77.08}}, "warnings": []}
    cache.store(tmp_path, result, *ARGS)
    got = cache.load(tmp_path, *ARGS)
    assert got is not None and got["from_cache"] is True
    assert got["parcel"]["centroid_wgs84"]["lat"] == 21.15
    assert any("from cache" in w for w in got["warnings"])


def test_failed_results_are_not_cached(tmp_path):
    cache.store(tmp_path, {"status": "FAILED", "warnings": []}, *ARGS)
    assert cache.load(tmp_path, *ARGS) is None


def test_ttl_expiry(tmp_path):
    cache.store(tmp_path, {"status": "REFER", "warnings": []}, *ARGS)
    assert cache.load(tmp_path, *ARGS, ttl_seconds=3600) is not None
    assert cache.load(tmp_path, *ARGS, ttl_seconds=-1) is None


def test_key_is_identity_sensitive(tmp_path):
    cache.store(tmp_path, {"status": "SUCCESS", "warnings": []}, *ARGS)
    assert cache.load(tmp_path, "Maharashtra", "Akola", "Akot", "Akolkhed", "4", None) is None  # diff khasra
    assert cache.load(tmp_path, *ARGS) is not None


def test_extra_params_change_the_key(tmp_path):
    cache.store(tmp_path, {"status": "SUCCESS", "warnings": []}, *ARGS[:-1], {"ri_circle": "A"})
    assert cache.load(tmp_path, *ARGS[:-1], {"ri_circle": "B"}) is None
    assert cache.load(tmp_path, *ARGS[:-1], {"ri_circle": "A"}) is not None
