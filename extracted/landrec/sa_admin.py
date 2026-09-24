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

from . import ai_assistant, auth, store

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
        if did and re.search(r"\b(assign|delegate|sop|सौंप)\b", low):
            return _assign_task(did, low, sid, s)
        if did and re.search(r"\b(reject|niras|asveekar|अस्वीकार|रद्द)\b", low):
            m = re.search(r"(?:because|reason|for)\s+(.+)$", qn, re.I)
            return _propose_reject(did, m.group(1) if m else "", sid, s)
        if did and re.search(r"\b(delete|remove|mitao|hatavo|हटाओ|मिटा)\b", low):
            return _propose_delete(did, sid, s)
        if did and re.search(r"\b(verify|approve|सत्यापित|स्वीकृत)\b", low):
            return _propose_verify(did, sid, s)
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
    "• all read answers: loans, court cases, fraud risk, PDFs, backup, map, stats, "
    "record search…\n"
    "Consequential actions ALWAYS stop at the 📋 AI Approval Center, and every step "
    "is logged in my 📊 SA Activity Report. Type 'exit SA' (or use the ⏻ button) to end."
)

SA_TRIGGER_RE = re.compile(r"^SA(?:\s+(.+))?$", re.I | re.S)
