"""
GPS Engine — Haryana HSAC EODB Map Adapter
==========================================
Portal: https://hsac.in/eodb/map
Tech Stack: React (HSAC GeoStack). Extra "Murabba" hierarchy level.
Language: English.

STATUS: partial + blocked. The HSAC EODB map sits behind an OTP / login wall, so
even with portal access this adapter cannot complete without a login/session
strategy. It connects, screenshots, detects the login requirement, and returns a
clear FAILED.

Hierarchy (documented): District -> Tehsil -> Village -> Murabba -> Khasra.
"""

import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.adapters.base import CadastralAdapter
from gps_engine.config import DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT, PAGE_LOAD_WAIT

logger = logging.getLogger("GPS.Adapter.HR")

NATIVE_CRS = "EPSG:32643"   # Haryana is UTM 43N

_LOGIN_MARKERS = ("otp", "one time password", "login", "sign in", "mobile number", "register")


class HRHSACAdapter(CadastralAdapter):
    """Adapter for the Haryana HSAC EODB map (blocked by an OTP login wall)."""

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

                self.logger.info("Step 1.1: Loading Haryana HSAC EODB map...")
                connected = self.navigate_with_retry(page, [self.portal_url], max_retries=2, timeout=15000)
                if not connected:
                    browser.close()
                    return {"status": "FAILED", "reason": "portal_unreachable",
                            "error": f"could not connect to {self.portal_url}",
                            "state": self.state_name, "portal_url": self.portal_url}

                time.sleep(PAGE_LOAD_WAIT)
                self.safe_screenshot(page, target_image)

                body = ""
                try:
                    body = page.inner_text("body").lower()
                except Exception:
                    pass
                browser.close()

                if any(m in body for m in _LOGIN_MARKERS):
                    self.logger.info("  HSAC shows a login / OTP wall — cannot proceed")
                    return {"status": "FAILED", "reason": "login_required",
                            "error": ("Haryana HSAC EODB map requires an OTP / mobile login before the "
                                      "district/tehsil/village/murabba/khasra selectors are usable. "
                                      "A login/session strategy is needed. Screenshot captured."),
                            "state": self.state_name, "portal_url": connected,
                            "image_path": str(target_image)}

                self.logger.info("  HSAC map loaded without an obvious login wall — navigation not yet mapped")
                return {"status": "FAILED", "reason": "navigation_not_mapped",
                        "error": ("Haryana HSAC map is reachable and did not show a login wall, but its "
                                  "React selector flow (District -> Tehsil -> Village -> Murabba -> Khasra) "
                                  "has not been mapped against the live DOM. Screenshot captured."),
                        "state": self.state_name, "portal_url": connected,
                        "image_path": str(target_image)}

        except Exception as e:
            self.logger.error(f"Haryana HSAC error: {e}", exc_info=True)
            return {"status": "FAILED", "reason": "adapter_exception", "state": self.state_name,
                    "portal_url": self.portal_url, "error": str(e)}
