# GPS Engine — Status & Test Results

Last updated: **2026-09-08**

For background (what the engine is, how it works) see [CONTEXT.md](CONTEXT.md).
For per-portal technical notes see [PORTAL_NOTES.md](PORTAL_NOTES.md).

---

## 1. At a glance

| State | Portal stack | Full 8-step pipeline | Geometry returned | Last live check |
|---|---|---|---|---|
| **Maharashtra** | Modern NIC (`/rest/`) | ✅ working | full parcel **polygon** + area | 2026-09-08 ✅ |
| **Uttar Pradesh** | Angular NIC (`/bhunakshaserver/`) | ✅ working | centroid + bounding box | 2026-09-08 ✅ |
| **Rajasthan** | Classic NIC (`ScalarDatahandler`) | ✅ working | centroid + bounding box | 2026-09-08 ✅ |
| **Chhattisgarh** | Classic NIC (`ScalarDatahandler`) | ✅ working | centroid + bounding box | 2026-09-08 ✅ |
| **Bihar** | migrated, now broken | ❌ blocked | — | 2026-09-08 ❌ |
| **Gujarat** | AnyROR (own stack) | ❌ portal offline | — | 2026-09-08 ❌ |
| **Madhya Pradesh** | — | ❌ portal unreachable | — | 2026-09-08 ❌ |
| **Uttarakhand** | — | ❌ portal unreachable | — | 2026-09-08 ❌ |
| **Delhi** | — | ❌ portal unreachable | — | 2026-09-08 ❌ |

**Offline test suite: 39 tests, all passing** (`pytest gps_engine/tests`), no network required.

---

## 2. Live test results — 2026-09-08

All four working states were run end-to-end with `--no-cache` (forced live portal hit).
Command form:

```bash
python3 -m gps_engine.cli run --state <S> --district <D> --tehsil <T> \
  --village <V> --khasra <K> [--ri-circle <C>] [--tvr-lat <lat> --tvr-lon <lon>] --no-cache
```

### Maharashtra — Akola / Akot / Akolkhed / survey 3
```
Status   : SUCCESS
Centroid : 21.15220061, 77.08880381
Area     : 9269.96 m²  (portal-reported: 9269.96)   ← exact match, real polygon
CRS      : EPSG:32643 (high confidence — from payload)
Satellite: z18, 9 tiles stitched
Overlay  : vector_projection (true polygon drawn)
Param 5  : 0.4 m  →  MATCH (GREEN)   [test TVR = 21.1522, 77.0888]
Timing   : 24.2 s
```

### Uttar Pradesh — Gorakhpur / Khajani / Nadini / gata 106  (BRD golden record)
```
Status   : SUCCESS
Centroid : 26.61926619, 83.1994305
Area     : 1210.0 m²  (portal-reported)
CRS      : EPSG:32644 (high confidence)
Satellite: z18, 16 tiles
Overlay  : dashed bounding box + centroid pin (portal exposes no polygon)
Param 5  : 15.7 m  →  MATCH (GREEN)   [test TVR = BRD 26.619406, 83.199410]
Timing   : 21.2 s
```
BRD golden record target is 26.619406 N, 83.199410 E — the derived centroid is **15.7 m off**,
well inside the 50 m MATCH band.

### Rajasthan — Ajmer / Ajmer / Ajaysar / khasra 1  (`--ri-circle "Ajmer Tritiya"`)
```
Status   : SUCCESS
Centroid : 26.43227937, 74.57138773
Area     : 4400.0 m²  (portal-reported)
CRS      : EPSG:32643 (high confidence)
Satellite: z18, 9 tiles
Overlay  : dashed bounding box + centroid pin
Param 5  : no field GPS supplied → SKIPPED
Timing   : 28.2 s
```

### Chhattisgarh — Kabirdham / Kukdur / Adhachara / khasra 1  (`--ri-circle "Kukdur"`)
```
Status   : SUCCESS
Centroid : 22.45863331, 81.38597063
Area     : 1290.0 m²  (portal-reported)
CRS      : EPSG:32644 (high confidence)
Satellite: z18, 9 tiles
Overlay  : dashed bounding box + centroid pin
Param 5  : no field GPS supplied → SKIPPED
Timing   : 23.3 s
```

---

## 3. What is NOT working, and why

### Bihar — portal migrated and is now broken for the public
- The map app loads and the 7-level hierarchy (District → Sub-Division → Circle → Mauza →
  Survey Type → Map Instance → Sheet) navigates fine.
- **But** the plot-search call `GET /ScalarDatahandler?OP=5&…` now returns **HTTP 401
  Unauthorized**. The portal ships the old classic front-end while the back-end has moved to
  the modern NIC `/rest/` stack (`/rest/MapInfo/getVVVVExtentGeoref`, `/rest/Layers/getLayers`
  observed on load). The plot-info panel spins forever — this is a portal-side bug, not ours.
- **Our code:** `adapters/br_bhunaksha.py` targets the classic `ScalarDatahandler` path, so it
  cannot return geometry. Navigation is intact. The adapter is marked EXPERIMENTAL in its
  docstring. It needs a rewrite to `/rest/MapInfo/getPlotInfo` (like Maharashtra) — but that
  can't be built or tested until Bihar's own plot lookup works again.

### Gujarat — portal offline
`anyror.gujarat.gov.in` shows *"This Application is currently offline — website maintenance"*.
The maintenance window has been extended repeatedly (last seen: until 2026-09-07 23:30, still
offline on 2026-09-08). AnyROR is also a **different technology** from BhuNaksha (integrated
map, Gujarati, survey-number based) and will need its own adapter, not a BhuNaksha one.

### Madhya Pradesh / Uttarakhand / Delhi — portals unreachable
`mpbhulekh.gov.in`, `bhunaksha.uk.gov.in`, `dlrc.delhigovt.nic.in` do not load — not from a
script and not from a real browser in this environment (connection timeout / error page).
Either the portals are down or they geo-restrict traffic to India. No adapter can be built or
verified against a portal that won't respond. Config entries exist for these states but no
adapter module is registered — requesting them raises a clear `NotImplementedError`.

---

## 4. Known limitations of the working states

1. **No polygon for UP / RJ / CG.** Their portals render the parcel as a raster with no vector
   API (confirmed by exhaustively probing WMS `GetFeatureInfo`). Parameter 5 only needs the
   centroid, which they give; the overlay draws the plot bounding box as a dashed footprint.
   A true polygon would need OpenCV contour-tracing on the highlight PNG — deferred.
2. **RJ / CG need `--ri-circle`.** Without it the adapter picks the first RI-circle / halka in
   the dropdown, which is often wrong and yields `REFER: plot_not_found`. Pass the real value.
3. **Village-name resolution is the weak point.** The naming layer is good but not perfect for
   villages with heavy spelling drift; low-confidence matches are flagged in `warnings[]`.
4. **No real-world hit-rate test yet.** The parcels above are ones we found by hand. A proper
   accuracy measurement needs a batch of actual plot references from Sitaara's loan records.
5. **Adapter code duplication** (CG/RJ/BR) — see CONTEXT.md §5 "Known tech debt".

---

## 5. Roadmap

| Priority | Item |
|---|---|
| High | Get a batch of real plot references from Sitaara → run `batch_test.py` → measure hit rate |
| High | Land Records engine (Parameters 1–3) and Registration engine — separate modules |
| Medium | Bihar: rewrite `br_bhunaksha.py` to the `/rest/` stack (once the portal is fixed) |
| Medium | Refactor CG/RJ/BR adapters onto shared lifecycle hooks |
| Medium | Gujarat AnyROR adapter (once the portal is back) |
| Low | MP / Uttarakhand / Delhi adapters (blocked on portal reachability) |
| Low | OpenCV polygon extraction for UP/RJ/CG from the WMS highlight PNG |
| Low | CAPTCHA handling hook (MP, and RJ's "Nakal" report — not needed for Parameter 5 today) |

---

## 6. How to reproduce these results

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium

# offline tests (no network)
python3 -m pytest gps_engine/tests -q

# one live run
python3 -m gps_engine.cli run --state Maharashtra --district Akola --tehsil Akot \
  --village Akolkhed --khasra 3 --tvr-lat 21.1522 --tvr-lon 77.0888 --no-cache

# live smoke test across the 4 working states
python3 -m gps_engine.batch_test --no-cache
```

Outputs land in `gps_engine/output/` (git-ignored): `gps_engine_result.json`, `satellite.png`,
`overlay.png`, `bhunaksha.png`, `parcel_wgs84.geojson`.
