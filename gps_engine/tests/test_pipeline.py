"""Offline tests for the pipeline's run-folder / audit-trail helpers."""

import json
import re

from gps_engine import pipeline


def test_make_run_id_plain_is_a_timestamp():
    rid = pipeline._make_run_id(None)
    assert re.fullmatch(r"\d{8}-\d{6}", rid)


def test_make_run_id_appends_application_id():
    rid = pipeline._make_run_id("SGRL00037212")
    assert rid.endswith("_SGRL00037212")
    assert re.fullmatch(r"\d{8}-\d{6}_SGRL00037212", rid)


def test_make_run_id_sanitises_unsafe_characters():
    rid = pipeline._make_run_id("loan/42 #x")
    # only timestamp + [A-Za-z0-9_.-] survive
    assert re.fullmatch(r"\d{8}-\d{6}_loan_42__x", rid)


def test_write_step_serialises_dataclass_dicts(tmp_path):
    payload = {"status": "SUCCESS", "centroid_wgs84": {"lat": 21.15, "lon": 77.08}}
    pipeline._write_step(tmp_path, "step3", payload)
    written = json.loads((tmp_path / "step3.json").read_text())
    assert written == payload


def test_write_step_tolerates_unserialisable_values(tmp_path):
    pipeline._write_step(tmp_path, "step9", {"path": object()})  # default=str keeps it alive
    assert (tmp_path / "step9.json").exists()


def test_point_latest_at_creates_symlink(tmp_path):
    run_dir = tmp_path / "runs" / "20260101-000000_APP1"
    run_dir.mkdir(parents=True)
    pipeline._point_latest_at(run_dir, tmp_path)
    link = tmp_path / "latest"
    assert link.is_symlink()
    assert link.resolve() == run_dir.resolve()


def test_point_latest_at_repoints_to_newest_run(tmp_path):
    for name in ("20260101-000000", "20260102-000000"):
        d = tmp_path / "runs" / name
        d.mkdir(parents=True)
        pipeline._point_latest_at(d, tmp_path)
    assert (tmp_path / "latest").resolve() == (tmp_path / "runs" / "20260102-000000").resolve()
