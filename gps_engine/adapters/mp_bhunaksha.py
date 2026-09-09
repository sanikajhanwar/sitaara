"""
GPS Engine — Madhya Pradesh BhuNaksha Adapter
=============================================
Portal: https://mpbhunaksha.gov.in  (also reached via mpbhulekh.gov.in)
Tech Stack: NIC BhuNaksha — classic `#level_N` <select> hierarchy + ScalarDatahandler servlet
Language: Hindi (Devanagari)

Modelled on the Chhattisgarh adapter (same NIC "classic" family). NOT yet verified
end-to-end — the portal is firewalled from our test environment (see docs/STATUS.md).
When the portal is reachable this adapter attempts the full navigation; if any step
fails it returns FAILED with a specific reason.

Hierarchy (assumed, classic NIC):
  Level 1: District (#level_1)
  Level 2: Tehsil (#level_2)
  Level 3: RI Circle / Halka (#level_3)   — pass via extra_params['ri_circle']
  Level 4: Village / Patwari Halka (#level_4)
  Plot search: input (#plotNo) + Enter

Geometry: GET ScalarDatahandler?OP=5&state=23&levels=<v1,..,>&plotno=<n>
          -> center_x/center_y + bbox + owner/area text (NO polygon).
"""

import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.adapters.base import CadastralAdapter
from gps_engine.config import (
    DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT, DEFAULT_MIN_ZOOM,
    AJAX_WAIT_SECONDS, ZOOM_SETTLE_SECONDS, PAGE_LOAD_WAIT,
)

logger = logging.getLogger("GPS.Adapter.MP")

STATE_CODE = "23"
NATIVE_CRS = "EPSG:32643"   # west/central MP is UTM 43N; east MP 44N — Step 3 re-checks
URLS = [
    "https://mpbhulekh.gov.in/mpbhunaksha.do",
    "https://mpbhulekh.gov.in/bhunaksha",
]


class MPBhuNakshaAdapter(CadastralAdapter):
    """Adapter for the Madhya Pradesh BhuNaksha portal (classic NIC stack)."""

    def capture(
        self,
        district: str,
        tehsil: str,
        village: str,
        khasra_no: str,
        image_path: Optional[Path] = None,
        headless: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target_image = image_path or (self.output_dir / "bhunaksha.png")
        extra_params = extra_params or {}
        ri_circle = extra_params.get("ri_circle", "")

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
                page = browser.new_page(viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT})

                self._giscode = None

                def _grab(resp):
                    if "getVVVVExtentGeoref" in resp.url:
                        try:
                            j = resp.json()
                            if j.get("gisCode"):
                                self._giscode = j["gisCode"]
                        except Exception:
                            pass
                page.on("response", _grab)

                self.logger.info("Step 1.1: Loading Madhya Pradesh BhuNaksha...")
                connected = self.navigate_with_retry(page, URLS, max_retries=2, timeout=15000)
                if not connected:
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_unreachable",
                            "error": f"could not connect to any MP BhuNaksha URL: {URLS}",
                            "state": self.state_name, "portal_url": URLS[0]}

                time.sleep(PAGE_LOAD_WAIT)
                debug_home = self.output_dir / "debug_mp_1_homepage.png"
                self.safe_screenshot(page, debug_home)

                if not page.query_selector("#level_1"):
                    self.safe_screenshot(page, target_image)
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_structure_unexpected",
                            "error": "no #level_1 dropdown — MP portal is not the classic NIC stack this "
                                     "adapter targets, or it did not finish loading",
                            "state": self.state_name, "portal_url": connected,
                            "image_path": str(target_image),
                            "debug_screenshots": {"homepage": str(debug_home)}}

                name_warnings = []

                r = self.select_named(page, "#level_1", district, "district")
                if not r["ok"]:
                    self.safe_screenshot(page, self.output_dir / "debug_mp_FAILED_district.png")
                    browser.close()
                    return {"status": "FAILED", "reason": "district_not_matched",
                            "error": r["warning"], "state": self.state_name, "portal_url": connected}
                if r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)

                r = self.select_named(page, "#level_2", tehsil, "tehsil")
                if not r["ok"]:
                    self.safe_screenshot(page, self.output_dir / "debug_mp_FAILED_tehsil.png")
                    browser.close()
                    return {"status": "FAILED", "reason": "tehsil_not_matched",
                            "error": r["warning"], "state": self.state_name, "portal_url": connected}
                if r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)

                # Level 3 — RI circle (optional). If not given, take the first option.
                if not (ri_circle and self.select_named(page, "#level_3", ri_circle, "ri_circle")["ok"]):
                    self.select_first_available(page, "#level_3")
                    if not ri_circle:
                        name_warnings.append("RI circle not specified — used first available; may be wrong")
                time.sleep(AJAX_WAIT_SECONDS)

                r = self.select_named(page, "#level_4", village, "village")
                if r["ok"] and r["warning"]:
                    name_warnings.append(r["warning"])
                if not (r["ok"] or self.select_first_available(page, "#level_4")):
                    self.safe_screenshot(page, self.output_dir / "debug_mp_FAILED_village.png")
                    browser.close()
                    return {"status": "FAILED", "reason": "village_not_matched",
                            "error": f"village '{village}' not found in #level_4",
                            "state": self.state_name, "portal_url": connected}

                level_values = self.read_level_values(page, ["#level_1", "#level_2", "#level_3", "#level_4"])
                if not level_values or level_values[-1] in (None, "", "0"):
                    for _ in range(8):
                        time.sleep(0.6)
                        lv = self.read_level_values(page, ["#level_1", "#level_2", "#level_3", "#level_4"])
                        if lv and lv[-1] not in (None, "", "0"):
                            level_values = lv
                            break
                if (not level_values or level_values[-1] in (None, "", "0")) and self._giscode:
                    gc = self._giscode.replace(".", "")
                    if len(gc) >= 8:
                        level_values = [gc[0:2], gc[2:4], gc[4:6], gc[6:]]
                self.logger.info(f"  hierarchy = {level_values}")

                time.sleep(AJAX_WAIT_SECONDS + 1.0)
                debug_vil = self.output_dir / "debug_mp_2_village.png"
                self.safe_screenshot(page, debug_vil)

                try:
                    page.wait_for_selector("#plotNo", timeout=10000)
                    page.fill("#plotNo", str(khasra_no))
                    page.keyboard.press("Enter")
                    time.sleep(ZOOM_SETTLE_SECONDS)
                except PWTimeout:
                    self.safe_screenshot(page, self.output_dir / "debug_mp_FAILED_khasra.png")
                    browser.close()
                    return {"status": "FAILED", "reason": "plot_search_box_missing",
                            "error": "khasra input #plotNo not found",
                            "state": self.state_name, "portal_url": connected}

                parcel_raw = self.classic_nic_plot_info(
                    page,
                    bhunaksha_state_code=self.portal_config.get("bhunaksha_state_code", STATE_CODE),
                    level_values=level_values,
                    plotno=str(khasra_no),
                    native_crs=self.portal_config.get("native_crs", NATIVE_CRS),
                )
                self.logger.info("  geometry: %s",
                                 {k: parcel_raw.get(k) for k in ("centroid_native", "bbox", "raw_area", "error")})

                plot_info_text = page.evaluate(
                    """() => { const e = document.querySelector('#plotinfo'); return e ? e.innerText.trim() : ''; }"""
                )
                found = bool((parcel_raw and parcel_raw.get("centroid_native"))
                             or (plot_info_text and str(khasra_no) in plot_info_text))

                self.safe_screenshot(page, target_image)
                browser.close()

                if not found:
                    return {"status": "REFER", "reason": "plot_not_found_on_portal",
                            "source": "LIVE_BHUNAKSHA_PORTAL", "state": self.state_name,
                            "portal_url": connected, "image_path": str(target_image),
                            "district": district, "tehsil": tehsil, "village": village,
                            "khasra_no": str(khasra_no)}

                return {
                    "status": "SUCCESS",
                    "source": "LIVE_BHUNAKSHA_PORTAL",
                    "state": self.state_name,
                    "portal_url": connected,
                    "image_path": str(target_image),
                    "district": district, "tehsil": tehsil, "village": village,
                    "khasra_no": str(khasra_no),
                    "parcel_raw": parcel_raw,
                    "plot_info_text": plot_info_text,
                    "name_warnings": name_warnings,
                    "zoom_level": DEFAULT_MIN_ZOOM,
                    "debug_screenshots": {"homepage": str(debug_home), "after_village": str(debug_vil)},
                }

        except Exception as e:
            self.logger.error(f"Madhya Pradesh BhuNaksha error: {e}", exc_info=True)
            return {"status": "FAILED", "reason": "adapter_exception", "state": self.state_name,
                    "portal_url": URLS[0], "error": str(e)}
