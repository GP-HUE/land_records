"""Bulk OCR (v3.12): multi-scan intake queue with background processing.

Covers: RBAC (viewer locked out; operator/verifier/admin roles), batch
creation + validation limits, background processing of a REAL mixed batch
(English + auto-detect Hindi/Telugu/Tamil + a blank failure), per-item
fields/confidence/verdicts, duplicate flagging, admin-only SA screening on
items, clean-import into pending_review (never bypassing verification),
per-item force import, retry of failed items, per-user batch visibility,
CSV report, and static wiring.

Run: LR_BASE=http://127.0.0.1:8000 python3 tests/test_bulk_ocr.py
"""
import json
import os
import sys
import time

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


def req(method, path, tok=None, data=None, headers=None, raw=False, retries=4):
    return _http(BASE, method, path, tok=tok, data=data, headers=headers,
                 raw=raw, retries=retries)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


def multipart(form_fields, files):
    """files: [(filename, bytes)] -> (body_bytes, content_type) for field 'files'."""
    b = "----bulktest" + uuid_hex()
    out = []
    for k, v in form_fields.items():
        out.append(("--" + b + "\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (k, v)).encode())
    for fn, data in files:
        out.append(("--" + b + "\r\nContent-Disposition: form-data; name=\"files\"; "
                    "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n" % fn).encode()
                   + data + b"\r\n")
    out.append(("--" + b + "--\r\n").encode())
    return b"".join(out), "multipart/form-data; boundary=" + b


def uuid_hex():
    import uuid as _u
    return _u.uuid4().hex[:12]


def sample(fn):
    with open(os.path.join(SAMPLES, fn), "rb") as f:
        return f.read()


def make_bulktest_scan(survey):
    """A unique, clean PIL-rendered land record -> guaranteed importable."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    lines = ["JAMABANDI / KHATAUNI CERTIFICATE", "State: Madhya Pradesh",
             "District: Bhopal", "Tehsil: Huzur", "Village: Bulktestpur", "",
             "Khata Number: 9", "Khasra Number: 3/1",
             "Survey Number: %d" % survey, "Landowner Name: Bulk Test Owner",
             "Father's Name: Suite Owner", "", "Area: 2.0 acre",
             "Land Type: Irrigated Agricultural", "Ownership: Private", "",
             "Khatauni Year: 2024-25"]
    img = Image.new("RGB", (760, 40 + 44 * len(lines)), (252, 250, 244))
    d = ImageDraw.Draw(img)
    y = 20
    for i, ln in enumerate(lines):
        d.text((30, y), ln, font=font, fill=(25, 25, 25))
        y += 44
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def wait_batch(bid, tok, timeout=720):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s, d = req("GET", "/api/bulk/batches/" + bid, tok=tok, retries=2)
        if s == 200 and d.get("status") == "done":
            return d
        time.sleep(5)
    raise AssertionError("batch %s did not finish in %ss" % (bid, timeout))


def by_name(batch, frag):
    return next((it for it in batch.get("items", []) if frag in (it.get("filename") or "")), None)


ADMIN = login("admin@landrec.gov.in", "Admin@123")
OP = login("demo.operator@demo.local", "Demo@Operator1")
VER = login("demo.verifier@demo.local", "Demo@Verifier1")
VIEWER_EMAIL = "bulk.viewer@demo.local"
s, d = req("POST", "/api/auth/signup", data={"email": VIEWER_EMAIL, "password": "Viewer@123x",
                                             "full_name": "Bulk Viewer", "role": "viewer"})
VIEWER = login(VIEWER_EMAIL, "Viewer@123x") if s in (200, 201) else None
if VIEWER is None:
    s2, d2 = req("POST", "/api/auth/login", data={"email": VIEWER_EMAIL, "password": "Viewer@123x"})
    VIEWER = d2["token"] if s2 == 200 else None

print("=" * 70)
print("A. RBAC + batch validation")
print("=" * 70)
body, ctype = multipart({}, [("f.png", sample("khatauni_guroli_2025.png"))])
s, d = req("POST", "/api/bulk/batches", tok=VIEWER, data=body, headers={"Content-Type": ctype})
check("A1 viewer cannot create a batch (403)", s == 403, (s, d))
s, d = req("GET", "/api/bulk/batches", tok=VIEWER)
check("A2 viewer cannot list batches (403)", s == 403, (s, d))
s, d = req("GET", "/api/bulk/batches")
check("A3 anonymous cannot list batches (401)", s == 401, (s, d))
s, d = req("POST", "/api/bulk/batches/abc123def456/import", tok=OP, data={})
check("A4 importing into a missing batch fails cleanly (404/400)", s in (400, 404), (s, d))

bad, ctype = multipart({}, [("virus.exe", b"MZfake")])
s, d = req("POST", "/api/bulk/batches", tok=OP, data=bad, headers={"Content-Type": ctype})
check("A5 unsupported file type rejected (400)", s == 400, (s, d))
empty, ctype = multipart({}, [("empty.png", b"")])
s, d = req("POST", "/api/bulk/batches", tok=OP, data=empty, headers={"Content-Type": ctype})
check("A6 empty file rejected (400)", s == 400, (s, d))
many, ctype = multipart({}, [("f%02d.png" % i, sample("bad_blank_scan.png")) for i in range(26)])
s, d = req("POST", "/api/bulk/batches", tok=ADMIN, data=many, headers={"Content-Type": ctype})
check("A7 more than 25 files rejected (400)", s == 400, (s, d))

# O0: ensure a rampur record exists so the bulk dup-check is deterministic
s0, d0 = req("POST", "/api/process/sample/khatauni_rampur_2025.png?lang=eng", tok=OP)
check("A8 pre-upload a rampur record for the duplicate check", s0 == 200 and d0.get("id"), (s0, d0))

print("=" * 70)
print("B. Batch A (admin, eng, 8 mixed files incl. a blank scan)")
print("=" * 70)
SURVEY = 501 + int(time.time()) % 400
bulktest_bytes = make_bulktest_scan(SURVEY)
filesA = [("english_jamabandi_sample.png", sample("english_jamabandi_sample.png")),
          ("khatauni_rampur_2025.png", sample("khatauni_rampur_2025.png")),
          ("khatauni_guroli_2025.png", sample("khatauni_guroli_2025.png")),
          ("handwritten_mutation_sample.png", sample("handwritten_mutation_sample.png")),
          ("xfer_old_2019.png", sample("xfer_old_2019.png")),
          ("xfer_mutation_2021.png", sample("xfer_mutation_2021.png")),
          ("bad_blank_scan.png", sample("bad_blank_scan.png")),
          ("bulktest_%d.png" % SURVEY, bulktest_bytes)]
body, ctype = multipart({"lang": "eng", "doc_type": "land_record"}, filesA)
s, d = req("POST", "/api/bulk/batches", tok=ADMIN, data=body, headers={"Content-Type": ctype})
check("B1 admin creates an 8-file batch", s == 200 and d.get("id"), (s, str(d)[:200]))
BA = d.get("id") if s == 200 else None
check("B2 batch carries a human batch number + total", bool(d.get("batch_no")) and d.get("total") == 8, d.get("batch_no"))
if not BA:
    skip("B3-B13", "batch A was not created")
    BATCHA = None
else:
    BATCHA = wait_batch(BA, ADMIN)
    check("B3 all 8 items reached a terminal state", len(BATCHA.get("items", [])) == 8
          and all(it["status"] in ("done", "failed") for it in BATCHA["items"]))
    check("B4 batch counters add up (done+failed=total)",
          (BATCHA.get("done") or 0) + (BATCHA.get("failed") or 0) == BATCHA.get("total"))
    blank = by_name(BATCHA, "bad_blank")
    check("B5 the blank scan FAILED with an OCR diagnosis",
          bool(blank) and blank["status"] == "failed" and bool(blank.get("error")),
          blank and blank.get("error"))
    eng = by_name(BATCHA, "english_jamabandi")
    check("B6 English khatauni processed with fields + confidence",
          bool(eng) and eng["status"] == "done"
          and (eng.get("fields", {}).get("village") or {}).get("value") == "Barkheda"
          and (eng.get("mean_conf") or 0) > 40, eng and eng.get("mean_conf"))
    rampur = by_name(BATCHA, "khatauni_rampur")
    check("B7 rampur item extracted survey 145",
          bool(rampur) and (rampur.get("fields", {}).get("survey_number") or {}).get("value") == "145",
          rampur and rampur.get("fields", {}).get("survey_number"))
    scr = (rampur or {}).get("screening")
    check("B8 ADMIN batch item got an instant SA CourtLink screen (stay order found)",
          bool(scr) and scr.get("count", 0) >= 1
          and any(m.get("case_number") == "WPL/2023/0234" for m in scr.get("matches", [])),
          scr and scr.get("count"))
    check("B9 rampur item flagged as duplicate of the pre-uploaded record",
          bool(rampur) and bool((rampur.get("validation") or {}).get("duplicate_of")),
          rampur and (rampur.get("validation") or {}).get("duplicate_of"))
    bt = by_name(BATCHA, "bulktest_")
    check("B10 the generated clean scan processed with verdict valid",
          bool(bt) and bt["status"] == "done"
          and (bt.get("validation") or {}).get("verdict") == "valid", bt and bt.get("status"))
    guroli = by_name(BATCHA, "khatauni_guroli")
    check("B11 guroli item screened to ZERO court cases (clean land)",
          bool(guroli) and (guroli.get("screening") is None
                            or (guroli.get("screening") or {}).get("count") == 0),
          guroli and guroli.get("screening"))

print("=" * 70)
print("C. Import — clean rows go to pending_review, never auto-approved")
print("=" * 70)
if not BATCHA:
    skip("C1-C9", "no batch A")
else:
    bt = by_name(BATCHA, "bulktest_")
    s, d = req("POST", "/api/bulk/batches/" + BA + "/import", tok=ADMIN, data={})
    check("C1 import-all-clean imports the clean scan(s)", s == 200 and d.get("imported", 0) >= 1
          and isinstance(d.get("doc_ids"), list), (s, d))
    s2, d2 = req("GET", "/api/bulk/batches/" + BA, tok=ADMIN)
    bt2 = by_name(d2 or {}, "bulktest_") or {}
    check("C2 the clean item now points at its imported record", bool(bt2.get("imported_doc_id")), bt2.get("imported_doc_id"))
    doc_id = bt2.get("imported_doc_id")
    if not doc_id:
        skip("C3-C4", "clean item did not import")
    else:
        s3, d3 = req("GET", "/api/documents/" + doc_id, tok=ADMIN)
        check("C3 imported record is PENDING REVIEW (verification not bypassed)",
              s3 == 200 and d3.get("status") == "pending_review", (s3, d3.get("status")))
        s4, d4 = req("GET", "/api/documents/" + doc_id + "/audit", tok=ADMIN)
        acts = [a.get("action") for a in (d4 or {}).get("audit", [])]
        check("C4 import is recorded as bulk_import in the audit trail", "bulk_import" in acts, acts)
    s, d = req("POST", "/api/bulk/batches/" + BA + "/import", tok=ADMIN, data={})
    check("C5 running import-all-clean again imports nothing new", s == 200 and d.get("imported") == 0, d)
    blank = by_name(BATCHA, "bad_blank") or {}
    s, d = req("POST", "/api/bulk/items/" + blank.get("id", "x") + "/import", tok=ADMIN, data={"force": True})
    check("C6 a FAILED item cannot be imported even with force (400)", s == 400, (s, d))
    rampur = by_name(BATCHA, "khatauni_rampur") or {}
    s, d = req("POST", "/api/bulk/items/" + rampur.get("id", "x") + "/import", tok=ADMIN, data={})
    check("C7 non-clean row refused without force (400)", s == 400, (s, d))
    s, d = req("POST", "/api/bulk/items/" + rampur.get("id", "x") + "/import", tok=ADMIN, data={"force": True})
    check("C8 non-clean row imports WITH explicit force", s == 200 and d.get("imported") is True, (s, d))
    s, d = req("POST", "/api/bulk/items/" + rampur.get("id", "x") + "/import", tok=ADMIN, data={"force": True})
    check("C9 re-importing the same item refused (400)", s == 400, (s, d))

print("=" * 70)
print("D. Retry + per-user visibility")
print("=" * 70)
if not BATCHA:
    skip("D1-D2", "no batch A")
else:
    blank = by_name(BATCHA, "bad_blank") or {}
    s, d = req("POST", "/api/bulk/items/" + blank.get("id", "x") + "/retry", tok=ADMIN, data={"lang": "eng"})
    check("D1 failed item can be re-queued", s == 200 and d.get("requeued") is True, (s, d))
    eng = by_name(BATCHA, "english_jamabandi") or {}
    s, d = req("POST", "/api/bulk/items/" + eng.get("id", "x") + "/retry", tok=ADMIN, data={"lang": "eng"})
    check("D2 retrying a non-failed item refused (400)", s == 400, (s, d))
s, d = req("GET", "/api/bulk/batches/" + (BA or "x"), tok=OP)
check("D3 another user's batch detail is hidden (403)", s == 403, (s, d))
s, d = req("GET", "/api/bulk/batches", tok=OP)
check("D4 operator's own list does not contain the admin batch",
      s == 200 and not any(b.get("id") == BA for b in d.get("batches", [])), (s, len((d or {}).get("batches", []))))

print("=" * 70)
print("E. Batch B (operator, auto-detect, 4 Indic scans) + screening RBAC")
print("=" * 70)
filesB = [("hindi_khatauni_sample.png", sample("hindi_khatauni_sample.png")),
          ("telugu_pahani_sample.png", sample("telugu_pahani_sample.png")),
          ("tamil_patta_sample.png", sample("tamil_patta_sample.png")),
          ("bad_noise_scan.png", sample("bad_noise_scan.png"))]
body, ctype = multipart({"lang": "", "doc_type": "land_record"}, filesB)
s, d = req("POST", "/api/bulk/batches", tok=OP, data=body, headers={"Content-Type": ctype})
check("E1 operator creates an auto-detect batch", s == 200 and d.get("id"), (s, str(d)[:150]))
BB = d.get("id") if s == 200 else None
if not BB:
    skip("E2-E5", "batch B was not created")
else:
    BATCHB = wait_batch(BB, OP)
    hin = by_name(BATCHB, "hindi_khatauni")
    check("E2 auto-detect Hindi khatauni processed", bool(hin) and hin["status"] == "done", hin and hin["status"])
    telu = by_name(BATCHB, "telugu_pahani")
    check("E3 auto-detect Telugu pahani processed", bool(telu) and telu["status"] == "done", telu and telu["status"])
    # the Hindi khatauni is Barkheda survey 312 — an admin batch would screen
    # a hit against the court DB; an OPERATOR batch must NEVER be screened
    check("E4 OPERATOR batch items carry NO SA screening (admin-only feature)",
          bool(hin) and hin.get("screening") is None, hin and hin.get("screening"))
    s, d = req("GET", "/api/bulk/batches/" + BB, tok=ADMIN)
    check("E5 admin can view any batch", s == 200 and d.get("id") == BB, (s, d))

print("=" * 70)
print("F. Batch C (verifier) + CSV report")
print("=" * 70)
body, ctype = multipart({"lang": "eng", "doc_type": "land_record"},
                        [("xfer_partition_2022.png", sample("xfer_partition_2022.png"))])
s, d = req("POST", "/api/bulk/batches", tok=VER, data=body, headers={"Content-Type": ctype})
BC = d.get("id") if s == 200 else None
check("F1 verifier can create a batch", bool(BC), (s, str(d)[:120]))
if BC:
    BATCHC = wait_batch(BC, VER, timeout=360)
    it = (BATCHC.get("items") or [{}])[0]
    check("F2 verifier batch completes", BATCHC.get("status") == "done"
          and it.get("status") in ("done", "failed"), BATCHC.get("status"))
    check("F3 verifier batch items carry no SA screening",
          it.get("screening") is None, it.get("screening"))
if BA:
    s, d = req("GET", "/api/bulk/batches/" + BA + "/report.csv", tok=ADMIN, raw=True)
    head = (d or b"").decode(errors="replace").splitlines()
    check("F4 CSV report downloads with header + one row per file",
          s == 200 and head and "survey" in head[0] and len(head) >= 9, (s, head[:1]))
else:
    skip("F4", "no batch A")

print("=" * 70)
print("G. Static wiring")
print("=" * 70)
idx = open(os.path.join(LR_ROOT, "landrec", "static", "index.html"), encoding="utf-8").read()
mn = open(os.path.join(LR_ROOT, "landrec", "main.py"), encoding="utf-8").read()
bk = open(os.path.join(LR_ROOT, "landrec", "bulkocr.py"), encoding="utf-8").read()
check("G1 bulk tab button + section present", 'data-tab="bulk"' in idx and 'id="tab-bulk"' in idx and 'id="bulkStartBtn"' in idx)
check("G2 switchTab routes the bulk tab", "'upload','bulk'," in idx or '"upload","bulk",' in idx)
fns = ["bulkFilesPicked", "startBulkBatch", "loadBulkBatches", "openBulkBatch",
       "loadBulkDetail", "importBulkClean", "importBulkItem", "retryBulkItem", "bulkReportCsv"]
check("G3 all bulk UI functions defined", all(("function " + f) in idx for f in fns), [f for f in fns if ("function " + f) not in idx])
check("G4 seven bulk endpoints, all operator-gated",
      len([l for l in mn.splitlines() if "/api/bulk" in l and l.strip().startswith("@app.")]) >= 7
      and mn.count('Depends(require_role("operator"))') >= 7)
check("G5 worker resumes interrupted items on boot", "WHERE status='processing'" in bk and "status='queued'" in bk)
check("G6 imported records forced to pending_review (no verification bypass)",
      'status="pending_review"' in bk and "bulk_import" in bk)

print("=" * 70)
print("RESULT: %d passed, %d failed, %d skipped" % (passed, failed, skipped))
sys.exit(1 if failed else 0)
