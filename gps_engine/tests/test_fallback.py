"""Offline tests for the fallback adapter + Step 1 routing."""

import pytest

from gps_engine.adapters.fallback import FallbackAdapter, _unreachable, _reachable_no_adapter
from gps_engine.step1_capture import Step1Capture


def test_unreachable_shape():
    r = _unreachable("Gujarat", "https://anyror.gujarat.gov.in", "net::ERR_TIMED_OUT", "2026-09-09T20:00:00+00:00")
    assert r["status"] == "FAILED"
    assert r["reason"] == "portal_unreachable"
    assert r["portal_check"]["reason"] == "portal_unreachable"
    assert r["portal_check"]["probed_at"] == "2026-09-09T20:00:00+00:00"
    assert r["portal_check"]["error"] == "net::ERR_TIMED_OUT"
    assert r["portal_check"]["attempts"] >= 1


def test_reachable_no_adapter_shape():
    r = _reachable_no_adapter("Delhi", "https://dlrc.delhigovt.nic.in", "https://dlrc.delhigovt.nic.in/home",
                              "DLRC Revenue Maps", "/tmp/x/bhunaksha.png", "2026-09-09T20:00:00+00:00")
    assert r["status"] == "FAILED"
    assert r["reason"] == "adapter_not_implemented"
    assert r["image_path"].endswith("bhunaksha.png")
    assert r["portal_check"]["page_title"] == "DLRC Revenue Maps"


def test_router_returns_fallback_for_state_without_adapter(tmp_path):
    adapter = Step1Capture(output_dir=tmp_path).get_adapter("Gujarat")
    assert isinstance(adapter, FallbackAdapter)
    assert adapter.portal_url  # a URL is configured to attempt


@pytest.mark.parametrize("state", ["Madhya Pradesh", "Uttarakhand", "Delhi", "Haryana"])
def test_router_returns_fallback_for_all_unbuilt_states(tmp_path, state):
    assert isinstance(Step1Capture(output_dir=tmp_path).get_adapter(state), FallbackAdapter)


def test_router_returns_real_adapter_for_built_state(tmp_path):
    adapter = Step1Capture(output_dir=tmp_path).get_adapter("Maharashtra")
    assert type(adapter).__name__ == "MHBhuNakshaAdapter"


def test_router_raises_for_unknown_state(tmp_path):
    with pytest.raises(ValueError, match="not a recognised state"):
        Step1Capture(output_dir=tmp_path).get_adapter("Narnia")
