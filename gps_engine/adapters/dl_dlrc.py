"""
GPS Engine — Delhi DLRC Revenue Maps Adapter
============================================
Portal: https://dlrc.delhigovt.nic.in
Tech Stack: ASP.NET Web Forms (VIEWSTATE).
Coverage: Lal Dora / revenue villages only. Urban (planned/DDA) plots are NOT on
DLRC — those should be verified directly against satellite imagery.

STATUS: partial. The DLRC portal is unreachable from our test environment, so its
live DOM could not be mapped. This adapter connects, screenshots, and returns a
clear FAILED until `_navigate_dlrc()` is filled in against the live portal.

Hierarchy (documented): Zone -> Revenue Village -> Khasra No.
"""

import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.adapters.base import CadastralAdapter
from gps_engine.config import DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT, PAGE_LOAD_WAIT

logger = logging.getLogger("GPS.Adapter.DL")

NATIVE_CRS = "EPSG:32643"   # Delhi is UTM 43N


class DLDLRCAdapter(CadastralAdapter):
    """Adapter for the Delhi DLRC Revenue Maps portal (partial — flow not yet mapped)."""

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
        # DLRC's top level is a "Zone"; callers pass it as tehsil (or extra_params['zone']).
        zone = (extra_params or {}).get("zone") or tehsil

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
                page = browser.new_page(viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT})

                self.logger.info("Step 1.1: Loading Delhi DLRC...")
                connected = self.navigate_with_retry(page, [self.portal_url], max_retries=2, timeout=15000)
                if not connected:
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_unreachable",
                            "error": f"could not connect to {self.portal_url}",
                            "state": self.state_name, "portal_url": self.portal_url}

                time.sleep(PAGE_LOAD_WAIT)
                self.safe_screenshot(page, target_image)
                self.logger.info("  loaded: %s", (page.title() or page.url))

                result = self._navigate_dlrc(page, zone, village, khasra_no)
                browser.close()
                result.setdefault("state", self.state_name)
                result.setdefault("portal_url", connected)
                result.setdefault("image_path", str(target_image))
                return result

        except Exception as e:
            self.logger.error(f"Delhi DLRC error: {e}", exc_info=True)
            return {"status": "FAILED", "reason": "adapter_exception", "state": self.state_name,
                    "portal_url": self.portal_url, "error": str(e)}

    def _navigate_dlrc(self, page, zone, village, khasra_no) -> Dict[str, Any]:
        """
        TODO (needs live portal): Zone -> Revenue Village -> Khasra, open the revenue
        map, read the parcel geometry. DLRC is ASP.NET (VIEWSTATE), so each dropdown
        change posts back the form. Until mapped against the live DOM, return FAILED.
        """
        snippet = ""
        try:
            snippet = page.inner_text("body")[:400]
        except Exception:
            pass
        self.logger.info("  DLRC navigation is not yet mapped — returning FAILED")
        return {
            "status": "FAILED",
            "reason": "navigation_not_mapped",
            "error": ("Delhi DLRC is reachable but its Zone/Village/Khasra flow has not been "
                      "mapped against the live DOM (portal was unreachable during development). "
                      "Screenshot captured. Note: urban/DDA plots are not on DLRC — verify those "
                      "against satellite directly. See gps_engine/adapters/dl_dlrc.py::_navigate_dlrc."),
            "page_snippet": snippet.strip() or None,
        }
