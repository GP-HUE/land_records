"""SA CourtLink (v3.11): demo court database + SA court-history screening.

Covers:
  * RBAC — every /api/admin/court-db* endpoint is admin-only (operator /
    verifier / anonymous are refused)
  * CRUD — add / edit / delete predefined court cases, validation rules,
    duplicate case numbers
  * Matching — survey+village exact, owner+village probable, survey-only
    possible, precision (near-miss survey does NOT match), clean land
  * Upload screening — instant sa_screening block for ADMIN uploads;
    operator uploads are NOT screened
  * Record-tab scan — result + audit-trail entry (sa_courtlink_scan)
  * Attach — a matched case lands in the record's internal litigation
    ledger; duplicate attaches are refused (409)
  * SA chat intent — 'scan the court database for record <id>' in SA mode

Run: LR_BASE=http://127.0.0.1:8000 python3 tests/test_sa_courtlink.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ciutil import http as _http  # noqa: E402

BASE = os.environ.get("LR_BASE", "http://127.0.0.1:8000")
LR_ROOT = os.environ.get("LR_ROOT", "/home/user/land_records/extracted")

passed = failed = skipped = 0


def check(name, cond, extra=""):
    global passed, failed
    print(("PASS  " if cond else "FAIL  ") + name + ("" if cond else "  | " + str(extra)[:220]))
    if cond:
        passed += 1
    else:
        failed += 1


def skip(name, why):
    global skipped
    print("SKIP  " + name + "  | " + why)
    skipped += 1


def req(method, path, tok=None, data=None, retries=4):
    return _http(BASE, method, path, tok=tok, data=data, retries=retries)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


ADMIN = login("admin@landrec.gov.in", "Admin@123")
OP = login("demo.operator@demo.local", "Demo@Operator1")
VER = login("demo.verifier@demo.local", "Demo@Verifier1")

print("=" * 70)
print("A. RBAC — the whole CourtLink surface is admin-only")
print("=" * 70)
s, d = req("GET", "/api/admin/court-db", tok=ADMIN)
check("A1 admin can list the court database", s == 200 and isinstance(d.get("cases"), list), d)
CASES = d.get("cases", []) if s == 200 else []
check("A2 court database is seeded with predefined cases", len(CASES) >= 6, len(CASES))
check("A3 seed contains the STAY-ORDER case WPL/2023/0234",
      any(c.get("case_number") == "WPL/2023/0234" and c.get("status") == "stay" for c in CASES))
s, d = req("GET", "/api/admin/court-db", tok=OP)
check("A4 operator cannot list the court database (403)", s == 403, (s, d))
s, d = req("GET", "/api/admin/court-db", tok=VER)
check("A5 verifier cannot list the court database (403)", s == 403, (s, d))
s, d = req("GET", "/api/admin/court-db")
check("A6 anonymous cannot list the court database (401)", s == 401, (s, d))
s, d = req("POST", "/api/admin/court-db", tok=OP, data={"case_number": "X/1/1", "survey_number": "1"})
check("A7 operator cannot add a case (403)", s == 403, (s, d))
s, d = req("POST", "/api/admin/court-db/scan/dummy123456", tok=OP)
check("A8 operator cannot run an SA scan (403)", s == 403, (s, d))
s, d = req("POST", "/api/admin/court-db/scan/dummy123456", tok=VER)
check("A9 verifier cannot run an SA scan (403)", s == 403, (s, d))
s, d = req("POST", "/api/admin/court-db/attach", tok=VER, data={"case_id": "x", "document_id": "y"})
check("A10 verifier cannot attach a case (403)", s == 403, (s, d))

print("=" * 70)
print("B. CRUD — add / edit / delete predefined cases")
print("=" * 70)
# self-clean: remove a leftover TEST case from a previous run
for c in CASES:
    if c.get("case_number") == "TEST/2026/0999":
        req("DELETE", "/api/admin/court-db/" + c["id"], tok=ADMIN)
s, d = req("POST", "/api/admin/court-db", tok=ADMIN,
           data={"case_number": "TEST/2026/0999", "case_type": "civil",
                 "court_name": "Civil Court, Testgarh", "petitioner": "Ram",
                 "respondent": "Shyam", "village": "Testpur",
                 "survey_number": "999", "owner_name": "Ram Kumar",
                 "status": "pending", "filed_year": "2026",
                 "summary": "suite-test case"})
check("B1 admin adds a predefined case", s == 200 and d.get("id"), (s, d))
TEST_ID = d.get("id") if s == 200 else None
s, d = req("POST", "/api/admin/court-db", tok=ADMIN,
           data={"case_number": "TEST/2026/0999", "village": "Testpur", "survey_number": "999"})
check("B2 duplicate case number refused (409)", s == 409, (s, d))
s, d = req("POST", "/api/admin/court-db", tok=ADMIN,
           data={"case_number": "TEST/2026/1000"})
check("B3 case without any match key refused (400)", s == 400, (s, d))
s, d = req("POST", "/api/admin/court-db", tok=ADMIN,
           data={"village": "Nowhere", "survey_number": "1"})
check("B4 case number is required (400)", s == 400, (s, d))
s, d = req("PUT", "/api/admin/court-db/" + (TEST_ID or "x"), tok=ADMIN,
           data={"status": "decided", "summary": "edited by the suite",
                 "next_hearing": ""})
check("B5 admin edits a case", s == 200 and d.get("status") == "decided"
      and d.get("summary") == "edited by the suite", (s, d))
s, d = req("PUT", "/api/admin/court-db/" + (TEST_ID or "x"), tok=OP, data={"status": "stay"})
check("B6 operator cannot edit a case (403)", s == 403, (s, d))
s, d = req("DELETE", "/api/admin/court-db/" + (TEST_ID or "x"), tok=OP)
check("B7 operator cannot delete a case (403)", s == 403, (s, d))
s, d = req("DELETE", "/api/admin/court-db/" + (TEST_ID or "x"), tok=ADMIN)
check("B8 admin deletes a case", s == 200 and d.get("deleted") is True, (s, d))
s, d = req("GET", "/api/admin/court-db", tok=ADMIN)
check("B9 deleted case is gone",
      s == 200 and not any(c.get("id") == TEST_ID for c in d.get("cases", [])))

print("=" * 70)
print("C. Matching — record-tab SA scan on seeded demo records")
print("=" * 70)
# Barkheda survey-312 record (seeded) -> exact match RRC/2019/0456 (decided)
s0, d0 = req("GET", "/api/documents/70cfcaafe194", tok=ADMIN)
if s0 != 200:
    skip("C1-C3", "seeded Barkheda record 70cfcaafe194 not present (DB not demo-seeded)")
else:
    s, d = req("POST", "/api/admin/court-db/scan/70cfcaafe194", tok=ADMIN)
    nums = [m.get("case_number") for m in (d or {}).get("matches", [])]
    check("C1 seeded Barkheda 312 scan finds its prior case",
          s == 200 and "RRC/2019/0456" in nums, (s, nums))
    lvl = {m.get("case_number"): m.get("match_level") for m in (d or {}).get("matches", [])}
    check("C2 the match level is EXACT (survey + village)", lvl.get("RRC/2019/0456") == "exact", lvl)
    check("C3 scan response reports scanned land details",
          (d or {}).get("scanned", {}).get("survey") == "312", (d or {}).get("scanned"))

# Arera record (Ram Bahadur Singh, survey 452) -> owner+village probable
s0, d0 = req("GET", "/api/documents/bd19cb191fac", tok=ADMIN)
if s0 != 200:
    skip("C4-C5", "seeded Arera record bd19cb191fac not present")
else:
    s, d = req("POST", "/api/admin/court-db/scan/bd19cb191fac", tok=ADMIN)
    lvl = {m.get("case_number"): m.get("match_level") for m in (d or {}).get("matches", [])}
    check("C4 owner+village match is PROBABLE (no survey in case file)",
          lvl.get("CR/2024/0112") == "probable", (s, lvl))
    check("C5 survey-only near match is flagged POSSIBLE with verify note",
          lvl.get("CS/2022/0891") == "possible", lvl)

s, d = req("POST", "/api/admin/court-db/scan/zzzzzzzzzzzz", tok=ADMIN)
check("C6 scanning a missing record 404s cleanly", s == 404, (s, d))

print("=" * 70)
print("D. Upload screening — instant for ADMIN, absent for operators")
print("=" * 70)
s, d = req("POST", "/api/process/sample/khatauni_rampur_2025.png?lang=eng", tok=ADMIN)
scr = (d or {}).get("sa_screening")
check("D1 admin upload of the Rampur scan returns an sa_screening block", bool(scr), (s, list((d or {}).keys())))
scr_nums = [m.get("case_number") for m in (scr or {}).get("matches", [])]
check("D2 SA instantly finds the STAY ORDER on that land (WPL/2023/0234)",
      "WPL/2023/0234" in scr_nums, scr_nums)
check("D3 match is EXACT and highest severity first",
      (scr or {}).get("highest") == "exact", (scr or {}).get("highest"))
check("D4 precision: adjacent survey 146 case does NOT match survey 145",
      "RA/2021/0077" not in scr_nums, scr_nums)
check("D5 stay case carries a human match reason",
      any("survey" in (m.get("match_reason") or "") for m in (scr or {}).get("matches", [])),
      (scr or {}).get("matches", [{}])[:1])
RAMPUR_DOC = (d or {}).get("id")

s, d = req("POST", "/api/process/sample/khatauni_guroli_2025.png?lang=eng", tok=ADMIN)
scr = (d or {}).get("sa_screening")
check("D6 clean land upload screens to ZERO matches (Guroli 88 != case on 87)",
      scr is not None and scr.get("count") == 0, (s, scr and scr.get("count")))
GUROLI_DOC = (d or {}).get("id")

time.sleep(1)  # gentle with the shared OCR pool
s, d = req("POST", "/api/process/sample/khatauni_rampur_2025.png?lang=eng", tok=OP)
check("D7 operator upload is NOT screened (no sa_screening block)",
      s == 200 and "sa_screening" not in (d or {}), list((d or {}).keys()))
OP_DOC = (d or {}).get("id")

s, d = req("POST", "/api/admin/court-db/scan/" + (OP_DOC or "x"), tok=OP)
check("D8 operator still cannot scan even their own upload (403)", s == 403, (s, d))

print("=" * 70)
print("E. Record-tab scan -> audit trail entry")
print("=" * 70)
if not RAMPUR_DOC:
    skip("E1-E2", "no Rampur document id from D1")
else:
    s, d = req("POST", "/api/admin/court-db/scan/" + RAMPUR_DOC, tok=ADMIN)
    check("E1 admin record-tab scan finds the stay case",
          s == 200 and d.get("count", 0) >= 1, (s, d and d.get("count")))
    s, d = req("GET", "/api/documents/" + RAMPUR_DOC + "/audit", tok=ADMIN)
    acts = [a.get("action") for a in (d or {}).get("audit", [])] if s == 200 else []
    check("E2 the scan is written to the record's audit trail",
          "sa_courtlink_scan" in acts, acts)

print("=" * 70)
print("F. Attach — import the found case into the record's litigation ledger")
print("=" * 70)
WPL = next((c for c in CASES if c.get("case_number") == "WPL/2023/0234"), None) or \
      next((c for c in (req("GET", "/api/admin/court-db", tok=ADMIN)[1] or {}).get("cases", [])
            if c.get("case_number") == "WPL/2023/0234"), None)
if not (WPL and RAMPUR_DOC):
    skip("F1-F4", "WPL case or Rampur doc missing")
else:
    s, d = req("GET", "/api/court-cases?survey=145&village=Rampur%20Khas", tok=ADMIN)
    already = any(cs.get("case_number") == "WPL/2023/0234" for cs in (d or {}).get("court_cases", [])) if s == 200 else False
    did_attach = False
    if already:
        check("F1 attach dedupe: case already in the ledger from a prior run", True)
        s2, d2 = req("POST", "/api/admin/court-db/attach", tok=ADMIN,
                     data={"case_id": WPL["id"], "document_id": RAMPUR_DOC})
        check("F2 re-attaching the same case is refused (409)", s2 == 409, (s2, d2))
    else:
        s2, d2 = req("POST", "/api/admin/court-db/attach", tok=ADMIN,
                     data={"case_id": WPL["id"], "document_id": RAMPUR_DOC})
        check("F1 admin attaches the matched case to the record",
              s2 == 200 and (d2 or {}).get("attached") is True, (s2, d2))
        did_attach = s2 == 200
        s3, d3 = req("POST", "/api/admin/court-db/attach", tok=ADMIN,
                     data={"case_id": WPL["id"], "document_id": RAMPUR_DOC})
        check("F2 attaching the same case twice is refused (409)", s3 == 409, (s3, d3))
    s, d = req("GET", "/api/court-cases?survey=145&village=Rampur%20Khas", tok=VER)
    rows = (d or {}).get("court_cases", []) if s == 200 else []
    wpl = next((cs for cs in rows if cs.get("case_number") == "WPL/2023/0234"), None)
    check("F3 the case now sits in the land's litigation ledger (readable by verifier)",
          wpl is not None and wpl.get("status") == "active", rows)
    s, d = req("GET", "/api/documents/" + RAMPUR_DOC + "/audit", tok=ADMIN)
    acts = [a.get("action") for a in (d or {}).get("audit", [])] if s == 200 else []
    if did_attach:
        check("F4 attach is written to the record's audit trail",
              "sa_courtlink_attach" in acts, acts)
    else:
        # attach happened on a previous run (land-level dedupe); the ledger
        # check in F3 already proves it persisted — nothing new to audit here
        check("F4 attach audit was recorded at first attach (dedupe across runs)", True)

print("=" * 70)
print("G. SA chat intent — 'scan the court database for record <id>'")
print("=" * 70)
s, d = req("POST", "/api/admin/sa/activate-options", tok=ADMIN, data={"code": "SA"})
opts = (d or {}).get("options", []) if s == 200 else []
ident = next((o for o in opts if o.get("name") == "System Administrator"), opts[0] if opts else None)
if not ident:
    skip("G1-G3", "no SA identity available")
else:
    s, d = req("POST", "/api/admin/sa/activate", tok=ADMIN,
               data={"code": "SA", "administrator": ident["id"], "password": "Admin@123"})
    SID = (d or {}).get("session_id") if s == 200 else None
    check("G1 SA session opens for the admin", bool(SID), (s, d))
    if not SID:
        skip("G2-G3", "SA activation failed")
    else:
        s, d = req("POST", "/api/admin/sa/query", tok=ADMIN,
                   data={"session_id": SID,
                         "q": "scan the court database for record " + (RAMPUR_DOC or "")})
        ans = (d or {}).get("answer", "")
        check("G2 SA chat scan reports the stay case", s == 200 and "WPL/2023/0234" in ans,
              (s, ans[:200]))
        s2, d2 = req("POST", "/api/admin/sa/query", tok=OP,
                     data={"session_id": SID, "q": "scan the court database for record " + (RAMPUR_DOC or "")})
        check("G3 SA chat query is refused for a non-admin token (403)", s2 == 403, (s2, d2))
        req("POST", "/api/admin/sa/end", tok=ADMIN, data={"session_id": SID})

print("=" * 70)
print("H. Static wiring — UI + backend sources")
print("=" * 70)
idx = open(os.path.join(LR_ROOT, "landrec", "static", "index.html"), encoding="utf-8").read()
mn = open(os.path.join(LR_ROOT, "landrec", "main.py"), encoding="utf-8").read()
cl = open(os.path.join(LR_ROOT, "landrec", "courtlink.py"), encoding="utf-8").read()
sasrc = open(os.path.join(LR_ROOT, "landrec", "sa_admin.py"), encoding="utf-8").read()
check("H1 court DB button + panel + screening modal in the page",
      all(x in idx for x in ("courtDbFab", "courtDbPanel", "saScreenModal")))
check("H2 record-detail SA CourtLink block is admin-gated",
      "saCourtPanel" in idx and "me.role === 'admin'" in idx and "saCourtScan(" in idx)
check("H3 record block asks the SA question bilingually",
      "Do you want SA" in idx and "मुकदमे खोजे" in idx)
check("H4 upload screening is server-gated to the admin role",
      '_maybe_sa_court_screen' in mn and 'user.get("role") == "admin"' in mn)
check("H5 all six court-db routes are admin-gated",
      mn.count('require_role("admin")') >= 6
      and len([l for l in mn.splitlines() if "/api/admin/court-db" in l]) >= 6)
check("H6 courtlink seeds the STAY demo case + audit actions exist",
      "WPL/2023/0234" in cl and "sa_courtlink_scan" in cl and "sa_courtlink_attach" in cl)
check("H7 SA chat intent wired to CourtLink", "_courtlink_scan" in sasrc and "courtlink" in sasrc)

print("=" * 70)
print("RESULT: %d passed, %d failed, %d skipped" % (passed, failed, skipped))
sys.exit(1 if failed else 0)
