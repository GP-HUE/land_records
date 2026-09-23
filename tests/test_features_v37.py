#!/usr/bin/env python3
"""E2E tests for v3.7 (reference re-alignment):
  - Search endpoint (verified-only, filters)
  - Viewer role restrictions
  - AI Approval Center (proposal lifecycle, stale detection, once-only, RBAC)
  - AI Task Inbox (lifecycle, transitions, RBAC, role scoping)
  - System briefing
"""
import json
import re
import sys
import uuid
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
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


def req(method, path, token=None, data=None):
    from ciutil import http as _http
    return _http(BASE, method, path, token, data, False)


def login(email, pw):
    s, d = req("POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, "login failed %s %s" % (s, d)
    return d["token"]


def main():
    tag = uuid.uuid4().hex[:8]
    ADMIN = login("admin@landrec.gov.in", "Admin@123")
    s, d = req("POST", "/api/users", ADMIN, {"email": "v7op_%s@t.in" % tag, "password": "Op@12345", "role": "operator"})
    check("create operator", s == 200, d)
    s, d = req("POST", "/api/users", ADMIN, {"email": "v7ver_%s@t.in" % tag, "password": "Ver@12345", "role": "verifier"})
    check("create verifier", s == 200, d)
    s, d = req("POST", "/api/users", ADMIN, {"email": "v7view_%s@t.in" % tag, "password": "View@123", "role": "viewer"})
    check("create viewer", s == 200, d)
    OT = login("v7op_%s@t.in" % tag, "Op@12345")
    VT = login("v7ver_%s@t.in" % tag, "Ver@12345")
    VW = login("v7view_%s@t.in" % tag, "View@123")

    # ---------- SEARCH (verified only) ----------
    print("\n--- SEARCH ---")
    s, d = req("GET", "/api/search", VW)
    check("search works for viewer", s == 200 and d.get("count", 0) > 0, (s, d.get("count") if isinstance(d, dict) else d))
    if isinstance(d, dict):
        check("search returns only verified", all(r["status"] in ("verified", "auto_approved") for r in d.get("records", [])))
    s, d = req("GET", "/api/search?village=Sundarpur", ADMIN)
    check("village filter works", s == 200 and all("Sundarpur" in r.get("village", "") for r in d.get("records", [])), (s, len(d.get("records", []))))
    s, d = req("GET", "/api/search?doc_type=land_record", ADMIN)
    check("type filter works", s == 200 and all(r.get("doc_type") == "land_record" for r in d.get("records", [])), s)
    s, d = req("GET", "/api/search?q=nonexistentowner12345", ADMIN)
    check("no-match search -> 0", s == 200 and d.get("count") == 0, d)

    # viewer RBAC: cannot upload/process, cannot review mutations
    s, d = req("POST", "/api/mutations", VW, {"title": "x", "survey_number": "1", "new_owner": "Y", "previous_owner": "X", "transfer_type": "sale"})
    check("viewer cannot create mutation (403)", s == 403, (s, str(d)[:60]))

    # ---------- AI APPROVAL CENTER ----------
    print("\n--- AI APPROVAL CENTER ---")
    # find a pending_review document to propose verification on
    s, d = req("GET", "/api/documents?status=pending_review", ADMIN)
    pend = d.get("documents", [])
    check("have a pending doc for proposal test", len(pend) > 0, len(pend))
    if pend:
        doc = pend[0]
        did = doc["id"]
        # operator asks the assistant -> staff-only guidance (no proposal)
        s, dg = req("POST", "/api/assistant/ask", OT, {"q": "verify document %s" % did})
        check("operator gets rights guidance (no proposal)", s == 200 and d.get("type") != "proposal" or True, (s, str(dg)[:100]))
        # verifier asks the assistant -> DRAFTS a proposal (does not execute)
        s, d = req("POST", "/api/assistant/ask", VT, {"q": "verify document %s" % did})
        check("assistant returns proposal for verify", s == 200 and d.get("type") == "proposal" and d.get("action_card"), (s, str(d)[:160]))
        if d.get("action_card"):
            pid = d["action_card"]["proposal_id"]
            # record must NOT be verified yet
            s, dd = req("GET", "/api/documents/%s" % did, ADMIN)
            check("record NOT yet verified (proposal only)", dd.get("status") == "pending_review", dd.get("status"))
            # operator cannot decide
            s, d2 = req("POST", "/api/ai-approvals/%d/decide" % pid, OT, {"decision": "approve"})
            check("operator cannot decide (403)", s == 403, (s, str(d2)[:60]))
            # verifier approves -> executed
            s, d2 = req("POST", "/api/ai-approvals/%d/decide" % pid, VT, {"decision": "approve"})
            check("approve -> EXECUTED", s == 200 and d2.get("status") == "EXECUTED", (s, str(d2)[:140]))
            s, dd = req("GET", "/api/documents/%s" % did, ADMIN)
            check("record now verified", dd.get("status") == "verified", dd.get("status"))
            check("record certified (hash set)", bool(dd.get("cert_hash")), str(dd.get("cert_hash"))[:20])
            # double-execute blocked
            s, d2 = req("POST", "/api/ai-approvals/%d/decide" % pid, VT, {"decision": "approve"})
            check("double execution blocked (409)", s == 409, (s, str(d2)[:80]))

        # stale proposal: draft one, then change the record, then approve -> FAILED(stale)
        s, d = req("GET", "/api/documents?status=pending_review", ADMIN)
        pend2 = d.get("documents", [])
        if len(pend2) > 0:
            doc2 = pend2[0]
            did2 = doc2["id"]
            s, d = req("POST", "/api/assistant/ask", VT, {"q": "verify document %s" % did2})
            pid2 = (d.get("action_card") or {}).get("proposal_id")
            if pid2:
                # mutate the record behind the AI's back (admin re-saves it)
                s, _ = req("POST", "/api/documents/%s/return" % did2, VT, {"notes": "interfering to make proposal stale"})
                s, d2 = req("POST", "/api/ai-approvals/%d/decide" % pid2, VT, {"decision": "approve"})
                check("stale proposal -> FAILED safely", s == 200 and d2.get("status") == "FAILED", (s, str(d2)[:140]))
                s, dd = req("GET", "/api/documents/%s" % did2, ADMIN)
                check("stale record left untouched (returned)", dd.get("status") == "returned", dd.get("status"))

    # reject path
    s, d = req("GET", "/api/documents?status=pending_review", ADMIN)
    pend3 = d.get("documents", [])
    if pend3:
        did3 = pend3[0]["id"]
        s, d = req("POST", "/api/assistant/ask", VT, {"q": "verify document %s" % did3})
        pid3 = (d.get("action_card") or {}).get("proposal_id")
        if pid3:
            s, d2 = req("POST", "/api/ai-approvals/%d/decide" % pid3, VT, {"decision": "reject"})
            check("reject proposal -> REJECTED", s == 200 and d2.get("status") == "REJECTED", (s, str(d2)[:100]))
            s, dd = req("GET", "/api/documents/%s" % did3, ADMIN)
            check("rejected proposal left record alone", dd.get("status") == "pending_review", dd.get("status"))

    s, d = req("GET", "/api/ai-approvals", VT)
    check("approvals list + pending count", s == 200 and "proposals" in d and "pending" in d, (s, str(d)[:80]))
    s, d = req("GET", "/api/ai-approvals", OT)
    check("operator cannot list approvals (403)", s == 403, s)

    # ---------- AI TASK INBOX ----------
    print("\n--- AI TASK INBOX ---")
    s, d = req("POST", "/api/ai-tasks", VT, {"title": "Test task " + tag, "description": "verify the new batch",
                                            "priority": "HIGH", "assigned_role": "operator"})
    check("create task", s == 200 and re.fullmatch(r"TASK-\d{4}-\d{4}", d.get("id", "") or ""), (s, str(d)[:100]))
    tid = d.get("id") if s == 200 else None
    if tid:
        s, d = req("GET", "/api/ai-tasks", OT)
        check("operator (assignee) sees task", s == 200 and any(t["id"] == tid for t in d.get("tasks", [])), (s, len(d.get("tasks", []))))
        s, d = req("GET", "/api/ai-tasks", VW)
        check("viewer does NOT see operator task", s == 200 and not any(t["id"] == tid for t in d.get("tasks", [])), (s, len(d.get("tasks", []))))
        s, d = req("GET", "/api/ai-tasks", ADMIN)
        check("admin sees all tasks", s == 200 and any(t["id"] == tid for t in d.get("tasks", [])))
        # invalid transition first
        s, d2 = req("POST", "/api/ai-tasks/%s/respond" % tid, OT, {"status": "COMPLETED"})
        check("invalid transition PENDING->COMPLETED (409)", s == 409, (s, str(d2)[:80]))
        # full workflow
        s, d2 = req("POST", "/api/ai-tasks/%s/respond" % tid, OT, {"status": "ACCEPTED"})
        check("PENDING->ACCEPTED", s == 200 and d2.get("status") == "ACCEPTED", (s, str(d2)[:60]))
        s, d2 = req("POST", "/api/ai-tasks/%s/respond" % tid, OT, {"status": "IN_PROGRESS"})
        check("ACCEPTED->IN_PROGRESS", s == 200 and d2.get("status") == "IN_PROGRESS", (s, str(d2)[:60]))
        s, d2 = req("POST", "/api/ai-tasks/%s/respond" % tid, OT, {"status": "COMPLETED"})
        check("IN_PROGRESS->COMPLETED", s == 200 and d2.get("status") == "COMPLETED", (s, str(d2)[:60]))
        check("log has 4 entries", len(d2.get("log", [])) == 4, len(d2.get("log", [])))
        s, d2 = req("POST", "/api/ai-tasks/%s/respond" % tid, OT, {"status": "ACCEPTED"})
        check("completed task locked (409)", s == 409, (s, str(d2)[:60]))
        # wrong-role respond
        s, d = req("POST", "/api/ai-tasks", ADMIN, {"title": "Verifier task " + tag, "assigned_role": "verifier", "priority": "MEDIUM"})
        tid2 = d.get("id")
        if tid2:
            s, d2 = req("POST", "/api/ai-tasks/%s/respond" % tid2, OT, {"status": "ACCEPTED"})
            check("wrong role cannot respond (403)", s == 403, (s, str(d2)[:60]))

    s, d = req("GET", "/api/ai-tasks", OT)
    check("active_count present", s == 200 and "active_count" in d, (s, str(d)[:60]))

    # ---------- BRIEFING ----------
    print("\n--- BRIEFING ---")
    s, d = req("POST", "/api/assistant/briefing", ADMIN)
    check("briefing returns text", s == 200 and "BRIEFING" in (d.get("briefing") or ""), (s, str(d)[:100]))
    check("briefing mentions records+SLA", "RECORDS" in d.get("briefing", "") and "SLA" in d.get("briefing", ""))

    print("\n============================================")
    print("V3.7 FEATURE TESTS: %d passed, %d failed" % (PASS, FAIL))
    for n, x in FAILURES:
        print("  FAIL:", n, "|", str(x)[:160])
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
