"""
GPS Engine — Gujarat AnyROR Adapter
===================================
Portal: https://anyror.gujarat.gov.in
Tech Stack: ASP.NET / vanilla JS. AnyROR is NOT BhuNaksha — it is an integrated
land-records portal; the cadastral map opens from a separate "map view" flow.
Language: Gujarati.

STATUS: partial. The AnyROR portal has been offline (maintenance) throughout our
testing, so its live DOM / map endpoint could not be mapped. This adapter:
  - connects to the portal (FAILED: portal_unreachable if it is down)
  - captures a screenshot
  - attempts to reach the rural land-record / map selection
  - returns FAILED: navigation_not_mapped where the flow needs live-DOM work

When someone has portal access, fill in `_navigate_anyror()` with the real
district/taluka/village/survey selectors and the map/geometry endpoint.
"""

import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.adapters.base import CadastralAdapter
from gps_engine.config import DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT, PAGE_LOAD_WAIT

logger = logging.getLogger("GPS.Adapter.GJ")

NATIVE_CRS = "EPSG:32643"   # Gujarat is UTM 42N/43N — mostly 43N


class GJAnyRORAdapter(CadastralAdapter):
    """Adapter for the Gujarat AnyROR portal (partial — map flow not yet mapped)."""

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
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
                page = browser.new_page(viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT})

                self.logger.info("Step 1.1: Loading Gujarat AnyROR...")
                connected = self.navigate_with_retry(page, [self.portal_url], max_retries=2, timeout=15000)
                if not connected:
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_unreachable",
                            "error": f"could not connect to {self.portal_url}",
                            "state": self.state_name, "portal_url": self.portal_url}

                time.sleep(PAGE_LOAD_WAIT)
                self.safe_screenshot(page, target_image)
                title = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                self.logger.info("  loaded: %s", title or page.url)

                result = self._navigate_anyror(page, district, tehsil, village, khasra_no)
                browser.close()

                result.setdefault("state", self.state_name)
                result.setdefault("portal_url", connected)
                result.setdefault("image_path", str(target_image))
                return result

        except Exception as e:
            self.logger.error(f"Gujarat AnyROR error: {e}", exc_info=True)
            return {"status": "FAILED", "reason": "adapter_exception", "state": self.state_name,
                    "portal_url": self.portal_url, "error": str(e)}

    def _navigate_anyror(self, page, district, tehsil, village, khasra_no) -> Dict[str, Any]:
        """
        TODO (needs live portal): select the 'View Land Record - Rural' / map flow, then
        district -> taluka -> village -> survey no, open the map, and read the parcel
        geometry. Until that is mapped against the live DOM, report a clear FAILED.
        """
        page_text = ""
        try:
            page_text = page.inner_text("body")[:400]
        except Exception:
            pass
        self.logger.info("  AnyROR navigation is not yet mapped — returning FAILED")
        return {
            "status": "FAILED",
            "reason": "navigation_not_mapped",
            "error": ("Gujarat AnyROR is reachable but its map/geometry flow has not been "
                      "mapped against the live DOM (portal was offline during development). "
                      "Screenshot captured. See gps_engine/adapters/gj_anyror.py::_navigate_anyror."),
            "page_snippet": page_text.strip() or None,
        }
