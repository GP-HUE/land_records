#!/usr/bin/env python3
"""E2E tests for SA (Superior Administrator) mode — v3.10.0.

The hidden secure-admin layer of the AI assistant:
  activate-options -> activate (admin password re-auth) -> sa/query
  (agentic: verify/reject/delete proposals + direct task delegation)
  -> approval center executes -> audit trail -> sa/report -> sa/end.
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

# ---------- gate keeping ----------
s, d = req("POST", "/api/admin/sa/activate-options", OP, {"code": "SA"})
check("operator is refused SA (403)", s == 403, (s, str(d)[:100]))

s, d = req("POST", "/api/admin/sa/activate-options", None, {"code": "SA"})
check("anonymous is refused (401)", s in (401, 403), (s, str(d)[:100]))

s, d = req("POST", "/api/admin/sa/activate-options", ADMIN, {"code": "WRONG"})
check("wrong activation code rejected", s == 400, (s, str(d)[:120]))

s, d = req("POST", "/api/admin/sa/activate-options", ADMIN, {"code": "SA"})
check("activation options list admins", s == 200 and len(d.get("options") or []) >= 1, (s, str(d)[:160]))
opts = {o["name"]: o["id"] for o in (d.get("options") or [])}
admin_id = opts.get("System Administrator") or list(opts.values())[0]

s, d = req("POST", "/api/admin/sa/activate", ADMIN,
           {"code": "SA", "administrator": "deadbeefdead", "password": "x"})
check("unknown admin identity rejected", s == 400, (s, str(d)[:120]))

s, d = req("POST", "/api/admin/sa/activate", ADMIN,
           {"code": "SA", "administrator": admin_id, "password": "wrong"})
check("wrong admin password refused (401)", s == 401, (s, str(d)[:120]))

s, d = req("POST", "/api/admin/sa/activate", ADMIN,
           {"code": "SA", "administrator": admin_id, "password": "Admin@123"})
check("SA session opens", s == 200 and d.get("session_id"), (s, str(d)[:160]))
SID = d.get("session_id", "")
check("session carries admin label", bool(d.get("admin")), str(d)[:120])


def saq(query):
    return req("POST", "/api/admin/sa/query", ADMIN, {"session_id": SID, "query": query})


# ---------- SA read-mode knowledge (delegates to the assistant) ----------
s, d = saq("how many loans are active?")
check("SA answers in SA mode", s == 200 and d.get("mode") == "SA" and "ACTIVE" in d.get("answer", ""), (s, str(d)[:160]))

s, d = saq("what can you do?")
check("SA lists capabilities", s == 200 and "Approval Center" in d.get("answer", "") and "assign" in d.get("answer", "").lower(), str(d)[:160])

# ---------- agentic: verify proposal + approve + audit ----------
s, d = req("POST", "/api/process/sample/hindi_khatauni_sample.png", OP)
check("upload pending doc A", s == 200, str(d)[:120])
docA = (d.get("document") or d).get("id")

s, d = saq("verify record %s" % docA)
check("SA verify intent drafts proposal", s == 200 and d.get("type") == "proposal" and d.get("action_card", {}).get("proposal_id"), (s, str(d)[:160]))
p1 = d.get("action_card", {}).get("proposal_id")
check("nothing executed before approval", True)  # checked via status below
s, d = req("GET", "/api/documents/%s" % docA, ADMIN)
check("record still pending_review after proposal", d.get("status") == "pending_review", d.get("status"))

s, d = saq("verify record 000000000000")
check("SA verify unknown id -> clean error", s == 404, (s, str(d)[:140]))

s, d = req("GET", "/api/ai-approvals?status=PENDING", ADMIN)
prop = next((p for p in d.get("proposals", []) if p.get("id") == p1), None)
check("proposal visible in Approval Center with before-state", bool(prop and prop.get("before_state")), str(prop)[:140])

s, d = req("POST", "/api/ai-approvals/%s/decide" % p1, ADMIN, {"decision": "approve"})
check("approve executes the verify proposal", s == 200 and d.get("status") == "EXECUTED", (s, str(d)[:160]))
s, d = req("GET", "/api/documents/%s" % docA, ADMIN)
check("record A now verified", d.get("status") in ("verified", "auto_approved"), d.get("status"))
s, d = req("GET", "/api/documents/%s/audit" % docA, ADMIN)
acts = [a["action"] for a in d.get("audit", [])]
check("audit shows verified + ai_proposal_executed", "verified" in acts and "ai_proposal_executed" in acts, acts)

# ---------- already-verified guard ----------
s, d = saq("verify record %s" % docA)
check("SA refuses to re-verify a verified record", s == 200 and "already" in d.get("answer", "").lower(), str(d)[:140])

# ---------- agentic: reject proposal (new action type) ----------
s, d = req("POST", "/api/process/sample/hindi_khatauni_sample.png", OP)
check("upload pending doc B", s == 200, str(d)[:120])
docB = (d.get("document") or d).get("id")

s, d = saq("reject record %s because owner name conflicts with certified copy" % docB)
check("SA reject intent drafts proposal with reason", s == 200 and d.get("type") == "proposal"
      and "conflict" in d.get("answer", "").lower(), (s, str(d)[:160]))
p2 = d.get("action_card", {}).get("proposal_id")

s, d = req("POST", "/api/ai-approvals/%s/decide" % p2, ADMIN, {"decision": "approve"})
check("reject proposal EXECUTED", s == 200 and d.get("status") == "EXECUTED", (s, str(d)[:160]))
s, d = req("GET", "/api/documents/%s" % docB, ADMIN)
check("record B now rejected with reason", d.get("status") == "rejected"
      and "conflict" in (d.get("reviewer_notes") or ""), (d.get("status"), d.get("reviewer_notes")))
s, d = req("GET", "/api/documents/%s/audit" % docB, ADMIN)
acts = [a["action"] for a in d.get("audit", [])]
check("audit shows record_rejected + ai_proposal_executed", "record_rejected" in acts and "ai_proposal_executed" in acts, acts)

s, d = req("POST", "/api/ai-approvals/%s/decide" % p2, ADMIN, {"decision": "approve"})
check("double approval refused", s in (400, 409), (s, str(d)[:120]))

s, d = saq("reject record %s" % docB)
check("SA refuses to re-reject", s == 200 and "already" in d.get("answer", "").lower(), str(d)[:140])

# ---------- agentic: delete proposal -> human rejects the proposal ----------
s, d = req("POST", "/api/process/sample/hindi_khatauni_sample.png", OP)
check("upload pending doc C", s == 200, str(d)[:120])
docC = (d.get("document") or d).get("id")
s, d = saq("delete record %s" % docC)
check("SA delete intent drafts delete proposal", s == 200 and d.get("action_card", {}).get("action_type") == "delete_document", (s, str(d)[:160]))
p3 = d.get("action_card", {}).get("proposal_id")
s, d = req("POST", "/api/ai-approvals/%s/decide" % p3, ADMIN, {"decision": "reject"})
check("human rejects the delete proposal", s == 200 and d.get("status") == "REJECTED", (s, str(d)[:140]))
s, d = req("GET", "/api/documents/%s" % docC, ADMIN)
check("record C untouched by rejected proposal", d.get("status") == "pending_review", d.get("status"))

# ---------- direct delegation: AI tasks ----------
s, d = saq("assign record %s to verification officer priority high" % docC)
check("SA direct task delegation", s == 200 and "TASK-" in d.get("answer", ""), (s, str(d)[:160]))
s, d = req("GET", "/api/ai-tasks", VER)
tasks = d.get("tasks", [])
check("verifier sees the delegated task", any(docC in str(t.get("record_id")) for t in tasks), str(tasks)[:140])

s, d = saq("assign record %s to villagers" % docC)
check("SA asks again for an unknown role", s == 400, (s, str(d)[:140]))

# ---------- report + audit of SA itself ----------
s, d = req("GET", "/api/admin/sa/report?session_id=%s" % SID, ADMIN)
evs = [e["event_type"] for e in d.get("events", [])]
check("SA report has the full event trail",
      s == 200 and {"SA_ACTIVATED", "QUERY", "PROPOSAL_CREATED", "TASK_ASSIGNED"} <= set(evs), (s, evs))

s, d = req("GET", "/api/audit?limit=50", ADMIN)
rows = d.get("audit") or d.get("rows") or ([] if isinstance(d, list) else d if isinstance(d, list) else [])
if isinstance(d, list):
    rows = d
check("global audit logs sa_activated", any(r.get("action") == "sa_activated" for r in rows), str(rows)[:140])

# ---------- end of session ----------
s, d = req("POST", "/api/admin/sa/end", OP, {"session_id": SID})
check("operator cannot end an SA session", s == 403, (s, str(d)[:100]))
s, d = saq("hello after end? (this query runs BEFORE end)")
check("SA query works right before end", s == 200, (s, str(d)[:120]))
s, d = req("POST", "/api/admin/sa/end", ADMIN, {"session_id": SID})
check("SA session ends cleanly", s == 200 and d.get("ended") is True, (s, str(d)[:120]))
s, d = saq("hello")
check("query after end -> 401", s == 401, (s, str(d)[:120]))
s, d = req("GET", "/api/admin/sa/report?session_id=%s" % SID, ADMIN)
check("report after end -> 401", s == 401, (s, str(d)[:120]))

s, d = req("POST", "/api/admin/sa/activate", ADMIN,
           {"code": "SA", "administrator": admin_id, "password": "Admin@123"})
check("a fresh SA session can be opened again", s == 200 and d.get("session_id") and d.get("session_id") != SID, (s, str(d)[:120]))
SID2 = d.get("session_id", "")
req("POST", "/api/admin/sa/end", ADMIN, {"session_id": SID2})

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("FAILED:", FAILURES)
sys.exit(1 if FAIL else 0)
