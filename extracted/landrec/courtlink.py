"""SA CourtLink — demo court database + SA court-case history screening.

Simulates the EXTERNAL courts database (eCourts-style) that a real
deployment would query: predefined court cases keyed to land identifiers
(village / survey / khasra / owner).  The SA (Superior Administrator) layer
matches an OCR-read document's extracted fields against this database to
surface PREVIOUS COURT-CASE HISTORY for that land — instantly, offline.

Access control (server-enforced): every endpoint in this module is
ADMIN-ONLY.  Data operators and verification officers cannot see the demo
court database, add/edit cases in it, run a scan, or attach a matched case.
When an ADMIN uploads a document, the screening runs immediately after OCR
and the findings ride back in the upload response ("sa_screening").

100% local & rule-based: no internet, no external AI, no data leaves the PC.
"""
import json
import re
import time
import uuid

from . import store

STATUSES = ("pending", "stay", "decided", "dismissed")
CASE_TYPES = ("civil", "criminal", "revenue", "revenue appeal", "writ",
              "possession", "title", "other")

# attach: demo-DB status -> internal court_cases status
_ATTACH_STATUS = {"pending": "active", "stay": "active",
                  "decided": "decided", "dismissed": "withdrawn"}

_MATCH_ORDER = {"exact": 0, "strong": 1, "probable": 2, "possible": 3}


class CourtLinkError(Exception):
    """Raised for any CourtLink failure; message is user-safe."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# table + demo seed
# --------------------------------------------------------------------------
def init_db():
    """Create the demo court database table and seed predefined cases.

    The seed represents what a courts-department database would already
    contain for these lands — it lets the user demo SA court-history
    detection immediately, and they can add/edit more cases from the UI.
    """
    c = store._conn()
    c.execute("""CREATE TABLE IF NOT EXISTS court_db_cases (
        id TEXT PRIMARY KEY, case_number TEXT, case_type TEXT,
        court_name TEXT, petitioner TEXT, respondent TEXT,
        village TEXT, survey_number TEXT, khasra_number TEXT,
        owner_name TEXT, status TEXT, filed_year TEXT, next_hearing TEXT,
        summary TEXT, created_by TEXT, created_name TEXT,
        created_at REAL, updated_at REAL)""")
    n = c.execute("SELECT COUNT(*) AS n FROM court_db_cases").fetchone()["n"]
    if n == 0:
        ts = time.time()
        for row in _SEED_CASES:
            c.execute("""INSERT INTO court_db_cases
                (id, case_number, case_type, court_name, petitioner, respondent,
                 village, survey_number, khasra_number, owner_name, status,
                 filed_year, next_hearing, summary, created_by, created_name,
                 created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (uuid.uuid4().hex[:12], row["case_number"], row["case_type"],
                       row["court_name"], row["petitioner"], row["respondent"],
                       row["village"], row["survey_number"], row["khasra_number"],
                       row["owner_name"], row["status"], row["filed_year"],
                       row["next_hearing"], row["summary"], "system",
                       "demo court database seed", ts, ts))
    c.commit()
    c.close()


_SEED_CASES = [
    # Exact (survey+village) match for the seeded Barkheda loop (survey 312).
    {"case_number": "RRC/2019/0456", "case_type": "revenue",
     "court_name": "Board of Revenue, Gwalior",
     "petitioner": "State of Madhya Pradesh", "respondent": "Ramswaroop Sharma",
     "village": "Barkheda", "survey_number": "312", "khasra_number": "45/2",
     "owner_name": "रामस्वरूप शर्मा", "status": "decided", "filed_year": "2019",
     "next_hearing": "",
     "summary": "Mutation-entry correction dispute — decided in favour of the recorded owner (order dated 14-03-2021)."},
    # Pending civil suit on the Sundarpur survey-452 transfer chain.
    {"case_number": "CS/2022/0891", "case_type": "civil",
     "court_name": "District & Sessions Court, Bhopal",
     "petitioner": "Sunita Devi", "respondent": "Kamla Devi Singh",
     "village": "Sundarpur", "survey_number": "452", "khasra_number": "",
     "owner_name": "Kamla Devi Singh", "status": "pending", "filed_year": "2022",
     "next_hearing": "2026-10-12",
     "summary": "Title suit — plaintiff claims a share in the ancestral land; transfer stay requested, not yet granted."},
    # STAY ORDER on Rampur Khas survey 145 — matches sample khatauni_rampur_2025.png.
    {"case_number": "WPL/2023/0234", "case_type": "writ",
     "court_name": "High Court of M.P., Principal Seat Jabalpur",
     "petitioner": "Ramesh Kumar Verma",
     "respondent": "State of M.P. & Ors. (landowner Mohanlal Verma)",
     "village": "Rampur Khas", "survey_number": "145", "khasra_number": "12/3",
     "owner_name": "Mohanlal Verma", "status": "stay", "filed_year": "2023",
     "next_hearing": "2026-11-03",
     "summary": "STAY ORDER in force: no sale, transfer or mutation on survey 145 until further orders of the Hon'ble Court."},
    # Adjacent survey 146 (precision proof: 145 matches, 146 does not).
    {"case_number": "RA/2021/0077", "case_type": "revenue appeal",
     "court_name": "Commissioner (Appeals), Bhopal Division",
     "petitioner": "Mohan Das", "respondent": "Mohanlal Verma",
     "village": "Rampur Khas", "survey_number": "146", "khasra_number": "",
     "owner_name": "Mohanlal Verma", "status": "dismissed", "filed_year": "2021",
     "next_hearing": "",
     "summary": "Appeal against a khata entry — dismissed for default on 21-08-2022."},
    # Owner+village (probable) match — no survey number recorded in the case.
    {"case_number": "CR/2024/0112", "case_type": "criminal",
     "court_name": "CJM Court, Bhopal",
     "petitioner": "State of M.P.", "respondent": "Ram Bahadur Singh",
     "village": "Arera", "survey_number": "", "khasra_number": "",
     "owner_name": "Ram Bahadur Singh", "status": "pending", "filed_year": "2024",
     "next_hearing": "2026-10-01",
     "summary": "Encroachment complaint naming the landowner; land parcel under police verification."},
    # Guroli survey 87 — the clean sample scans survey 88, so NO match.
    {"case_number": "CS/2025/0102", "case_type": "civil",
     "court_name": "Civil Court, Sehore",
     "petitioner": "Gram Panchayat Guroli", "respondent": "Unknown encroacher",
     "village": "Guroli", "survey_number": "87", "khasra_number": "",
     "owner_name": "", "status": "pending", "filed_year": "2025",
     "next_hearing": "2026-12-08",
     "summary": "Possession dispute on the village-common boundary strip adjoining survey 87."},
]


# --------------------------------------------------------------------------
# CRUD (admin-only — enforced at the endpoint layer in main.py)
# --------------------------------------------------------------------------
def list_cases():
    c = store._conn()
    rows = c.execute("SELECT * FROM court_db_cases "
                     "ORDER BY COALESCE(filed_year,'') DESC, case_number").fetchall()
    c.close()
    return [dict(r) for r in rows]


def get_case(cid):
    c = store._conn()
    r = c.execute("SELECT * FROM court_db_cases WHERE id=?", (cid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _find_by_case_number(case_number):
    c = store._conn()
    r = c.execute("SELECT * FROM court_db_cases WHERE lower(case_number)=?",
                  (_norm(case_number),)).fetchone()
    c.close()
    return dict(r) if r else None


_EDITABLE = ("case_number", "case_type", "court_name", "petitioner",
             "respondent", "village", "survey_number", "khasra_number",
             "owner_name", "status", "filed_year", "next_hearing", "summary")


def _validate(data, existing=None):
    out = {}
    for k in _EDITABLE:
        v = str(data.get(k) if data.get(k) is not None else
                (existing.get(k) if existing else "") or "").strip()
        out[k] = v
    if not out["case_number"]:
        raise CourtLinkError("Case number is required (e.g. CS/2024/0117).", 400)
    if out["status"] and out["status"] not in STATUSES:
        raise CourtLinkError("Status must be one of: " + ", ".join(STATUSES), 400)
    if not out["status"]:
        out["status"] = (existing or {}).get("status") or "pending"
    if not out["case_type"]:
        out["case_type"] = "civil"
    # A case must be findable: village + (survey OR owner), or a survey alone.
    if not (out["survey_number"] or (out["village"] and out["owner_name"])):
        raise CourtLinkError(
            "Give at least a survey number, or a village + owner name, "
            "so SA can match this case to a land record.", 400)
    return out


def create_case(data, user):
    vals = _validate(data or {})
    if _find_by_case_number(vals["case_number"]):
        raise CourtLinkError("A case with number %s already exists in the "
                             "court database." % vals["case_number"], 409)
    cid = uuid.uuid4().hex[:12]
    ts = time.time()
    c = store._conn()
    c.execute("""INSERT INTO court_db_cases
        (id, case_number, case_type, court_name, petitioner, respondent,
         village, survey_number, khasra_number, owner_name, status,
         filed_year, next_hearing, summary, created_by, created_name,
         created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (cid, vals["case_number"], vals["case_type"], vals["court_name"],
               vals["petitioner"], vals["respondent"], vals["village"],
               vals["survey_number"], vals["khasra_number"], vals["owner_name"],
               vals["status"], vals["filed_year"], vals["next_hearing"],
               vals["summary"], user["id"],
               user.get("email") or user.get("full_name") or "", ts, ts))
    c.commit()
    c.close()
    store.audit(None, user["id"], user.get("email") or "", "court_db_case_added",
                "%s — %s vs %s (survey %s, %s)" % (
                    vals["case_number"], vals["petitioner"], vals["respondent"],
                    vals["survey_number"], vals["village"]))
    return get_case(cid)


def update_case(cid, data, user):
    existing = get_case(cid)
    if not existing:
        raise CourtLinkError("Case not found in the court database.", 404)
    vals = _validate(data or {}, existing)
    clash = _find_by_case_number(vals["case_number"])
    if clash and clash["id"] != cid:
        raise CourtLinkError("Another case already uses number %s."
                             % vals["case_number"], 409)
    c = store._conn()
    sets = ", ".join("%s=?" % k for k in _EDITABLE)
    c.execute("UPDATE court_db_cases SET %s, updated_at=? WHERE id=?" % sets,
              [vals[k] for k in _EDITABLE] + [time.time(), cid])
    c.commit()
    c.close()
    store.audit(None, user["id"], user.get("email") or "", "court_db_case_updated",
                "%s (survey %s, %s)" % (vals["case_number"],
                                        vals["survey_number"], vals["village"]))
    return get_case(cid)


def delete_case(cid, user):
    case = get_case(cid)
    if not case:
        raise CourtLinkError("Case not found in the court database.", 404)
    c = store._conn()
    c.execute("DELETE FROM court_db_cases WHERE id=?", (cid,))
    c.commit()
    c.close()
    store.audit(None, user["id"], user.get("email") or "", "court_db_case_deleted",
                "%s (survey %s, %s)" % (case["case_number"],
                                        case["survey_number"], case["village"]))
    return {"deleted": True, "case_number": case["case_number"]}


# --------------------------------------------------------------------------
# SA screening — match a land record against the court database
# --------------------------------------------------------------------------
def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def _owner_match(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    ta, tb = set(a.split()), set(b.split())
    need = max(1, int(round(0.6 * max(len(ta), len(tb)))))
    return len(ta & tb) >= need


def _match_case(case, survey, village, owner, khasra):
    """Return (level, reasons) for one case, or (None, [])."""
    c_sv, c_vil, c_own, c_kh = (_digits(case.get("survey_number")),
                                _norm(case.get("village")),
                                case.get("owner_name"),
                                _norm(case.get("khasra_number")))
    d_sv, d_vil, d_kh = _digits(survey), _norm(village), _norm(khasra)
    sv_hit = c_sv and d_sv and c_sv == d_sv
    vil_hit = c_vil and d_vil and (c_vil == d_vil or c_vil in d_vil
                                   or d_vil in c_vil)
    own_hit = _owner_match(c_own, owner)
    kh_hit = c_kh and d_kh and (c_kh == d_kh)
    # Precision guard: when BOTH the case and the document carry a survey
    # number and they DIFFER, the case concerns another parcel of the same
    # owner — it is NOT that land's history, so it must not surface at all
    # (a case on survey 146 must not appear when scanning survey 145).
    if c_sv and d_sv and c_sv != d_sv:
        return None, []
    reasons = []
    if sv_hit and vil_hit:
        reasons = ["survey number %s matches" % survey,
                   "village '%s' matches" % case.get("village")]
        if kh_hit:
            reasons.append("khasra %s also matches" % case.get("khasra_number"))
        if own_hit:
            reasons.append("landowner name matches")
        return "exact", reasons
    if sv_hit and own_hit:
        return "strong", ["survey number %s matches" % survey,
                          "landowner name matches (village differs or not recorded)"]
    if sv_hit:
        return "possible", ["survey number %s matches" % survey,
                            "case village is '%s' — verify it is the same land"
                            % (case.get("village") or "not recorded")]
    if own_hit and vil_hit:
        return "probable", ["landowner name matches",
                            "village '%s' matches" % case.get("village"),
                            "no survey number in the case file — verify before acting"]
    if own_hit and not c_sv:
        return "possible", ["landowner name matches",
                            "case has no survey/village anchor — manual verification advised"]
    return None, []


def screen_values(survey="", village="", owner="", khasra="", khata=""):
    """Pure screening: match raw extracted values against the court DB."""
    matches = []
    for case in list_cases():
        level, reasons = _match_case(case, survey, village, owner, khasra)
        if level:
            m = dict(case)
            m["match_level"] = level
            m["match_reason"] = "; ".join(reasons)
            matches.append(m)
    matches.sort(key=lambda m: (_MATCH_ORDER.get(m["match_level"], 9),
                                str(m.get("case_number") or "")))
    return {"count": len(matches),
            "highest": matches[0]["match_level"] if matches else None,
            "scanned": {"survey": survey, "village": village,
                        "owner": owner, "khasra": khasra},
            "matches": matches}


def _field_value(fields, key):
    v = (fields or {}).get(key)
    if isinstance(v, dict):
        return str(v.get("value") or "").strip()
    return str(v or "").strip()


def screen_fields(fields):
    return screen_values(survey=_field_value(fields, "survey_number"),
                         village=_field_value(fields, "village"),
                         owner=_field_value(fields, "owner_name"),
                         khasra=_field_value(fields, "khasra_number"),
                         khata=_field_value(fields, "khata_number"))


def screen_document(doc_id, user, source="record_tab"):
    """SA scan of a stored document.  source='record_tab' writes the finding
    into the record's hash-chained audit trail (an explicit admin action);
    'upload'/'sa_chat' stay silent there (upload path reports via the
    response; SA chat logs to sa_events itself)."""
    doc = store.get_document(doc_id)
    if not doc:
        raise CourtLinkError("Record not found: %s" % doc_id, 404)
    try:
        fields = json.loads(doc.get("extracted_json") or "{}")
    except Exception:  # noqa: BLE001
        fields = {}
    res = screen_fields(fields)
    res["document_id"] = doc_id
    res["filename"] = doc.get("filename")
    if source == "record_tab":
        top = "; ".join(m["case_number"] for m in res["matches"][:4])
        store.audit(doc_id, user["id"], user.get("email") or "",
                    "sa_courtlink_scan",
                    "SA CourtLink scan: %d prior court case(s) found%s" % (
                        res["count"], (" — " + top) if top else ""))
    return res


# --------------------------------------------------------------------------
# attach a matched case to the record's internal litigation ledger
# --------------------------------------------------------------------------
def attach_case(case_id, doc_id, user):
    case = get_case(case_id)
    if not case:
        raise CourtLinkError("Case not found in the court database.", 404)
    doc = store.get_document(doc_id)
    if not doc:
        raise CourtLinkError("Record not found: %s" % doc_id, 404)
    try:
        fields = json.loads(doc.get("extracted_json") or "{}")
    except Exception:  # noqa: BLE001
        fields = {}
    survey = case.get("survey_number") or _field_value(fields, "survey_number")
    village = case.get("village") or _field_value(fields, "village")
    if not survey:
        raise CourtLinkError("Neither the case nor the record has a survey "
                             "number — cannot attach to the land ledger.", 400)
    for ex in store.all_court_cases():
        if (_norm(ex.get("case_number")) == _norm(case["case_number"])
                and _digits(ex.get("survey_number")) == _digits(survey)):
            raise CourtLinkError("Case %s is already attached to survey %s."
                                 % (case["case_number"], survey), 409)
    created = store.create_court_case({
        "survey_number": survey, "village": village,
        "khasra_number": case.get("khasra_number") or _field_value(fields, "khasra_number"),
        "case_type": case.get("case_type"), "case_number": case.get("case_number"),
        "court_name": case.get("court_name"),
        "filed_date": case.get("filed_year"),
        "status": _ATTACH_STATUS.get(case.get("status"), "active"),
        "parties": "%s vs %s" % (case.get("petitioner") or "?",
                                 case.get("respondent") or "?"),
        "relief_sought": case.get("summary"),
        "decision_summary": case.get("summary") if case.get("status") in ("decided", "dismissed") else "",
        "notes": "Imported from the demo court database by SA CourtLink "
                 "(court-db id %s). Next hearing: %s" % (
                     case["id"], case.get("next_hearing") or "—")}, user)
    store.audit(doc_id, user["id"], user.get("email") or "",
                "sa_courtlink_attach",
                "Court case %s (survey %s) attached from the demo court database"
                % (case["case_number"], survey))
    return {"attached": True, "court_case": created}
