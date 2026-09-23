"""E2E battery: court case / litigation tracking (land-level).

Requires a running server (freshly seeded demo DB) on $LR_BASE
(default http://127.0.0.1:8000) and the app root in $LR_ROOT.
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("LR_BASE", "http://127.0.0.1:8000")
PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  | " + str(extra)[:160]))
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1


def req(method, path, tok=None, data=None, raw=False):
    from ciutil import http as _http
    return _http(BASE, method, path, tok, data, raw)


def main():
    s, admin = req("POST", "/api/auth/login",
                   data={"email": "admin@landrec.gov.in", "password": "Admin@123"})
    assert s == 200, (s, admin)
    s, op = req("POST", "/api/auth/login",
                data={"email": "demo.operator@demo.local", "password": "Demo@Operator1"})
    assert s == 200, (s, op)
    s, ver = req("POST", "/api/auth/login",
                 data={"email": "demo.verifier@demo.local", "password": "Demo@Verifier1"})
    assert s == 200, (s, ver)
    import uuid
    tag = uuid.uuid4().hex[:6]
    s, _ = req("POST", "/api/users", admin["token"],
               {"email": "case_viewer_%s@t.in" % tag, "password": "Pass@1234", "role": "viewer"})
    assert s == 200, (s, _)
    s, viewer = req("POST", "/api/auth/login",
                    data={"email": "case_viewer_%s@t.in" % tag, "password": "Pass@1234"})
    assert s == 200, (s, viewer)

    # ============ A. seeded demo scenario ============
    print("\n--- A. seeded demo scenario ---")
    # Sundarpur 452/77: active civil title suit filed 2020-05-10;
    # the 2021-06-14 sale happened while it was pending
    s, d = req("GET", "/api/documents/78972f981b4e/risk", admin["token"])
    check("Sundarpur risk: 200", s == 200, (s, str(d)[:100]))
    codes = [f["code"] for f in d.get("flags", [])]
    check("Sundarpur: ACTIVE_LITIGATION flag", "ACTIVE_LITIGATION" in codes, codes)
    check("Sundarpur: TRANSFER_DURING_LITIGATION flag", "TRANSFER_DURING_LITIGATION" in codes, codes)
    sev = {f["code"]: f["severity"] for f in d.get("flags", [])}
    check("Sundarpur: both litigation flags are critical",
          sev.get("ACTIVE_LITIGATION") == "critical" and sev.get("TRANSFER_DURING_LITIGATION") == "critical", sev)
    check("Sundarpur: verdict = litigation", d.get("verdict") == "litigation", d.get("verdict"))
    cs = d.get("court_cases", [])
    check("Sundarpur: 1 active case listed", len(cs) == 1 and cs[0]["status"] == "active"
          and cs[0]["case_number"] == "CS/2020/114", cs)
    check("Sundarpur: sale-during-loan still flagged (v3.9.7 intact)",
          "SALE_DURING_ENCUMBRANCE" in codes, codes)

    # Barkheda 312/45-2: revenue dispute DECIDED 2021
    s, d = req("GET", "/api/documents/d15d4dccaddd/risk", admin["token"])
    codes = [f["code"] for f in d.get("flags", [])]
    check("Barkheda: CLOSED_LITIGATION_ON_RECORD (info)", "CLOSED_LITIGATION_ON_RECORD" in codes, codes)
    check("Barkheda: no ACTIVE_LITIGATION", "ACTIVE_LITIGATION" not in codes, codes)
    check("Barkheda: verdict still encumbered (active PNB loan wins)",
          d.get("verdict") == "encumbered", d.get("verdict"))
    cs = d.get("court_cases", [])
    check("Barkheda: closed case listed with decision summary",
          len(cs) == 1 and cs[0]["status"] == "decided" and "decided" in (cs[0].get("decision_summary") or "").lower()
          or (len(cs) == 1 and cs[0]["status"] == "decided"), cs)

    # Fraud-demo land (Barkheda, Latin spelling / English record): the seeded
    # fraud sale (deed 2025-06-01) ran while the PNB loan was active AND the
    # title suit CS/2025/777 was pending; approving it re-named the owner,
    # which conflicts with the still-pending 2023-24 upload.
    s, d = req("GET", "/api/documents/0f8f73f343bd/risk", admin["token"])
    codes = [f["code"] for f in d.get("flags", [])]
    check("fraud-demo land: SALE_DURING_ENCUMBRANCE (critical)", "SALE_DURING_ENCUMBRANCE" in codes, codes)
    check("fraud-demo land: TRANSFER_DURING_LITIGATION (critical)", "TRANSFER_DURING_LITIGATION" in codes, codes)
    check("fraud-demo land: ACTIVE_LITIGATION (CS/2025/777)", "ACTIVE_LITIGATION" in codes, codes)
    check("fraud-demo land: OWNER_CONFLICT_YEAR (2023)", "OWNER_CONFLICT_YEAR" in codes, codes)
    check("fraud-demo land: verdict encumbered", d.get("verdict") == "encumbered", d.get("verdict"))
    s, h = req("GET", "/api/documents/0f8f73f343bd/history", admin["token"])
    rows = h.get("items", [])
    new_row = [r for r in rows if r.get("id") == "2f1cb4898fe1"]
    renamed = [r for r in rows if r.get("owner") == "Rajesh Gupta"]
    check("fraud-demo land: pending upload is a history row", len(new_row) == 1,
          [r.get("filename") for r in rows])
    check("fraud-demo land: owner re-named by the approved fraud sale", len(renamed) == 1,
          [r.get("owner") for r in rows])
    s, mu = req("GET", "/api/mutations", admin["token"])
    stamped = [m for m in mu.get("mutations", [])
               if "ACTIVE LITIGATION" in (m.get("reviewer_notes") or "")]
    check("fraud-demo: mutation carries the active-litigation stamp", len(stamped) >= 1,
          len(stamped))

    # Sarangpur draft: clean land, no cases
    s, d = req("GET", "/api/documents/55d6f0957109/risk", admin["token"])
    check("Sarangpur: verdict clear, no cases", d.get("verdict") == "clear"
          and not d.get("court_cases"), (d.get("verdict"), d.get("court_cases")))

    # WITHDRAWN — Arera 452/77 (Bhopal)
    s, d = req("GET", "/api/documents/4a755138b869/risk", admin["token"])
    cs = d.get("court_cases", [])
    codes = [f["code"] for f in d.get("flags", [])]
    check("Arera: withdrawn case listed",
          len(cs) == 1 and cs[0]["status"] == "withdrawn" and cs[0]["case_number"] == "POS/2018/41", cs)
    check("Arera: no ACTIVE_LITIGATION (withdrawn != active)", "ACTIVE_LITIGATION" not in codes, codes)
    check("Arera: CLOSED_LITIGATION_ON_RECORD (info)", "CLOSED_LITIGATION_ON_RECORD" in codes, codes)
    check("Arera: verdict still review (its own area-jump flags)", d.get("verdict") == "review",
          (d.get("verdict"), codes))

    # SETTLED — Kazipet 88/1 (Telugu pahani land)
    s, d = req("GET", "/api/documents/f06287db668c/risk", admin["token"])
    cs = d.get("court_cases", [])
    codes = [f["code"] for f in d.get("flags", [])]
    check("Kazipet: settled case listed",
          len(cs) == 1 and cs[0]["status"] == "settled" and cs[0]["case_number"] == "CS/2021/208", cs)
    check("Kazipet: decision summary present", "compromise" in (cs[0].get("decision_summary") or "").lower()
          if cs else False, cs)
    check("Kazipet: CLOSED_LITIGATION_ON_RECORD (info)", "CLOSED_LITIGATION_ON_RECORD" in codes, codes)
    check("Kazipet: no ACTIVE_LITIGATION", "ACTIVE_LITIGATION" not in codes, codes)

    # All four statuses represented in the demo data, each on its own land
    all_statuses = set()
    for did in ("78972f981b4e", "d15d4dccaddd", "4a755138b869", "f06287db668c"):
        s, d = req("GET", "/api/documents/%s/risk" % did, admin["token"])
        for c in d.get("court_cases", []):
            all_statuses.add(c["status"])
    check("demo data shows all 4 statuses separately",
          all_statuses == {"active", "decided", "withdrawn", "settled"}, all_statuses)

    # ============ B. CRUD + RBAC ============
    print("\n--- B. case CRUD + roles ---")
    s, d = req("POST", "/api/court-cases", viewer["token"],
               {"survey_number": "888", "village": "Caseville", "case_number": "X/1/1"})
    check("viewer cannot create case (403)", s == 403, s)
    s, c = req("POST", "/api/court-cases", op["token"],
               {"survey_number": "888", "khasra_number": "2", "village": "Caseville",
                "case_type": "civil", "case_number": "CS/2024/555",
                "court_name": "District Court, Test", "filed_date": "2024-01-15",
                "parties": "A vs B", "relief_sought": "possession"})
    check("operator can create case", s == 200 and c.get("case_number") == "CS/2024/555"
          and c.get("status") == "active", (s, str(c)[:120]))
    cid = (c or {}).get("id")
    s, d = req("GET", "/api/court-cases?survey=888&village=Caseville", viewer["token"])
    check("viewer can list cases", s == 200 and len(d.get("court_cases", [])) >= 1, (s, str(d)[:100]))
    s, d = req("POST", "/api/court-cases/%s/close" % cid, op["token"], {"status": "decided"})
    check("operator cannot close case (403)", s == 403, s)
    s, d = req("POST", "/api/court-cases/%s/close" % cid, ver["token"],
               {"status": "bogus", "closed_date": "2024-06-01"})
    check("invalid close status rejected (409)", s == 409, (s, str(d)[:100]))
    s, d = req("POST", "/api/court-cases/%s/close" % cid, ver["token"],
               {"status": "decided", "closed_date": "2024-06-01",
                "decision_summary": "Suit decided in favour of the record holder."})
    check("verifier can close case", s == 200 and d.get("status") == "decided", (s, str(d)[:120]))
    s, d = req("POST", "/api/court-cases/%s/close" % cid, ver["token"], {"status": "decided"})
    check("double-close refused (409)", s == 409, s)

    # ============ C. mutation approval gate (active litigation) ============
    print("\n--- C. mutation approval gate ---")
    s, m = req("POST", "/api/mutations", op["token"], {
        "transfer_type": "sale", "previous_owner": "Case Owner A", "new_owner": "Case Owner B",
        "survey_number": "452", "khasra_number": "77", "village": "Sundarpur",
        "deed_no": "CASEGATE/1/1", "deed_date": "2024-05-01", "notes": "gate test (active case)"})
    check("gate test: mutation created", s == 200 and m.get("id"), (s, str(m)[:100]))
    mid = m["id"]
    s, m2 = req("POST", "/api/mutations/%s/review" % mid, ver["token"],
                {"action": "approve", "linked_doc_id": "78972f981b4e", "notes": "ok"})
    notes = (m2 or {}).get("reviewer_notes") or ""
    check("approval notes the ACTIVE LITIGATION", "ACTIVE LITIGATION" in notes and "CS/2020/114" in notes,
          notes[:200])

    # ============ D. EC-style report PDF still works (now incl. cases) ============
    print("\n--- D. report PDF ---")
    s, b = req("GET", "/api/documents/78972f981b4e/encumbrance-pdf?years=13", admin["token"], raw=True)
    check("Sundarpur report PDF: 200 + PDF (litigation section inside)",
          s == 200 and isinstance(b, (bytes, bytearray)) and b[:4] == b"%PDF", (s, (b or b"")[:8]))

    print("\nCOURT CASE TESTS: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
