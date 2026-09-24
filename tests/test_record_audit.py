#!/usr/bin/env python3
"""E2E tests for the per-record Audit Trail (v3.10.0):

GET /api/documents/{id}/audit returns the hash-chained, tamper-evident
history of actions ON one record (like the land History, but for actions):
upload -> draft -> submit -> verify/certify -> PDF downloads, oldest first.
"""
import os
import sys

BASE = os.environ.get("LR_BASE", "http://127.0.0.1:8000")
PASS = FAIL = 0
FAILURES = []


def check(name, cond, extra=""):
    global PASS, FAIL
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                        ("  | " + str(extra)[:140]) if (extra and not cond) else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(name)


def req(method, path, token=None, data=None):
    from ciutil import http as _http
    return _http(BASE, method, path, token, data, False)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


ADMIN = login("admin@landrec.gov.in", "Admin@123")
OP = login("demo.operator@demo.local", "Demo@Operator1")
VER = login("demo.verifier@demo.local", "Demo@Verifier1")

# ------- build a full lifecycle on one record -------
s, d = req("POST", "/api/process/sample/english_jamabandi_sample.png", OP)
check("operator uploads a sample", s == 200, str(d)[:140])
doc = (d.get("document") or d).get("id")

s, d = req("POST", "/api/documents/%s/save-draft" % doc, OP, {"fields": {}})
check("draft saved", s in (200, 204), str(d)[:120])

s, d = req("POST", "/api/documents/%s/submit" % doc, OP, {})
check("submitted for verification", s in (200, 204), str(d)[:120])

s, d = req("POST", "/api/documents/%s/verify" % doc, VER, {"corrections": {"owner_name": "Audit Trail Verifier"}})
check("verifier saves & verifies", s in (200, 204), str(d)[:140])

from ciutil import http as _http
s, d = _http(BASE, "GET", "/api/documents/%s/certified-pdf" % doc, VER, None, True)
check("certified pdf downloads (audited)", s == 200 and d[:4] == b"%PDF", s)

# ------- the audit trail itself -------
s, d = req("GET", "/api/documents/%s/audit" % doc, OP)
check("operator can read the trail", s == 200, (s, str(d)[:120]))
rows = d.get("audit", [])
acts = [r.get("action") for r in rows]

check("upload event present", "document_created" in acts, acts)
check("draft event present", "draft_saved" in acts, acts)
check("submit event present", "submitted_for_verification" in acts, acts)
check("verified + certified events present", "verified" in acts and "certified" in acts, acts)
check("pdf download audited", "certified_copy_downloaded" in acts, acts)
check("correction recorded with field detail", any(r.get("action") == "correction" and "owner_name" in (r.get("detail") or "") for r in rows), str(rows)[:200])

ts = [r.get("ts") or 0 for r in rows]
check("chronological order (oldest first)", ts == sorted(ts), ts[:5])
check("every row carries user + hash prefix", all(r.get("username") and r.get("hash") for r in rows), str(rows)[:160])
check("hash prefix is 16 chars", all(len(r.get("hash", "")) == 16 for r in rows), [r.get("hash") for r in rows][:3])
check("actor identities differ by role", any(r.get("username") == "demo.operator@demo.local" for r in rows)
      and any(r.get("username") == "demo.verifier@demo.local" for r in rows), [r.get("username") for r in rows][:6])

# ------- guards -------
s, d = req("GET", "/api/documents/deadbeef0000/audit", ADMIN)
check("unknown record -> 404", s == 404, (s, str(d)[:100]))

s, d = req("GET", "/api/documents/%s/audit" % doc, None)
check("anonymous -> 401", s in (401, 403), s)

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("FAILED:", FAILURES)
sys.exit(1 if FAIL else 0)
