"""
GPS Engine — Command Line Interface
==================================

    # Full pipeline (steps 1-8):
    python3 -m gps_engine.cli run --state "Maharashtra" --district "Akola" \
        --tehsil "Akot" --village "Akolkhed" --khasra "3" \
        --tvr-lat 21.1522 --tvr-lon 77.0888

    # Step 1 only (locate + screenshot):
    python3 -m gps_engine.cli step1 --state "Uttar Pradesh" --district "Gorakhpur" \
        --tehsil "Khajani" --village "Nadini" --khasra "106" --headed
"""

import argparse
import json
import logging
import sys
from gps_engine.step1_capture import Step1Capture
from gps_engine import pipeline


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(
        description="GPS Engine CLI — Cadastral Map & Satellite Superimposition Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Pipeline step to run")

    # ── Step 1 ──────────────────────────────────────────────────────────────
    step1 = subparsers.add_parser("step1", help="Step 1: Locate plot on State Cadastral Portal & capture map")
    step1.add_argument("--state",    required=True, help="State name (e.g. 'Uttar Pradesh', 'Rajasthan')")
    step1.add_argument("--district", required=True, help="District name")
    step1.add_argument("--tehsil",   required=True, help="Tehsil / Taluka / Circle name")
    step1.add_argument("--village",  required=True, help="Village / Mauza name")
    step1.add_argument("--khasra",   required=True, help="Khasra / Survey / Gat / Killa No.")
    step1.add_argument("--headed",   action="store_true",     help="Show browser window during capture")

    # ── run: full pipeline Step 1 -> 8 ─────────────────────────────────────
    run_p = subparsers.add_parser("run", help="Full GPS engine: capture + geometry + WGS84 centroid + overlay + verdict")
    run_p.add_argument("--state",    required=True)
    run_p.add_argument("--district", required=True)
    run_p.add_argument("--tehsil",   required=True)
    run_p.add_argument("--village",  required=True)
    run_p.add_argument("--khasra",   required=True)
    run_p.add_argument("--application-id", default=None)
    run_p.add_argument("--category", default="Rural", help="Rural | Urban (MH)")
    run_p.add_argument("--ri-circle", default=None, help="RI Circle (RJ / CG) — else first option is used")
    run_p.add_argument("--halka", default=None, help="Halka (RJ) — else first option is used")
    run_p.add_argument("--subdivision", default=None, help="Sub-Division (Bihar)")
    run_p.add_argument("--survey-type", default=None, help="Survey type RS/CS/SS (Bihar)")
    run_p.add_argument("--tvr-lat", type=float, default=None, help="Field engineer GPS latitude (Step 8)")
    run_p.add_argument("--tvr-lon", type=float, default=None, help="Field engineer GPS longitude (Step 8)")
    run_p.add_argument("--span-m", type=float, default=300.0, help="Satellite frame size in metres (Step 4)")
    run_p.add_argument("--no-superimpose", action="store_true", help="Skip Steps 4-7 (satellite + overlay)")
    run_p.add_argument("--no-cache", action="store_true", help="Ignore the 24h result cache; always hit the portal")
    run_p.add_argument("--headed",   action="store_true")

    args = parser.parse_args()

    if args.command == "step1":
        import time
        from gps_engine.config import OUTPUT_DIR
        run_dir = OUTPUT_DIR / "runs" / time.strftime("%Y%m%d-%H%M%S")
        engine = Step1Capture(output_dir=run_dir)
        result = engine.capture_plot(
            state=args.state,
            district=args.district,
            tehsil=args.tehsil,
            village=args.village,
            khasra_no=args.khasra,
            headless=not args.headed
        )
        print("\n" + "=" * 70)
        print(f" GPS ENGINE — STEP 1 RESULT ({args.state.upper()})")
        print("=" * 70)
        print(f"  Status       : {result.get('status')}")
        print(f"  Source       : {result.get('source')}")
        print(f"  Portal URL   : {result.get('portal_url')}")
        print(f"  Target Plot  : Khasra {result.get('khasra_no')} ({result.get('village')}, {result.get('tehsil')}, {result.get('district')})")
        print(f"  Image Path   : {result.get('image_path')}")
        print(f"  Metadata     : {result.get('metadata_path')}")
        if result.get("debug_screenshots"):
            print("\n  Debug Screenshots:")
            for label, path in result["debug_screenshots"].items():
                print(f"    [{label}] {path}")
        if result.get("error"):
            print(f"\n  ERROR: {result.get('error')}")
        print("=" * 70 + "\n")

    elif args.command == "run":
        extra = {"category": args.category}
        for k, v in [("ri_circle", args.ri_circle), ("halkas", args.halka),
                     ("subdivision", args.subdivision), ("survey_type", args.survey_type)]:
            if v:
                extra[k] = v
        result = pipeline.run(
            state=args.state, district=args.district, tehsil=args.tehsil,
            village=args.village, khasra_no=args.khasra,
            application_id=args.application_id, headless=not args.headed,
            extra_params=extra,
            tvr_lat=args.tvr_lat, tvr_lon=args.tvr_lon,
            span_m=args.span_m, superimpose=not args.no_superimpose,
            use_cache=not args.no_cache,
        )
        print("\n" + "=" * 70)
        print(f" GPS ENGINE — RUN RESULT ({args.state.upper()})")
        print("=" * 70)
        print(f"  Status   : {result.get('status')}  {result.get('reason') or ''}")
        parcel = result.get("parcel") or {}
        if parcel.get("centroid_wgs84"):
            c = parcel["centroid_wgs84"]
            print(f"  Centroid : {c['lat']}, {c['lon']}")
            print(f"  Area     : {parcel.get('area_sqm')} m2 (portal: {parcel.get('area_sqm_portal_reported')})")
            print(f"  CRS      : {parcel.get('source_crs')} ({parcel.get('source_crs_confidence')})")
        print(f"  Cad. map : {(result.get('image') or {}).get('path')}")
        sat = result.get("satellite") or {}
        if sat.get("status") == "SUCCESS":
            print(f"  Satellite: {sat.get('path')}  (z{sat.get('zoom')}, {sat.get('tiles_downloaded')} tiles)")
        ov = result.get("overlay") or {}
        if ov.get("status") == "SUCCESS":
            print(f"  Overlay  : {ov.get('path')}  ({ov.get('alignment_method')})")
        dist = result.get("distance") or {}
        if dist.get("status") == "SUCCESS":
            print(f"  Param 5  : {dist['distance_m']} m  ->  {dist['verdict']} ({dist['color']})")
        elif dist.get("status") == "SKIPPED":
            print(f"  Param 5  : no field GPS supplied — pass --tvr-lat/--tvr-lon for the verdict")
        st = result.get("step_timings_seconds") or {}
        if st:
            print(f"  Timing   : {result.get('timing_seconds')}s total  ("
                  + ", ".join(f"{k} {v}s" for k, v in st.items()) + ")")
        else:
            print(f"  Timing   : {result.get('timing_seconds')}s")
        for w in result.get("warnings", []):
            print(f"  ! {w}")
        print(f"  Run dir  : {result.get('output_dir')}")
        print(f"  Result   : {result.get('output_dir')}/gps_engine_result.json")
        print("=" * 70 + "\n")

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
