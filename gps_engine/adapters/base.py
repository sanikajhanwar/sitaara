"""
GPS Engine — Cadastral Portal Adapter Base Class
================================================
Defines the contract and shared utilities for all state cadastral portal adapters.
Every state adapter inherits from CadastralAdapter and implements the `capture` method.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
import logging
import json
import re
import time

from gps_engine.config import (
    DEFAULT_IMAGE_WIDTH,
    DEFAULT_IMAGE_HEIGHT,
    DEFAULT_MIN_ZOOM,
    OUTPUT_DIR,
)

logger = logging.getLogger("GPS.CadastralAdapter")


class CadastralAdapter(ABC):
    """
    Abstract Base Class for state-specific cadastral map portal automation.
    """

    def __init__(self, state_name: str, portal_config: Dict[str, Any], output_dir: Optional[Path] = None):
        self.state_name = state_name
        self.portal_config = portal_config
        self.portal_url = portal_config.get("url", "")
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(f"GPS.Adapter.{portal_config.get('adapter', state_name)}")

    @abstractmethod
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
        """
        Navigate the state portal and capture a screenshot of the cadastral map.

        Args:
            district: District name
            tehsil: Tehsil / Taluka / Circle / Sub-division name
            village: Village / Mauza name
            khasra_no: Khasra / Survey / Gat / Killa number
            image_path: Destination path for the captured screenshot (default: output/bhunaksha.png)
            headless: Whether to run Playwright in headless mode
            extra_params: Optional dict for portal-specific parameters (e.g. ri_circle, murabba)

        Returns:
            Dict containing status ("SUCCESS" | "FAILED"), image_path, portal_url, error (if any), etc.
        """
        pass

    def navigate_with_retry(self, page, urls: Union[str, List[str]], max_retries: int = 3, timeout: int = 35000) -> Optional[str]:
        """
        Tries connecting to one or multiple portal URLs with retry and fallback.
        """
        url_list = [urls] if isinstance(urls, str) else urls
        for attempt in range(max_retries):
            for u in url_list:
                try:
                    self.logger.info(f"Connecting to {u} (attempt {attempt+1})...")
                    page.goto(u, timeout=timeout, wait_until="domcontentloaded")
                    return u
                except Exception as e:
                    self.logger.warning(f"Connection attempt to {u} failed: {e}")
                    time.sleep(1.5)
        return None

    def safe_screenshot(self, page, path: Path, timeout: int = 10000, full_page: bool = False) -> bool:
        """
        Captures screenshot safely without blocking on external font or slow resource loads.
        """
        try:
            page.screenshot(path=str(path), timeout=timeout, full_page=full_page, animations="disabled")
            return True
        except Exception as e:
            self.logger.warning(f"Standard screenshot failed ({e}). Retrying with evaluate...")
            try:
                # Force font load bypass
                page.evaluate("document.fonts && document.fonts.ready")
                page.screenshot(path=str(path), timeout=5000, full_page=False, animations="disabled")
                return True
            except Exception as e2:
                self.logger.error(f"Screenshot capture failed: {e2}")
                return False

    def select_first_available(self, page, selector: str, timeout: int = 8) -> bool:
        """
        Selects the first valid non-placeholder option from a native HTML <select>.
        """
        start = time.time()
        while time.time() - start < timeout:
            res = page.evaluate(f'''() => {{
                const sel = document.querySelector("{selector}");
                if (!sel || sel.options.length <= 1) return null;
                for (let i = 0; i < sel.options.length; i++) {{
                    const val = sel.options[i].value;
                    if (val && val !== "0" && val !== "-1" && val !== "") {{
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

    def select_named(self, page, selector: str, query: str, level_name: str = "level",
                     timeout: int = 12) -> Dict[str, Any]:
        """
        Resolve `query` (English or Devanagari) against a native <select>'s live options via
        gps_engine.naming, then select the winning option by its exact text.

        Returns {"ok": bool, "matched": <option text>, "score": float, "method": str,
                 "warning": <str|None>}.
        """
        from gps_engine.naming import resolve
        try:
            from gps_engine.config import NAMING_OVERRIDES
        except Exception:
            NAMING_OVERRIDES = {}

        start = time.time()
        options: List[str] = []
        while time.time() - start < timeout:
            options = page.evaluate(
                """(sel) => { const s = document.querySelector(sel);
                    return s ? Array.from(s.options).map(o => o.text) : []; }""",
                selector,
            )
            if len([o for o in options if o and o.strip() not in ("--Select--", "----Select----")]) >= 1:
                break
            time.sleep(0.5)

        m = resolve(options, query, overrides=NAMING_OVERRIDES)
        if m is None:
            return {"ok": False, "matched": None, "score": 0.0, "method": None,
                    "warning": f"could not resolve {level_name} '{query}' among {len(options)} options"}

        selected = page.evaluate(
            """([sel, text]) => {
                const s = document.querySelector(sel);
                if (!s) return false;
                for (let i = 0; i < s.options.length; i++) {
                    if (s.options[i].text === text) {
                        s.selectedIndex = i;
                        s.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""",
            [selector, m.text],
        )
        warning = None
        if m.low_confidence:
            warning = f"{level_name} '{query}' -> '{m.text}' at low confidence {m.score:.2f} ({m.method})"
        self.logger.info(f"  [{selector}] {level_name}: '{query}' -> '{m.text}' ({m.score:.2f}, {m.method})")
        return {"ok": bool(selected), "matched": m.text, "score": m.score, "method": m.method,
                "warning": warning}

    def select_named_matselect(self, page, mat_select_selector: str, query: str,
                               level_name: str = "level", timeout: int = 15) -> Dict[str, Any]:
        """
        Angular Material <mat-select> version of select_named: open the overlay, resolve `query`
        against the live <mat-option> texts via gps_engine.naming, click the winner, and verify
        the trigger text updated. Returns the same dict shape as select_named.
        """
        from gps_engine.naming import resolve
        try:
            from gps_engine.config import NAMING_OVERRIDES
        except Exception:
            NAMING_OVERRIDES = {}

        try:
            page.wait_for_selector(mat_select_selector, timeout=timeout * 1000)
            page.click(mat_select_selector)
            page.wait_for_selector("mat-option", timeout=8000)
        except Exception as e:
            return {"ok": False, "matched": None, "score": 0.0, "method": None,
                    "warning": f"{level_name}: could not open mat-select ({e})"}

        # wait for the option list to actually populate
        import time as _t
        start = _t.time()
        options: List[str] = []
        while _t.time() - start < timeout:
            options = page.evaluate(
                """() => Array.from(document.querySelectorAll('mat-option')).map(o => o.innerText.trim())"""
            )
            if len(options) > 1:
                break
            _t.sleep(0.4)

        m = resolve(options, query, overrides=NAMING_OVERRIDES)
        if m is None:
            page.keyboard.press("Escape")
            return {"ok": False, "matched": None, "score": 0.0, "method": None,
                    "warning": f"could not resolve {level_name} '{query}' among {len(options)} mat-options"}

        clicked = page.evaluate(
            """(text) => {
                const opts = Array.from(document.querySelectorAll('mat-option'));
                const o = opts.find(x => x.innerText.trim() === text);
                if (!o) return false;
                o.click();
                return true;
            }""",
            m.text,
        )
        try:
            page.wait_for_selector("mat-option", state="detached", timeout=5000)
        except Exception:
            pass

        trigger = page.evaluate(
            """(sel) => { const s = document.querySelector(sel);
                const v = s && s.querySelector('.mat-mdc-select-value, .mat-select-value');
                return v ? v.innerText.trim() : ''; }""",
            mat_select_selector,
        )
        ok = bool(clicked) and (m.text[:6] in trigger or trigger[:6] in m.text or trigger != "")
        warning = None
        if m.low_confidence:
            warning = f"{level_name} '{query}' -> '{m.text}' at low confidence {m.score:.2f}"
        self.logger.info(f"  [{mat_select_selector}] {level_name}: '{query}' -> '{m.text}' ({m.score:.2f}); trigger now '{trigger}'")
        return {"ok": ok, "matched": m.text, "score": m.score, "method": m.method, "warning": warning}

    def read_level_values(self, page, level_selector_ids: List[str]) -> List[Optional[str]]:
        """Snapshot the current <select> values for the hierarchy dropdowns."""
        try:
            return page.evaluate(
                """(ids) => ids.map(id => { const s = document.querySelector(id); return s ? s.value : null; })""",
                level_selector_ids,
            )
        except Exception:
            return []

    def classic_nic_plot_info(self, page, bhunaksha_state_code: str, plotno: str, native_crs: str,
                              level_selector_ids: Optional[List[str]] = None,
                              level_values: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Classic / 'ScalarDatahandler' NIC BhuNaksha (CG, RJ, BR, MP...).
        Calls `ScalarDatahandler?OP=5&state=<code>&levels=<v1,v2,...,>&plotno=<n>` from inside
        the page session and normalises the payload for gps_engine.step2_geometry.

        These portals render the parcel server-side (WMS) and do NOT return a polygon; OP=5
        gives the plot centroid (center_x/center_y), bounding box, and an owner/area info blob.

        Pass EITHER `level_values` (a snapshot taken right after village selection — preferred,
        since some portals blank the dropdowns once the map renders) OR `level_selector_ids`
        (read live now).

        Returns a `parcel_raw` dict: {centroid_native, bbox, native_crs, raw_area, info, giscode}
        or {"error": ...}.
        """
        if level_values is None:
            level_values = self.read_level_values(page, level_selector_ids or [])

        if not level_values or any(v in (None, "", "0", "-1") for v in level_values):
            return {"error": f"incomplete_hierarchy: {level_values}"}

        levels_param = ",".join(str(v) for v in level_values) + ","
        try:
            data = page.evaluate(
                """async ([stateCode, levels, plotno]) => {
                    const u = 'ScalarDatahandler?OP=5&state=' + encodeURIComponent(stateCode)
                            + '&levels=' + encodeURIComponent(levels)
                            + '&plotno=' + encodeURIComponent(plotno);
                    const r = await fetch(u);
                    const txt = await r.text();
                    try { return JSON.parse(txt); } catch(e) { return {__raw: txt.slice(0, 500)}; }
                }""",
                [bhunaksha_state_code, levels_param, str(plotno)],
            )
        except Exception as e:
            return {"error": f"OP5_call_failed: {e}"}

        if not data or data.get("__raw") is not None:
            return {"error": "OP5_unparseable", "raw": (data or {}).get("__raw")}
        if str(data.get("has_data", "Y")).upper() == "N":
            return {"error": "plot_has_no_data", "plotno": plotno}

        def _num(*keys):
            for k in keys:
                v = data.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
            return None

        cx, cy = _num("center_x"), _num("center_y")
        xmin, ymin = _num("xmin"), _num("ymin")
        xmax, ymax = _num("xmax"), _num("ymax")
        bbox = [xmin, ymin, xmax, ymax] if None not in (xmin, ymin, xmax, ymax) else None
        if (cx is None or cy is None) and bbox:
            cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2

        raw_area = None
        info = data.get("info") or ""
        m = re.search(r"([0-9]+\.?[0-9]*)\s*(हेक्टेयर|hectare|hect|ha\b)", info, re.I)
        if m:
            raw_area = float(m.group(1)) * 10000.0   # ha -> m2

        if cx is None or cy is None:
            return {"error": "no_centroid_or_bbox_in_OP5", "raw_keys": list(data.keys())}

        return {
            "centroid_native": [cx, cy],
            "bbox": bbox,
            "native_crs": native_crs,
            "raw_area": raw_area,
            "info": info.strip() or None,
            "giscode": data.get("gisCode"),
        }

    def bhunakshaserver_plot_info(self, page, server_base: str, giscode: str,
                                  plotno: str, native_crs: str) -> Dict[str, Any]:
        """
        Angular NIC BhuNaksha (UP and similar) served under `<origin>/bhunakshaserver/`.
        Combines two calls made from the page session:
          POST <base>/MapInfo/getPlotByPlotNo   (form: giscode, plotno)  -> plot bbox {minx,miny,maxx,maxy,id}
          POST <base>/MapInfo/getPlotInfo       (json: {gisCode, plotNo}) -> owner/area text blob
        No polygon is exposed; returns centroid (bbox centre) + bbox + area for step2_geometry.
        """
        if not giscode:
            return {"error": "no_giscode"}
        try:
            data = page.evaluate(
                """async ([base, giscode, plotno]) => {
                    const out = {};
                    try {
                      const r1 = await fetch(base + '/MapInfo/getPlotByPlotNo', {
                          method: 'POST',
                          headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                          body: 'giscode=' + encodeURIComponent(giscode) + '&plotno=' + encodeURIComponent(plotno)
                      });
                      out.bbox = await r1.json();
                    } catch(e) { out.bbox_err = '' + e; }
                    try {
                      const r2 = await fetch(base + '/MapInfo/getPlotInfo', {
                          method: 'POST',
                          headers: {'Content-Type': 'application/json'},
                          body: JSON.stringify({gisCode: giscode, plotNo: String(plotno)})
                      });
                      out.info = await r2.text();
                    } catch(e) { out.info_err = '' + e; }
                    return out;
                }""",
                [server_base.rstrip("/"), giscode, str(plotno)],
            )
        except Exception as e:
            return {"error": f"bhunakshaserver_calls_failed: {e}"}

        bb = data.get("bbox") or {}
        xs = [bb.get("minx"), bb.get("maxx")]
        ys = [bb.get("miny"), bb.get("maxy")]
        if any(v is None for v in xs + ys):
            return {"error": "no_bbox_in_getPlotByPlotNo", "raw": data}
        xmin, xmax = float(xs[0]), float(xs[1])
        ymin, ymax = float(ys[0]), float(ys[1])

        raw_area = None
        info = data.get("info") or ""
        m = re.search(r"([0-9]+\.?[0-9]*)\s*(हेक्टेयर|hectare|hect|ha\b)", info, re.I)
        if m:
            raw_area = float(m.group(1)) * 10000.0

        return {
            "centroid_native": [(xmin + xmax) / 2, (ymin + ymax) / 2],
            "bbox": [xmin, ymin, xmax, ymax],
            "native_crs": native_crs,
            "raw_area": raw_area,
            "info": info.strip()[:2000] or None,
            "giscode": giscode,
            "plot_id": bb.get("id"),
        }

    def save_metadata(self, data: Dict[str, Any], path: Optional[Path] = None) -> Path:
        """Persist metadata dictionary to JSON."""
        target_path = path or (self.output_dir / "step1_metadata.json")
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.logger.info(f"Metadata saved to: {target_path}")
        return target_path
