"""
GPS Engine — STEP 1: Cadastral Map Capture (State Router)
==========================================================
Routes the request to the appropriate state CadastralAdapter based on CADASTRAL_PORTALS registry.
Production only — strictly live automation, zero mock/simulation code paths.

Output Contract:
  output/bhunaksha.png        : Cadastral map image with target plot highlighted.
  output/step1_metadata.json  : Provenance, coordinates, status, and portal metadata.
"""

import importlib
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gps_engine.config import CADASTRAL_PORTALS, OUTPUT_DIR
from gps_engine.adapters.base import CadastralAdapter

logger = logging.getLogger("GPS.Step1Router")

# State -> (adapter module, adapter class). Only states with a built adapter are listed.
# Gujarat / Madhya Pradesh / Uttarakhand / Delhi / Haryana are in config.CADASTRAL_PORTALS
# but their portals are offline / unreachable, so no adapter exists yet — see docs/STATUS.md.
ADAPTER_MAP = {
    "mh_bhunaksha": ("gps_engine.adapters.mh_bhunaksha", "MHBhuNakshaAdapter"),
    "up_bhunaksha": ("gps_engine.adapters.up_bhunaksha", "UPBhuNakshaAdapter"),
    "rj_bhunaksha": ("gps_engine.adapters.rj_bhunaksha", "RJBhuNakshaAdapter"),
    "cg_bhunaksha": ("gps_engine.adapters.cg_bhunaksha", "CGBhuNakshaAdapter"),
    "br_bhunaksha": ("gps_engine.adapters.br_bhunaksha", "BRBhuNakshaAdapter"),
}

# States whose adapter runs the full pipeline end-to-end today (Bihar navigates but its
# portal's geometry endpoint is currently broken — see docs/STATUS.md).
SUPPORTED_STATES = ["Maharashtra", "Uttar Pradesh", "Rajasthan", "Chhattisgarh", "Bihar"]


class Step1Capture:
    """
    Main Step 1 Entry Point: Routes state requests to dedicated portal adapters.
    """

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def get_adapter(self, state: str) -> CadastralAdapter:
        """
        Instantiate the registered adapter for the specified state.
        """
        portal_config = CADASTRAL_PORTALS.get(state)
        if not portal_config:
            raise ValueError(
                f"'{state}' is not a recognised state. Supported: {', '.join(SUPPORTED_STATES)}."
            )

        adapter_name = portal_config.get("adapter")
        if adapter_name not in ADAPTER_MAP:
            raise NotImplementedError(
                f"No adapter for {state} yet — its portal is offline or unreachable "
                f"(see docs/STATUS.md). States with a working adapter: "
                f"{', '.join(SUPPORTED_STATES)}."
            )

        module_path, class_name = ADAPTER_MAP[adapter_name]
        try:
            module = importlib.import_module(module_path)
            adapter_class = getattr(module, class_name)
            return adapter_class(state_name=state, portal_config=portal_config, output_dir=self.output_dir)
        except (ImportError, AttributeError) as e:
            raise NotImplementedError(
                f"Adapter for {state} ({module_path}) failed to load: {e}"
            )

    def capture_plot(
        self,
        state: str,
        district: str,
        tehsil: str,
        village: str,
        khasra_no: str,
        headless: bool = True,
        extra_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute Step 1 capture against the live state portal.
        """
        logger.info("=" * 70)
        logger.info(f"GPS ENGINE — STEP 1: {state.upper()} CADASTRAL CAPTURE")
        logger.info("=" * 70)
        logger.info(f"  State      : {state}")
        logger.info(f"  District   : {district}")
        logger.info(f"  Tehsil     : {tehsil}")
        logger.info(f"  Village    : {village}")
        logger.info(f"  Khasra No. : {khasra_no}")
        logger.info(f"  Headless   : {headless}")

        image_path = self.output_dir / "bhunaksha.png"
        metadata_path = self.output_dir / "step1_metadata.json"

        adapter = self.get_adapter(state)
        result = adapter.capture(
            district=district,
            tehsil=tehsil,
            village=village,
            khasra_no=khasra_no,
            image_path=image_path,
            headless=headless,
            extra_params=extra_params,
        )

        # Standardize metadata
        result.update({
            "step": 1,
            "step_name": "Cadastral Map Plot Identification & Capture",
            "metadata_path": str(metadata_path),
        })

        adapter.save_metadata(result, metadata_path)
        return result
