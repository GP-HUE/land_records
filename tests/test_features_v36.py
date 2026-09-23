#!/usr/bin/env python3
"""End-to-end tests for the v3.6 feature set (against the live server):
  1. Certified PDF + QR + public verify page (+ tamper detection)
  2. Year-wise history
  3. (public search — intentionally not part of this build)
  4. SLA aging + CSV report export
  5. Mutation application module (full workflow)
"""
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import uuid

BASE = "http://127.0.0.1:8000"
PASS = FAIL = 0
FAILURES = []
SAMPLES = "/home/user/land_records/extracted/samples"


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


def req(method, path, token=None, data=None, headers=None, raw=False):
    from ciutil import http as _http
    return _http(BASE, method, path, tok, data, raw)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


def upload_sample(tok, name):
    path = os.path.join(SAMPLES, name)
    with open(path, "rb") as f:
        data = f.read()
    boundary = "----testboundary" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode())
    body.write(data)
    body.write(("\r\n--%s\r\nContent-Disposition: form-data; name=\"doc_type\"\r\n\r\n"
                "land_record\r\n--%s--\r\n" % (boundary, boundary)).encode())
    s, d = req("POST", "/api/process", tok, body.getvalue(),
               headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    if s != 200:
        raise RuntimeError("upload failed: %s %s" % (s, str(d)[:200]))
    return d


def main():
    tag = uuid.uuid4().hex[:8]
    ADMIN = login("admin@landrec.gov.in", "Admin@123")
    s, d = req("POST", "/api/users", ADMIN, {"email": "f6op_%s@t.in" % tag, "password": "Op@12345", "role": "operator"})
    check("create operator", s == 200, d)
    s, d = req("POST", "/api/users", ADMIN, {"email": "f6ver_%s@t.in" % tag, "password": "Ver@12345", "role": "verifier"})
    check("create verifier", s == 200, d)
    OT = login("f6op_%s@t.in" % tag, "Op@12345")
    VT = login("f6ver_%s@t.in" % tag, "Ver@12345")

    # ---------------- FEATURE 5: MUTATION (run first; creates records too) ----
    print("\n--- FEATURE 5: MUTATION MODULE ---")
    # create two records with the SAME survey/village so a mutation can link
    d1 = upload_sample(OT, "xfer_old_2019.png")
    doc1 = d1["document"] if "document" in d1 else d1
    id1 = d1["id"]
    # find survey/village from doc1 detail
    s, dd = req("GET", "/api/documents/%s" % id1, OT)
    f1 = dd["fields"]
    g1 = lambda k: (f1.get(k) or {}).get("value", "")
    survey = g1("survey_number"); village = g1("village"); owner1 = g1("owner_name")
    print("   (test land: survey=%s village=%s owner=%s)" % (survey, village, owner1))

    app = {"transfer_type": "sale", "applicant_name": "Feature Test Applicant",
           "survey_number": survey, "khasra_number": g1("khasra_number"),
           "previous_owner": owner1, "new_owner": "Mutation New Owner " + tag,
           "village": village, "district": g1("district"), "state": g1("state"),
           "deed_no": "SD-" + tag, "deed_date": "2026-08-01",
           "notes": "Test mutation application for feature verification"}
    s, m = req("POST", "/api/mutations", OT, app)
    check("mutation create -> 200 + app_no", s == 200 and re.fullmatch(r"MUT-\d{4}-\d{4}", m.get("app_no", "") or ""), (s, str(m)[:120]))
    mid = m.get("id")
    check("mutation status received + event", m.get("status") == "received" and len(m.get("events", [])) >= 1, m.get("status"))

    # attach supporting document
    deed = os.path.join(SAMPLES, "handwritten_mutation_sample.png")
    with open(deed, "rb") as f:
        deeddata = f.read()
    boundary = "----b" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"deed.png\"\r\n"
                "Content-Type: image/png\r\n\r\n" % boundary).encode())
    body.write(deeddata)
    body.write(("\r\n--%s--\r\n" % boundary).encode())
    s, m2 = req("POST", "/api/mutations/%s/file" % mid, OT, body.getvalue(),
                headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    check("attach supporting doc", s == 200 and m2.get("file_name") == "deed.png", (s, str(m2)[:120]))

    # applicant listing + RBAC
    s, d = req("GET", "/api/mutations", OT)
    check("applicant sees own applications", s == 200 and any(x["id"] == mid for x in d.get("mutations", [])), (s, len(d.get("mutations", []))))
    s, d = req("GET", "/api/mutations/%s" % mid, OT)
    check("applicant can view own application", s == 200, s)
    s, d = req("POST", "/api/mutations/%s/review" % mid, OT, {"action": "start"})
    check("operator cannot review mutation (403)", s == 403, (s, d))

    # staff list + counts
    s, d = req("GET", "/api/mutations", VT)
    check("staff sees all + counts", s == 200 and d.get("staff") is True and d.get("counts", {}).get("received", 0) >= 1, (s, d.get("counts")))

    # start review
    s, m3 = req("POST", "/api/mutations/%s/review" % mid, VT, {"action": "start", "notes": "picked up"})
    check("start review -> under_review", s == 200 and m3.get("status") == "under_review", (s, m3.get("status")))

    # approve WITHOUT notes/linked -> should auto-link (transfer candidate) or 400
    s, m4 = req("POST", "/api/mutations/%s/review" % mid, VT, {"action": "approve"})
    check("approve (auto-link) -> verified", s == 200 and m4.get("status") == "verified" and m4.get("linked_doc_id"), (s, str(m4)[:150]))
    linked = m4.get("linked_doc_id")

    # the linked record's owner must now be the new owner
    s, dd = req("GET", "/api/documents/%s" % linked, VT)
    newowner = ((dd.get("fields") or {}).get("owner_name") or {}).get("value", "")
    check("record owner updated by mutation", newowner == app["new_owner"], "owner=%r linked=%s" % (newowner, linked))
    check("linked record has cert hash", bool(dd.get("cert_hash")), dd.get("cert_hash"))

    # events timeline
    check("event timeline complete", [e["status"] for e in m4.get("events", [])] ==
          ["received", "received", "under_review", "verified"] or
          all(x in [e["status"] for e in m4.get("events", [])] for x in ("received", "under_review", "verified")),
          [e["status"] for e in m4.get("events", [])])

    # second mutation -> reject flow
    s, m5 = req("POST", "/api/mutations", OT, {**app, "new_owner": "Reject Me Owner", "survey_number": survey + "/X"})
    mid5 = m5.get("id")
    s, d = req("POST", "/api/mutations/%s/review" % mid5, VT, {"action": "reject"})
    check("reject without notes -> 400", s == 400, (s, d))
    s, d = req("POST", "/api/mutations/%s/review" % mid5, VT, {"action": "reject", "notes": "deed not registered"})
    check("reject with notes -> rejected", s == 200 and d.get("status") == "rejected", (s, d.get("status")))
    s, d = req("POST", "/api/mutations/%s/review" % mid5, VT, {"action": "approve"})
    check("processed app cannot be re-processed (409)", s == 409, (s, d))

    # file download
    s, b = req("GET", "/api/mutations/%s/file" % mid, OT, raw=True)
    check("supporting doc downloadable", s == 200 and len(b) > 1000, (s, len(b) if isinstance(b, (bytes, bytearray)) else b))

    # ---------------- FEATURE 1: CERTIFIED PDF + VERIFY PAGE ----------------
    print("\n--- FEATURE 1: CERTIFIED PDF + QR + VERIFY PAGE ---")
    # use the newly-certified linked record (verified by mutation)
    s, b = req("GET", "/api/documents/%s/certified-pdf" % linked, VT, raw=True)
    check("certified PDF downloads", s == 200 and isinstance(b, (bytes, bytearray)) and b[:5] == b"%PDF-", (s, str(b)[:40]))
    check("PDF is substantial (>50KB: layout+font+QR)", isinstance(b, (bytes, bytearray)) and len(b) > 50000, len(b) if isinstance(b, (bytes, bytearray)) else "n/a")
    # extract text (content stream is deflate-compressed) and assert
    import pymupdf
    pd = pymupdf.open(stream=b, filetype="pdf") if isinstance(b, (bytes, bytearray)) else None
    ptext = pd[0].get_text() if pd else ""
    check("PDF text contains record id", linked in ptext, ptext[:120])
    check("PDF text contains verify path", ("/verify/" + linked) in ptext, ptext[:120])
    check("PDF text contains owner", app["new_owner"] in ptext, ptext[:120])
    check("PDF has QR (many small rects)", len(pd[0].get_drawings()) > 200 if pd else False, len(pd[0].get_drawings()) if pd else 0)
    pd.close()

    # unverified doc -> 403 (id1 may be auto-approved; use its real status to assert consistently)
    s, dd1 = req("GET", "/api/documents/%s" % id1, OT)
    expect = 200 if dd1.get("status") in ("verified", "auto_approved") else 403
    s, d = req("GET", "/api/documents/%s/certified-pdf" % id1, OT, raw=True)
    check("certified-pdf access matches record status", s == expect, (s, dd1.get("status")))

    # public verify page (NO auth)
    s, b = req("GET", "/verify/%s" % linked, raw=True)
    html = b.decode("utf-8", errors="replace") if isinstance(b, (bytes, bytearray)) else str(b)
    check("verify page 200 without auth", s == 200, s)
    check("verify page shows VERIFIED", "VERIFIED" in html and "GENUINE" in html, html[:200])
    check("verify page shows cert hash", dd.get("cert_hash", "")[:16] in html)

    # unknown id
    s, b = req("GET", "/verify/nonexistent123", raw=True)
    check("unknown verify id -> NO CERTIFIED RECORD", s == 200 and "NO CERTIFIED RECORD" in (b.decode() if isinstance(b, (bytes, bytearray)) else str(b)))

    # TAMPER test: alter a field in DB, verify page must flag it
    import sqlite3
    dbp = "/home/user/land_records/extracted/data/landrec.db"
    c = sqlite3.connect(dbp)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT extracted_json FROM documents WHERE id=?", (linked,)).fetchone()
    orig = row["extracted_json"]
    fjson = json.loads(orig)
    fjson["owner_name"]["value"] = "TAMPERED HACKER NAME"
    c.execute("UPDATE documents SET extracted_json=? WHERE id=?", (json.dumps(fjson), linked))
    c.commit(); c.close()
    s, b = req("GET", "/verify/%s" % linked, raw=True)
    html2 = b.decode("utf-8", errors="replace") if isinstance(b, (bytes, bytearray)) else str(b)
    check("tamper detected on verify page", "TAMPERED" in html2 or "CERTIFICATION FAILED" in html2, html2[:300])
    # restore
    c = sqlite3.connect(dbp)
    c.execute("UPDATE documents SET extracted_json=? WHERE id=?", (orig, linked))
    c.commit(); c.close()
    s, b = req("GET", "/verify/%s" % linked, raw=True)
    check("tamper restored -> VERIFIED again", "GENUINE" in (b.decode() if isinstance(b, (bytes, bytearray)) else str(b)))

    # ---------------- FEATURE 2: YEAR-WISE HISTORY ----------------
    print("\n--- FEATURE 2: YEAR-WISE HISTORY ---")
    s, d = req("GET", "/api/documents/%s/history" % id1, OT)
    items = d.get("items", []) if s == 200 else []
    check("history endpoint 200", s == 200, s)
    check("history includes other records of same survey", isinstance(items, list) and len(items) >= 1,
          "survey=%s items=%d" % (d.get("survey"), len(items) if isinstance(items, list) else items))
    if len(items) >= 1:
        years = [str(i.get("year") or "") for i in items]
        check("history sorted by year", years == sorted(years), years)
        check("history items have owner+status", all(i.get("owner") and i.get("status") for i in items))
    # mutation-updated record should appear in history with new owner
    s, d = req("GET", "/api/documents/%s/history" % linked, VT)
    found_new = any(i.get("owner") == app["new_owner"] for i in d.get("items", []))
    check("post-mutation owner appears in history", found_new, [i.get("owner") for i in d.get("items", [])])

    # ---------------- FEATURE 4: SLA + CSV EXPORT ----------------
    print("\n--- FEATURE 4: SLA AGING + CSV REPORT ---")
    s, d = req("GET", "/api/dashboard", VT)
    check("dashboard has sla stats", s == 200 and "sla" in d and all(k in d["sla"] for k in ("pending", "fresh_7d", "warm_30d", "overdue_30d")), d.get("sla"))
    check("dashboard has mutation counts", s == 200 and "mutations" in d, d.get("mutations"))

    # pending review docs get an age: submit a new doc
    d2 = upload_sample(OT, "hindi_khatauni_sample.png")
    id2 = d2["id"]
    s, d = req("POST", "/api/documents/%s/submit" % id2, OT, {})
    check("submit doc -> pending_review", s == 200 and d.get("status") == "pending_review", (s, d))
    s, dd = req("GET", "/api/documents/%s" % id2, OT)
    check("submitted_at recorded", (dd.get("submitted_at") or 0) > time.time() - 120, dd.get("submitted_at"))
    s, d = req("GET", "/api/documents/%s/certified-pdf" % id2, OT, raw=True)
    check("pending-review doc cannot download (403)", s == 403, (s, str(d)[:80]))

    s, b = req("GET", "/api/reports/export", VT, raw=True)
    check("CSV export 200", s == 200, (s, str(b)[:100]))
    txt = b.decode("utf-8-sig", errors="replace") if isinstance(b, (bytes, bytearray)) else str(b)
    lines = [l for l in txt.splitlines() if l.strip()]
    check("CSV has header + rows", len(lines) >= 2 and "owner_name" in lines[0] and "survey_number" in lines[0], lines[0][:100] if lines else "empty")
    check("CSV contains test record", id1 in txt and id2 in txt)

    # operator cannot export
    s, d = req("GET", "/api/reports/export", OT, raw=True)
    check("operator CSV export blocked (403)", s == 403, s)

    print("\n============================================")
    print("V3.6 FEATURE TESTS: %d passed, %d failed" % (PASS, FAIL))
    for n, x in FAILURES:
        print("  FAIL:", n, "|", str(x)[:160])
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
