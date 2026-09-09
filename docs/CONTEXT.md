# GPS Engine — Complete Context

This document is the full background for the `gps_engine/` module: the business problem it
solves, where it sits in the larger verification system, how the 8-step pipeline works, the
three different government-portal technologies it has to deal with, and the design decisions
(and their trade-offs) behind the current code.

For current build status and test results see [STATUS.md](STATUS.md).
For per-state portal technical notes see [PORTAL_NOTES.md](PORTAL_NOTES.md).

---

## 1. The business problem

When **Sitaara Housing Finance Ltd** processes a property loan, the lender must confirm four
things before disbursing against the property as collateral:

1. The borrower genuinely owns the property.
2. The property physically exists at the claimed address.
3. The land area and boundaries in the sale deed match government revenue records.
4. The field engineer inspected the *exact same* parcel on the ground — not a neighbour's
   plot, not an empty field with the right survey number painted on a rock.

Point 4 is where collateral fraud happens: a borrower mortgages a well-developed plot on paper
but the actual pledged land is somewhere else, worth a fraction of the valuation.

## 2. The 3-way verification model

The full system (of which this engine is one piece) cross-checks three independent sources:

| Source | What it is | Example fields |
|---|---|---|
| **1. Property Document** | The sale/gift/conveyance deed, run through OCR into structured JSON | owner name, khasra no., area, 4-side boundaries |
| **2. Government Land Records** | State portals — BhuNaksha (maps), Khatauni / Jamabandi / Bhulekh (text records) | official owner, official area, the cadastral map |
| **3. Technical Valuation Report (TVR)** | The field engineer's site inspection + mobile-phone GPS pin | measured area, observed boundaries, **GPS lat/long** |

Those feed a **5-parameter comparison matrix**:

| # | Parameter | Doc vs Portal | Portal vs TVR |
|---|---|---|---|
| 1 | Ownership | deed owner vs Khatauni holder | Khatauni holder vs TVR owner |
| 2 | Address | deed khasra/village vs portal record | portal record vs inspected site |
| 3 | Area / size | deed area vs portal record | portal record vs physical measurement |
| 4 | Boundary | deed 4 sides vs portal cadastral | portal cadastral vs TVR boundary |
| **5** | **Geo-coordinates** | deed GPS vs BhuNaksha centroid | **BhuNaksha centroid vs TVR mobile GPS** |

**This repo builds Parameter 5 only.** Parameters 1–4 (Land Records engine, Registration
engine, OCR pipeline) are separate modules and are not in this repository.

### Why Parameter 5 needs its own engine

Rural and peri-urban Indian sale deeds almost never contain latitude/longitude — they list a
Khasra/Gata number and boundary descriptions ("north: road, south: plot 107"). Government
cadastral maps *do* encode exact geometry, but in a local metric grid projection (UTM), not
the WGS84 lat/long that phones and satellite maps use.

So to verify Parameter 5 we must:

1. find the parcel on the state's cadastral portal,
2. pull its boundary geometry (or at least its centre point),
3. convert that from the local UTM grid to WGS84 lat/long,
4. fetch satellite imagery of the same ground,
5. draw the parcel onto the satellite image (so a human reviewer can eyeball it),
6. measure the distance from that official centre to the field engineer's GPS pin,
7. turn that distance into a GREEN / AMBER / RED verdict.

That is the 8-step pipeline. (Steps 5 and 7 of the original design — manual ground-control
points and homography warping — collapse to nothing when step 2/3 give us real coordinates,
so the code has one file for "steps 5–7".)

---

## 3. The 8-step pipeline

| Step | File | What it does | Output |
|---|---|---|---|
| **1** | `step1_capture.py` + `adapters/` | Open the state portal, drill District → Tehsil → Village, search the khasra number, screenshot the highlighted map, and grab whatever geometry the portal's API exposes | `bhunaksha.png`, `step1_metadata.json`, `parcel_raw` dict |
| **2** | `step2_geometry.py` | Normalise `parcel_raw` (WKT / GeoJSON / GML / OpenLayers coords / centroid+bbox) into one geometry in the portal's native projection | `parcel_native.geojson` |
| **3** | `step3_transform.py` | Reproject native UTM → WGS84 with `pyproj`; area-weighted centroid; area on a local equal-area projection; sanity gates (centroid inside the state, plausible area) | `parcel_wgs84.geojson`, `centroid_wgs84`, `area_sqm` |
| **4** | `step4_satellite.py` | From the centroid + a ground span, compute the covering grid of Web-Mercator XYZ tiles, download from ESRI World Imagery (no API key), stitch, record the exact image bbox + a world file | `satellite.png`, `satellite.pgw` |
| **5–7** | `step6_overlay.py` | Project each parcel vertex → pixel using the satellite bbox and draw it (translucent red fill + solid edge). Centroid-only parcels get a dashed bounding-box footprint. Yellow pin on the verified centroid | `overlay.png` |
| **8** | `step8_distance.py` | Haversine distance between the BhuNaksha centroid and the TVR GPS → verdict | `distance` block in the result JSON |

Orchestrated by `pipeline.py` (importable) and `cli.py` (`python -m gps_engine.cli run`).

### The Parameter-5 verdict (Step 8)

| Distance `d` | Verdict | Colour | Meaning |
|---|---|---|---|
| `d ≤ 50 m` | MATCH | 🟢 GREEN | Same plot. Field engineer stood on the genuine property. |
| `50 m < d ≤ 200 m` | REFER | 🟡 AMBER | Adjacent-plot / boundary ambiguity. Credit + technical review. |
| `d > 200 m` | NEGATIVE | 🔴 RED | Location mismatch. High fraud risk. |

Thresholds live in `config.py` (`DISTANCE_THRESHOLD_MATCH_METERS`,
`DISTANCE_THRESHOLD_REFER_METERS`).

If no TVR GPS is supplied, Step 8 returns `SKIPPED` and Parameter 5 is reported as "GPS derived
from BhuNaksha only".

---

## 4. The three government-portal technologies

Every state runs some flavour of NIC **BhuNaksha**, but there are three incompatible
generations of it in the field, and a state can be mid-migration between two. Each needs its
own adapter code.

### A. Modern NIC (`/rest/MapInfo/`) — Maharashtra

Plain HTML `<select>` dropdowns (`#level_0` … `#level_4`), OpenLayers map, a `/rest/` JSON API.
`POST /rest/MapInfo/getPlotInfo` returns **the full parcel polygon** as a WKT `MULTIPOLYGON`
in the state's UTM projection, plus area and bounding box. This is the only stack that gives a
real polygon.

### B. Classic NIC (`ScalarDatahandler` servlet) — Chhattisgarh, Rajasthan, (Bihar)

Also plain `<select>` dropdowns, but geometry comes from
`GET ScalarDatahandler?OP=5&state=<code>&levels=<v1,v2,…,>&plotno=<n>` which returns JSON with
`center_x` / `center_y` (the plot centroid), a bounding box, and an owner/area text blob —
**no polygon** (the parcel is drawn server-side as a raster). The centroid is enough for
Parameter 5.

### C. Angular NIC (`/bhunakshaserver/`) — Uttar Pradesh

Angular Material `mat-select` dropdowns, `/bhunakshaserver/MapInfo/*` REST back-end.
`getVVVVExtentGeoref` gives the village extent + an explicit CRS + the `gisCode`;
`getPlotByPlotNo` gives the **plot bounding box** (no polygon); `getPlotInfo` gives owner/area
text. Centroid = bbox centre.

### Why no polygon for B and C

We probed the WMS `GetFeatureInfo` endpoint of these portals with every `INFO_FORMAT`
(`application/json`, `geo+json`, `vnd.ogc.gml`, `text/xml`) — it always returns a PNG. The
parcel highlight is a server-rendered raster with no vector API. Getting a true polygon would
require OpenCV contour-tracing on that highlight PNG. That is deferred, because **Parameter 5
only needs the centroid**, which these portals do provide. The overlay draws the bounding box
as a dashed footprint instead of a polygon.

---

## 5. Key design decisions & trade-offs

### Zero mocks — every adapter hits the live portal
There is no simulation path anywhere in the code. This makes the offline test suite smaller
(it covers steps 2–8 and the naming layer with fixtures, not step 1), but it means a "SUCCESS"
is always a real portal result. Live behaviour is verified by `batch_test.py` and manual runs.

### One file per step
Chosen for readability over a more compressed design — a reviewer can open `step4_satellite.py`
and understand satellite fetching in isolation. `pipeline.py` is the only file that knows the
whole sequence.

### Adapter pattern, one class per state
`CadastralAdapter` (ABC) defines the contract; each state subclass implements `capture()`.
Shared portal helpers (`select_named`, `classic_nic_plot_info`, `bhunakshaserver_plot_info`,
retry/screenshot utilities) live on the base class.

**Known tech debt:** the four classic-NIC adapters (CG, RJ, BR) repeat ~120 lines of
near-identical navigation structure. A planned refactor to shared lifecycle hooks
(`connect` / `resolve_hierarchy` / `search_plot` / `capture_canvas`) was not done — the
duplication is tolerable for the current 4-state scope but should be addressed before adding
many more states.

### Names: a resolution layer, not hardcoded tables
`naming.py` reads the *live* dropdown `<option>` list and matches the caller's English (or
Devanagari) name against it — transliterate each option to Latin, strip diacritics, delete
schwas, phonetic-fold (ph→f, kh→k …), fuzzy-match with `rapidfuzz`. A small
`NAMING_OVERRIDES` table in `config.py` handles genuinely un-resolvable Persian-origin names.
This replaced ~200 lines of hand-maintained Hindi dictionaries.

### CRS: trust the payload, fall back to a table, gate on sanity
A wrong EPSG guess shifts the centroid by kilometres. The code always prefers the SRS name in
the portal's own response. Only if that's absent does it use `STATE_UTM_FALLBACK` (in
`step3_transform.py`), and then it flags low confidence. Every result is gated: the centroid
must fall inside the state's bounding box and imply a plausible parcel size, or the pipeline
returns `REFER`.

### Satellite: ESRI World Imagery, keyless
No API key, no billing, good rural resolution. `SOURCES` in `step4_satellite.py` is a dict —
swap in Google Static or Mapbox (with a key) if licensing ever requires it.

### Graceful degradation
Portal down → `FAILED` (scheduler retries next run). Plot genuinely not found, or geometry
missing, or a sanity gate trips → `REFER` with a specific `reason` (never a silent success).
Only a clean, sanity-checked result is `SUCCESS`. Everything is also recorded in `warnings[]`
for the audit trail, and the output JSON is validated against
`schema/gps_engine_result.schema.json` on every write.

### 24-hour result cache
`cache.py` keys on the parcel identity and stores SUCCESS/REFER results for 24 h (a transient
outage is never cached). Step 8 is always recomputed on a cache hit because it depends on the
caller's TVR GPS. `--no-cache` bypasses it.

### Every run is its own immutable audit folder
`pipeline.run()` writes to `output/runs/<timestamp>[_<application_id>]/`, never to a shared
path — so verifying 100 loan applications leaves 100 retained records instead of each run
overwriting the last. Inside the folder: `step1.json … step8.json` (each step's full
`to_dict()` — inputs, raw portal payload, status, warnings), the raw Step 1 metadata, the
portal screenshots, the GeoJSON, the satellite mosaic, the overlay, and the aggregate
`gps_engine_result.json` (which also carries `run_id`, `output_dir`, and per-step timings).
`output/latest` is a symlink to the newest run. This satisfies the BRD's audit-trail
requirement (§6.2/§6.4) without any step logic changing.

---

## 6. Tech stack

| Concern | Choice |
|---|---|
| Browser automation | Playwright (sync API), Chromium |
| Vector geometry | `shapely` |
| Projection | `pyproj` |
| Name matching | `rapidfuzz` + `indic-transliteration` |
| Satellite tiles | ESRI World Imagery XYZ, stitched with `pillow` |
| Raster sidecar | hand-written world file (`.pgw`) — no GDAL dependency |
| Schema | `jsonschema` |
| Tests | `pytest` (offline; live tests are manual) |
