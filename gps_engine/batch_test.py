"""
GPS Engine — Live batch smoke test
==================================
Runs the pipeline against a list of real parcels across the working states and
prints a pass/fail table. Touches live government portals — run manually, not in CI.

    python3 -m gps_engine.batch_test
    python3 -m gps_engine.batch_test --state Maharashtra
"""

from __future__ import annotations

import argparse
import logging
import time

from gps_engine import pipeline

logging.getLogger("GPS").setLevel(logging.WARNING)

# (state, district, tehsil, village, khasra, extra)
PARCELS = [
    ("Maharashtra",   "Akola",     "Akot",    "Akolkhed",  "3",   {"category": "Rural"}),
    ("Maharashtra",   "Akola",     "Akot",    "Akolkhed",  "50",  {"category": "Rural"}),
    ("Maharashtra",   "Akola",     "Akot",    "Adgaon Kh", "1",   {"category": "Rural"}),
    ("Uttar Pradesh", "Gorakhpur", "Khajani", "Nadini",    "106", {}),
    ("Uttar Pradesh", "Gorakhpur", "Khajani", "Nadini",    "1",   {}),
    ("Uttar Pradesh", "Agra",      "Agra",    "Akbarpur",  "10",  {}),
    ("Rajasthan",     "Ajmer",     "Ajmer",   "Ajaysar",   "1",   {"ri_circle": "Ajmer Tritiya"}),
    ("Rajasthan",     "Ajmer",     "Ajmer",   "Ajaysar",   "25",  {"ri_circle": "Ajmer Tritiya"}),
    ("Chhattisgarh",  "Kabirdham", "Kukdur",  "Adhachara", "1",   {"ri_circle": "Kukdur"}),
    ("Chhattisgarh",  "Kabirdham", "Kukdur",  "Adhachara", "20",  {"ri_circle": "Kukdur"}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=None, help="filter to one state")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    rows = []
    for state, dist, teh, vil, kh, extra in PARCELS:
        if args.state and state != args.state:
            continue
        t0 = time.time()
        try:
            r = pipeline.run(state, dist, teh, vil, kh, extra_params=extra,
                             superimpose=False, use_cache=not args.no_cache)
            status = r.get("status")
            reason = r.get("reason") or ""
            c = (r.get("parcel") or {}).get("centroid_wgs84") or {}
            centroid = f"{c.get('lat')},{c.get('lon')}" if c else "-"
        except Exception as e:
            status, reason, centroid = "EXC", str(e)[:50], "-"
        rows.append((state, f"{vil}/{kh}", status, centroid, reason, round(time.time() - t0, 1)))
        print(f"  {state:14} {vil+'/'+kh:16} {status:8} {centroid:24} {reason[:30]:30} {rows[-1][-1]}s")

    ok = sum(1 for r in rows if r[2] == "SUCCESS")
    refer = sum(1 for r in rows if r[2] == "REFER")
    print(f"\n  {ok} SUCCESS / {refer} REFER / {len(rows) - ok - refer} FAILED-or-EXC  (of {len(rows)})")


if __name__ == "__main__":
    main()
