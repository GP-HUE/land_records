#!/usr/bin/env python3
"""THOROUGH system test battery for the Land Record System (v3.5).
Hits the live server over HTTP exactly like a browser/judge would.
"""
import concurrent.futures
import io
import json
import os
import subprocess
import sys
import time
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
    print("%s  %s%s" % (tag, name, ("  | " + str(extra)[:110]) if (extra and not cond) else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def req(method, path, token=None, data=None, headers=None, raw=False):
    url = BASE + path
    hdrs = dict(headers or {})
    body = None
    if data is not None:
        if isinstance(data, (dict, list)):
            body = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
        else:
            body = data
            hdrs.setdefault("Content-Type", "application/octet-stream")
    r = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    if token and not hdrs.get("Authorization"):
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=600) as resp:
            b = resp.read()
            return resp.status, (b if raw else (json.loads(b) if b else None))
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b) if b else None
        except Exception:
            return e.code, (b if raw else b[:200].decode(errors="replace"))


def login(email, pw):
    login_pace()
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    return s, (d or {}).get("token") if isinstance(d, dict) else None


# ---------- pacing: the server rate-limits 10 logins/min/IP and
# 10 OCR/min/IP (by design). The test suite must pace itself or it
# would false-trip the very limits it is testing. ----------
_ocr_calls = []
_login_calls = []


def login_pace():
    now = time.time()
    _login_calls[:] = [t for t in _login_calls if now - t < 60]
    if len(_login_calls) >= 8:
        time.sleep(61 - (now - _login_calls[0]) + 1)
        _login_calls[:] = []
    _login_calls.append(time.time())


def ocr_pace():
    now = time.time()
    _ocr_calls[:] = [t for t in _ocr_calls if now - t < 60]
    if len(_ocr_calls) >= 9:
        time.sleep(61 - (now - _ocr_calls[0]) + 1)
        _ocr_calls[:] = []
    _ocr_calls.append(time.time())


ADMIN, ADMIN_PW = "admin@landrec.gov.in", "Admin@123"
S, ADMIN_T = login(ADMIN, ADMIN_PW)
check("A1 admin login", S == 200 and ADMIN_T, "status=%s" % S)
AT = ADMIN_T

print("cooldown 62s (clearing rate-limit windows)...")
time.sleep(62)
# ================= PHASE 1: SECURITY & AUTH =================
login_pace(); S, D = req("POST", "/api/auth/login", data={"email": ADMIN, "password": "wrong"})
check("A2 wrong password -> 401", S == 401, "status=%s" % S)

login_pace(); S, D = req("POST", "/api/auth/login", data={"email": "nobody@nowhere.xyz", "password": "X"})
check("A3 unknown email -> 401", S == 401, "status=%s" % S)

S, D = req("GET", "/api/samples")
check("A8 no token -> 401", S == 401, "status=%s" % S)

S, D = req("GET", "/api/samples", token="garbage.token.here")
check("A9 garbage token -> 401", S == 401, "status=%s" % S)

# ?token= URL channel (the preview-proxy path)
S, D = req("GET", "/api/samples?token=" + AT)
check("A10 ?token= URL channel works", S == 200 and isinstance(D, dict), "status=%s" % S)

UN1 = "t1_%s@landrec.gov.in" % uuid.uuid4().hex[:8]
S, D = req("POST", "/api/auth/signup", data={"email": UN1, "password": "Weak", "full_name": "T1"})
check("A4a signup weak password rejected", S == 400, "status=%s" % S)

S, D = req("POST", "/api/auth/signup", data={"email": UN1, "password": "T@st1234", "full_name": "T1"})
check("A4b signup ok", S in (200, 201), "status=%s d=%s" % (S, str(D)[:80]))

S, D = req("POST", "/api/auth/signup", data={"email": UN1, "password": "T@st1234", "full_name": "dup"})
check("A5 duplicate email rejected", S == 400, "status=%s" % S)

S, T1 = login(UN1, "T@st1234")
check("A6 new user can login", S == 200 and T1, "status=%s" % S)

S, D = req("GET", "/api/auth/me", token=T1)
check("A6b new user role = operator", isinstance(D, dict) and D.get("user", {}).get("role") == "operator",
      str(D)[:80])

# role enforcement
S, D = req("GET", "/api/users", token=T1)
check("R1 operator cannot list users", S == 403, "status=%s" % S)

S, D = req("GET", "/api/dashboard", token=T1)
check("R4 operator can see dashboard", S == 200, "status=%s" % S)

# admin creates a verifier
UV = "t2_%s@landrec.gov.in" % uuid.uuid4().hex[:8]
S, D = req("POST", "/api/users", token=AT, data={"email": UV, "password": "V@st1234",
                                                 "full_name": "Verifier", "role": "verifier"})
check("A12 admin creates verifier", S in (200, 201), "status=%s %s" % (S, str(D)[:80]))
S, TV = login(UV, "V@st1234")
check("A12b verifier can login", S == 200 and TV, "status=%s" % S)

# password policy on change
S, D = req("POST", "/api/auth/change-password", token=T1, data={"current_password": "T@st1234", "new_password": "short"})
check("A11a weak new password rejected", S == 400, "status=%s" % S)
S, D = req("POST", "/api/auth/change-password", token=T1, data={"current_password": "T@st1234", "new_password": "N3w@Pass"})
check("A11b password changed", S == 200, "status=%s %s" % (S, str(D)[:60]))
S, D = login(UN1, "T@st1234")
check("A11c old password now rejected", S == 401, "status=%s" % S)
S, T1 = login(UN1, "N3w@Pass")
check("A11d new password works", S == 200 and T1, "status=%s" % S)

# admin reset password
S, D = req("POST", "/api/users/%s/reset-password" % (D.get("id") if False else ""), token=AT) if False else (200, None)
_s, users = req("GET", "/api/users", token=AT)
user_rows = users.get("users", []) if isinstance(users, dict) else []
uv_row = [u for u in user_rows if u["email"] == UV][0]
S, D = req("POST", "/api/users/%s/reset-password" % uv_row["id"], token=AT, data={"password": "R3set@Pass"})
check("A13 admin reset password", S == 200, "status=%s %s" % (S, str(D)[:80]))
S, TV = login(UV, "R3set@Pass")
check("A13b reset password works", S == 200 and TV, "status=%s" % S)

# deactivate user
UD = "t3_%s@landrec.gov.in" % uuid.uuid4().hex[:8]
req("POST", "/api/users", token=AT, data={"email": UD, "password": "D@st1234", "full_name": "Deact",
                                          "role": "operator"})
_s, users = req("GET", "/api/users", token=AT)
user_rows = users.get("users", []) if isinstance(users, dict) else []
ud_row = [u for u in user_rows if u["email"] == UD][0]
S, D = req("PATCH", "/api/users/%s" % ud_row["id"], token=AT, data={"is_active": False})
check("A15a deactivate user", S == 200, "status=%s" % S)
S, D = login(UD, "D@st1234")
check("A15b deactivated user cannot login", S in (401, 403), "status=%s" % S)

# export / import round-trip
S, EXP = req("GET", "/api/users/export", token=AT)
ok_exp = isinstance(EXP, dict) and EXP.get("format") == "landrec-users" and len(EXP.get("users", [])) >= 3
check("A14a export accounts", S == 200 and ok_exp, "status=%s" % S)
S, D = req("POST", "/api/users/import", token=AT, data=EXP)
check("A14b import accounts", S == 200 and isinstance(D, dict) and D.get("total", 0) >= 3,
      "status=%s %s" % (S, str(D)[:80]))

# lockout (dedicated email)
UL = "lock_%s@landrec.gov.in" % uuid.uuid4().hex[:8]
req("POST", "/api/auth/signup", data={"email": UL, "password": "L@ck1234", "full_name": "L"})
for _ in range(5):
    login_pace(); req("POST", "/api/auth/login", data={"email": UL, "password": "nope"})
S, D = login(UL, "L@ck1234")  # correct password, but account now locked
check("A7 lockout after 5 fails (correct pw rejected while locked)", S in (401, 429),
      "status=%s" % S)

# ================= PHASE 2: UPLOAD & PROCESSING =================
SAMP_DIR = os.path.join(os.environ.get("LR_ROOT", "/home/user/land_records/extracted"), "samples")

def multipart_upload(path, filename, field="file"):
    boundary = "----lrtest" + uuid.uuid4().hex
    fn = os.path.basename(path)
    body = (("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n" % (boundary, field, fn)).encode()
            + open(path, "rb").read() + ("\r\n--%s--\r\n" % boundary).encode())
    return req("POST", "/api/process", token=AT, data=body,
               headers={"Content-Type": "multipart/form-data; boundary=" + boundary})

ocr_pace(); time.sleep(0.2)
S, D = multipart_upload(SAMP_DIR + "/tamil_patta_sample.png", "tamil_patta_sample.png")
ok = isinstance(D, dict) and "id" in D
check("U1 upload image via multipart", S == 200 and ok, "status=%s %s" % (S, str(D)[:100]))
DOC_A = D.get("id") if ok else None

S, D = req("POST", "/api/process", token=AT, data=b"hello",
           headers={"Content-Type": "application/octet-stream"})
# wrong content-type but valid bytes -> treated as file 'upload' w/ no ext -> 400
check("U2 unsupported/no-extension rejected", S in (400, 422), "status=%s %s" % (S, str(D)[:80]))

S, D = req("POST", "/api/process", token=AT, data=b"",
           headers={"Content-Type": "multipart/form-data; boundary=x"})
check("U3 empty file rejected", S in (400, 422), "status=%s" % S)

big = os.path.join("/tmp", "bigtest.png")
with open(big, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + os.urandom(21 * 1024 * 1024))
S, D = multipart_upload(big, "bigtest.png")
check("U4 >20MB rejected (413)", S == 413, "status=%s" % S)

ocr_pace()
corrupt = os.path.join("/tmp", "corrupt.png")
with open(corrupt, "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + os.urandom(5000))
t0 = time.time()
S, D = multipart_upload(corrupt, "corrupt.png")
dt = time.time() - t0
check("U5 corrupt PNG -> clean error, no hang", S == 500 and dt < 30,
      "status=%s dt=%.1fs %s" % (S, dt, str(D)[:80]))

# duplicate: same file twice
ocr_pace()
S, D = multipart_upload(SAMP_DIR + "/telugu_pahani_sample.png", "dup_test.png")
dup1 = D.get("id") if isinstance(D, dict) else None
ocr_pace()
S2, D2 = multipart_upload(SAMP_DIR + "/telugu_pahani_sample.png", "dup_test2.png")
dup2 = D2.get("id") if isinstance(D2, dict) else None
check("U7a same file uploads twice -> two docs", S == 200 and S2 == 200 and dup1 and dup2 and dup1 != dup2,
      "%s / %s" % (dup1, dup2))
warn = isinstance(D2, dict) and any(i.get("field") == "duplicate" for i in D2.get("validation", {}).get("issues", []))
check("U7b second copy flagged as possible duplicate", warn, str(D2.get("validation", {}))[:100] if isinstance(D2, dict) else "")

# parallel uploads (stream integrity under load)
ocr_pace()
def par(i):
    ocr_pace()
    return multipart_upload(SAMP_DIR + "/english_jamabandi_sample.png", "par%d.png" % i)
with concurrent.futures.ThreadPoolExecutor(4) as ex:
    results = list(ex.map(par, range(4)))
all_ok = all(r[0] == 200 and isinstance(r[1], dict) and r[1].get("id") for r in results)
ids = [r[1].get("id") for r in results if isinstance(r[1], dict)]
check("U8 4 parallel uploads all succeed, distinct docs", all_ok and len(set(ids)) == 4,
      "statuses=%s ids=%s" % ([r[0] for r in results], ids))

# path traversal on sample endpoint
S, D = req("POST", "/api/process/sample/..%2F..%2F..%2Fetc%2Fpasswd", token=AT)
check("U9 path traversal blocked (400 or 404, nothing served)", S in (400, 404),
      "status=%s %s" % (S, str(D)[:60]))

ocr_pace()
S, D = req("POST", "/api/process/sample/english_jamabandi_sample.png", token=AT)
check("U10 sample processing via API", S == 200 and isinstance(D, dict) and len(D.get("fields", {})) >= 8,
      "status=%s fields=%s" % (S, len(D.get("fields", {})) if isinstance(D, dict) else 0))

# ================= PHASE 3: DOCUMENT LIFECYCLE + LEARNING =================
S, DOCS = req("GET", "/api/documents", token=AT)
ids_all = [d["id"] for d in (DOCS.get("documents", []) if isinstance(DOCS, dict) else (DOCS or []))]
check("D0 documents endpoint 200", S == 200, "status=%s" % S)
check("D1 documents list contains uploads", DOC_A in ids_all and dup1 in ids_all,
      "have=%s" % len(ids_all))

S, DD = req("GET", "/api/documents/%s" % dup1, token=AT)
check("D2 document detail has fields", S == 200 and isinstance(DD, dict) and len(DD.get("fields", {})) >= 5,
      "status=%s" % S)

# operator cannot verify
S, D = req("POST", "/api/documents/%s/verify" % dup1, token=T1, data={"corrections": {}})
check("D4 operator cannot verify", S == 403, "status=%s" % S)

S, D = req("POST", "/api/documents/%s/verify" % dup1, token=TV, data={"corrections": {"village": "రामపూర్ (confirmed)"}})
check("D3 verifier verifies with correction", S == 200, "status=%s %s" % (S, str(D)[:80]))

S, DD = req("GET", "/api/documents/%s" % dup1, token=AT)
st = DD.get("status") if isinstance(DD, dict) else None
val = DD.get("fields", {}).get("village", {}).get("value", "") if isinstance(DD, dict) else ""
check("D3b status=verified + correction saved", st == "verified" and "confirmed" in val,
      "status=%s val=%s" % (st, val))

S, AUD = req("GET", "/api/audit", token=AT)
acts = [a["action"] for a in AUD.get("audit", [])] if isinstance(AUD, dict) else []
check("D5 audit trail has document_created + verified",
      "document_created" in acts and "verified" in acts, str(set(acts))[:80])

# THE LEAK TEST via API (team's exact scenario)
ocr_pace()
S, L1 = req("POST", "/api/process/sample/hindi_khatauni_sample.png?lang=hin", token=AT)
o1 = L1.get("fields", {}).get("owner_name", {}).get("value", "") if isinstance(L1, dict) else ""
S, D = req("POST", "/api/documents/%s/verify" % L1.get("id", ""), token=TV,
           data={"corrections": {"owner_name": o1 + " लीकटेस्ट"}})
ocr_pace()
S2, L2 = req("POST", "/api/process/sample/hindi_khatauni_sample.png?lang=hin", token=AT)
o2 = L2.get("fields", {}).get("owner_name", {}).get("value", "") if isinstance(L2, dict) else ""
check("D7 LEAK TEST: 2nd doc owner != 1st doc's correction", o2 == o1 and "लीकटेस्ट" not in o2,
      "o1=%s o2=%s" % (o1, o2))

# corrections list + delete
S, COR = req("GET", "/api/corrections", token=AT)
rows = COR.get("corrections", []) if isinstance(COR, dict) else []
check("D8a corrections recorded (>=2 rows)", len(rows) >= 2, "rows=%d" % len(rows))
if rows:
    rid = rows[0]["id"]
    S, D = req("DELETE", "/api/corrections/%d" % rid, token=AT)
    S2, COR2 = req("GET", "/api/corrections", token=AT)
    check("D8b correction deleted", S == 200 and len(COR2.get("corrections", [])) == len(rows) - 1,
          "status=%s" % S)

# ================= PHASE 4: CRASH RECOVERY =================
S, SYS = req("GET", "/api/system", token=AT)
wpid = SYS.get("ocr_worker", {}).get("worker_pid") if isinstance(SYS, dict) else None
check("C0 worker running pre-test", S == 200 and wpid, "worker_pid=%s" % wpid)

def kill_worker_after(delay):
    time.sleep(delay)
    try:
        os.kill(wpid, 9)
        return True
    except Exception:
        return False

t0 = time.time()
kt = concurrent.futures.ThreadPoolExecutor(1)
kt.submit(kill_worker_after, 3.0)
ocr_pace()
S, D = req("POST", "/api/process/sample/hindi_khatauni_sample.png?lang=hin", token=AT)
dt = time.time() - t0
kt.shutdown()
check("C1 worker killed mid-OCR -> request still succeeds (respawn+retry)",
      S == 200 and isinstance(D, dict) and D.get("fields"), "status=%s dt=%.1fs" % (S, dt))

ocr_pace()
S, D = req("POST", "/api/process/sample/hindi_khatauni_sample.png?lang=hin", token=AT)
check("C2 next request after crash also works", S == 200 and D.get("fields"), "status=%s" % S)

# ================= PHASE 5: RATE LIMIT (last — consumes the budget) =================
codes = []
for i in range(11):
    s, _ = req("POST", "/api/process/sample/english_jamabandi_sample.png?lang=eng", token=AT)
    codes.append(s)
    time.sleep(0.15)
check("U6 rate limiter triggers on burst (>=1 x 429, rest 200)",
      codes.count(429) >= 1 and set(codes) <= {200, 429}, "codes=%s" % codes)

# ================= PHASE 6: SYSTEM & HEADERS =================
S, SYS = req("GET", "/api/system", token=AT)
check("S1 system status healthy", S == 200 and SYS.get("ocr_worker", {}).get("worker_alive"),
      str(SYS.get("ocr_worker"))[:80] if isinstance(SYS, dict) else "")

S, L = req("GET", "/api/languages", token=AT)
check("S2 languages endpoint", S == 200 and "eng" in L.get("installed", []), str(L)[:60])

S, DASH = req("GET", "/api/dashboard", token=AT)
check("S3 dashboard stats", S == 200 and DASH.get("total", 0) >= 5 and "avg_ocr_confidence" in DASH,
      str(DASH)[:80] if isinstance(DASH, dict) else "")

S, P = req("GET", "/api/ocr-progress", token=AT)
check("S4 ocr-progress endpoint", S == 200 and "stage" in (P or {}), str(P)[:60])

# security headers
r = urllib.request.urlopen(BASE + "/", timeout=10)
h = {k.lower(): v for k, v in r.headers.items()}
check("S5 security headers (nosniff+referrer+CSP)",
      h.get("x-content-type-options") == "nosniff" and "referrer-policy" in h
      and "content-security-policy" in h, str(h)[:100])

# keepalive (the tab-count mechanism)
S, D = req("POST", "/api/keepalive", data={})
check("S6 keepalive endpoint", S == 200, "status=%s" % S)

print()
print("=" * 60)
print("TOTAL: %d passed, %d failed  of %d checks" % (PASS, FAIL, PASS + FAIL))
if FAILURES:
    print("FAILED CHECKS:")
    for n, e in FAILURES:
        print("  - %s  [%s]" % (n, str(e)[:100]))
print("=" * 60)
sys.exit(1 if FAIL else 0)
