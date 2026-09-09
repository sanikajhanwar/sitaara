"""
GPS Engine — Maharashtra BhuNaksha Adapter
==========================================
Portal: https://mahabhunakasha.mahabhumi.gov.in/27/index.html
Tech Stack: modern NIC BhuNaksha ("bhunaksha LGD" template) — plain <select> + OpenLayers + WMS
Language: Marathi (Devanagari)

See docs/PORTAL_NOTES.md.

Hierarchy:  State(#level_0) -> Category(#level_1 R/U) -> District(#level_2)
            -> Taluka(#level_3) -> Village(#level_4) -> Plot (#plotNo + #plotNoButton)

Geometry:   POST /rest/MapInfo/getPlotInfo  {state, giscode, plotno, srs}
            -> response.the_geom  = WKT MULTIPOLYGON in EPSG:32643 (UTM 43N)
            -> response.area, xmin/ymin/xmax/ymax, plotid

This adapter captures BOTH the map screenshot (Step 1 contract) AND the raw parcel
geometry (`parcel_raw`) so Step 2/3 need no second browser run.
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

logger = logging.getLogger("GPS.Adapter.MH")

STATE_CODE = "27"
NATIVE_CRS = "EPSG:32643"   # UTM 43N. East Maharashtra (Vidarbha) parcels may be 44N;
                            # Step 3 re-checks via the centroid-in-state gate.


class MHBhuNakshaAdapter(CadastralAdapter):
    """Adapter for the Maharashtra BhuNaksha portal (modern NIC stack)."""

    PORTAL_URL = "https://mahabhunakasha.mahabhumi.gov.in/27/index.html"

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
        category = extra_params.get("category", "Rural")   # 'Rural' | 'Urban'

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
                page = browser.new_page(viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT})

                # ── 1.1 load ────────────────────────────────────────────────
                self.logger.info("Step 1.1: Loading Maharashtra BhuNaksha...")
                if not self.navigate_with_retry(page, self.PORTAL_URL, max_retries=3, timeout=45000):
                    browser.close()
                    return self._fail("Could not connect to Maharashtra portal", self.PORTAL_URL)
                time.sleep(PAGE_LOAD_WAIT)
                page.wait_for_selector("#level_2", timeout=20000)
                debug_home = self.output_dir / "debug_mh_1_homepage.png"
                self.safe_screenshot(page, debug_home)

                # ── 1.2 category ───────────────────────────────────────────
                cat_val = "U" if category.lower().startswith("u") else "R"
                page.evaluate(
                    """(v)=>{const s=document.querySelector('#level_1'); if(s){s.value=v; s.dispatchEvent(new Event('change',{bubbles:true}));}}""",
                    cat_val,
                )
                time.sleep(AJAX_WAIT_SECONDS)

                # ── 1.3 district / taluka / village ────────────────────────
                name_warnings = []
                for sel, val, label in [("#level_2", district, "district"),
                                        ("#level_3", tehsil, "taluka"),
                                        ("#level_4", village, "village")]:
                    r = self.select_named(page, sel, val, label)
                    if not r["ok"]:
                        self.safe_screenshot(page, self.output_dir / f"debug_mh_FAILED_{label}.png")
                        browser.close()
                        return self._fail(r["warning"] or f"{label} '{val}' not found in {sel}", self.PORTAL_URL)
                    if r["warning"]:
                        name_warnings.append(r["warning"])
                    time.sleep(AJAX_WAIT_SECONDS)
                    self.safe_screenshot(page, self.output_dir / f"debug_mh_{sel[-1]}_{label}.png")
                time.sleep(2.0)
                debug_dist = self.output_dir / "debug_mh_2_district.png"
                debug_teh = self.output_dir / "debug_mh_3_taluka.png"
                debug_vil = self.output_dir / "debug_mh_4_village.png"

                # giscode: RVM + district(2) + taluka(2) + village(18); pull from the WMS layer
                giscode = page.evaluate(
                    """() => {
                        try {
                          for (const l of window.map.getLayers().getArray()) {
                            const s = l.getSource && l.getSource();
                            const pr = s && s.getParams && s.getParams();
                            if (pr && (pr.gis_code || pr.giscode)) return pr.gis_code || pr.giscode;
                          }
                        } catch(e){}
                        return null;
                    }"""
                )
                if not giscode:
                    d_val = page.evaluate("()=>document.querySelector('#level_2').value")
                    t_val = page.evaluate("()=>document.querySelector('#level_3').value")
                    v_val = page.evaluate("()=>document.querySelector('#level_4').value")
                    giscode = f"RVM{d_val}{t_val}{v_val}" if all([d_val, t_val, v_val]) else None
                self.logger.info(f"  giscode = {giscode}")

                # ── 1.4 plot search ───────────────────────────────────────
                self.logger.info(f"Step 1.4: Searching plot / survey no. = '{khasra_no}'...")
                try:
                    page.wait_for_selector("#plotNo", state="attached", timeout=15000)
                except PWTimeout:
                    self.safe_screenshot(page, self.output_dir / "debug_mh_FAILED_plot.png")
                    browser.close()
                    return self._fail("Plot search box #plotNo not found", self.PORTAL_URL)
                # The #surveyNumber dropdown is the reliable highlight trigger (confirmed on probe);
                # the #plotNo text box + button is a secondary path.
                picked = page.evaluate(
                    """(no) => {
                        const s = document.querySelector('#surveyNumber');
                        let viaSelect = false;
                        if (s) for (let i=0;i<s.options.length;i++) if (String(s.options[i].value)===String(no)) {
                            s.selectedIndex=i; s.dispatchEvent(new Event('change',{bubbles:true})); viaSelect = true; break;
                        }
                        const inp = document.querySelector('#plotNo');
                        const btn = document.querySelector('#plotNoButton');
                        if (inp) { inp.value = String(no); inp.dispatchEvent(new Event('input',{bubbles:true})); }
                        if (!viaSelect && btn) btn.click();
                        return {viaSelect, hadInput: !!inp};
                    }""",
                    str(khasra_no),
                )
                self.logger.info(f"  plot select: {picked}")
                time.sleep(ZOOM_SETTLE_SECONDS)

                debug_plot = self.output_dir / "debug_mh_5_plot_search.png"
                self.safe_screenshot(page, debug_plot)

                # ── 1.5 geometry via REST (Step 2 raw material) ────────────
                parcel_raw = self._fetch_plot_info(page, giscode, str(khasra_no))

                # ── 1.6 plot-found assertion ──────────────────────────────
                plot_info_text = page.evaluate(
                    """() => { const e = document.querySelector('#plotinfo'); return e ? e.innerText.trim() : ''; }"""
                )
                found = bool(
                    (parcel_raw and parcel_raw.get("wkt"))
                    or (plot_info_text and str(khasra_no) in plot_info_text)
                )
                if not found:
                    self.safe_screenshot(page, target_image)
                    browser.close()
                    return {
                        "status": "REFER",
                        "reason": "plot_not_found_on_portal",
                        "source": "LIVE_BHUNAKSHA_PORTAL",
                        "state": self.state_name,
                        "portal_url": self.PORTAL_URL,
                        "image_path": str(target_image),
                        "district": district, "tehsil": tehsil, "village": village,
                        "khasra_no": str(khasra_no),
                        "debug_screenshots": {"plot_search": str(debug_plot)},
                    }

                # ── 1.7 final map screenshot (map element only) ────────────
                self._screenshot_map(page, target_image)

                # map view extent for georeferencing the PNG (EPSG:3857 -> also give 4326)
                map_meta = page.evaluate(
                    """() => {
                        try {
                          const v = window.map.getView();
                          return { proj: v.getProjection().getCode(),
                                   zoom: v.getZoom(),
                                   extent: v.calculateExtent(window.map.getSize()) };
                        } catch(e) { return null; }
                    }"""
                )

                browser.close()
                self.logger.info(f"SUCCESS — screenshot {target_image}, geometry {'captured' if parcel_raw.get('wkt') else 'MISSING'}")

                return {
                    "status": "SUCCESS",
                    "source": "LIVE_BHUNAKSHA_PORTAL",
                    "state": self.state_name,
                    "portal_url": self.PORTAL_URL,
                    "image_path": str(target_image),
                    "district": district, "tehsil": tehsil, "village": village,
                    "khasra_no": str(khasra_no),
                    "giscode": giscode,
                    "plot_info_text": plot_info_text,
                    "parcel_raw": parcel_raw,           # -> gps_engine.step2_geometry
                    "map_meta": map_meta,
                    "name_warnings": name_warnings,
                    "zoom_level": DEFAULT_MIN_ZOOM,
                    "debug_screenshots": {
                        "homepage": str(debug_home),
                        "after_district": str(debug_dist),
                        "after_taluka": str(debug_teh),
                        "after_village": str(debug_vil),
                        "after_plot": str(debug_plot),
                    },
                }

        except Exception as e:
            self.logger.error(f"Maharashtra BhuNaksha error: {e}", exc_info=True)
            return self._fail(str(e), self.PORTAL_URL)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _fail(self, error: str, url: str, reason: Optional[str] = None) -> Dict[str, Any]:
        if reason is None:
            e = str(error).lower()
            reason = "portal_unreachable" if any(k in e for k in ("net::", "err_", "timeout", "could not connect")) \
                else "step1_failed"
        return {"status": "FAILED", "reason": reason, "state": self.state_name, "portal_url": url, "error": error}

    def _fetch_plot_info(self, page, giscode: Optional[str], plotno: str) -> Dict[str, Any]:
        """Call /rest/MapInfo/getPlotInfo inside the page session and normalise the payload."""
        if not giscode:
            return {"error": "no_giscode"}
        try:
            data = page.evaluate(
                """async ([giscode, plotno]) => {
                    const body = 'state=%s&giscode=' + encodeURIComponent(giscode)
                               + '&plotno=' + encodeURIComponent(plotno) + '&srs=3857';
                    const r = await fetch('../rest/MapInfo/getPlotInfo', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body
                    });
                    const j = await r.json();
                    return { the_geom: j.the_geom, area: j.area, formatedArea: j.formatedArea,
                             xmin: j.xmin, ymin: j.ymin, xmax: j.xmax, ymax: j.ymax,
                             plotid: j.plotid, giscode: j.giscode, plotno: j.plotno };
                }""" % STATE_CODE,
                [giscode, plotno],
            )
        except Exception as e:
            self.logger.warning(f"getPlotInfo call failed: {e}")
            return {"error": f"getPlotInfo_failed: {e}"}

        if not data or not data.get("the_geom"):
            return {"error": "no_geometry_in_response", "raw": data}

        bbox = None
        if all(data.get(k) is not None for k in ("xmin", "ymin", "xmax", "ymax")):
            bbox = [data["xmin"], data["ymin"], data["xmax"], data["ymax"]]
        return {
            "wkt": data["the_geom"],
            "native_crs": NATIVE_CRS,
            "raw_area": data.get("area"),
            "bbox": bbox,
            "plot_id": data.get("plotid"),
            "giscode": data.get("giscode") or giscode,
        }

    def _screenshot_map(self, page, target_image: Path) -> None:
        for sel in ("#map", ".ol-viewport", "canvas.ol-unselectable", "#openlayers-map"):
            try:
                el = page.locator(sel).first
                if el.is_visible():
                    el.screenshot(path=str(target_image), animations="disabled")
                    return
            except Exception:
                continue
        self.safe_screenshot(page, target_image)
