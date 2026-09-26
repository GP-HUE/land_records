"""AI assistant v3.13.2 — court-database Q&A (SA) + area cross-check (both AIs).

Covers:
  * Normal AI: 📐 recorded-vs-computed map area in chat — per-record answers,
    survey-targeted answers, details line, whole-database mismatch screening,
    no-boundary guidance, and the "what is computed area?" explainer
  * SA AI: plain-language Q&A over the demo court database — counts, status
    filters (stay/pending/decided), village/party/court word match, courts
    covered, filed-year filter, hearing sort, full case card, unknown case
  * RBAC / routing safety: court-DB Q&A exists ONLY in SA mode (the normal
    assistant keeps answering from the portal's own litigation ledger), the
    classic SA intents (scan for record <id>, active-cases stats) are intact,
    and the area cross-check answers identically inside an SA session

Run: LR_BASE=http://127.0.0.1:8000 python3 tests/test_ai_area_courtdb.py
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ciutil import http as _http  # noqa: E402

BASE = os.environ.get("LR_BASE", "http://127.0.0.1:8000")
LR_ROOT = os.environ.get("LR_ROOT", "/home/user/land_records/extracted")
SAMPLES = os.path.join(LR_ROOT, "samples")

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


def req(method, path, tok=None, data=None, headers=None, retries=4):
    return _http(BASE, method, path, tok=tok, data=data, headers=headers, retries=retries)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


def ask(tok, q):
    s, d = req("POST", "/api/assistant/ask", tok, {"q": q})
    assert s == 200, "ask failed %s %s" % (s, str(d)[:200])
    return d


def multipart(field_name, form_fields, files):
    b = "----areacourt" + uuid.uuid4().hex[:12]
    out = []
    for k, v in form_fields.items():
        out.append(("--" + b + "\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (k, v)).encode())
    for fn, data in files:
        out.append(("--" + b + "\r\nContent-Disposition: form-data; name=\"%s\"; "
                    "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n"
                    % (field_name, fn)).encode() + data + b"\r\n")
    out.append(("--" + b + "--\r\n").encode())
    return b"".join(out), "multipart/form-data; boundary=" + b


def upload(tok, sample_name, form_extra):
    form = {"doc_type": "land_record", "lang": "eng"}
    form.update(form_extra or {})
    with open(os.path.join(SAMPLES, sample_name), "rb") as fh:
        data = fh.read()
    body, ctype = multipart("file", form, [(sample_name, data)])
    s, d = req("POST", "/api/process", tok=tok, data=body,
               headers={"Content-Type": ctype})
    assert s == 200, (s, str(d)[:300])
    return d


ADMIN = login("admin@landrec.gov.in", "Admin@123")

print("=" * 70)
print("A. Static wiring — v3.13.2 assistant upgrade")
print("=" * 70)
ai_src = open(os.path.join(LR_ROOT, "landrec", "ai_assistant.py"), encoding="utf-8").read()
sa_src = open(os.path.join(LR_ROOT, "landrec", "sa_admin.py"), encoding="utf-8").read()
mn_src = open(os.path.join(LR_ROOT, "landrec", "main.py"), encoding="utf-8").read()
qs_src = open(os.path.join(LR_ROOT, "QUICKSTART.txt"), encoding="utf-8").read()
check("A1 normal AI has the area cross-check machinery",
      all(x in ai_src for x in ("AREA_WORD", "AREA_FOCUS", "_area_screen",
                                "_doc_area", "_area_verdict", "AREA_MAX_PLOT_M2")))
check("A2 SA AI has the court-database Q&A machinery",
      all(x in sa_src for x in ("_court_db_query", "_CASE_NO_RE", "_COURT_Q_RE",
                                "_COURT_DB_HINT_RE", "_case_card", "COURT_DB_QUERY")))
check("A3 normal AI imports the extractor (shoelace ring area)",
      "from . import ai_support, common, extractor, risk, store" in ai_src)
import re as _re4
_m = _re4.search(r'APP_VERSION = "(\d+)\.(\d+)\.(\d+)"', mn_src)
check("A4 version bumped + changelog",
      _m is not None and tuple(int(x) for x in _m.groups()) >= (3, 14, 0)
      and "v3.14.0" in qs_src,
      _m.group(0) if _m else "no APP_VERSION")

print("=" * 70)
print("B. Normal AI — the 'what is computed area?' explainer")
print("=" * 70)
r = ask(ADMIN, "what is the computed area and the recorded area?")
check("B1 explainer answers recorded 📄 vs computed 📏",
      "RECORDED VS COMPUTED AREA" in r["answer"].upper()
      and "shoelace" in r["answer"].lower(), r["answer"][:160])
check("B2 explainer mentions the badge thresholds + guards",
      "5%" in r["answer"] and "494" in r["answer"], r["answer"][:200])

print("=" * 70)
print("C. Normal AI — area cross-check on a NEW-kind record (boundary: document)")
print("=" * 70)
up = upload(ADMIN, "khatauni_newkind_rampur_2025.png", {"doc_kind": "new"})
DOC = up.get("id") or up.get("document_id") or ""
coords = up.get("coordinates") or {}
check("C1 NEW-kind upload parsed 4/4 corners and set the boundary",
      bool(DOC) and coords.get("found") == 4 and coords.get("boundary_set") is True,
      (DOC, coords))
s, dd = req("GET", "/api/documents/" + DOC, tok=ADMIN)
det = dd if s == 200 else {}
REC_OR = ((det.get("fields") or {}).get("area") or {}).get("value", "")
print("      recorded area OCR'd as: %r" % REC_OR)

if not DOC:
    skip("C2-C9", "upload failed")
else:
    r = ask(ADMIN, "computed area of " + DOC)
    check("C2 per-record answer: recorded vs computed, ✓ green verdict",
          "Recorded" in r["answer"] and "Computed" in r["answer"]
          and "✓" in r["answer"] and "MATCHES" in r["answer"].upper(),
          r["answer"][:260])
    check("C3 computed acreage is the sample's ~1.8 acre plot (1.7-2.0)",
          any(("≈ %.2f acres" % a) in r["answer"]
              for a in [x / 100.0 for x in range(170, 201)]), r["answer"][:300])
    check("C4 answer mentions the corner-coordinate boundary source",
          "NEW-kind" in r["answer"] or "coordinate" in r["answer"].lower(),
          r["answer"][:260])

    r = ask(ADMIN, "details of " + DOC)
    check("C5 'details of <id>' appends the one-line map-area verdict",
          "Map area" in r["answer"] and "computed ≈" in r["answer"], r["answer"][-320:])

    r = ask(ADMIN, "kya survey 145 ka area sahi hai? area check karo")
    check("C6 survey-targeted: 'area of survey 145' answers for THAT record",
          DOC in str(r.get("results") or [{}][0].get("id", ""))
          and "✓" in r["answer"], r["answer"][:260])

    r = ask(ADMIN, "which records have an area mismatch?")
    check("C7 whole-database screening answers with the cross-check header",
          "Area cross-check" in r["answer"], r["answer"][:200])

    # Deliberate mismatch: same position, but ~4x the recorded plot area
    lat, lon = (coords.get("pin") or [23.3522, 77.3517])[:2]
    big = [[lat - 0.0018, lon - 0.0018], [lat - 0.0018, lon + 0.0018],
           [lat + 0.0018, lon + 0.0018], [lat + 0.0018, lon - 0.0018],
           [lat - 0.0018, lon - 0.0018]]
    s, d = req("POST", "/api/map/records/%s/boundary" % DOC, ADMIN,
               {"coordinates": big})
    check("C8 boundary override for the mismatch demo accepted", s == 200, (s, str(d)[:120]))
    r = ask(ADMIN, "which records have an area mismatch?")
    hit = DOC in r["answer"] or any(row.get("id") == DOC for row in (r.get("results") or []))
    check("C9 the oversized boundary is called out as a MISMATCH",
          "MISMATCH" in r["answer"].upper() and hit and "⚠" in r["answer"],
          r["answer"][:320])
    r = ask(ADMIN, "computed area of " + DOC)
    check("C10 per-record verdict flips to ⚠ MISMATCH (>5% off)",
          "MISMATCH" in r["answer"].upper() and "⚠" in r["answer"], r["answer"][:260])
    # restore the truthful boundary for later suites
    req("POST", "/api/map/records/%s/boundary" % DOC, ADMIN, {"coordinates": [
        [23.35175, 77.35135], [23.35175, 77.35205], [23.35265, 77.35205],
        [23.35265, 77.35135], [23.35175, 77.35135]]})

print("=" * 70)
print("D. Normal AI — no boundary yet -> guidance, not a fake number")
print("=" * 70)
up2 = upload(ADMIN, "english_jamabandi_sample.png", {"doc_kind": "old"})
DOC2 = up2.get("id") or up2.get("document_id") or ""
if not DOC2:
    skip("D1-D2", "second upload failed")
else:
    s, dd = req("GET", "/api/documents/" + DOC2, tok=ADMIN)
    has_bound = s == 200 and dd.get("boundary")
    if has_bound:
        skip("D1-D2", "old-kind upload unexpectedly has a boundary")
    else:
        r = ask(ADMIN, "computed area of " + DOC2)
        check("D1 says computed area unavailable (no boundary) and how to get one",
              "no map boundary" in r["answer"].lower()
              and "NEW" in r["answer"], r["answer"][:260])

print("=" * 70)
print("E. RBAC: court-DB Q&A exists ONLY in SA; normal AI keeps the ledger")
print("=" * 70)
r = ask(ADMIN, "how many cases are in the court database?")
check("E1 normal AI answers from the PORTAL ledger, not the court database",
      "COURT DATABASE" not in r["answer"].upper(), r["answer"][:200])
r = ask(ADMIN, "how many court cases are active?")
check("E2 classic ledger stats answer unchanged",
      "on record" in r["answer"].lower(), r["answer"][:160])

print("=" * 70)
print("F. SA AI — court cases database Q&A")
print("=" * 70)
s, d = req("POST", "/api/admin/sa/activate-options", tok=ADMIN, data={"code": "SA"})
opts = (d or {}).get("options", []) if s == 200 else []
ident = next((o for o in opts if o.get("name") == "System Administrator"),
             opts[0] if opts else None)
SID = None
if ident:
    s, d = req("POST", "/api/admin/sa/activate", tok=ADMIN,
               data={"code": "SA", "administrator": ident["id"], "password": "Admin@123"})
    SID = (d or {}).get("session_id") if s == 200 else None
if not SID:
    skip("F1-F10", "SA activation failed")
else:

    def saq(q):
        s, d = req("POST", "/api/admin/sa/query", tok=ADMIN,
                   data={"session_id": SID, "q": q})
        assert s == 200, (s, str(d)[:200])
        return d

    r = saq("how many cases are in the court database?")
    import re as _re
    m_n = _re.search(r"(\d+) case\(s\) on file", r["answer"])
    check("F1 database counts: >=6 seeded cases with status breakdown",
          "COURT DATABASE" in r["answer"].upper() and m_n and int(m_n.group(1)) >= 6
          and "stay" in r["answer"] and "pending" in r["answer"], r["answer"][:240])
    check("F2 answer closes with portal-ledger counts + scan tip",
          "ledger" in r["answer"].lower() and "scan the court database" in r["answer"],
          r["answer"][-240:])

    r = saq("show stay orders in the court database")
    check("F3 stay filter lists exactly the WPL stay order",
          "WPL/2023/0234" in r["answer"] and "STAY" in r["answer"]
          and "CS/2022/0891" not in r["answer"], r["answer"][:280])

    r = saq("court cases in Rampur Khas")
    check("F4 village word-match finds both Rampur Khas cases (145 + 146)",
          "WPL/2023/0234" in r["answer"] and "RA/2021/0077" in r["answer"],
          r["answer"][:320])

    r = saq("which courts are covered in the court database?")
    check("F5 courts-covered breakdown",
          "High Court of M.P." in r["answer"] and "District & Sessions Court" in r["answer"],
          r["answer"][:320])

    r = saq("is there any case against Kamla Devi Singh?")
    check("F6 party-name lookup finds the Sundarpur title suit",
          "CS/2022/0891" in r["answer"] and "Kamla Devi Singh" in r["answer"],
          r["answer"][:320])

    r = saq("details of case CS/2022/0891")
    check("F7 full case card for a case number",
          all(w in r["answer"] for w in ("COURT-DATABASE CASE", "Sunita Devi",
                                         "District & Sessions Court, Bhopal",
                                         "Sundarpur", "PENDING")), r["answer"][:320])
    r = saq("details of case XX/1999/0001")
    check("F8 unknown case number -> honest miss + tip",
          "not in the court database" in r["answer"], r["answer"][:200])

    r = saq("upcoming hearings in the court database")
    first_bullet = next((ln for ln in r["answer"].splitlines() if ln.startswith("•")), "")
    check("F9 hearing view sorted by next hearing (earliest first)",
          "next hearing" in r["answer"] and "CR/2024/0112" in first_bullet
          and "2026-10-01" in first_bullet, r["answer"][:400])

    r = saq("which cases were filed in 2022?")
    check("F10 filed-year filter",
          "CS/2022/0891" in r["answer"] and "WPL/2023/0234" not in r["answer"],
          r["answer"][:280])

    print("=" * 70)
    print("G. SA routing safety + the area cross-check inside SA")
    print("=" * 70)
    r = saq("how many court cases are active?")
    check("G1 ledger-stats question still routes to the PORTAL ledger",
          "on record" in r["answer"].lower()
          and "COURT DATABASE" not in r["answer"].upper(), r["answer"][:200])
    if DOC:
        r = saq("scan the court database for record " + DOC)
        check("G2 classic per-record scan intent intact (stay order found)",
              "WPL/2023/0234" in r["answer"], r["answer"][:240])
        r = saq("computed area of " + DOC)
        check("G3 area cross-check works inside SA (recorded vs computed ✓)",
              "MATCHES" in r["answer"].upper() and "✓" in r["answer"],
              r["answer"][:260])
        r = saq("which records have an area mismatch?")
        check("G4 whole-database area screening works inside SA too",
              "Area cross-check" in r["answer"], r["answer"][:200])
    else:
        skip("G2-G4", "no NEW-kind doc")
    req("POST", "/api/admin/sa/end", tok=ADMIN, data={"session_id": SID})

print("=" * 70)
print("RESULT: %d passed, %d failed, %d skipped" % (passed, failed, skipped))
sys.exit(1 if failed else 0)
