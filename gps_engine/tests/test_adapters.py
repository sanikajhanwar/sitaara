"""Offline tests for adapter registration and routing — every state has an adapter."""

import pytest

from gps_engine.config import CADASTRAL_PORTALS
from gps_engine.step1_capture import Step1Capture, ADAPTER_MAP


EXPECTED_CLASS = {
    "Maharashtra": "MHBhuNakshaAdapter",
    "Uttar Pradesh": "UPBhuNakshaAdapter",
    "Rajasthan": "RJBhuNakshaAdapter",
    "Chhattisgarh": "CGBhuNakshaAdapter",
    "Bihar": "BRBhuNakshaAdapter",
    "Madhya Pradesh": "MPBhuNakshaAdapter",
    "Uttarakhand": "UKBhuNakshaAdapter",
    "Gujarat": "GJAnyRORAdapter",
    "Delhi": "DLDLRCAdapter",
    "Haryana": "HRHSACAdapter",
}


def test_every_config_state_has_an_adapter_in_the_map():
    for state, cfg in CADASTRAL_PORTALS.items():
        assert cfg["adapter"] in ADAPTER_MAP, f"{state} -> {cfg['adapter']} missing from ADAPTER_MAP"


@pytest.mark.parametrize("state,cls", EXPECTED_CLASS.items())
def test_router_loads_the_right_adapter_class(tmp_path, state, cls):
    adapter = Step1Capture(output_dir=tmp_path).get_adapter(state)
    assert type(adapter).__name__ == cls
    assert adapter.portal_url  # every adapter has a URL to attempt


def test_router_raises_valueerror_for_unknown_state(tmp_path):
    with pytest.raises(ValueError, match="not a recognised state"):
        Step1Capture(output_dir=tmp_path).get_adapter("Narnia")


def test_mh_fail_classifies_connection_errors_as_portal_unreachable(tmp_path):
    from gps_engine.adapters.mh_bhunaksha import MHBhuNakshaAdapter
    a = MHBhuNakshaAdapter(state_name="Maharashtra",
                           portal_config=CADASTRAL_PORTALS["Maharashtra"], output_dir=tmp_path)
    assert a._fail("net::ERR_CONNECTION_TIMED_OUT", "u")["reason"] == "portal_unreachable"
    assert a._fail("Could not connect to Maharashtra portal", "u")["reason"] == "portal_unreachable"
    assert a._fail("Plot search box #plotNo not found", "u")["reason"] == "step1_failed"
