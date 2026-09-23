"""E2E battery: encumbrance (loan) module + land-level fraud/risk engine.

Requires a freshly seeded demo DB (the Sundarpur SBI loan + Barkheda PNB
loan scenario) and a running server on :8000.
"""
import os
import json
import sys
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


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, (s, d)
    return d["token"]


def main():
    admin = login("admin@landrec.gov.in", "Admin@123")
    op = login("demo.operator@demo.local", "Demo@Operator1")
    ver = login("demo.verifier@demo.local", "Demo@Verifier1")
    # viewer: create one
    import uuid
    tag = uuid.uuid4().hex[:6]
    s, _ = req("POST", "/api/users", admin,
               {"email": "enc_viewer_%s@t.in" % tag, "password": "Pass@1234", "role": "viewer"})
    assert s == 200, (s, _)
    viewer = login("enc_viewer_%s@t.in" % tag, "Pass@1234")

    # ============ A. DEMO SCENARIO (seeded data) ============
    print("\n--- A. seeded demo scenario ---")
    # Barkheda (312/45-2) has an ACTIVE PNB loan
    s, d = req("GET", "/api/documents/d15d4dccaddd/risk", admin)  # hindi khatauni, Barkheda
    check("Barkheda risk: 200", s == 200, (s, str(d)[:100]))
    check("Barkheda risk: active=1", d.get("active") == 1, d.get("active"))
    codes = [f["code"] for f in d.get("flags", [])]
    check("Barkheda risk: ACTIVE_ENCUMBRANCE flag", "ACTIVE_ENCUMBRANCE" in codes, codes)
    check("Barkheda verdict: encumbered", d.get("verdict") == "encumbered", d.get("verdict"))
    pnb = [e for e in d.get("encumbrances", []) if "Punjab" in (e.get("creditor") or "")]
    check("Barkheda: PNB loan listed", len(pnb) == 1 and pnb[0]["status"] == "active", pnb)

    # Sundarpur (452/77): SBI loan settled 2022; 2021 sale happened DURING the loan
    s, d = req("GET", "/api/documents/78972f981b4e/risk", admin)  # xfer_new_2023
    check("Sundarpur risk: 200", s == 200, (s, str(d)[:100]))
    codes = [f["code"] for f in d.get("flags", [])]
    check("Sundarpur risk: SALE_DURING_ENCUMBRANCE (critical)",
          "SALE_DURING_ENCUMBRANCE" in codes, codes)
    sev = {f["code"]: f["severity"] for f in d.get("flags", [])}
    check("Sundarpur: sale-during-loan is critical", sev.get("SALE_DURING_ENCUMBRANCE") == "critical", sev)
    check("Sundarpur risk: REJECTED_CONFLICT_COPY (info)", "REJECTED_CONFLICT_COPY" in codes, codes)
    check("Sundarpur: no ACTIVE encumbrance (SBI settled)", d.get("active") == 0, d.get("active"))
    sbi = [e for e in d.get("encumbrances", []) if "State Bank" in (e.get("creditor") or "")]
    check("Sundarpur: SBI loan listed as settled",
          len(sbi) == 1 and sbi[0]["status"] == "settled" and sbi[0]["settlement_date"] == "2022-08-30", sbi)

    # clean land: Sarangpur draft 118 (single record, no encumbrances)
    s, d = req("GET", "/api/documents/55d6f0957109/risk", admin)
    check("Sarangpur (clean) verdict: clear", d.get("verdict") == "clear",
          (d.get("verdict"), [f["code"] for f in d.get("flags", [])]))
    check("Sarangpur (clean): no flags", len(d.get("flags", [])) == 0, d.get("flags"))

    # messy land (Arera 452/77): owner rolled over 2020->2022 with no mutation
    # and the area jumped 4.8 -> 3.20 — the engine MUST flag both (negative test)
    s, d = req("GET", "/api/documents/4a755138b869/risk", admin)
    codes = [f["code"] for f in d.get("flags", [])]
    check("Arera (messy): OWNER_CHANGE_NO_MUTATION flagged", "OWNER_CHANGE_NO_MUTATION" in codes, codes)
    check("Arera (messy): AREA_JUMP flagged", "AREA_JUMP" in codes, codes)
    check("Arera (messy) verdict: review", d.get("verdict") == "review", d.get("verdict"))

    # ============ B. CRUD + ROLES ============
    print("\n--- B. encumbrance CRUD + roles ---")
    s, d = req("POST", "/api/encumbrances", viewer,
               {"survey_number": "999", "village": "Testville", "creditor": "X Bank",
                "amount": "100000", "mortgage_date": "2024-01-01"})
    check("viewer cannot create (403)", s == 403, s)
    s, d = req("POST", "/api/encumbrances", op,
               {"survey_number": "999", "village": "Testville", "khasra_number": "5",
                "creditor": "HDFC Bank", "amount": "250000",
                "mortgage_date": "2023-04-15", "reference_no": "HDFC/23/9"})
    check("operator can create", s == 200 and d.get("creditor") == "HDFC Bank", (s, str(d)[:100]))
    eid = (d or {}).get("id")
    check("created status=active by default", (d or {}).get("status") == "active", d)
    s, d = req("GET", "/api/encumbrances?survey=999&village=Testville", viewer)
    check("viewer can list", s == 200 and len(d.get("encumbrances", [])) >= 1, (s, str(d)[:100]))
    check("list reports >=1 active", (d.get("active") or 0) >= 1, d.get("active"))
    s, d = req("POST", "/api/encumbrances/%s/settle" % eid, op, {"settlement_date": "2024-05-01"})
    check("operator cannot settle (403)", s == 403, s)
    s, d = req("GET", "/api/encumbrances?survey=999&village=Testville", admin)
    active_before = d.get("active") or 0
    check("before settle: new encumbrance is active", active_before >= 1, active_before)
    s, d = req("POST", "/api/encumbrances/%s/settle" % eid, ver,
               {"settlement_date": "2024-05-01", "notes": "bank NOC received"})
    check("verifier can settle", s == 200 and d.get("status") == "settled", (s, str(d)[:100]))
    s, d = req("POST", "/api/encumbrances/%s/settle" % eid, ver, {"settlement_date": "2024-06-01"})
    check("double-settle refused (409)", s == 409, s)
    s, d = req("GET", "/api/encumbrances?survey=999&village=Testville", admin)
    check("after settle: active count decreased by 1",
          (d.get("active") or 0) == active_before - 1, (active_before, d.get("active")))

    # ============ C. MUTATION GATE (active encumbrance noted on approval) ============
    print("\n--- C. mutation approval gate ---")
    # new mutation on the Barkheda land (has the ACTIVE PNB loan), linked to
    # the hindi khatauni record; approval must note the live loan.
    s, m = req("POST", "/api/mutations", op,
               {"transfer_type": "gift", "previous_owner": "Ramswoop Sharma",
                "new_owner": "Sunita Sharma", "survey_number": "312",
                "khasra_number": "45/2", "village": "Barkheda", "district": "Bhopal",
                "state": "Madhya Pradesh", "deed_no": "GIFT/2024/77",
                "deed_date": "2024-02-10", "notes": "gift demo (gate test)"})
    check("gate test: mutation created", s == 200 and m.get("id"), (s, str(m)[:100]))
    mid = m["id"]
    s, m2 = req("POST", "/api/mutations/%s/review" % mid, ver,
                {"action": "approve", "linked_doc_id": "d15d4dccaddd", "notes": "ok"})
    notes = (m2 or {}).get("reviewer_notes") or ""
    check("approval notes the active encumbrance",
          "ACTIVE ENCUMBRANCE" in notes and "Punjab" in notes, notes[:160])
    s, aud = req("GET", "/api/audit?limit=500", admin)
    acts = [a for a in (aud or {}).get("audit", [])
            if a.get("action") == "mutation_approved_with_encumbrance"]
    check("audit trail records the gate event", len(acts) >= 1, len(acts))

    # ============ D. EC PDF ============
    print("\n--- D. encumbrance certificate PDF ---")
    s, b = req("GET", "/api/documents/78972f981b4e/encumbrance-pdf?years=13", admin, raw=True)
    check("Sundarpur EC PDF: 200 + PDF", s == 200 and b[:4] == b"%PDF", (s, b[:8]))
    check("Sundarpur EC PDF: substantial", len(b) > 2500, len(b))
    s, b = req("GET", "/api/documents/d15d4dccaddd/encumbrance-pdf?years=13", admin, raw=True)
    check("Barkheda EC PDF: 200 + PDF (active loan listed)", s == 200 and b[:4] == b"%PDF", (s, b[:8]))
    s, b = req("GET", "/api/documents/4a755138b869/encumbrance-pdf?years=30", admin, raw=True)
    check("Arera EC PDF: 30-yr window ok", s == 200 and b[:4] == b"%PDF", (s, b[:8]))

    # ============ E. owner-name matcher (script/spelling tolerant) ============
    print("\n--- E. owner matcher ---")
    sys.path.insert(0, os.environ.get("LR_ROOT", "/home/user/land_records/extracted"))
    from landrec.risk import _same_owner
    check("same person, Devanagari vs Latin",
          _same_owner("रामस्वरूप शर्मा", "Ramswaroop Sharma"), "transliteration match")
    check("different people stay distinct",
          not _same_owner("Ram Bahadur Singh", "Mahesh Verma"))
    check("spelling variant of same person",
          _same_owner("Kamla Devi Singh", "Kamla Devi Sing"))
    check("empty names never match", not _same_owner("", "Nobody"))

    print("\nENCUMBRANCE/RISK TESTS: %d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
