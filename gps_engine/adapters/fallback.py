"""
GPS Engine — Fallback Adapter
=============================
Used for states that are in `config.CADASTRAL_PORTALS` but do not have a real
navigation adapter yet (Gujarat, Madhya Pradesh, Uttarakhand, Delhi, Haryana).

It behaves like any other Step 1 adapter — it *attempts* to open the state's
portal — but it has no code to drive that portal's dropdowns / geometry API, so
it always ends in `FAILED`. The point is the audit record: every attempt is
timestamped and written to the run folder, so there is a clear log of when a
portal was tried and what happened, and we notice automatically if a dead
portal comes back.

Outcomes:
  portal_unreachable       - could not connect on any retry (DNS / timeout / refused)
  adapter_not_implemented  - portal loaded, but there is no adapter to navigate it
"""

import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.adapters.base import CadastralAdapter
from gps_engine.config import DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT, PAGE_LOAD_WAIT

logger = logging.getLogger("GPS.Adapter.Fallback")

_MAX_RETRIES = 2          # keep the unreachable-portal path well under a minute
_GOTO_TIMEOUT_MS = 15000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unreachable(state: str, url: str, error: Optional[str], probed_at: str) -> Dict[str, Any]:
    return {
        "status": "FAILED",
        "source": "FALLBACK_ADAPTER",
        "state": state,
        "portal_url": url,
        "reason": "portal_unreachable",
        "error": error,
        "portal_check": {
            "probed_at": probed_at,
            "reason": "portal_unreachable",
            "attempts": _MAX_RETRIES,
            "error": error,
            "portal_url": url,
        },
    }


def _reachable_no_adapter(state: str, url: str, final_url: str, title: str,
                          image_path: str, probed_at: str) -> Dict[str, Any]:
    return {
        "status": "FAILED",
        "source": "FALLBACK_ADAPTER",
        "state": state,
        "portal_url": url,
        "reason": "adapter_not_implemented",
        "error": f"portal for {state} is reachable but no navigation adapter is built yet",
        "image_path": image_path,
        "debug_screenshots": {"portal": image_path},
        "portal_check": {
            "probed_at": probed_at,
            "reason": "adapter_not_implemented",
            "portal_url": url,
            "final_url": final_url,
            "page_title": title,
        },
    }


class FallbackAdapter(CadastralAdapter):
    """Attempts the portal, records the outcome, never continues the pipeline."""

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
        url = self.portal_url or self.portal_config.get("url", "")
        probed_at = _now()

        self.logger.info("=" * 70)
        self.logger.info("GPS ENGINE — STEP 1: %s  (no adapter — reachability attempt only)",
                         self.state_name.upper())
        self.logger.info("=" * 70)
        self.logger.info("  Portal URL : %s", url or "(none in config)")
        self.logger.info("  Attempted  : %s", probed_at)

        if not url:
            self.logger.error("  -> no portal URL configured for %s", self.state_name)
            r = _unreachable(self.state_name, url, "no 'url' in config.CADASTRAL_PORTALS", probed_at)
            r["district"], r["tehsil"], r["village"], r["khasra_no"] = district, tehsil, village, str(khasra_no)
            return r

        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:
            self.logger.error("  -> Playwright unavailable: %s", e)
            return _unreachable(self.state_name, url, f"playwright unavailable: {e}", probed_at)

        last_error = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page(viewport={"width": DEFAULT_IMAGE_WIDTH, "height": DEFAULT_IMAGE_HEIGHT})

            connected = False
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    self.logger.info("  Connecting (attempt %d/%d)...", attempt, _MAX_RETRIES)
                    page.goto(url, timeout=_GOTO_TIMEOUT_MS, wait_until="domcontentloaded")
                    connected = True
                    break
                except Exception as e:
                    last_error = str(e).splitlines()[0]
                    self.logger.warning("  attempt %d failed: %s", attempt, last_error)
                    time.sleep(2)

            if not connected:
                browser.close()
                self.logger.error("  -> portal_unreachable after %d attempts: %s", _MAX_RETRIES, last_error)
                r = _unreachable(self.state_name, url, last_error, probed_at)
                r["district"], r["tehsil"], r["village"], r["khasra_no"] = district, tehsil, village, str(khasra_no)
                return r

            time.sleep(PAGE_LOAD_WAIT)
            self.safe_screenshot(page, target_image)
            try:
                title = page.title()
            except Exception:
                title = ""
            final_url = page.url
            browser.close()

        self.logger.info("  Loaded     : %s", title or final_url)
        self.logger.info("  -> adapter_not_implemented (portal reachable; screenshot saved)")
        r = _reachable_no_adapter(self.state_name, url, final_url, title, str(target_image), probed_at)
        r["district"], r["tehsil"], r["village"], r["khasra_no"] = district, tehsil, village, str(khasra_no)
        return r
