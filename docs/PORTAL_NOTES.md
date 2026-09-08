# Portal Technical Notes

Per-state reference for how each government cadastral portal works: URLs, DOM selectors, the
API call that returns geometry, the coordinate system, and quirks. Compiled from live probing
(most recent: 2026-09-08).

See [CONTEXT.md](CONTEXT.md) §4 for the high-level "three portal generations" overview and
[STATUS.md](STATUS.md) for what currently works.

---

## Portal generation summary

| Generation | Geometry endpoint | Returns | States on it |
|---|---|---|---|
| **Modern NIC** | `POST /rest/MapInfo/getPlotInfo` | full polygon (WKT) + area + bbox | Maharashtra; Bihar (back-end only) |
| **Classic NIC** | `GET ScalarDatahandler?OP=5` | centroid + bbox + owner text (no polygon) | Chhattisgarh, Rajasthan; Bihar (front-end) |
| **Angular NIC** | `POST /bhunakshaserver/MapInfo/getPlotByPlotNo` | plot bbox + owner text (no polygon) | Uttar Pradesh |
| **AnyROR** | integrated ArcGIS/emap query | (not yet characterised) | Gujarat |

---

## Maharashtra — Modern NIC  ✅

- **Portal:** `https://mahabhunakasha.mahabhumi.gov.in/27/index.html`
- **State code:** `27`   **Native CRS:** `EPSG:32643` (UTM 43N; east Maharashtra may be 44N —
  Step 3's centroid-in-state gate re-checks)
- **Adapter:** `adapters/mh_bhunaksha.py`

### Navigation (plain `<select>`)
| Level | Selector | Example option |
|---|---|---|
| State | `#level_0` | pre-selected |
| Category | `#level_1` | `Rural [R]` / `Urban [U]` |
| District | `#level_2` | `05 अकोला` |
| Taluka | `#level_3` | `02 आकोट` |
| Village | `#level_4` | `270500020047510000 अकोलखेड` |
| Plot | `#plotNo` + `#plotNoButton`, or `#surveyNumber` dropdown | |

### Geometry
```
POST /rest/MapInfo/getPlotInfo
body: state=27 & giscode=RVM<dist2><taluka2><village18> & plotno=<n> & srs=3857
```
Response `the_geom` = WKT `MULTIPOLYGON` in UTM; also `area` (m²), `xmin/ymin/xmax/ymax`.
`giscode` = `"RVM"` + district(2) + taluka(2) + village(18); also readable from the WMS layer
params. The parcel is WMS-rendered (not an OpenLayers vector feature), so `the_geom` is the
only client-side route to the polygon.

---

## Chhattisgarh — Classic NIC  ✅

- **Portal:** `https://bhunaksha.cg.nic.in/`
- **`ScalarDatahandler` state code:** `22`   **Native CRS:** `EPSG:32644` (UTM 44N)
- **Adapter:** `adapters/cg_bhunaksha.py`

### Navigation
`#level_1` District → `#level_2` Tehsil → `#level_3` RI Circle → `#level_4` Village →
`#plotNo` + Enter. **Pass `--ri-circle`** or the adapter guesses the first option.

### Geometry
```
GET ScalarDatahandler?OP=5&state=22&levels=<v1,v2,v3,v4,>&plotno=<n>
```
JSON: `center_x`, `center_y` (plot centroid, UTM), `xmin/ymin/xmax/ymax`, `has_data`,
`gisCode`, `info` (owner/area text — area parsed from `"… हेक्टेयर"`). **No polygon.**

### Quirk
The portal blanks `#level_4` once `initVillMap()` finishes rendering. The adapter snapshots the
hierarchy values *immediately* after village selection, retries for a few seconds, and falls
back to reconstructing them from the `gisCode` seen in the `getVVVVExtentGeoref` network
response.

---

## Rajasthan — Classic NIC  ✅

- **Portal:** `https://bhunaksha.rajasthan.gov.in/Viewmap/`  (`bhunaksha.raj.nic.in` is dead)
- **`ScalarDatahandler` state code:** `08`   **Native CRS:** `EPSG:32643` (UTM 43N)
- **Adapter:** `adapters/rj_bhunaksha.py`

### Navigation
Six levels: `#level_1` District → `#level_2` Tehsil → `#level_3` RI Circle → `#level_4` Halka
→ `#level_5` Village → `#level_6` Sheet → `#plotNo` + Enter. **Pass `--ri-circle`** (and
`--halka` if known); otherwise the first option is used.

### Geometry
Same `ScalarDatahandler?OP=5` shape as Chhattisgarh, `state=08`, six `levels` values.
The "Nakal" report button needs a CAPTCHA — but the `OP=5` geometry call does not.

---

## Uttar Pradesh — Angular NIC  ✅

- **Portal:** `https://upbhunaksha.gov.in`
- **Native CRS:** `EPSG:32644` (confirmed — the API response carries the CRS explicitly)
- **Adapter:** `adapters/up_bhunaksha.py`

### Navigation (Angular Material `mat-select`)
| Level | Selector |
|---|---|
| District | `mat-select#mat-select-0` |
| Tehsil | `mat-select#mat-select-2` |
| Village | `mat-select#mat-select-4` |
| Khasra | `input#plotNo` + Enter |

The adapter clicks the `mat-select`, reads the live `mat-option` list, resolves the name, clicks
the winner, and verifies the trigger text changed (an earlier version mis-selected ~1 run in 4).

### Geometry (all under `<origin>/bhunakshaserver/`)
| Call | Body | Returns |
|---|---|---|
| `POST /MapInfo/getVVVVExtentGeoref` | `gisLevels=<a,b,c>` | `{xmin,ymin,xmax,ymax, crs, gisCode}` — CRS is explicit; adapter grabs `gisCode` from a `page.on("response")` listener |
| `POST /MapInfo/getPlotByPlotNo` | `giscode=<g>&plotno=<n>` | `{minx,miny,maxx,maxy, id}` — plot bbox, UTM 44N. No polygon. |
| `POST /MapInfo/getPlotInfo` | JSON `{gisCode, plotNo}` | plain-text owner/khata/area blob |

Centroid = bbox centre.

---

## Bihar — migrated, currently broken  ❌

- **Portal:** `https://bhunaksha.bihar.gov.in/10/index.jsp` → *View Map* → `/10/indexmain.jsp`
- **Adapter:** `adapters/br_bhunaksha.py` (EXPERIMENTAL — targets the dead classic path)

### Navigation (still works)
Click *View Map*, then seven `<select>` levels: `#level_1` District → `#level_2` Sub-Division →
`#level_3` Circle → `#level_4` Mauza → `#level_5` Survey Type (RS/CS/SS) → `#level_6` Map
Instance → `#level_7` Sheet → plot search box + Enter.

### Why it's broken (2026-09-08)
The shipped front-end calls `GET /ScalarDatahandler?OP=5&state=10&levels=<…>,&plotno=<n>` and
the server responds **HTTP 401**. Meanwhile the page loads `/rest/user/removeSession/`,
`/rest/Levels/count`, `/rest/MapInfo/getVVVVExtentGeoref`, `/rest/Layers/getLayers` — i.e. the
back-end is now the **modern NIC `/rest/` stack**. The portal is half-migrated and its own plot
lookup hangs. `br_bhunaksha.py` needs rewriting to `/rest/MapInfo/getPlotInfo` (see the
Maharashtra adapter), which can't be verified until the portal is usable.

---

## Gujarat — AnyROR, offline  ❌

- **Portal:** `https://anyror.gujarat.gov.in` — returns *"Application currently offline —
  website maintenance"* (extended repeatedly through 2026-09-08).
- **Not BhuNaksha.** AnyROR is an integrated map (`AnyrorMap.aspx`), Gujarati UI, survey-number
  based, map opens in a popup/iframe, heavier anti-bot. Needs its own adapter design once the
  portal is reachable.

---

## Madhya Pradesh / Uttarakhand / Delhi — unreachable  ❌

| State | Host | Observed |
|---|---|---|
| Madhya Pradesh | `mpbhulekh.gov.in` | connection timeout / renderer freeze, script and browser alike |
| Uttarakhand | `bhunaksha.uk.gov.in` | error page / not serving |
| Delhi | `dlrc.delhigovt.nic.in` | connection fails |

Likely down or India-only geo-restricted. Config entries exist; no adapter is registered.
Delhi additionally only covers Lal Dora / revenue villages — urban plots there should skip
straight to satellite.

---

## Reachability by tool

- **Bash / `curl` / Playwright-in-Bash:** resolves `upbhunaksha.gov.in`, `bhunaksha.cg.nic.in`,
  `bhunaksha.rajasthan.gov.in`, `bhunaksha.bihar.gov.in`, `hsac.in`, and
  `mahabhunakasha.mahabhumi.gov.in`. Fails DNS/connection for MP, UK, DL.
- **Browser (Claude-in-Chrome):** same set reachable; MP/UK/DL still fail; Gujarat reachable
  but shows its offline page.
