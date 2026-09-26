"""SA — Superior Administrator mode (hidden secure layer of the AI assistant).

Typed 'SA' (or 'SA <code>') into the offline AI assistant starts a two-step
activation: the server returns the registered administrator identities, the
human picks one and re-enters THAT administrator's password (verified
server-side only).  On success a short-lived SA session is created.

In SA mode the assistant can plan & coordinate work across the portal:
  • draft proposals (verify / reject / delete a record) — each consequential
    action still stops at the AI Approval Center, is re-checked against its
    before-state, executes exactly once, and lands in the audit trail
  • delegate work directly as AI Tasks (non-destructive coordination)
  • answer everything the normal assistant knows (delegated to ai_assistant)

Every event of the session is logged (sa_events table) and viewable via the
📊 SA Activity Report.  Sessions live in memory only: an app restart ends
every SA session — deliberate, so a dormant elevated session cannot survive.

100% local & rule-based: no internet, no external AI, no data leaves the PC.
"""
import os
import re
import secrets
import time

from . import ai_assistant, auth, courtlink, store

# Activation code: "SA" by default; a deployment can change it via env
# (then the trigger typed into the chat becomes 'SA <code>').
SA_CODE = os.environ.get("LR_SA_CODE", "SA")
# No clock-based expiry per requirement: an SA session lasts until the admin
# explicitly ends it ('exit SA' / ⏻ End SA), LOGS OUT (logout() ends all of
# that user's SA sessions), or the app restarts (sessions are memory-only).
SESSION_TTL = None

_sessions = {}  # sid -> {user_id, name, email, created}


class SAError(Exception):
    """Raised for any SA failure; message is user-safe."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# activation / session lifecycle
# --------------------------------------------------------------------------
def activate_options(code, current_user):
    """Step 1: validate the activation code, return admin identities."""
    if (code or "").strip() != SA_CODE:
        raise SAError("Invalid SA activation code.", 400)
    admins = [u for u in store.list_users()
              if u.get("role") == "admin" and u.get("is_active")]
    if not admins:
        raise SAError("No active administrator account exists on this system.", 400)
    return {"options": [{"id": u["id"], "name": u.get("full_name") or u["email"]}
                        for u in admins]}


def activate(code, admin_id, password, current_user):
    """Step 2: re-authenticate the chosen administrator and open a session."""
    if (code or "").strip() != SA_CODE:
        raise SAError("Invalid SA activation code.", 400)
    user = store.get_user(admin_id)
    if not user or user.get("role") != "admin" or not user.get("is_active"):
        raise SAError("Unknown administrator identity.", 400)
    if not auth.verify_password(password or "", user["salt"], user["password_hash"]):
        raise SAError("Password incorrect — SA activation refused.", 401)
    sid = secrets.token_hex(12)
    _sessions[sid] = {"user_id": user["id"],
                      "name": user.get("full_name") or user["email"],
                      "email": user["email"],
                      "created": time.time()}
    store.sa_event(sid, "SA_ACTIVATED",
                   "SA session opened for %s (triggered by %s)"
                   % (_sessions[sid]["name"], current_user.get("email") or ""))
    store.audit(None, current_user.get("id", ""), current_user.get("email", ""),
                "sa_activated", "SA session opened as %s" % _sessions[sid]["name"])
    # no expiry: the session ends on 'end SA', logout, or app restart
    return {"session_id": sid, "admin": _sessions[sid]["name"],
            "expires_at": None, "policy": "until logout / exit SA / app restart"}


def _require(session_id):
    s = _sessions.get(session_id or "")
    if not s:
        raise SAError("SA session not found or ended — type 'SA' to activate again.", 401)
    return s


def end_sessions_for_user(user_id):
    """End every SA session of this user — called from logout so an elevated
    mode never survives the login session it belongs to."""
    for sid in [s for s, v in _sessions.items() if v["user_id"] == user_id]:
        store.sa_event(sid, "SA_ENDED", "admin logged out — session closed")
        store.audit(None, user_id, _sessions[sid]["email"], "sa_ended",
                    "SA session ended by logout")
        _sessions.pop(sid, None)


def end(session_id, current_user=None):
    s = _require(session_id)
    store.sa_event(session_id, "SA_ENDED", "SA session closed")
    store.audit(None, s["user_id"], s["email"], "sa_ended", "SA session closed")
    _sessions.pop(session_id, None)
    return {"ended": True, "admin": s["name"]}


def report(session_id, current_user=None):
    s = _require(session_id)
    return {"admin": s["name"], "events": store.sa_events_for(session_id)}


# --------------------------------------------------------------------------
# SA brain — agentic intents first, otherwise the full normal assistant
# --------------------------------------------------------------------------
def _doc_or_error(did):
    doc = store.get_document(did)
    if not doc:
        raise SAError("I couldn't find document #%s. Check the 12-character ID in the "
                      "Records tab and try again." % did, 404)
    return doc


def _sa_user(s):
    return {"id": s["user_id"], "email": s["email"], "full_name": s["name"], "role": "admin"}


def _propose_verify(did, sid, s):
    import json as _json
    doc = _doc_or_error(did)
    if doc.get("status") in ("verified", "auto_approved"):
        return {"type": "chat", "results": [], "mode": "SA",
                "answer": "Record #%s is already %s — nothing to propose. "
                          "Ask me 'details of %s' or 'risk of %s' instead."
                          % (did, doc["status"].replace("_", " "), did, did)}
    fields = _json.loads(doc.get("extracted_json") or "{}")
    p = store.create_proposal(
        "verify_document", did,
        "SA proposal: verify record #%s as-is (no field changes)" % did,
        "Record #%s — %s (status: %s)" % (did, doc.get("filename", ""), doc.get("status") or ""),
        {"status": doc.get("status"), "fields": fields},
        {"status": "verified"},
        "Ordered via SA (Superior Administrator) session", _sa_user(s))
    store.sa_event(sid, "PROPOSAL_CREATED",
                   "verify_document on #%s -> proposal #%s" % (did, p["id"]))
    return {"type": "proposal", "results": [], "mode": "SA",
            "answer": ("🛡️ SA: I drafted proposal #%s — VERIFY record #%s (%s) exactly as it is. "
                       "Nothing has changed yet: it waits in the 📋 AI Approval Center, is "
                       "re-checked against its before-state, executes exactly once and is "
                       "hash-chained into the audit trail on approval."
                       % (p["id"], did, doc.get("filename", ""))),
            "action_card": {"proposal_id": p["id"], "action_type": p["action_type"],
                            "action_description": "Verify record #%s as-is" % did,
                            "target_display": p["target_display"],
                            "label": "📋 Open Approval Center"}}


def _propose_reject(did, reason, sid, s):
    doc = _doc_or_error(did)
    if doc.get("status") == "rejected":
        return {"type": "chat", "results": [], "mode": "SA",
                "answer": "Record #%s is already REJECTED — nothing left to propose." % did}
    reason = (reason or "").strip() or "Rejected on SA (Superior Administrator) instruction"
    p = store.create_proposal(
        "reject_document", did,
        "SA proposal: REJECT record #%s — %s" % (did, reason),
        "Record #%s — %s (status: %s)" % (did, doc.get("filename", ""), doc.get("status") or ""),
        {"status": doc.get("status")},
        {"status": "rejected", "reason": reason},
        "Ordered via SA (Superior Administrator) session", _sa_user(s))
    store.sa_event(sid, "PROPOSAL_CREATED",
                   "reject_document on #%s -> proposal #%s (%s)" % (did, p["id"], reason))
    return {"type": "proposal", "results": [], "mode": "SA",
            "answer": ("🛡️ SA: I drafted proposal #%s — REJECT record #%s with reason "
                       "'%s'. Rejection permanently closes the record, so it needs your "
                       "approval in the 📋 AI Approval Center first — nothing has changed yet."
                       % (p["id"], did, reason)),
            "action_card": {"proposal_id": p["id"], "action_type": p["action_type"],
                            "action_description": "Reject record #%s — %s" % (did, reason),
                            "target_display": p["target_display"],
                            "label": "📋 Open Approval Center"}}


def _propose_delete(did, sid, s):
    doc = _doc_or_error(did)
    p = store.create_proposal(
        "delete_document", did,
        "SA proposal: permanently delete record #%s" % did,
        "Record #%s — %s (status: %s)" % (did, doc.get("filename", ""), doc.get("status") or ""),
        {"status": doc.get("status")},
        {"status": "deleted"},
        "Ordered via SA (Superior Administrator) session", _sa_user(s))
    store.sa_event(sid, "PROPOSAL_CREATED",
                   "delete_document on #%s -> proposal #%s" % (did, p["id"]))
    return {"type": "proposal", "results": [], "mode": "SA",
            "answer": ("⚠ SA: I drafted proposal #%s — PERMANENTLY DELETE record #%s (%s). "
                       "Approving removes the scan file (the audit trail is kept forever). "
                       "Nothing has changed yet — approve it in the 📋 AI Approval Center."
                       % (p["id"], did, doc.get("filename", ""))),
            "action_card": {"proposal_id": p["id"], "action_type": p["action_type"],
                            "action_description": "Permanently delete record #%s" % did,
                            "target_display": p["target_display"],
                            "label": "📋 Open Approval Center"}}


_ROLE_WORDS = {
    "verifier": "verifier", "verification officer": "verifier", "verification": "verifier",
    "operator": "operator", "data officer": "operator", "data entry": "operator",
    "admin": "admin", "administrator": "admin",
}


def _assign_task(did, qn, sid, s):
    """Delegation is coordination, not record mutation — runs directly."""
    doc = _doc_or_error(did)
    role = None
    for word, r in _ROLE_WORDS.items():
        if re.search(r"\b%s\b" % re.escape(word), qn):
            role = r
            break
    if not role:
        raise SAError("Assign to which role? Say e.g. 'assign #%s to verification officer' "
                      "or 'assign #%s to operator'." % (did, did), 400)
    m = re.search(r"\bpriority\s+(low|medium|high|critical)\b", qn)
    priority = (m.group(1).upper() if m else "HIGH")
    title = "SA directive: review record #%s (%s)" % (did, doc.get("filename", "")[:40])
    desc = ("Assigned by SA (Superior Administrator) session on behalf of %s. "
            "Record #%s — %s, status %s. Please review and take the appropriate action."
            % (s["name"], did, doc.get("filename", ""), (doc.get("status") or "").replace("_", " ")))
    t = store.create_task(title, desc, priority, role, _sa_user(s), record_id=did)
    store.sa_event(sid, "TASK_ASSIGNED", "%s -> %s (record #%s)" % (t["id"], role, did))
    return {"type": "action", "results": [], "mode": "SA",
            "answer": ("🛡️ SA: delegated. Task %s created for the %s role (priority %s) on "
                       "record #%s — it appears in the '🤖 AI Tasks' inbox of every %s. "
                       "Coordination like this runs directly; record-changing actions always "
                       "wait in the AI Approval Center."
                       % (t["id"], role, priority, did, role))}


def _courtlink_scan(did, sid, s):
    """SA CourtLink from the chat: 'scan the court database for record <id>'.
    Read-only here — the record's audit trail is written only when the scan
    is run from the record page; the session itself logs to sa_events."""
    _doc_or_error(did)
    res = courtlink.screen_document(
        did, {"id": s["user_id"], "email": s.get("email") or ""}, source="sa_chat")
    store.sa_event(sid, "COURTLINK", "%s -> %s prior case(s)" % (did, res["count"]))
    if not res["count"]:
        txt = ("⚖️ I screened record #%s against the demo court database — "
               "✅ NO prior court case found for this land "
               "(survey %s, village %s)." % (
                   did, res["scanned"].get("survey") or "—",
                   res["scanned"].get("village") or "—"))
    else:
        lines = []
        for m in res["matches"][:6]:
            lines.append("• %s — %s | %s | status: %s | match: %s (%s)" % (
                m.get("case_number"), m.get("case_type"), m.get("court_name"),
                (m.get("status") or "").upper(), m.get("match_level"),
                m.get("match_reason")))
        txt = ("⚖️ SA CourtLink: record #%s has PRIOR COURT-CASE HISTORY — "
               "%d case(s) found in the demo court database:\n%s\n"
               "Open the record and use the 🛡️ SA CourtLink panel to attach "
               "a case to its litigation ledger." % (
                   did, res["count"], "\n".join(lines)))
    return {"type": "court_scan", "mode": "SA", "results": res["matches"],
            "scan": res, "answer": txt}


# --------------------------------------------------------------------------
# Court-database Q&A — an admin can ask ANYTHING about the court cases
# database (the demo eCourts-style feed) in plain words: counts, statuses,
# stay orders, courts covered, parties, villages, hearing dates, case numbers.
# --------------------------------------------------------------------------
_CASE_NO_RE = re.compile(r"\b([A-Za-z]{2,4}/\d{4}/\d{3,6})\b")
_COURT_Q_RE = re.compile(
    r"\b(court|courts|litigation|case|cases|dispute|विवाद|मुकदमा|मुकदम|अदालत|केस|"
    r"stay|hearing|sunwai|सुनवाई|petitioner|respondent|वादी|प्रतिवादी)\b", re.I)
_COURT_DB_HINT_RE = re.compile(
    r"\b(database|db|courtlink|ecourt|e-?courts?|external|department|registry|"
    r"repository|stay|dismiss|dismissed|hearing|hearings|sunwai|सुनवाई|petitioner|"
    r"respondent|वादी|प्रतिवादी|filed|filing)\b", re.I)
# questions with these words target the PORTAL's own ledger / risk tooling
# (not the external court database) — leave them to the normal assistant
_LEDGER_HINT_RE = re.compile(
    r"\b(activ\w*|records?|documents?|lands?|plots?|survey|risk\w*|loans?|"
    r"encumbr\w*|attach\w*|ledger|mutations?)\b", re.I)
# definitional/explainer questions ("what do court case statuses mean?") keep
# their classic HELP answers
_DEF_HINT_RE = re.compile(
    r"\b(what\b[^.]{0,25}\b(mean|means|meaning|kya|matlab)|meaning|matlab|kya|"
    r"explain|samjhao|sunjhao|kaise|how (do|does|to)|statuses mean)\b", re.I)

_CDB_STOP = set("""
the a an and or of in on for to from by at as is are was were be been with without
show list tell give get me all any every each which what who whom whose how many much
there their these those this that it its do does did can could please kindly
court courts courtlink database db case cases litigation dispute disputed record records
entry entries status statuses pending stay stayed dismiss dismissed decided hearing
hearings next upcoming filed filing year years detail details about info information
active order orders listed listing number numbers total count counts onfile on-file
owner owners village villages land lands party parties versus vs against sa admin
batao bataye bataiye dikhao dikha dekho bata de do karo karna hain hai ho mukadma
dalit dakhil panjikrit registered cover covered involves
""".split())


def _case_card(c):
    lines = ["⚖️ COURT-DATABASE CASE — %s" % (c.get("case_number") or "?"),
             "Type: %s | Status: %s" % (c.get("case_type") or "?",
                                        (c.get("status") or "?").upper()),
             "Court: %s" % (c.get("court_name") or "?"),
             "Parties: %s vs %s" % (c.get("petitioner") or "?",
                                    c.get("respondent") or "?")]
    land = []
    if c.get("survey_number"):
        land.append("survey %s" % c["survey_number"])
    if c.get("khasra_number"):
        land.append("khasra %s" % c["khasra_number"])
    if c.get("village"):
        land.append("village %s" % c["village"])
    if c.get("owner_name"):
        land.append("recorded owner %s" % c["owner_name"])
    if land:
        lines.append("Land: " + " · ".join(land))
    when = []
    if c.get("filed_year"):
        when.append("filed %s" % c["filed_year"])
    if c.get("next_hearing"):
        when.append("next hearing %s" % c["next_hearing"])
    if when:
        lines.append("Timeline: " + " · ".join(when))
    if c.get("summary"):
        lines.append("Summary: %s" % c["summary"])
    lines.append("🛡️ SA tips: 'scan the court database for record <id>' screens a land "
                 "against this database; attach a matched case from the record's "
                 "⚖️ SA CourtLink panel into its litigation ledger.")
    return "\n".join(lines)


def _court_db_query(low, qn, sid, s):
    """Plain-language answers over the demo court database (admin-only feed)."""
    cases = courtlink.list_cases()
    store.sa_event(sid, "COURT_DB_QUERY", qn[:200])

    # 1) one specific case by its number: 'details of case CS/2022/0891'
    m = _CASE_NO_RE.search(qn)
    if m:
        want = m.group(1).lower()
        hit = next((c for c in cases if (c.get("case_number") or "").lower() == want),
                   None)
        if not hit:
            return {"type": "search", "results": [], "mode": "SA",
                    "answer": ("⚖️ Case '%s' is not in the court database (%d cases "
                               "on file). Say 'list every case in the court database' "
                               "to see them all." % (m.group(1).upper(), len(cases)))}
        return {"type": "search", "results": [], "mode": "SA",
                "answer": _case_card(hit)}

    # 2) grouped view: 'which courts are covered?'
    if re.search(r"\bwhich courts?\b|\bcourts? (covered|involved|handling|names?)\b", low):
        by = {}
        for c in cases:
            by[c.get("court_name") or "?"] = by.get(c.get("court_name") or "?", 0) + 1
        body = "\n".join("• %s — %d case(s)" % (k, n)
                         for k, n in sorted(by.items(), key=lambda x: -x[1]))
        return {"type": "stats", "results": [], "mode": "SA",
                "answer": "⚖️ The court database spans %d court(s):\n%s" % (len(by), body)}

    # 3) filters: status / hearing focus / filed year / free-text words
    statuses = None
    if re.search(r"stay|रोक", low):
        statuses = ("stay",)
    elif re.search(r"pending|लंबित|लम्बित|चल रह", low):
        statuses = ("pending",)
    elif re.search(r"decided|निपटा|फैसला", low):
        statuses = ("decided",)
    elif re.search(r"dismiss|खारिज", low):
        statuses = ("dismissed",)
    elif re.search(r"\bactive\b", low):
        statuses = ("pending", "stay")
    hearing_focus = re.search(r"hearing|sunwai|सुनवाई|next date|upcoming", low)
    years = re.findall(r"\b(?:19|20)\d{2}\b", qn)
    words = [w for w in re.findall(r"[\u0900-\u097F]{3,}|[a-z]{3,}", low)
             if w not in _CDB_STOP]

    pool = []
    for c in cases:
        if statuses and (c.get("status") or "") not in statuses:
            continue
        if years and (c.get("filed_year") or "") not in years:
            continue
        if words:
            hay = " ".join(str(c.get(k) or "") for k in
                           ("village", "court_name", "petitioner", "respondent",
                            "owner_name", "case_number", "case_type", "summary")).lower()
            if not any(w in hay for w in words):
                continue
        pool.append(c)
    if hearing_focus:
        pool.sort(key=lambda c: (not c.get("next_hearing"), c.get("next_hearing") or ""))

    counts = {}
    for c in cases:
        counts[c.get("status") or "?"] = counts.get(c.get("status") or "?", 0) + 1
    head = "⚖️ COURT DATABASE — %d case(s) on file: %s." % (
        len(cases), " · ".join("%d %s" % (n, st)
                              for st, n in sorted(counts.items())) or "none")
    scope = []
    if statuses:
        scope.append("status %s" % "/".join(statuses))
    if years:
        scope.append("filed in %s" % ", ".join(years))
    if words:
        scope.append("matching '%s'" % "', '".join(words[:4]))
    if scope:
        head += " Filtered to %s: %d hit(s)." % (" + ".join(scope), len(pool))
    if hearing_focus:
        head += " Sorted by next hearing."

    lines = [head]
    if not pool:
        lines.append("No case in the database matches that — broaden the filter, or "
                     "say 'list every case in the court database'.")
    else:
        for c in pool[:8]:
            bits = [c.get("case_number") or "?", c.get("case_type") or "?",
                    c.get("court_name") or "?",
                    "%s vs %s" % (c.get("petitioner") or "?",
                                  c.get("respondent") or "?")]
            land = []
            if c.get("survey_number"):
                land.append("survey " + c["survey_number"])
            if c.get("village"):
                land.append(c["village"])
            if land:
                bits.append(", ".join(land))
            bits.append((c.get("status") or "?").upper())
            if c.get("next_hearing"):
                bits.append("next hearing " + c["next_hearing"])
            lines.append("• " + " — ".join(bits))
        if len(pool) > 8:
            lines.append("…and %d more. Narrow it with a status / village / year."
                         % (len(pool) - 8))
    ledger = store.all_court_cases()
    active = sum(1 for c in ledger if c.get("status") == "active")
    lines.append("📚 Portal's own ledger: %d case(s), %d active (ask 'how many court "
                 "cases are active' for that one). Scan any land: 'scan the court "
                 "database for record <id>'." % (len(ledger), active))
    return {"type": "search", "results": [], "mode": "SA",
            "answer": "\n".join(lines)}


def query(session_id, q, current_user):
    """One SA-mode message: agentic intents first, else the full assistant."""
    s = _require(session_id)
    sid = session_id
    qn = " ".join((q or "").strip().split())
    if not qn:
        raise SAError("Type an instruction or a question first.", 400)
    low = qn.lower()
    store.sa_event(sid, "QUERY", qn[:300])

    did_match = re.search(ai_assistant.DOC_ID_RE, low)
    did = did_match.group(0) if did_match else None

    try:
        if did and re.search(r"\b(court|litigation|क़ेस|केस|मुकदमा|मुकदम|अदालत)\b", low) \
                and re.search(r"\b(scan|screen|detect|check|history|database|जाँच|जांच|खोज)\b", low):
            return _courtlink_scan(did, sid, s)
        if did and re.search(r"\b(assign|delegate|sop|सौंप)\b", low):
            return _assign_task(did, low, sid, s)
        if did and re.search(r"\b(reject|niras|asveekar|अस्वीकार|रद्द)\b", low):
            m = re.search(r"(?:because|reason|for)\s+(.+)$", qn, re.I)
            return _propose_reject(did, m.group(1) if m else "", sid, s)
        if did and re.search(r"\b(delete|remove|mitao|hatavo|हटाओ|मिटा)\b", low):
            return _propose_delete(did, sid, s)
        if did and re.search(r"\b(verify|approve|सत्यापित|स्वीकृत)\b", low):
            return _propose_verify(did, sid, s)
        # ⚖️ court-database Q&A: any question about the court cases database
        # itself (counts/statuses/stay orders/courts/parties/hearings or a
        # case number). Record-scoped scans above keep their priority; portal-
        # ledger and definitional questions still go to the normal assistant.
        if not did and (_CASE_NO_RE.search(qn) or
                (_COURT_Q_RE.search(low) and (
                    _COURT_DB_HINT_RE.search(low) or
                    (not _LEDGER_HINT_RE.search(low) and not _DEF_HINT_RE.search(low))))):
            return _court_db_query(low, qn, sid, s)
        if re.search(r"\b(what can (you|sa) do|sa (capabilities|powers)|sa mode kya)\b", low):
            store.sa_event(sid, "HELP", "capabilities")
            return {"type": "help", "results": [], "mode": "SA",
                    "answer": SA_CAPABILITIES}
    except SAError:
        raise
    except ValueError as e:
        raise SAError(str(e), 400)

    # everything else: the complete normal assistant, framed as SA
    res = ai_assistant.answer(qn, user_id=s["user_id"], role="admin", user=_sa_user(s))
    res["mode"] = "SA"
    return res


SA_CAPABILITIES = (
    "🛡️ SA (Superior Administrator) can do everything the normal assistant can, plus:\n"
    "• 'verify record <id>' — drafts an approval-card proposal (executes once approved)\n"
    "• 'reject record <id> because <reason>' — a rejection proposal, approval-gated\n"
    "• 'delete record <id>' — a deletion proposal, approval-gated (audit trail kept)\n"
    "• 'assign record <id> to verification officer / operator' — direct delegation to "
    "the AI Tasks inbox\n"
    "• 'scan the court database for record <id>' — SA CourtLink: matches the land "
    "against the demo court database and reports any prior court-case history\n"
    "• ask me ANYTHING about the ⚖️ court database — 'how many cases in the court "
    "database?', 'show stay orders', 'cases in Rampur Khas', 'which courts are "
    "covered?', 'details of case CS/2022/0891', 'upcoming hearings'\n"
    "• 📐 area cross-check — 'computed area of <record id>', 'area of survey 312', "
    "'which records have an area mismatch?' (works in normal mode too)\n"
    "• all read answers: loans, court cases, fraud risk, PDFs, backup, map, stats, "
    "record search…\n"
    "Consequential actions ALWAYS stop at the 📋 AI Approval Center, and every step "
    "is logged in my 📊 SA Activity Report. Type 'exit SA' (or use the ⏻ button) to end."
)

SA_TRIGGER_RE = re.compile(r"^SA(?:\s+(.+))?$", re.I | re.S)
