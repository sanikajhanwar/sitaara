"""
GPS Engine — Uttarakhand BhuNaksha Adapter
==========================================
Portal: https://bhunaksha.uk.gov.in
Tech Stack: Angular + Angular Material (mat-select) + OpenLayers, `/bhunakshaserver/` REST
Language: Hindi (Devanagari)

Modelled on the Uttar Pradesh adapter (same Angular NIC family). NOT yet verified
end-to-end — the portal is unreachable from our test environment (see docs/STATUS.md).
When reachable, this adapter attempts the full navigation; any failure returns FAILED
with a specific reason.

Hierarchy:
  District  -> mat-select#mat-select-0
  Tehsil    -> mat-select#mat-select-2
  Village   -> mat-select#mat-select-4
  Khasra    -> input#plotNo + Enter

Geometry: <origin>/bhunakshaserver/MapInfo/getPlotByPlotNo (bbox) + getPlotInfo (owner/area).
No polygon exposed — centroid = bbox centre.
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

logger = logging.getLogger("GPS.Adapter.UK")

NATIVE_CRS = "EPSG:32644"   # Uttarakhand is UTM 44N
SELECTORS = {
    "district_select": "mat-select#mat-select-0",
    "tehsil_select":   "mat-select#mat-select-2",
    "village_select":  "mat-select#mat-select-4",
    "khasra_input":    "input#plotNo",
}


class UKBhuNakshaAdapter(CadastralAdapter):
    """Adapter for the Uttarakhand BhuNaksha portal (Angular NIC stack)."""

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

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
                page = browser.new_page(viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT})

                self._giscode = None
                self._server_base = self.portal_url.rstrip("/") + "/bhunakshaserver"

                def _grab_giscode(resp):
                    if "getVVVVExtentGeoref" in resp.url:
                        try:
                            j = resp.json()
                            if j.get("gisCode"):
                                self._giscode = j["gisCode"]
                        except Exception:
                            pass
                page.on("response", _grab_giscode)

                self.logger.info("Step 1.1: Loading Uttarakhand BhuNaksha...")
                connected = self.navigate_with_retry(page, [self.portal_url], max_retries=2, timeout=15000)
                if not connected:
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_unreachable",
                            "error": f"could not connect to {self.portal_url}",
                            "state": self.state_name, "portal_url": self.portal_url}

                time.sleep(PAGE_LOAD_WAIT)
                debug_home = self.output_dir / "debug_uk_1_homepage.png"
                self.safe_screenshot(page, debug_home)

                if not page.query_selector("mat-select"):
                    self.safe_screenshot(page, target_image)
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_structure_unexpected",
                            "error": "no <mat-select> on the page — UK portal is not the Angular NIC stack "
                                     "this adapter targets, or it did not finish loading",
                            "state": self.state_name, "portal_url": connected,
                            "image_path": str(target_image),
                            "debug_screenshots": {"homepage": str(debug_home)}}

                name_warnings = []
                for sel_key, val, label in [
                    ("district_select", district, "district"),
                    ("tehsil_select",   tehsil,   "tehsil"),
                    ("village_select",  village,  "village"),
                ]:
                    r = self.select_named_matselect(page, SELECTORS[sel_key], val, label)
                    if not r["ok"]:
                        self.safe_screenshot(page, self.output_dir / f"debug_uk_FAILED_{label}.png")
                        browser.close()
                        return {"status": "FAILED", "reason": f"{label}_not_matched",
                                "error": r["warning"] or f"{label} '{val}' not resolvable",
                                "state": self.state_name, "portal_url": connected}
                    if r["warning"]:
                        name_warnings.append(r["warning"])
                    time.sleep(AJAX_WAIT_SECONDS + (1.5 if label == "village" else 0))

                debug_vil = self.output_dir / "debug_uk_2_village.png"
                self.safe_screenshot(page, debug_vil)

                try:
                    page.wait_for_selector(SELECTORS["khasra_input"], timeout=10000)
                    page.fill(SELECTORS["khasra_input"], str(khasra_no))
                    page.keyboard.press("Enter")
                    time.sleep(ZOOM_SETTLE_SECONDS)
                except PWTimeout:
                    self.safe_screenshot(page, self.output_dir / "debug_uk_FAILED_khasra.png")
                    browser.close()
                    return {"status": "FAILED", "reason": "plot_search_box_missing",
                            "error": f"khasra input {SELECTORS['khasra_input']} not found",
                            "state": self.state_name, "portal_url": connected}

                parcel_raw = {"error": "no_giscode"}
                if self._giscode:
                    parcel_raw = self.bhunakshaserver_plot_info(
                        page, self._server_base, self._giscode, str(khasra_no),
                        native_crs=self.portal_config.get("native_crs", NATIVE_CRS),
                    )
                self.logger.info("  giscode=%s  geometry=%s", self._giscode,
                                 {k: parcel_raw.get(k) for k in ("centroid_native", "bbox", "raw_area", "error")})

                found = bool(parcel_raw.get("centroid_native"))
                self.safe_screenshot(page, target_image)
                browser.close()

                if not found:
                    return {"status": "REFER", "reason": "plot_not_found_on_portal",
                            "source": "LIVE_BHUNAKSHA_PORTAL", "state": self.state_name,
                            "portal_url": connected, "image_path": str(target_image),
                            "district": district, "tehsil": tehsil, "village": village,
                            "khasra_no": str(khasra_no), "parcel_raw": parcel_raw}

                return {
                    "status": "SUCCESS",
                    "source": "LIVE_BHUNAKSHA_PORTAL",
                    "state": self.state_name,
                    "portal_url": connected,
                    "image_path": str(target_image),
                    "district": district, "tehsil": tehsil, "village": village,
                    "khasra_no": str(khasra_no),
                    "parcel_raw": parcel_raw,
                    "name_warnings": name_warnings,
                    "zoom_level": DEFAULT_MIN_ZOOM,
                    "debug_screenshots": {"homepage": str(debug_home), "after_village": str(debug_vil)},
                }

        except Exception as e:
            self.logger.error(f"Uttarakhand BhuNaksha error: {e}", exc_info=True)
            return {"status": "FAILED", "reason": "adapter_exception", "state": self.state_name,
                    "portal_url": self.portal_url, "error": str(e)}
