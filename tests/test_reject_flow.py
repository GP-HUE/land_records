#!/usr/bin/env python3
"""E2E test for the new Reject action (v3.5.21) against the live server."""
import os
import json
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
    print("%s  %s%s" % (tag, name, ("  | " + str(extra)[:120]) if (extra and not cond) else ""))
    if cond:
        PASS += 1
    else:
        FAIL += 1


def req(method, path, token=None, data=None):
    from ciutil import http as _http
    return _http(BASE, method, path, token, data, False)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


ADMIN = login("admin@landrec.gov.in", "Admin@123")
tag = uuid.uuid4().hex[:8]
OP = "rej_op_%s@t.in" % tag
VER = "rej_ver_%s@t.in" % tag

s, d = req("POST", "/api/users", ADMIN, {"email": OP, "password": "Op@12345", "role": "operator"})
check("create operator", s == 200, d)
s, d = req("POST", "/api/users", ADMIN, {"email": VER, "password": "Ver@12345", "role": "verifier"})
check("create verifier", s == 200, d)

OT = login(OP, "Op@12345")
VT = login(VER, "Ver@12345")

# sample list -> pick one
s, d = req("GET", "/api/samples", OT)
check("sample list", s == 200 and d.get("samples"), d)
sample = d["samples"][0]

# operator processes sample
s, d = req("POST", "/api/process/sample/" + sample, OT)
check("process sample", s == 200, str(d)[:150])
doc = d["document"] if isinstance(d, dict) and "document" in d else d
doc_id = doc["id"]
status0 = doc.get("status")
print("   (doc %s initial status: %s)" % (doc_id, status0))

# RBAC: operator cannot reject
s, d = req("POST", "/api/documents/%s/reject" % doc_id, OT, {"notes": "try"})
check("operator reject -> 403", s == 403, (s, d))

# verifier, empty notes -> 400
s, d = req("POST", "/api/documents/%s/reject" % doc_id, VT, {"notes": "  "})
check("empty notes -> 400", s == 400, (s, d))

# verifier rejects with notes
s, d = req("POST", "/api/documents/%s/reject" % doc_id, VT, {"notes": "Fake document, owner mismatch with registry"})
check("verifier reject -> 200 rejected", s == 200 and d.get("status") == "rejected", (s, d))

# doc state persisted
s, d = req("GET", "/api/documents/%s" % doc_id, VT)
check("doc status persisted", s == 200 and d.get("status") == "rejected", (s, d.get("status") if isinstance(d, dict) else d))
check("reviewer notes kept", d.get("reviewer_notes") == "Fake document, owner mismatch with registry", d.get("reviewer_notes"))

# re-reject -> 409 (terminal)
s, d = req("POST", "/api/documents/%s/reject" % doc_id, VT, {"notes": "again"})
check("re-reject -> 409", s == 409, (s, d))

# rejected is TERMINAL: verify / return / submit / save-draft all blocked
s, d = req("POST", "/api/documents/%s/verify" % doc_id, VT, {"corrections": {}})
check("verify-after-reject -> 409", s == 409, (s, d))
s, d = req("POST", "/api/documents/%s/return" % doc_id, VT, {"notes": "x"})
check("return-after-reject -> 409", s == 409, (s, d))
s, d = req("POST", "/api/documents/%s/submit" % doc_id, OT, {})
check("submit-after-reject -> 409", s == 409, (s, d))
s, d = req("POST", "/api/documents/%s/save-draft" % doc_id, OT, {"fields": {}})
check("save-draft-after-reject -> 409", s == 409, (s, d))

# dashboard shows rejected >= 1
s, d = req("GET", "/api/dashboard", VT)
check("dashboard rejected >= 1", s == 200 and (d.get("rejected") or 0) >= 1, d.get("rejected"))

# audit trail has record_rejected for this doc
s, d = req("GET", "/api/audit/%s" % doc_id, VT)
acts = [e.get("action") for e in (d.get("entries") or d.get("audit") or [])] if isinstance(d, dict) else []
check("audit has record_rejected", "record_rejected" in acts, acts)

# queue no longer contains the rejected doc
s, d = req("GET", "/api/documents?status=pending_review", VT)
ids = [x["id"] for x in d.get("documents", [])]
check("rejected doc not in pending queue", doc_id not in ids)

# rejected filter works
s, d = req("GET", "/api/documents?status=rejected", VT)
ids = [x["id"] for x in d.get("documents", [])]
check("status=rejected filter returns doc", doc_id in ids, ids[:5])

print()
print("TOTAL: %d/%d passed" % (PASS, PASS + FAIL))
sys.exit(1 if FAIL else 0)
