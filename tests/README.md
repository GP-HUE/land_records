# Test batteries

Run against the LIVE server (`python3 -m uvicorn landrec.main:app --host 0.0.0.0 --port 8000`):

| File | What it covers |
|---|---|
| full_system_test.py | 56-check system battery: auth, RBAC, upload/OCR, workflow, audit chain, rate limits |
| regression_test.py | 18 unit-level checks: extraction, Bidi, learned corrections, tamper detection |
| test_features_v36.py | 45 checks: Certified PDF + QR, history, SLA/CSV, mutation module |
| test_features_v37.py | 38 checks: search/RBAC, AI Approval Center, AI Task Inbox, briefing |
| test_ai_rescue.py | 37 checks: AI OCR rescue + least-loaded officer routing |
| test_map_tab.py | 56 checks: Land Map tab (tile sources, pins, geocode) |
| test_map_sheet.js | 12 checks: village sheet cascade + plot grid (node) |
| test_geocode_villages.js | 5 checks: background village geocoding (node) |
| test_reject_flow.py | 18 checks: reject workflow + terminal state |
