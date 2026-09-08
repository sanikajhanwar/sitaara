"""
GPS Engine — Uttar Pradesh BhuNaksha Adapter
=============================================
Portal: https://upbhunaksha.gov.in
Tech Stack: Angular + Angular Material (CDK overlays) + OpenLayers Map Canvas
Language: Hindi (Devanagari script) with numeric code prefixes (e.g. '188 गोरखपुर')

Verified sequence:
  1. Load homepage (mat-select-0 populated with all 75 UP districts)
  2. Select District (mat-select-0) -> triggers AJAX load of Tehsil
  3. Select Tehsil (mat-select-2) -> triggers AJAX load of Village
  4. Select Village (mat-select-4) -> triggers OpenLayers map canvas tile load
  5. Fill input#plotNo with Khasra number & press Enter -> triggers plot highlight + map zoom
  6. Screenshot viewport -> bhunaksha.png
"""

import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.adapters.base import CadastralAdapter
from gps_engine.config import (
    DEFAULT_IMAGE_WIDTH,
    DEFAULT_IMAGE_HEIGHT,
    DEFAULT_MIN_ZOOM,
    AJAX_WAIT_SECONDS,
    ZOOM_SETTLE_SECONDS,
    PAGE_LOAD_WAIT,
)

logger = logging.getLogger("GPS.Adapter.UP")

# Selectors verified against live portal. District/Tehsil/Village names are resolved
# from the live <mat-option> list by gps_engine.naming (no hard-coded translation tables).
SELECTORS = {
    "district_select": "mat-select#mat-select-0",
    "tehsil_select":   "mat-select#mat-select-2",
    "village_select":  "mat-select#mat-select-4",
    "khasra_input":    "input#plotNo",
    "map_canvas":      "canvas",
}


class UPBhuNakshaAdapter(CadastralAdapter):
    """
    Adapter for Uttar Pradesh BhuNaksha portal (upbhunaksha.gov.in).
    """

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

            self.logger.info(f"Connecting to live UP BhuNaksha: {self.portal_url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-gpu"]
                )
                page = browser.new_page(
                    viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT}
                )

                # capture the village gisCode from the portal's own extent call
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

                # ── Step 1.1: Load Portal ──────────────────────────────────────
                self.logger.info("Step 1.1: Loading portal...")
                page.goto(self.portal_url, timeout=35000, wait_until="domcontentloaded")
                time.sleep(PAGE_LOAD_WAIT)
                debug_home = self.output_dir / "debug_up_1_homepage.png"
                page.screenshot(path=str(debug_home))

                name_warnings = []

                # District / Tehsil / Village via Angular Material mat-select + naming layer
                for sel_key, val, label, debug_name in [
                    ("district_select", district, "district", "debug_up_2_district.png"),
                    ("tehsil_select",   tehsil,   "tehsil",   "debug_up_3_tehsil.png"),
                    ("village_select",  village,  "village",  "debug_up_4_village.png"),
                ]:
                    self.logger.info(f"Selecting {label} = '{val}'...")
                    r = self.select_named_matselect(page, SELECTORS[sel_key], val, label)
                    if not r["ok"]:
                        page.screenshot(path=str(self.output_dir / f"debug_up_FAILED_{label}.png"))
                        browser.close()
                        return {"status": "FAILED", "error": r["warning"] or f"{label} '{val}' not resolvable",
                                "portal_url": self.portal_url}
                    if r["warning"]:
                        name_warnings.append(r["warning"])
                    time.sleep(AJAX_WAIT_SECONDS + (1.5 if label == "village" else 0))
                    page.screenshot(path=str(self.output_dir / debug_name))
                debug_dist = self.output_dir / "debug_up_2_district.png"
                debug_teh = self.output_dir / "debug_up_3_tehsil.png"
                debug_vil = self.output_dir / "debug_up_4_village.png"

                # Khasra search
                self.logger.info(f"Step 1.5: Searching Khasra No. = '{khasra_no}'...")
                try:
                    page.wait_for_selector(SELECTORS["khasra_input"], timeout=10000)
                    page.fill(SELECTORS["khasra_input"], str(khasra_no))
                    page.keyboard.press("Enter")
                    time.sleep(ZOOM_SETTLE_SECONDS)
                    debug_khasra = self.output_dir / "debug_up_5_khasra_search.png"
                    page.screenshot(path=str(debug_khasra))
                except PWTimeout:
                    debug_fail = self.output_dir / "debug_up_FAILED_khasra.png"
                    page.screenshot(path=str(debug_fail))
                    browser.close()
                    return {
                        "status": "FAILED",
                        "error": f"Khasra input selector {SELECTORS['khasra_input']} not found",
                        "portal_url": self.portal_url
                    }

                # ── Step 1.6: Geometry (bbox + area) via bhunakshaserver ───────
                parcel_raw = {"error": "no_giscode"}
                if self._giscode:
                    parcel_raw = self.bhunakshaserver_plot_info(
                        page, self._server_base, self._giscode, str(khasra_no),
                        native_crs=self.portal_config.get("native_crs", "EPSG:32644"),
                    )
                self.logger.info(f"  giscode={self._giscode}  parcel_raw={ {k: parcel_raw.get(k) for k in ('centroid_native','bbox','raw_area','error')} }")

                found = bool(parcel_raw.get("centroid_native"))

                self.logger.info("Step 1.6: Capturing final cadastral map screenshot...")
                page.screenshot(path=str(target_image), full_page=False)
                browser.close()

                if not found:
                    return {
                        "status": "REFER",
                        "reason": "plot_not_found_on_portal",
                        "source": "LIVE_BHUNAKSHA_PORTAL",
                        "state": self.state_name,
                        "portal_url": self.portal_url,
                        "image_path": str(target_image),
                        "district": district, "tehsil": tehsil, "village": village,
                        "khasra_no": str(khasra_no),
                        "parcel_raw": parcel_raw,
                        "debug_screenshots": {"after_khasra": str(debug_khasra)},
                    }

                self.logger.info(f"SUCCESS — Cadastral screenshot saved to {target_image}")
                return {
                    "status": "SUCCESS",
                    "source": "LIVE_BHUNAKSHA_PORTAL",
                    "state": self.state_name,
                    "portal_url": self.portal_url,
                    "image_path": str(target_image),
                    "district": district,
                    "tehsil": tehsil,
                    "village": village,
                    "khasra_no": str(khasra_no),
                    "parcel_raw": parcel_raw,
                    "name_warnings": name_warnings,
                    "zoom_level": DEFAULT_MIN_ZOOM,
                    "debug_screenshots": {
                        "homepage":       str(debug_home),
                        "after_district": str(debug_dist),
                        "after_tehsil":   str(debug_teh),
                        "after_village":  str(debug_vil),
                        "after_khasra":   str(debug_khasra),
                    }
                }

        except Exception as e:
            self.logger.error(f"UP BhuNaksha error: {e}", exc_info=True)
            return {
                "status": "FAILED",
                "state": self.state_name,
                "portal_url": self.portal_url,
                "error": str(e)
            }
