"""
GPS Engine — Bihar BhuNaksha Adapter  (EXPERIMENTAL — not verified end-to-end)
============================================================================
Portal: http://bhunaksha.bihar.gov.in/10/index.jsp (View Map -> indexmain.jsp)
Tech Stack: NIC BhuNaksha Web Application
Language: English & Hindi mixed

STATUS (re-probed 2026-09-08): the portal has migrated its backend to the modern
NIC `/rest/` stack (same family as Maharashtra), but still ships the *old* classic
front-end. The plot-search call `GET /ScalarDatahandler?OP=5` now returns HTTP 401,
so the portal's own plot lookup is broken for the public. This adapter targets the
classic `ScalarDatahandler` path (`classic_nic_plot_info`) and therefore cannot
currently return geometry. It needs a rewrite to `/rest/MapInfo/getPlotInfo`
(see gps_engine/adapters/mh_bhunaksha.py) once Bihar's portal is usable again.
Navigation of the 7-level hierarchy still works and is kept intact. See docs/STATUS.md.

Hierarchy:
  Landing trigger: Click input[value*='View']
  Level 1: District (#level_1)
  Level 2: Sub-Division / Anumandal (#level_2)
  Level 3: Circle / Anchal (#level_3)
  Level 4: Mauza / Village (#level_4)
  Level 5: Survey Type (#level_5) e.g. RS / CS
  Level 6: Map Instance (#level_6)
  Level 7: Sheet No (#level_7)
  Plot Search: Input (#plotNo) + Enter
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

logger = logging.getLogger("GPS.Adapter.BR")


class BRBhuNakshaAdapter(CadastralAdapter):
    """
    Adapter for Bihar BhuNaksha portal (bhunaksha.bihar.gov.in/10/index.jsp).
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
        extra_params = extra_params or {}

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            urls = [
                "http://bhunaksha.bihar.gov.in/10/index.jsp",
                "https://bhunaksha.bihar.gov.in/10/index.jsp",
            ]

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-gpu"]
                )
                page = browser.new_page(
                    viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT}
                )

                # ── Step 1.1: Load Portal & Click View Map ─────────────────────
                self.logger.info("Step 1.1: Loading portal...")
                connected_url = self.navigate_with_retry(page, urls, max_retries=3, timeout=35000)
                if not connected_url:
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_unreachable",
                            "error": "Could not connect to Bihar portal", "portal_url": urls[0]}

                time.sleep(PAGE_LOAD_WAIT)
                debug_home = self.output_dir / "debug_br_1_homepage.png"
                self.safe_screenshot(page, debug_home)

                # Click View Map button if present
                try:
                    view_btn = page.locator("input[value*='View']").first
                    if view_btn.is_visible():
                        view_btn.click()
                        time.sleep(3)
                except Exception as e:
                    self.logger.info(f"View Map trigger note: {e}")

                # Bihar hierarchy: District > Sub-Division > Circle/Anchal > Mauza >
                #   Survey-type (RS/CS/SS) > Map-instance > Sheet.
                # tehsil -> Circle;  extra_params: subdivision, survey_type, map_instance, sheet.
                subdivision = extra_params.get("subdivision")
                survey_type = extra_params.get("survey_type")
                name_warnings = []

                # ── #level_1 District ─────────────────────────────────────────
                self.logger.info(f"Step 1.2: Selecting District = '{district}'...")
                r = self.select_named(page, "#level_1", district, "district")
                if not r["ok"]:
                    self.safe_screenshot(page, self.output_dir / "debug_br_FAILED_district.png")
                    browser.close()
                    return {"status": "FAILED", "error": r["warning"], "portal_url": connected_url}
                if r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)
                debug_dist = self.output_dir / "debug_br_2_district.png"
                self.safe_screenshot(page, debug_dist)

                # ── #level_2 Sub-Division ────────────────────────────────────
                if subdivision and self.select_named(page, "#level_2", subdivision, "subdivision")["ok"]:
                    pass
                else:
                    self.select_first_available(page, "#level_2")
                    if not subdivision:
                        name_warnings.append("sub-division not specified — used first available; may be wrong")
                time.sleep(AJAX_WAIT_SECONDS)

                # ── #level_3 Circle / Anchal (from tehsil) ───────────────────
                self.logger.info(f"Step 1.4: Selecting Circle = '{tehsil}'...")
                r = self.select_named(page, "#level_3", tehsil, "circle")
                if not r["ok"]:
                    self.select_first_available(page, "#level_3")
                    name_warnings.append(f"circle '{tehsil}' not matched — used first available")
                elif r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)

                # ── #level_4 Mauza / Village ────────────────────────────────
                self.logger.info(f"Step 1.5: Selecting Mauza = '{village}'...")
                r = self.select_named(page, "#level_4", village, "mauza")
                if not r["ok"]:
                    self.safe_screenshot(page, self.output_dir / "debug_br_FAILED_village.png")
                    browser.close()
                    return {"status": "FAILED", "error": r["warning"], "portal_url": connected_url}
                if r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)
                debug_vil = self.output_dir / "debug_br_3_village.png"
                self.safe_screenshot(page, debug_vil)

                # ── #level_5 Survey type, #level_6 instance, #level_7 sheet ──
                if not (survey_type and self.select_named(page, "#level_5", survey_type, "survey_type")["ok"]):
                    self.select_first_available(page, "#level_5")
                    if not survey_type:
                        name_warnings.append("survey type (RS/CS/SS) not specified — used first available")
                time.sleep(1.5)
                self.select_first_available(page, "#level_6")
                time.sleep(1.5)
                self.select_first_available(page, "#level_7")
                time.sleep(AJAX_WAIT_SECONDS + 1.0)

                LV = [f"#level_{i}" for i in range(1, 8)]
                level_values = self.read_level_values(page, LV)
                if not level_values or any(v in (None, "", "0") for v in level_values[:5]):
                    for _ in range(8):
                        time.sleep(0.6)
                        lv = self.read_level_values(page, LV)
                        if lv and all(v not in (None, "", "0") for v in lv[:5]):
                            level_values = lv
                            break
                self.logger.info(f"  hierarchy = {level_values}")

                # ── Step 1.7: Search Khasra ────────────────────────────────────
                self.logger.info(f"Step 1.7: Searching Khasra No. = '{khasra_no}'...")
                try:
                    page.wait_for_selector("#plotNo", timeout=10000)
                    page.fill("#plotNo", str(khasra_no))
                    page.keyboard.press("Enter")
                    time.sleep(ZOOM_SETTLE_SECONDS)
                    debug_khasra = self.output_dir / "debug_br_4_khasra_search.png"
                    self.safe_screenshot(page, debug_khasra)
                except PWTimeout:
                    debug_fail = self.output_dir / "debug_br_FAILED_khasra.png"
                    self.safe_screenshot(page, debug_fail)
                    browser.close()
                    return {
                        "status": "FAILED",
                        "error": "Khasra input #plotNo not found",
                        "portal_url": connected_url
                    }

                # ── Step 1.8: Geometry via ScalarDatahandler OP=5 ──────────────
                parcel_raw = self.classic_nic_plot_info(
                    page,
                    bhunaksha_state_code=self.portal_config.get("bhunaksha_state_code", "10"),
                    level_values=level_values,
                    plotno=str(khasra_no),
                    native_crs=self.portal_config.get("native_crs", "EPSG:32645"),
                )
                self.logger.info(
                    "  geometry: %s",
                    {k: parcel_raw.get(k) for k in ("centroid_native", "bbox", "raw_area", "error")},
                )

                plot_info_text = page.evaluate(
                    """() => { const e = document.querySelector('#plotinfo'); return e ? e.innerText.trim() : ''; }"""
                )
                found = bool(
                    (parcel_raw and parcel_raw.get("centroid_native"))
                    or (plot_info_text and str(khasra_no) in plot_info_text)
                )

                self.logger.info("Step 1.8: Capturing final cadastral map screenshot...")
                self.safe_screenshot(page, target_image)
                browser.close()

                if not found:
                    return {
                        "status": "REFER",
                        "reason": "plot_not_found_on_portal",
                        "source": "LIVE_BHUNAKSHA_PORTAL",
                        "state": self.state_name,
                        "portal_url": connected_url,
                        "image_path": str(target_image),
                        "district": district, "tehsil": tehsil, "village": village,
                        "khasra_no": str(khasra_no),
                        "debug_screenshots": {"after_khasra": str(debug_khasra)},
                    }

                self.logger.info(
                    f"SUCCESS — screenshot {target_image}; "
                    f"geometry {'captured' if parcel_raw.get('centroid_native') else 'MISSING: ' + str(parcel_raw.get('error'))}"
                )
                return {
                    "status": "SUCCESS",
                    "source": "LIVE_BHUNAKSHA_PORTAL",
                    "state": self.state_name,
                    "portal_url": connected_url,
                    "image_path": str(target_image),
                    "district": district,
                    "tehsil": tehsil,
                    "village": village,
                    "khasra_no": str(khasra_no),
                    "parcel_raw": parcel_raw,
                    "plot_info_text": plot_info_text,
                    "name_warnings": name_warnings,
                    "zoom_level": DEFAULT_MIN_ZOOM,
                    "debug_screenshots": {
                        "homepage":       str(debug_home),
                        "after_district": str(debug_dist),
                        "after_village":  str(debug_vil),
                        "after_khasra":   str(debug_khasra),
                    }
                }

        except Exception as e:
            self.logger.error(f"Bihar BhuNaksha error: {e}", exc_info=True)
            return {
                "status": "FAILED",
                "state": self.state_name,
                "portal_url": urls[0] if 'urls' in locals() else "",
                "error": str(e)
            }
