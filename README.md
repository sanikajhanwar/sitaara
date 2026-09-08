# GPS Engine — BhuNaksha + Satellite Superimposition Pipeline

Automates **Parameter 5 (Geo-Coordinates)** of Sitaara Housing Finance Ltd's property
verification: given a plot's address, it locates the parcel on the state government cadastral
portal, derives its GPS location, overlays it on satellite imagery, and checks it against the
field engineer's GPS pin — producing a GREEN / AMBER / RED verdict.

> Built for Sitaara Housing Finance Ltd. This repository is one module of a larger property-
> verification system; the Land-Records and Registration engines (Parameters 1–4) are separate.

---

## What it does

```
address ──▶ 1. find plot on state BhuNaksha portal      (Playwright)
            2. extract parcel geometry from portal API
            3. reproject local UTM → WGS84, centroid, area   (pyproj / shapely)
            4. download + stitch satellite tiles             (ESRI World Imagery)
          5-7. draw the parcel onto the satellite image      (pillow)
            8. Haversine distance vs field GPS → verdict
                    │
                    ▼
        output/gps_engine_result.json  +  satellite.png  +  overlay.png
```

Full background: **[docs/CONTEXT.md](docs/CONTEXT.md)**.
Build status & live test results: **[docs/STATUS.md](docs/STATUS.md)**.

---

## Status

| State | Pipeline | Geometry |
|---|---|---|
| Maharashtra | ✅ working | full parcel polygon |
| Uttar Pradesh | ✅ working | centroid + bbox |
| Rajasthan | ✅ working | centroid + bbox |
| Chhattisgarh | ✅ working | centroid + bbox |
| Bihar | ❌ portal migrated & broken | — |
| Gujarat / MP / Uttarakhand / Delhi | ❌ portal offline / unreachable | — |

39 offline tests, all passing. Details and reasons in [docs/STATUS.md](docs/STATUS.md).

---

## Setup

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
```

Python 3.10+.

## Run

```bash
python3 -m gps_engine.cli run \
  --state "Maharashtra" --district "Akola" --tehsil "Akot" \
  --village "Akolkhed" --khasra "3" \
  --tvr-lat 21.1522 --tvr-lon 77.0888
```

Place names may be English or Devanagari. For Rajasthan and Chhattisgarh also pass
`--ri-circle "<circle>"`.

| Flag | Purpose |
|---|---|
| `--tvr-lat` / `--tvr-lon` | field engineer's GPS — required for the Parameter-5 verdict |
| `--ri-circle` / `--halka` / `--subdivision` / `--survey-type` | state-specific hierarchy levels |
| `--span-m` | satellite frame size in metres (default 300) |
| `--no-superimpose` | skip steps 4–7 (geometry + centroid only) |
| `--no-cache` | ignore the 24 h result cache |
| `--headed` | show the browser window |

Outputs go to `gps_engine/output/` (git-ignored).

## Test

```bash
python3 -m pytest gps_engine/tests -q          # 39 offline tests, no network
python3 -m gps_engine.batch_test --no-cache    # live smoke test, 4 working states
```

---

## Repository layout

```
gps_engine/
  cli.py                  CLI entry point  (python -m gps_engine.cli)
  pipeline.py             orchestrates steps 1 → 8
  step1_capture.py        step 1 — router to the per-state adapter
  step2_geometry.py       step 2 — normalise portal geometry
  step3_transform.py      step 3 — UTM → WGS84, centroid, area, sanity gates
  step4_satellite.py      step 4 — satellite tile fetch + stitch
  step6_overlay.py        steps 5–7 — draw parcel on satellite
  step8_distance.py       step 8 — Haversine → verdict
  naming.py               English ↔ Devanagari place-name resolution
  cache.py                24 h result cache
  config.py               portal registry + constants
  adapters/
    base.py               CadastralAdapter ABC + shared portal helpers
    mh_bhunaksha.py        Maharashtra   (modern NIC)
    up_bhunaksha.py        Uttar Pradesh (Angular NIC)
    rj_bhunaksha.py        Rajasthan     (classic NIC)
    cg_bhunaksha.py        Chhattisgarh  (classic NIC)
    br_bhunaksha.py        Bihar         (experimental — see docs/STATUS.md)
  schema/                 JSON Schema for the result payload
  tests/                  offline test suite + fixtures

docs/
  CONTEXT.md             full background: problem, model, 8 steps, portal tech, design
  STATUS.md              per-state status + live test results + roadmap
  PORTAL_NOTES.md        per-portal technical reference (selectors, endpoints, CRS, quirks)
```
