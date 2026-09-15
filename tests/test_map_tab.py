#!/usr/bin/env python3
"""Land Map tab battery (v3.5.25 map fixes):
  A. Static assertions — every fix must be present in the SERVED page
  B. API-level map flows — pins (set/clear/persist/RBAC/validation),
     geocode (real + fictional), map records payload
"""
import json
import re
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
PASS = FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    tag = "PASS" if cond else "FAIL"
    if not cond:
        FAILURES.append((name, extra))
    print("%s  %s%s" % (tag, name, ("  | " + str(extra)[:140]) if (extra and not cond) else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def req(method, path, token=None, data=None, raw=False):
    hdrs = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=body, method=method, headers=hdrs)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            b = resp.read()
            return resp.status, (b if raw else (json.loads(b) if b else None))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b[:200].decode(errors="replace")


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


def main():
    ADMIN = login("admin@landrec.gov.in", "Admin@123")
    # dedicated fresh accounts for this battery
    import uuid
    tag = uuid.uuid4().hex[:6]
    s, d = req("POST", "/api/users", ADMIN, {"email": "mf_ver_%s@t.in" % tag, "password": "Ver@12345", "role": "verifier"})
    assert s == 200, d
    s, d = req("POST", "/api/users", ADMIN, {"email": "mf_op_%s@t.in" % tag, "password": "Op@12345", "role": "operator"})
    assert s == 200, d
    VT = login("mf_ver_%s@t.in" % tag, "Ver@12345")
    OT = login("mf_op_%s@t.in" % tag, "Op@12345")

    # ================= A. STATIC ASSERTIONS ON SERVED PAGE =================
    print("--- A. SERVED PAGE CONTAINS ALL MAP FIXES ---")
    s, page = req("GET", "/", ADMIN, raw=True)
    page = page.decode("utf-8", errors="replace")
    check("page served", s == 200, s)

    checks = [
        ("tileerror shows banner", "tl.on('tileerror'"),
        ("tileload HIDES banner (stale-warning fix)", "tl.on('tileload'"),
        ("map load hint present", 'id="mapLoadHint"'),
        ("hint hides on tileload", "h) h.classList.add('hidden')"),
        ("banner has dismiss button", "mapFallback').classList.add('hidden')\" style=\"border:1px solid #fde68a"),
        ("switchView: invalidateSize before fit (ordering fix)", "mapLeaflet.invalidateSize();\n        mapDrawMarkers();"),
        ("fit only once (mapHasFitted)", "if(!mapHasFitted){ mapFitAll(); mapHasFitted = true; }"),
        ("village cache persisted (save)", "localStorage.setItem('lrVillageCache'"),
        ("village cache restored (load)", "localStorage.getItem('lrVillageCache')"),
        ("geocode loop until done", "while(true)"),
        ("geocode offline bail (failures>=3)", "if(failures >= 3) break"),
        ("marker popup on select (_recId)", "mapOpenMarkerPopup"),
        ("plot search not-found hint", 'id="sheetPlotHint"'),
        ("sheet->real map jump", "function mapSheetToMap"),
        ("plot info: Open on Real Map button", "mapSheetToMap()\">🗺️ Open on Real Map"),
        ("Show-on-Map plot-missing warning", "not found in this village sheet"),
        ("map grid background (not a dead grey box)", "background-size:48px 48px"),
        ("leaflet bundled ref", "/static/vendor/leaflet/leaflet.js"),
        ("tile source switcher (access-blocked fix)", 'id="mapTileSource"'),
        ("tile source: OSM", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
        ("tile source: CARTO fallback", "basemaps.cartocdn.com"),
        ("tile source: Esri satellite fallback", "server.arcgisonline.com"),
        ("tile source: topo fallback", "tile.opentopomap.org"),
        ("schematic offline mode", "mapSchematicNote"),
        ("schematic option in dropdown", "value=\"schematic\""),
        ("mapSetTileSource function", "function mapSetTileSource(key, auto)"),
        ("tile source persisted", "lrTileSource"),
        ("banner suggests schematic", "Use Schematic (offline)"),
        ("banner mentions blocked network", "blocked on this network"),
        ("old OSM-only layer gone", "mapTileLayer = L.tileLayer(src.url"),
    ]
    for name, needle in checks:
        check(name, needle in page, needle[:60])

    # bundled leaflet actually served
    s, b = req("GET", "/static/vendor/leaflet/leaflet.js", raw=True)
    check("bundled leaflet.js served", s == 200 and len(b) > 100000, (s, len(b) if isinstance(b, (bytes, bytearray)) else b))
    s, b = req("GET", "/static/vendor/leaflet/leaflet.css", raw=True)
    check("bundled leaflet.css served", s == 200 and b"leaflet-pane" in b, s)

    # ================= B. API-LEVEL MAP FLOWS =================
    print("\n--- B. MAP API FLOWS ---")
    # a record to pin: take any existing doc
    s, d = req("GET", "/api/documents", VT)
    docs = d.get("documents", [])
    check("have documents", len(docs) > 0, len(docs))
    doc = docs[0]
    did = doc["id"]

    # operator cannot pin (RBAC)
    s, d = req("PUT", "/api/map/records/%s/location" % did, OT, {"lat": 26.5, "lon": 80.2})
    check("operator pin blocked (403)", s == 403, (s, str(d)[:60]))

    # verifier pins
    s, d = req("PUT", "/api/map/records/%s/location" % did, VT, {"lat": 26.5, "lon": 80.2})
    check("verifier pin set (200)", s == 200 and d.get("ok"), (s, d))

    # persisted in map records
    s, d = req("GET", "/api/map/records", VT)
    rec = next((r for r in d.get("records", []) if r["id"] == did), None)
    check("pin persisted in /api/map/records", rec and rec.get("lat") == 26.5 and rec.get("lon") == 80.2, rec and (rec.get("lat"), rec.get("lon")))

    # record detail carries lat/lon (green pin path)
    s, d = req("GET", "/api/documents/%s" % did, VT)
    check("record detail has lat/lon", d.get("lat") == 26.5, (d.get("lat"), d.get("lon")))

    # invalid coords rejected
    s, d = req("PUT", "/api/map/records/%s/location" % did, VT, {"lat": 999, "lon": 80.2})
    check("out-of-range lat rejected (400)", s == 400, (s, str(d)[:60]))
    s, d = req("PUT", "/api/map/records/%s/location" % did, VT, {"lat": "abc", "lon": 80.2})
    check("non-numeric rejected (400)", s == 400, (s, str(d)[:60]))

    # audit entry for pin
    s, d = req("GET", "/api/audit/%s" % did, VT)
    acts = [a.get("action") for a in d.get("audit", [])]
    check("pin audited (location_set)", "location_set" in acts, acts[-4:])

    # clear pin
    s, d = req("PUT", "/api/map/records/%s/location" % did, VT, {"lat": None, "lon": None})
    check("pin cleared (200)", s == 200 and d.get("lat") is None, (s, d))
    s, d = req("GET", "/api/map/records", VT)
    rec = next((r for r in d.get("records", []) if r["id"] == did), None)
    check("pin clear persisted", rec and rec.get("lat") is None, rec and (rec.get("lat"), rec.get("lon")))

    # geocode: real village + district
    s, d = req("POST", "/api/map/geocode", VT, {"query": "Bhopal, Madhya Pradesh, India"})
    check("geocode real city", s == 200 and d.get("lat") is not None, (s, str(d)[:80]))
    s, d = req("POST", "/api/map/geocode", VT, {"query": "Zzzqqxx Village, Nowhereland, India"})
    check("fictional village -> no match, no crash", s == 200 and d.get("lat") is None, (s, str(d)[:80]))
    s, d = req("POST", "/api/map/geocode", VT, {"query": ""})
    check("empty query rejected (400)", s == 400, s)

    # map records payload fields
    s, d = req("GET", "/api/map/records", VT)
    r0 = d.get("records", [{}])[0]
    for f in ("id", "owner", "survey", "khasra", "village", "tehsil", "district", "state", "lat", "lon"):
        check("map record field: " + f, f in r0, list(r0.keys()))

    print("\n============================================")
    print("MAP TAB TESTS: %d passed, %d failed" % (PASS, FAIL))
    for n, x in FAILURES:
        print("  FAIL:", n, "|", str(x)[:160])
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
