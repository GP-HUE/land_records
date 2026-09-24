#!/usr/bin/env python3
"""E2E tests for the v3.10.0 assistant knowledge upgrade:

the offline AI assistant must now answer questions about every recently
added feature — loans/encumbrance, court cases, fraud risk, PDFs, backup,
real map, mutations, per-record audit trail, SA mode — plus new live-stats
and land-screening searches, without breaking the classic intents.
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


def ask(q):
    s, d = req("POST", "/api/assistant/ask", ADMIN, {"q": q})
    assert s == 200, "ask failed %s %s" % (s, str(d)[:200])
    return d


# ---------------- feature explainers (help) ----------------
r = ask("what is the encumbrance module?")
check("loan explainer", "🏦" in r["answer"] and "EC Report" in r["answer"], r["answer"][:120])

r = ask("kya is zameen par loan hai? how to settle it")
check("loan explainer (Hindi keywords)", "settle" in r["answer"].lower() or "🏦" in r["answer"], r["answer"][:120])

r = ask("what do court case statuses mean?")
check("court-case explainer with all statuses",
      all(w in r["answer"] for w in ("active", "decided", "withdrawn", "settled")), r["answer"][:120])

r = ask("how does the fraud risk engine work?")
check("fraud-risk explainer lists flags",
      "OWNER_CHANGE_NO_MUTATION" in r["answer"] and "SALE_DURING_ENCUMBRANCE" in r["answer"], r["answer"][:120])

r = ask("how do I download a certified pdf?")
check("PDF explainer covers certified copy + QR", "Certified Copy" in r["answer"] and "QR" in r["answer"], r["answer"][:120])

r = ask("what is the EC report?")
check("PDF explainer covers EC report", "13" in r["answer"] and ("Sub-Registrar" in r["answer"] or "encumbrance" in r["answer"].lower()), r["answer"][:120])

r = ask("explain backup and restore")
check("backup explainer", "manifest.json" in r["answer"] and "safety copy" in r["answer"], r["answer"][:120])

r = ask("how do I use the real map and boundaries?")
check("map explainer", "🗺" in r["answer"] or "Real Map" in r["answer"], r["answer"][:120])

r = ask("what is the mutation process?")
check("mutation explainer incl. safety gate", "SAFETY GATE" in r["answer"] or "ENCUMBRANCE" in r["answer"], r["answer"][:120])

r = ask("where is the audit trail of a record?")
check("per-record audit-trail explainer", "📜" in r["answer"] or "Audit Trail" in r["answer"], r["answer"][:120])

r = ask("what is SA mode?")
check("SA explainer", "Superior Administrator" in r["answer"] and "Approval Center" in r["answer"], r["answer"][:120])

r = ask("hello")
check("greeting advertises the new modules", "fraud risk" in r["answer"].lower() and "SA" in r["answer"], r["answer"][:140])

# ---------------- live statistics ----------------
r = ask("how many loans are active?")
check("loan stats", "ACTIVE" in r["answer"] and "loan" in r["answer"].lower(), r["answer"][:120])

r = ask("how many court cases are active?")
check("court-case stats", "court case(s)" in r["answer"], r["answer"][:120])

r = ask("how many mutations are there?")
check("mutation stats", "Mutation" in r["answer"] or "mutation" in r["answer"], r["answer"][:120])

r = ask("how many tasks are open?")
check("task stats", "AI Tasks" in r["answer"], r["answer"][:120])

r = ask("how many proposals are pending?")
check("proposal stats", "Approval Center" in r["answer"], r["answer"][:120])

# ---------------- land screening searches ----------------
r = ask("which records are risky?")
check("risk screening runs over all lands", "screened" in r["answer"], r["answer"][:160])
check("risk screening returns land rows", len(r.get("results") or []) >= 1, str(r.get("results"))[:160])

r = ask("which records have an active loan?")
check("loan screening", "active loan" in r["answer"].lower() and len(r.get("results") or []) >= 1, r["answer"][:160])

r = ask("which records have a court case?")
check("litigation screening", ("active case" in r["answer"].lower() or "LITIGATION" in r["answer"]), r["answer"][:160])

# ---------------- per-record risk via id ----------------
s, d = req("GET", "/api/documents/0f8f73f343bd", ADMIN)
if s == 200:
    r = ask("risk of 0f8f73f343bd")
    check("risk of <seed fraud-demo record>", "Verdict" in r["answer"] and "ENCUMBERED" in r["answer"], r["answer"][:160])
    check("risk answer names the active bank", "Punjab National Bank" in r["answer"], r["answer"][:160])
else:
    r = ask("risk of 0f8f73f343bd")
    check("risk of unknown record -> graceful", "couldn't find" in r["answer"], r["answer"][:160])

# ---------------- briefing includes the new modules ----------------
s, d = req("POST", "/api/assistant/briefing", ADMIN, {})
brf = d.get("briefing", "")
check("briefing includes loans + court cases", s == 200 and "LOANS" in brf and "COURT CASES" in brf, brf[:200])

# ---------------- regressions: classic intents still work ----------------
r = ask("how many verified records?")
check("classic stats intent", "verified record(s)" in r["answer"], r["answer"][:100])

r = ask("how does verification work?")
check("classic help intent", "Verification workflow" in r["answer"], r["answer"][:100])

r = ask("show pending records")
check("classic search intent", r.get("results") is not None and ("matching record" in r["answer"].lower() or "pending" in r["answer"].lower()), r["answer"][:100])

r = ask("zqxwvtnl unheard phrase")
check("unknown fallback mentions new topics", "couldn't match" in r["answer"] and "encumbrance" in r["answer"].lower(), r["answer"][:160])

print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    print("FAILED:", FAILURES)
sys.exit(1 if FAIL else 0)
