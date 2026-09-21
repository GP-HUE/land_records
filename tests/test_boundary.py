#!/usr/bin/env python3
"""E2E battery: plot boundary (digitize/estimate/import) + coordinates."""
import os
import json
import math
import sys
import urllib.request
import urllib.error
import uuid

BASE = os.environ.get("LR_BASE", "http://127.0.0.1:8000")
PASS = FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    tag = "PASS" if cond else "FAIL"
    if not cond:
        FAILURES.append((name, extra))
    print("%s  %s%s" % (tag, name, ("  | " + str(extra)[:130]) if (extra and not cond) else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def req(method, path, token=None, data=None):
    hdrs = {}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=body, method=method, headers=hdrs)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            b = resp.read()
            return resp.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b[:200].decode(errors="replace")


def ring_area_m2(ring):
    lat0 = ring[0][0] * math.pi / 180
    mlat, mlon = 111320.0, 111320.0 * math.cos(lat0)
    a2 = 0.0
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        a2 += (a[0] * mlat) * (b[1] * mlon) - (b[0] * mlat) * (a[1] * mlon)
    return abs(a2) / 2.0


def main():
    tag = uuid.uuid4().hex[:6]
    ADMIN_T = login = None
    s, d = req("POST", "/api/auth/login", data={"email": "admin@landrec.gov.in", "password": "Admin@123"})
    check("admin login", s == 200, (s, d))
    ADMIN_T = d["token"]
    for role in ("verifier", "operator"):
        s, d = req("POST", "/api/users", ADMIN_T,
                   {"email": "bnd_%s_%s@t.in" % (role, tag), "password": "Pass@1234", "role": role})
        check("create %s" % role, s == 200, d)
    s, d = req("POST", "/api/auth/login", data={"email": "bnd_verifier_%s@t.in" % tag, "password": "Pass@1234"})
    VT = d["token"]
    s, d = req("POST", "/api/auth/login", data={"email": "bnd_operator_%s@t.in" % tag, "password": "Pass@1234"})
    OT = d["token"]

    # find a record with a readable area value
    s, d = req("GET", "/api/documents", VT)
    docs = d.get("documents", [])
    target = None
    for x in docs:
        f = x.get("fields") or {}
        if (f.get("area") or {}).get("value"):
            target = x
            break
    check("found a record with an area value", target is not None, len(docs))
    if not target:
        print("\nSKIP: no documents in DB"); sys.exit(1)
    doc_id = target["id"]
    area_val = str((target["fields"]["area"] or {}).get("value", ""))

    # Make the test robust: the estimate endpoint needs either an exact pin
    # OR a geocodable village (Nominatim). Test uploads may carry fake
    # village names, so guarantee an exact pin when the record has none.
    s, d = req("GET", "/api/map/records", VT)
    _rec = next((x for x in (d or {}).get("records", []) if x["id"] == doc_id), None)
    if _rec and _rec.get("lat") is None:
        s2, d2 = req("PUT", "/api/map/records/%s/location" % doc_id, VT,
                     {"lat": 23.2585, "lon": 77.4020})
        check("exact pin set (fallback for non-geocodable test villages)",
              s2 == 200, (s2, str(d2)[:100]))

    # ============ A. ESTIMATE FROM AREA ============
    print("\n--- A. ESTIMATE FROM AREA ---")
    s, d = req("POST", "/api/map/records/%s/boundary/estimate" % doc_id, VT, {})
    check("estimate -> 200", s == 200, (s, str(d)[:120]))
    if s == 200:
        ring = d.get("boundary") or []
        check("estimated ring has 4 corners", len(ring) == 4, len(ring))
        check("source = estimated", d.get("source") == "estimated", d.get("source"))
        m2 = d.get("area_m2") or 0
        got_m2 = ring_area_m2(ring)
        check("estimated size ~ recorded area (+-10%)", 0 < m2 and abs(got_m2 - m2) / m2 < 0.10,
              "claimed %s m2 vs computed %.1f m2 (%s)" % (m2, got_m2, area_val))
        # square sanity: two distinct corners
        lats = {p[0] for p in ring}; lons = {p[1] for p in ring}
        check("ring spans 2x2 corners", len(lats) == 2 and len(lons) == 2, (lats, lons))

    # ============ B. DIGITIZED BOUNDARY ============
    print("\n--- B. DIGITIZED BOUNDARY ---")
    c0 = (d.get("center") or [23.25, 77.41])
    dlat, dlon = 0.002, 0.002
    custom = [[c0[0], c0[1]], [c0[0] + dlat, c0[1]], [c0[0] + dlat, c0[1] + dlon],
              [c0[0] + dlat * 0.5, c0[1] + dlon * 1.4], [c0[0], c0[1] + dlon * 0.7]]
    s, d = req("POST", "/api/map/records/%s/boundary" % doc_id, VT, {"coordinates": custom})
    check("digitize -> 200", s == 200, (s, str(d)[:120]))
    if s == 200:
        check("ring preserved (5 corners, [lat,lon])", d.get("boundary") == custom or len(d.get("boundary", [])) == 5,
              d.get("boundary"))
        check("source = digitized", d.get("source") == "digitized", d.get("source"))
    # persisted + in map payload
    s, d = req("GET", "/api/map/records", VT)
    rec = next((r for r in d.get("records", []) if r["id"] == doc_id), None)
    check("boundary in /api/map/records", rec and rec.get("boundary") and len(rec["boundary"]) == 5,
          rec and rec.get("boundary_source"))
    # detail endpoint exposes parsed boundary + source
    s, d = req("GET", "/api/documents/%s" % doc_id, VT)
    check("detail endpoint: boundary parsed", s == 200 and isinstance(d.get("boundary"), list) and d.get("boundary_source") == "digitized",
          (s, d.get("boundary_source")))
    # audit
    s, d = req("GET", "/api/audit/%s" % doc_id, VT)
    acts = [a.get("action") for a in d.get("audit", [])]
    check("audit: boundary_set entries", acts.count("boundary_set") >= 2, acts[-4:])

    # ============ C. GEOJSON IMPORT ============
    print("\n--- C. GEOJSON IMPORT ---")
    # GeoJSON Polygon in [lon, lat] order
    geo = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
        [c0[1], c0[0]], [c0[1] + 0.001, c0[0]], [c0[1] + 0.001, c0[0] + 0.001],
        [c0[1], c0[0] + 0.001], [c0[1], c0[0]]
    ]]}}
    s, d = req("POST", "/api/map/records/%s/boundary/import" % doc_id, VT, {"geojson": geo})
    check("geojson import -> 200", s == 200, (s, str(d)[:140]))
    if s == 200:
        r2 = d.get("boundary") or []
        # [lon,lat] -> [lat,lon] conversion check
        check("lon/lat converted to lat/lon", len(r2) >= 4 and abs(r2[0][0] - c0[0]) < 1e-6 and abs(r2[0][1] - c0[1]) < 1e-6, r2[:2])
        check("source = imported", d.get("source") == "imported", d.get("source"))
    # bad geojson
    s, d = req("POST", "/api/map/records/%s/boundary/import" % doc_id, VT, {"geojson": {"type": "Point", "coordinates": [1, 2]}})
    check("Point geojson rejected (400)", s == 400, (s, str(d)[:100]))
    s, d = req("POST", "/api/map/records/%s/boundary/import" % doc_id, VT, {"geojson": "not json"})
    check("non-dict geojson rejected (400)", s == 400, (s, str(d)[:100]))

    # ============ D. VALIDATION ============
    print("\n--- D. VALIDATION ---")
    s, d = req("POST", "/api/map/records/%s/boundary" % doc_id, VT, {"coordinates": [[1, 2], [3, 4], [5, 6]]})
    check("3 corners rejected (400)", s == 400, (s, str(d)[:100]))
    s, d = req("POST", "/api/map/records/%s/boundary" % doc_id, VT,
               {"coordinates": [[999, 1], [2, 1], [3, 2], [4, 3]]})
    check("out-of-range vertex rejected (400)", s == 400, (s, str(d)[:100]))
    s, d = req("POST", "/api/map/records/%s/boundary" % doc_id, VT,
               {"coordinates": [[1, "x"], [2, 1], [3, 2], [4, 3]]})
    check("non-numeric vertex rejected (400)", s == 400, (s, str(d)[:100]))

    # ============ E. RBAC ============
    print("\n--- E. RBAC ---")
    s, d = req("POST", "/api/map/records/%s/boundary" % doc_id, OT, {"coordinates": custom})
    check("operator cannot digitize (403)", s == 403, (s, str(d)[:80]))
    s, d = req("POST", "/api/map/records/%s/boundary/estimate" % doc_id, OT, {})
    check("operator cannot estimate (403)", s == 403, (s, str(d)[:80]))
    s, d = req("POST", "/api/map/records/%s/boundary/clear" % doc_id, OT, {})
    check("operator cannot clear (403)", s == 403, (s, str(d)[:80]))

    # ============ F. CLEAR ============
    print("\n--- F. CLEAR ---")
    s, d = req("POST", "/api/map/records/%s/boundary/clear" % doc_id, VT, {})
    check("clear -> 200", s == 200, (s, str(d)[:80]))
    s, d = req("GET", "/api/documents/%s" % doc_id, VT)
    check("boundary cleared in detail", s == 200 and d.get("boundary") is None, d.get("boundary"))
    s, d = req("GET", "/api/audit/%s" % doc_id, VT)
    acts = [a.get("action") for a in d.get("audit", [])]
    check("audit: boundary_cleared", "boundary_cleared" in acts, acts[-3:])

    # missing record
    s, d = req("POST", "/api/map/records/nonexistent12/boundary", VT, {"coordinates": custom})
    check("unknown record -> 404", s == 404, (s, str(d)[:80]))

    print("\n============================================")
    print("BOUNDARY TESTS: %d passed, %d failed" % (PASS, FAIL))
    for n, x in FAILURES:
        print("  FAIL:", n, "|", str(x)[:150])
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
