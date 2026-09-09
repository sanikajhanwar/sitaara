"""
GPS Engine — Rajasthan BhuNaksha Adapter
=========================================
Portal: https://bhunaksha.rajasthan.gov.in/Viewmap/
Tech Stack: NIC BhuNaksha Web Application (standard select dropdowns + OpenLayers map canvas)
Language: Hindi (Devanagari script) with numeric codes e.g. "01 अजमेर"

Hierarchy:
  Level 1: District (#level_1)
  Level 2: Tehsil (#level_2)
  Level 3: RI Circle (#level_3)
  Level 4: Halkas / Sub-circle (#level_4)
  Level 5: Village / Mauza (#level_5)
  Level 6: Sheet No (#level_6)
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

logger = logging.getLogger("GPS.Adapter.RJ")




class RJBhuNakshaAdapter(CadastralAdapter):
    """
    Adapter for Rajasthan BhuNaksha portal (bhunaksha.rajasthan.gov.in/Viewmap/).
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

        ri_circle = extra_params.get("ri_circle", "")
        halkas = extra_params.get("halkas", "")

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            portal_url = "https://bhunaksha.rajasthan.gov.in/Viewmap/"
            self.logger.info(f"Connecting to live Rajasthan BhuNaksha: {portal_url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=headless,
                    args=["--no-sandbox", "--disable-gpu"]
                )
                page = browser.new_page(
                    viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT}
                )

                # ── Step 1.1: Load Portal ──────────────────────────────────────
                self.logger.info("Step 1.1: Loading portal...")
                page.goto(portal_url, timeout=45000, wait_until="domcontentloaded")
                time.sleep(PAGE_LOAD_WAIT)
                debug_home = self.output_dir / "debug_rj_1_homepage.png"
                page.screenshot(path=str(debug_home))

                name_warnings = []

                # ── Step 1.2: Select District (#level_1) ───────────────────────
                self.logger.info(f"Step 1.2: Selecting District = '{district}'...")
                r = self.select_named(page, "#level_1", district, "district")
                if not r["ok"]:
                    page.screenshot(path=str(self.output_dir / "debug_rj_FAILED_district.png"))
                    browser.close()
                    return {"status": "FAILED", "error": r["warning"], "portal_url": portal_url}
                if r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)
                debug_dist = self.output_dir / "debug_rj_2_district.png"
                page.screenshot(path=str(debug_dist))

                # ── Step 1.3: Select Tehsil (#level_2) ─────────────────────────
                self.logger.info(f"Step 1.3: Selecting Tehsil = '{tehsil}'...")
                r = self.select_named(page, "#level_2", tehsil, "tehsil")
                if not r["ok"]:
                    page.screenshot(path=str(self.output_dir / "debug_rj_FAILED_tehsil.png"))
                    browser.close()
                    return {"status": "FAILED", "error": r["warning"], "portal_url": portal_url}
                if r["warning"]:
                    name_warnings.append(r["warning"])
                time.sleep(AJAX_WAIT_SECONDS)
                debug_teh = self.output_dir / "debug_rj_3_tehsil.png"
                page.screenshot(path=str(debug_teh))

                # ── Step 1.4: Select RI Circle (#level_3) ──────────────────────
                self.logger.info(f"Step 1.4: Selecting RI Circle = '{ri_circle or 'auto'}'...")
                if not (ri_circle and self.select_named(page, "#level_3", ri_circle, "ri_circle")["ok"]):
                    self._select_first_available(page, "#level_3")
                    if not ri_circle:
                        name_warnings.append("RI circle not specified — used first available; may be wrong")
                time.sleep(AJAX_WAIT_SECONDS)

                # ── Step 1.5: Select Halkas (#level_4) ─────────────────────────
                self.logger.info(f"Step 1.5: Selecting Halkas = '{halkas or 'auto'}'...")
                if not (halkas and self.select_named(page, "#level_4", halkas, "halka")["ok"]):
                    self._select_first_available(page, "#level_4")
                    if not halkas:
                        name_warnings.append("Halka not specified — used first available; may be wrong")
                time.sleep(AJAX_WAIT_SECONDS)

                # ── Step 1.6: Select Village (#level_5) ────────────────────────
                self.logger.info(f"Step 1.6: Selecting Village = '{village}'...")
                r = self.select_named(page, "#level_5", village, "village")
                if r["ok"] and r["warning"]:
                    name_warnings.append(r["warning"])
                if not (r["ok"] or self._select_first_available(page, "#level_5")):
                    page.screenshot(path=str(self.output_dir / "debug_rj_FAILED_village.png"))
                    browser.close()
                    return {"status": "FAILED", "error": f"Village '{village}' not found in #level_5",
                            "portal_url": portal_url}
                time.sleep(AJAX_WAIT_SECONDS)
                debug_vil = self.output_dir / "debug_rj_4_village.png"
                page.screenshot(path=str(debug_vil))

                # ── Step 1.7: Select Sheet (#level_6) if required ──────────────
                self.logger.info("Step 1.7: Selecting Sheet No (#level_6)...")
                self._select_first_available(page, "#level_6")
                time.sleep(AJAX_WAIT_SECONDS + 1.0)

                LV = ["#level_1", "#level_2", "#level_3", "#level_4", "#level_5", "#level_6"]
                level_values = self.read_level_values(page, LV)
                if not level_values or any(v in (None, "", "0") for v in level_values[:5]):
                    for _ in range(8):
                        time.sleep(0.6)
                        lv = self.read_level_values(page, LV)
                        if lv and all(v not in (None, "", "0") for v in lv[:5]):
                            level_values = lv
                            break
                self.logger.info(f"  hierarchy = {level_values}")

                # ── Step 1.8: Search Khasra ────────────────────────────────────
                self.logger.info(f"Step 1.8: Searching Khasra No. = '{khasra_no}'...")
                try:
                    page.wait_for_selector("#plotNo", timeout=10000)
                    page.fill("#plotNo", str(khasra_no))
                    page.keyboard.press("Enter")
                    time.sleep(ZOOM_SETTLE_SECONDS)
                    debug_khasra = self.output_dir / "debug_rj_5_khasra_search.png"
                    page.screenshot(path=str(debug_khasra))
                except PWTimeout:
                    debug_fail = self.output_dir / "debug_rj_FAILED_khasra.png"
                    page.screenshot(path=str(debug_fail))
                    browser.close()
                    return {
                        "status": "FAILED",
                        "error": "Khasra input #plotNo not found",
                        "portal_url": portal_url
                    }

                # Extract plot metadata text if available
                plot_info_text = page.evaluate('''() => {
                    const el = document.querySelector('#plotinfo');
                    return el ? el.innerText.trim() : "";
                }''')

                # ── Geometry via ScalarDatahandler OP=5 ───────────────────────
                parcel_raw = self.classic_nic_plot_info(
                    page,
                    bhunaksha_state_code=self.portal_config.get("bhunaksha_state_code", "08"),
                    level_values=level_values,
                    plotno=str(khasra_no),
                    native_crs=self.portal_config.get("native_crs", "EPSG:32643"),
                )
                self.logger.info(
                    "  geometry: %s",
                    {k: parcel_raw.get(k) for k in ("centroid_native", "bbox", "raw_area", "error")},
                )

                found = bool(
                    (parcel_raw and parcel_raw.get("centroid_native"))
                    or (plot_info_text and str(khasra_no) in plot_info_text)
                )

                self.logger.info("Step 1.9: Capturing final cadastral map screenshot...")
                page.screenshot(path=str(target_image), full_page=False)
                browser.close()

                if not found:
                    return {
                        "status": "REFER",
                        "reason": "plot_not_found_on_portal",
                        "source": "LIVE_BHUNAKSHA_PORTAL",
                        "state": self.state_name,
                        "portal_url": portal_url,
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
                    "portal_url": portal_url,
                    "image_path": str(target_image),
                    "district": district,
                    "tehsil": tehsil,
                    "village": village,
                    "khasra_no": str(khasra_no),
                    "plot_info_text": plot_info_text,
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
            self.logger.error(f"Rajasthan BhuNaksha error: {e}", exc_info=True)
            el = str(e).lower()
            reason = "portal_unreachable" if any(k in el for k in ("net::", "err_", "timeout")) else "adapter_exception"
            return {
                "status": "FAILED",
                "reason": reason,
                "state": self.state_name,
                "portal_url": self.portal_url,
                "error": str(e)
            }


    def _select_first_available(self, page, selector: str, timeout: int = 8) -> bool:
        """Helper to select the first valid option in a standard HTML select element."""
        start = time.time()
        while time.time() - start < timeout:
            res = page.evaluate(f'''() => {{
                const sel = document.querySelector("{selector}");
                if (!sel || sel.options.length <= 1) return null;
                for (let i = 0; i < sel.options.length; i++) {{
                    const val = sel.options[i].value;
                    if (val && val !== "0" && val !== "-1") {{
                        sel.selectedIndex = i;
                        sel.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return sel.options[i].text.trim();
                    }}
                }}
                return false;
            }}''')
            if res:
                self.logger.info(f"  -> [{selector}] Selected first option '{res}'")
                return True
            time.sleep(0.5)
        return False
