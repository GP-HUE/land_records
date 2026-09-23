#!/usr/bin/env python3
"""E2E battery for the AI OCR Rescue + least-loaded officer routing (v3.8).
  A. blank scan   -> AI retries (lang hints + enhanced scans) -> unreadable
                     -> forced pending_review -> routed to least-loaded
                     officer -> AI task created -> audit entries
  B. least-loaded selection with a pre-loaded officer
  C. normal doc   -> NO rescue triggered, NO routing
  D. verify a routed doc -> routing cleared
  E. queue/loads + dashboard ai_routed + detail view fields
"""
import io
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import uuid

BASE = "http://127.0.0.1:8000"
SAMPLES = "/home/user/land_records/extracted/samples"
DB = "/home/user/land_records/extracted/data/landrec.db"
PASS = FAIL = 0
FAILURES = []


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


def upload(token, path):
    name = os.path.basename(path)
    data = open(path, "rb").read()
    boundary = "----b" + uuid.uuid4().hex
    body = io.BytesIO()
    body.write(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name)).encode())
    body.write(data)
    body.write(("\r\n--%s\r\nContent-Disposition: form-data; name=\"doc_type\"\r\n\r\n"
                "land_record\r\n--%s--\r\n" % (boundary, boundary)).encode())
    s, d = req("POST", "/api/process", token, body.getvalue(),
               headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    assert s == 200, "upload failed: %s %s" % (s, str(d)[:200])
    return d


def main():
    tag = uuid.uuid4().hex[:6]
    ADMIN = login("admin@landrec.gov.in", "Admin@123")
    for role, name in (("verifier", "air_verA"), ("verifier", "air_verB"), ("operator", "air_op")):
        s, d = req("POST", "/api/users", ADMIN,
                   {"email": "%s_%s@t.in" % (name, tag), "password": "Pass@1234", "role": role})
        check("create %s" % name, s == 200, d)
    A = "air_verA_%s@t.in" % tag
    B = "air_verB_%s@t.in" % tag
    VT_A = login(A, "Pass@1234")
    VT_B = login(B, "Pass@1234")
    OT = login("air_op_%s@t.in" % tag, "Pass@1234")

    # ============ A. BLANK SCAN -> RESCUE ATTEMPT -> ROUTED ============
    print("\n--- A. BLANK SCAN: AI rescue + routing ---")
    d = upload(OT, os.path.join(SAMPLES, "bad_blank_scan.png"))
    doc_id = d["id"]
    ai = d.get("ai") or {}
    check("upload response has ai block", bool(ai), str(d.keys()))
    check("AI attempted extra passes", ai.get("passes", 0) >= 2, ai.get("passes"))
    check("AI tried language + enhanced passes",
          any("language pass" in t for t in ai.get("tried", [])) and
          any("enhanced scan" in t for t in ai.get("tried", [])), ai.get("tried"))
    check("blank scan judged unreadable", ai.get("unreadable") is True, ai.get("diagnosis"))
    check("diagnosis mentions blank/dark/scan",
          any("blank" in x or "dark" in x for x in ai.get("diagnosis", [])), ai.get("diagnosis"))
    check("routed to an officer", bool(ai.get("routed_to")), ai.get("routed_to"))
    check("routed officer load reported", isinstance(ai.get("routed_load"), int), ai.get("routed_load"))

    s, dd = req("GET", "/api/documents/%s" % doc_id, OT)
    check("doc forced to pending_review (never auto-approved)", dd.get("status") == "pending_review", dd.get("status"))
    check("routed_to_name stored", bool(dd.get("routed_to_name")), dd.get("routed_to_name"))
    check("routing_reason stored", bool(dd.get("routing_reason")), dd.get("routing_reason"))

    s, d = req("GET", "/api/audit/%s" % doc_id, VT_A)
    acts = [a.get("action") for a in d.get("audit", [])]
    check("audit: ai_rescue entry", "ai_rescue" in acts, acts)
    check("audit: ai_routed entry", "ai_routed" in acts, acts)

    # AI task created in the verifier inbox
    s, d = req("GET", "/api/ai-tasks", VT_A)
    tasks = d.get("tasks", [])
    routed_task = [t for t in tasks if t.get("record_id") == doc_id]
    check("AI task created for routed doc", len(routed_task) == 1, len(tasks))
    if routed_task:
        check("task is HIGH priority", routed_task[0].get("priority") == "HIGH", routed_task[0].get("priority"))
        check("task title mentions AI-unreadable", "AI-unreadable" in (routed_task[0].get("title") or ""), routed_task[0].get("title"))
    # operator must NOT see the verifier task
    s, d = req("GET", "/api/ai-tasks", OT)
    check("operator does not see verifier task", not any(t.get("record_id") == doc_id for t in d.get("tasks", [])))

    # ============ B. LEAST-LOADED SELECTION ============
    print("\n--- B. LEAST-LOADED OFFICER SELECTION ---")
    # pre-load officer A with 3 routed pending docs directly in the DB
    c = sqlite3.connect(DB)
    for i in range(3):
        c.execute("""INSERT INTO documents (id, filename, status, routed_to, routed_to_name, routing_reason, routed_at)
                     VALUES (?,?,?,?,?,?,?)""",
                  ("preload%d%s" % (i, tag), "preload_%d.png" % i, "pending_review",
                   VT_A and (A or ""), "Officer A", "preload", time.time()))
    c.commit()
    # get officer A's user id
    c2 = sqlite3.connect(DB)
    uidA = c2.execute("SELECT id FROM users WHERE email=?", (A.lower(),)).fetchone()[0]
    uidB = c2.execute("SELECT id FROM users WHERE email=?", (B.lower(),)).fetchone()[0]
    for i in range(3):
        c2.execute("UPDATE documents SET routed_to=? WHERE id=?", (uidA, "preload%d%s" % (i, tag)))
    c2.commit()

    s, d = req("GET", "/api/queue/loads", VT_A)
    officers = {o["email"]: o for o in d.get("officers", [])}
    Al, Bl = A.lower(), B.lower()  # create_user lowercases emails
    a_load = officers.get(Al, {}).get("routed_pending", -1)
    b_load = officers.get(Bl, {}).get("routed_pending", -1)
    check("loads: officer A has >=3 pending (3 preloads + section-A doc)", a_load >= 3, officers.get(Al))
    check("loads: officer B has fewer pending than A", b_load < a_load, (b_load, a_load))
    # the expected recipient: minimum (load, email) across ALL officers
    # (ties broken alphabetically — many zero-load test officers exist)
    expected_off = min(officers.values(), key=lambda o: (o["load"], o["email"]))
    uid_expected = c2.execute("SELECT id FROM users WHERE email=?", (expected_off["email"],)).fetchone()[0]
    check("expected least-loaded officer is NOT the loaded officer A", uid_expected != uidA,
          (expected_off["email"], "vs A", Al))
    check("least_loaded flag on expected officer", expected_off.get("least_loaded") is True, expected_off)

    # clean the preloads' uploaded_by=None issue: they are routed docs, fine.
    d = upload(OT, os.path.join(SAMPLES, "bad_noise_scan.png"))
    doc2 = d["id"]
    ai2 = d.get("ai") or {}
    check("2nd unreadable doc routed", bool(ai2.get("routed_to")), ai2.get("routed_to"))
    s, dd2 = req("GET", "/api/documents/%s" % doc2, OT)
    check("routed to the least-loaded officer (not the loaded officer A)",
          dd2.get("routed_to") == uid_expected,
          (dd2.get("routed_to"), "expected", uid_expected, "got-name", dd2.get("routed_to_name")))
    # cleanup preloads
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM documents WHERE id LIKE ?", ("preload%s" % tag,))
    c.commit()
    c.close()

    # ============ C. NORMAL DOC -> NO RESCUE, NO ROUTING ============
    print("\n--- C. NORMAL DOC: NO INTERFERENCE ---")
    d = upload(OT, os.path.join(SAMPLES, "hindi_khatauni_sample.png"))
    doc3 = d["id"]
    ai3 = d.get("ai")
    s, dd3 = req("GET", "/api/documents/%s" % doc3, OT)
    check("good doc: no routing", not dd3.get("routed_to_name"), dd3.get("routed_to_name"))
    check("good doc: owner extracted", bool((dd3.get("fields", {}).get("owner_name") or {}).get("value")),
          (dd3.get("fields", {}).get("owner_name") or {}).get("value"))

    # ============ D. VERIFY ROUTED DOC -> ROUTING CLEARED ============
    print("\n--- D. VERIFY ROUTED DOC ---")
    s, d = req("POST", "/api/documents/%s/verify" % doc_id, VT_A, {"corrections": {}})
    check("verify routed doc", s == 200 and d.get("status") == "verified", (s, str(d)[:80]))
    s, dd = req("GET", "/api/documents/%s" % doc_id, OT)
    check("routing cleared after verify", not dd.get("routed_to") and not dd.get("routed_to_name"),
          (dd.get("routed_to"), dd.get("routed_to_name")))
    s, d = req("GET", "/api/audit/%s" % doc_id, VT_A)
    acts = [a.get("action") for a in d.get("audit", [])]
    check("audit: ai_routing_cleared", "ai_routing_cleared" in acts, acts[-5:])

    # ============ E. UI DATA ============
    print("\n--- E. QUEUE / DASHBOARD DATA ---")
    s, d = req("GET", "/api/documents?status=pending_review", VT_A)
    check("queue list exposes routing fields", all("routed_to_name" in x for x in d.get("documents", [])))
    s, d = req("GET", "/api/dashboard", OT)
    check("dashboard has ai_routed count", "ai_routed" in d, d.get("ai_routed"))

    # served page has the UI pieces
    s, page = req("GET", "/", ADMIN, raw=True)
    page = page.decode("utf-8", errors="replace") if isinstance(page, (bytes, bytearray)) else str(page)
    for name, needle in (
        ("upload AI rescue banner", "aiRescueBanner"),
        ("queue loads row", "queueLoadsRow"),
        ("queue AI-Routed chip", "AI-Routed →"),
        ("detail routing banner", "AI-Routed to:"),
        ("dashboard AI-routed card", "ai_routed"),
    ):
        check("UI: " + name, needle in page, needle)

    print("\n============================================")
    print("AI RESCUE TESTS: %d passed, %d failed" % (PASS, FAIL))
    for n, x in FAILURES:
        print("  FAIL:", n, "|", str(x)[:160])
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
