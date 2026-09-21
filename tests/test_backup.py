"""E2E battery: data backup & restore (admin).

Requires a running server (freshly seeded demo DB) on the port in
$LR_BASE (default http://127.0.0.1:8000) and the app root in $LR_ROOT.
"""
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("LR_BASE", "http://127.0.0.1:8000")
ROOT = os.environ.get("LR_ROOT", "/home/user/land_records/extracted")
DB = os.path.join(ROOT, "data", "landrec.db")
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  | " + str(extra)[:160]))
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1


def req(method, path, tok=None, data=None, raw=False):
    body = json.dumps(data).encode() if data is not None else None
    h = {"Content-Type": "application/json"} if data is not None else {}
    if tok:
        h["Authorization"] = "Bearer " + tok
    r = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            b = resp.read()
            return resp.status, (b if raw else (json.loads(b) if b else None))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b[:200].decode(errors="replace")


def census():
    con = sqlite3.connect(DB)
    c = {"docs": con.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
         "muts": con.execute("SELECT COUNT(*) FROM mutations").fetchone()[0]}
    con.close()
    return c


def main():
    s, admin = req("POST", "/api/auth/login",
                   data={"email": "admin@landrec.gov.in", "password": "Admin@123"})
    assert s == 200, (s, admin)
    s, op = req("POST", "/api/auth/login",
                data={"email": "demo.operator@demo.local", "password": "Demo@Operator1"})
    assert s == 200, (s, op)

    # ---- RBAC ----
    s, d = req("GET", "/api/backup/export", op["token"])
    check("operator cannot export (403)", s == 403, s)

    # ---- export ----
    s, zb = req("GET", "/api/backup/export", admin["token"], raw=True)
    check("export: 200 + bytes", s == 200 and isinstance(zb, (bytes, bytearray)), s)
    import io, zipfile
    ok_zip = False
    names = []
    manifest = {}
    if isinstance(zb, (bytes, bytearray)):
        try:
            z = zipfile.ZipFile(io.BytesIO(zb))
            ok_zip = z.testzip() is None
            names = z.namelist()
            if "manifest.json" in names:
                manifest = json.loads(z.read("manifest.json"))
        except Exception:
            ok_zip = False
    check("export: valid ZIP", ok_zip, "corrupt zip")
    check("export: contains landrec.db", "landrec.db" in names, names[:5])
    check("export: contains uploads/", any(n.startswith("uploads/") for n in names), len(names))
    check("export: manifest has counts",
          manifest.get("documents", 0) > 0 and manifest.get("mutations", 0) >= 0, manifest)
    if not ok_zip:
        print("\nBACKUP TESTS: %d passed, %d failed (zip corrupt - aborting)" % (PASS, FAIL))
        return 1

    # ---- round-trip ----
    before = census()
    s, m = req("POST", "/api/mutations", admin["token"], {
        "transfer_type": "sale", "previous_owner": "CI A", "new_owner": "CI B",
        "survey_number": "7777", "khasra_number": "1", "village": "CIville",
        "deed_no": "CI/1/1", "deed_date": "2026-01-01", "notes": "round-trip marker"})
    check("round-trip: marker mutation created", s == 200 and m.get("id"), (s, str(m)[:80]))
    mid = (m or {}).get("id")
    changed = census()
    check("round-trip: state changed (mutations +1)",
          changed["muts"] == before["muts"] + 1, (before, changed))

    boundary = "----ciboundary987"
    mp = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"b.zip\"\r\n"
           "Content-Type: application/zip\r\n\r\n") % boundary).encode() + zb + \
          ("\r\n--%s--\r\n" % boundary).encode()
    r = urllib.request.Request(BASE + "/api/backup/import", data=mp, method="POST",
                               headers={"Content-Type": "multipart/form-data; boundary=" + boundary,
                                        "Authorization": "Bearer " + admin["token"]})
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            rd, rs = json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        rd, rs = json.loads(e.read() or b"{}"), e.code
    check("round-trip: import 200", rs == 200, (rs, str(rd)[:120]))
    after = census()
    check("round-trip: mutations reverted", after["muts"] == before["muts"], (before, after))
    safe = rd.get("safety_copy", "")
    check("round-trip: safety copy created", bool(safe) and os.path.isdir(safe), safe)
    if safe and os.path.isdir(safe):
        con = sqlite3.connect(os.path.join(safe, "landrec.db"))
        n = con.execute("SELECT COUNT(*) FROM mutations WHERE id=?", (mid,)).fetchone()[0]
        con.close()
        check("round-trip: marker mutation preserved in safety copy", n == 1, n)

    # ---- invalid imports ----
    bad_body = b"this is not a zip"
    r = urllib.request.Request(BASE + "/api/backup/import", data=bad_body, method="POST",
                               headers={"Content-Type": "application/octet-stream",
                                        "Authorization": "Bearer " + admin["token"]})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            rs = resp.status
    except urllib.error.HTTPError as e:
        rs = e.code
    check("import rejects non-ZIP (400)", rs == 400, rs)

    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("readme.txt", "no db here")
    r = urllib.request.Request(BASE + "/api/backup/import", data=zbuf.getvalue(), method="POST",
                               headers={"Content-Type": "application/octet-stream",
                                        "Authorization": "Bearer " + admin["token"]})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            rs = resp.status
    except urllib.error.HTTPError as e:
        rs = e.code
    check("import rejects ZIP without landrec.db (400)", rs == 400, rs)

    print("\nBACKUP TESTS: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
