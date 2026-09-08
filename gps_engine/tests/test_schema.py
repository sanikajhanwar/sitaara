"""The result JSON schema loads, and representative payloads validate against it."""

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA = json.loads((Path(__file__).parents[1] / "schema" / "gps_engine_result.schema.json").read_text())


def test_schema_is_valid_draft7():
    jsonschema.Draft7Validator.check_schema(SCHEMA)


def test_minimal_success_payload_validates():
    payload = {
        "application_id": "SGRL00037212",
        "status": "SUCCESS",
        "reason": None,
        "state": "Maharashtra",
        "nav_resolved": {"district": "Akola", "tehsil": "Akot", "village": "Akolkhed", "khasra": "3"},
        "parcel": {"found": True, "centroid_wgs84": {"lat": 21.1522, "lon": 77.0888},
                   "source_crs_confidence": "high", "extraction_method": "wkt",
                   "polygon_available": True, "vertex_count": 13},
        "distance": {"status": "SUCCESS", "distance_m": 20.7, "verdict": "MATCH", "color": "GREEN"},
        "parameter5_verdict": "MATCH",
        "warnings": [],
        "timing_seconds": 24.4,
    }
    jsonschema.validate(payload, SCHEMA)


def test_bad_verdict_rejected():
    payload = {
        "status": "SUCCESS", "state": "X", "warnings": [], "timing_seconds": 1,
        "nav_resolved": {"district": "a", "tehsil": "b", "village": "c", "khasra": "1"},
        "parameter5_verdict": "PROBABLY_FINE",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)


def test_out_of_range_latitude_rejected():
    payload = {
        "status": "SUCCESS", "state": "X", "warnings": [], "timing_seconds": 1,
        "nav_resolved": {"district": "a", "tehsil": "b", "village": "c", "khasra": "1"},
        "parcel": {"found": True, "centroid_wgs84": {"lat": 999, "lon": 77}},
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, SCHEMA)
