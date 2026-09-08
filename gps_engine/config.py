"""
GPS Engine — Configuration
===========================
Portal registry and shared constants for the Step 1 cadastral capture pipeline.

CADASTRAL_PORTALS: maps every supported state to its cadastral map portal metadata.
  Each entry specifies the adapter module name, live URL, tech stack, nav hierarchy,
  and script language so the router (step1_capture.py) can load the right adapter.

Portal taxonomy (per BRD Section 5):
  Each state has up to 3 portal types:
    1. Land Records  — owner name, area, encumbrances   (Bhulekh / Jamabandi / 7-12)
    2. Cadastral Map — GPS + boundary polygon            (BhuNaksha / AnyROR / HSAC)  ← Step 1
    3. Registration  — deed verification                 (IGRS / ePanjiyan / IGR)

  Step 1 ONLY interacts with the Cadastral Map portal.
  Land Records and Registration portals are handled in later steps.
"""

from pathlib import Path

# ── Output Directory ─────────────────────────────────────────────────────────
OUTPUT_DIR = Path(__file__).parent / "output"

# ── Image Capture Defaults ───────────────────────────────────────────────────
DEFAULT_IMAGE_WIDTH  = 1200
DEFAULT_IMAGE_HEIGHT = 800
DEFAULT_MIN_ZOOM     = 17

# ── Haversine Verification Thresholds (BRD Section 4, Column 5 logic) ────────
DISTANCE_THRESHOLD_MATCH_METERS = 50.0    # ≤ 50 m  → MATCH  (Green  ✔)
DISTANCE_THRESHOLD_REFER_METERS = 200.0   # ≤ 200 m → REFER  (Amber  ⚠)
# > 200 m → NEGATIVE (Red ✖)

# ── Playwright Timing Defaults ────────────────────────────────────────────────
AJAX_WAIT_SECONDS  = 2.5   # wait after each dropdown selection for AJAX cascade
ZOOM_SETTLE_SECONDS = 3.0  # wait after khasra search for map zoom + tile render
PAGE_LOAD_WAIT     = 4     # seconds after domcontentloaded for JS/Angular init


# ─────────────────────────────────────────────────────────────────────────────
# CADASTRAL PORTAL REGISTRY
# Source: BRD Section 5 — State-wise Government Portals
#
# Keys:
#   adapter     : module name in gps_engine/adapters/ (without .py)
#   url         : live portal URL
#   tech_stack  : 'angular_material' | 'aspnet_webforms' | 'react' | 'vanilla_js'
#   nav_levels  : ordered list of dropdown/input levels the user must fill
#   script_lang : language of portal UI text ('hi'=Hindi, 'mr'=Marathi,
#                 'gu'=Gujarati, 'en'=English)
#   notes       : implementation notes for the adapter developer
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# NAMING OVERRIDES — for names transliteration + fuzzy match cannot resolve
# (British-era / Persian-origin place names, heavy spelling drift).
# Keyed by English (lower-case) -> the Devanagari core the portal actually shows.
# gps_engine.naming.resolve() consults this before fuzzy matching.
# ─────────────────────────────────────────────────────────────────────────────
NAMING_OVERRIDES = {
    "forbesganj":  "फारबिसगंज",
    "nadini":      "नटिनी",       # Gorakhpur / Khajani — portal spelling
}


CADASTRAL_PORTALS = {

    "Uttar Pradesh": {
        "adapter":     "up_bhunaksha",
        "url":         "https://upbhunaksha.gov.in",
        "tech_stack":  "nic_bhunaksha_angular",   # Angular front-end, /bhunakshaserver/ REST back-end
        "nav_levels":  ["district", "tehsil", "village", "khasra"],
        "native_crs":  "EPSG:32644",   # confirmed in getVVVVExtentGeoref response
        "script_lang": "hi",
        "verified":    "2026-09-06",
        "notes": (
            "Angular Material mat-select (IDs mat-select-0/2/4). Devanagari option text with "
            "numeric code prefix e.g. '188 गोरखपुर'. Search: Enter on input#plotNo. "
            "Geometry: <origin>/bhunakshaserver/MapInfo/getPlotByPlotNo (bbox, UTM 44N) + "
            "getPlotInfo (owner/area text). NO polygon exposed — centroid = bbox centre. "
            "gisCode captured from getVVVVExtentGeoref response during navigation."
        ),
    },

    "Rajasthan": {
        "adapter":     "rj_bhunaksha",
        "url":         "https://bhunaksha.rajasthan.gov.in/Viewmap/",   # bhunaksha.raj.nic.in no longer resolves
        "bhunaksha_state_code": "08",       # ScalarDatahandler ?state= code
        "level_ids":   ["#level_1", "#level_2", "#level_3", "#level_4", "#level_5", "#level_6"],
        "tech_stack":  "nic_bhunaksha_classic",   # #level_1..#level_6 selects + OpenLayers
        "nav_levels":  ["district", "tehsil", "ri_circle", "halka", "village", "sheet", "khasra"],
        "native_crs":  "EPSG:32643",        # UTM 43N
        "verified":    "2026-09-06",
        "notes": (
            "Classic NIC. #level_1 District .. #level_5 Village, #level_6 Sheet. "
            "Geometry: ScalarDatahandler?OP=5&state=08&levels=<v1..v6,>&plotno=<n> -> "
            "center_x/center_y + bbox + owner info (NO polygon). Nakal report needs a CAPTCHA "
            "but OP=5 geometry does not. Auto-picks first option for RI/halka/sheet — "
            "pass real values via extra_params for production."
        ),
    },

    "Maharashtra": {
        "adapter":     "mh_bhunaksha",
        "url":         "https://mahabhunakasha.mahabhumi.gov.in/27/index.html",
        "state_code":  "27",
        "tech_stack":  "nic_bhunaksha_modern",   # plain <select> + OpenLayers + WMS
        "nav_levels":  ["category", "district", "taluka", "village", "survey_no"],
        "geom_endpoint":     "/rest/MapInfo/getPlotInfo",   # resp.the_geom = WKT MULTIPOLYGON
        "plotlist_endpoint": "/rest/VillageMapService/kidelistFromGisCodeMH",
        "native_crs":  "EPSG:32643",   # UTM 43N; east MH may be 44N (Step 3 re-checks)
        "script_lang": "mr",
        "verified":    "2026-09-06",
        "notes": (
            "Modern NIC BhuNaksha. Selectors #level_0..#level_4 (level_1 = Category R/U). "
            "Plot search #plotNo + #plotNoButton; plot dropdown #surveyNumber. "
            "giscode = 'RVM'+district(2)+taluka(2)+village(18). "
            "getPlotInfo POST {state,giscode,plotno,srs} -> the_geom WKT in UTM. "
            "See docs/PORTAL_NOTES.md."
        ),
    },

    "Gujarat": {
        "adapter":     "gj_anyror",
        "url":         "https://anyror.gujarat.gov.in",
        "tech_stack":  "vanilla_js",
        "nav_levels":  ["district", "taluka", "village", "survey_no"],
        "script_lang": "gu",
        "notes": (
            "AnyROR portal — navigate to Map View tab after village/survey selection. "
            "Gujarati script labels. Selectors to be confirmed by live DOM dump."
        ),
    },

    "Madhya Pradesh": {
        "adapter":     "mp_bhunaksha",
        "url":         "https://mpbhulekh.gov.in/bhunaksha",
        "tech_stack":  "aspnet_webforms",
        "nav_levels":  ["district", "tehsil", "village", "khasra"],
        "script_lang": "hi",
        "notes": (
            "MP Bhulekh BhuNaksha module. ASP.NET with VIEWSTATE. "
            "Hindi interface. Selectors to be confirmed by live DOM dump."
        ),
    },

    "Uttarakhand": {
        "adapter":     "uk_bhunaksha",
        "url":         "https://bhunaksha.uk.gov.in",
        "tech_stack":  "angular_material",
        "nav_levels":  ["district", "tehsil", "village", "khasra"],
        "script_lang": "hi",
        "notes": (
            "Similar Angular stack to UP. GPS centroid in map URL. "
            "Selectors to be confirmed by live DOM dump."
        ),
    },

    "Chhattisgarh": {
        "adapter":     "cg_bhunaksha",
        "url":         "https://bhunaksha.cg.nic.in",
        "bhunaksha_state_code": "22",     # ScalarDatahandler ?state= code (NOT the LGD code)
        "tech_stack":  "nic_bhunaksha_classic",   # #level_1..#level_4 + ScalarDatahandler servlet
        "nav_levels":  ["district", "tehsil", "ri_circle", "village", "khasra"],
        "native_crs":  "EPSG:32644",       # UTM 44N
        "verified":    "2026-09-06",
        "notes": (
            "Classic NIC. #level_1 District, #level_2 Tehsil, #level_3 RI Circle, #level_4 Village. "
            "Geometry: ScalarDatahandler?OP=5&state=22&levels=<v1,..,>&plotno=<n> -> "
            "center_x/center_y + bbox + owner/area info (NO polygon; parcel is WMS-rendered). "
            "See docs/PORTAL_NOTES.md."
        ),
    },

    "Bihar": {
        "adapter":     "br_bhunaksha",
        "url":         "https://bhunaksha.bihar.gov.in/10/index.jsp",
        "bhunaksha_state_code": "10",
        "tech_stack":  "nic_bhunaksha_classic",   # ScalarDatahandler; 7 hierarchy levels
        "nav_levels":  ["district", "subdivision", "circle", "mauza", "survey_type", "map_instance", "sheet", "khasra"],
        "native_crs":  "EPSG:32645",   # most of Bihar is UTM 45N; west Bihar 44N (gate re-checks)
        "verified":    "2026-09-06",
        "notes": (
            "Classic NIC. 7 levels: #level_1 District .. #level_5 Survey type (RS/CS), "
            "#level_6 map instance, #level_7 sheet. Requires clicking a 'View Map' control first. "
            "Geometry: ScalarDatahandler?OP=5&state=10&levels=<v1..v7,>&plotno=<n> -> "
            "center_x/center_y + bbox (NO polygon). giscode e.g. 'CS07020604981680600'."
        ),
    },

    "Haryana": {
        "adapter":     "hr_hsac",
        "url":         "https://hsac.in/eodb/map",
        "tech_stack":  "react",
        "nav_levels":  ["district", "tehsil", "village", "murabba", "khasra"],
        "script_lang": "en",
        "notes": (
            "HSAC GeoStack EODB portal. Has extra Murabba level. "
            "English interface. React-based. Selectors to be confirmed by live DOM dump."
        ),
    },

    "Delhi": {
        "adapter":     "dl_dlrc",
        "url":         "https://dlrc.delhigovt.nic.in",
        "tech_stack":  "aspnet_webforms",
        "nav_levels":  ["zone", "village", "khasra"],
        "script_lang": "en",
        "notes": (
            "DLRC Revenue Maps. Covers Lal Dora / revenue villages only. "
            "Urban properties NOT applicable — use Google Maps satellite directly. "
            "English interface. Selectors to be confirmed by live DOM dump."
        ),
    },
}


