"""
GPS Engine — Cadastral Portal Adapters
======================================
Each adapter handles one state's cadastral map portal end-to-end:
  - Playwright browser automation
  - Portal-specific navigation sequence
  - Screenshot capture
  - GPS coordinate extraction where available from the portal

Import pattern used by step1_capture.py:
    from gps_engine.adapters.up_bhunaksha import UPBhuNakshaAdapter
"""
