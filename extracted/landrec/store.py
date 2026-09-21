"""Persistence: SQLite store for users, documents, audit trail, learned corrections."""
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid

from . import auth, common, paths

DATA_DIR = os.path.join(paths.data_dir(), "data")
DB_PATH = os.path.join(DATA_DIR, "landrec.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

# Document classification (DILRMP-aligned)
DOC_TYPES = {
    "land_record": "Land Record (Khatauni / ROR / 7-12)",
    "mutation":    "Mutation Record (Namantaran / Ferfar)",
    "sale_deed":   "Sale Deed (Registry / Conveyance)",
    "tax_receipt": "Tax Receipt (Lagan / Revenue Slip)",
}

# Roles allowed to perform actions (checked server-side)
UPLOAD_ROLES = {"operator", "verifier", "admin"}
VERIFY_ROLES = {"verifier", "admin"}
ADMIN_ROLES = {"admin"}


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'operator',
        token_version INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at REAL
    );
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        filename TEXT,
        stored_path TEXT,
        mime_type TEXT,
        file_size INTEGER,
        uploaded_by TEXT,
        uploaded_at REAL,
        ocr_text TEXT,
        mean_conf REAL,
        languages TEXT,
        extracted_json TEXT,
        validation_json TEXT,
        verdict TEXT,
        status TEXT,
        dedup_key TEXT
    );
    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id TEXT,
        ts REAL,
        user_id TEXT,
        username TEXT,
        action TEXT,
        detail TEXT
    );
    CREATE TABLE IF NOT EXISTS corrections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        field_id TEXT,
        wrong TEXT,
        right TEXT,
        count INTEGER DEFAULT 1,
        UNIQUE(field_id, wrong, right)
    );
    CREATE TABLE IF NOT EXISTS login_attempts (
        email TEXT PRIMARY KEY,
        failures INTEGER DEFAULT 0,
        locked_until REAL DEFAULT 0
    );
    """)
    # Migrations for databases created before doc_type / workflow / audit-hash
    for col, typedef in [("doc_type", "TEXT DEFAULT 'land_record'"),
                         ("reviewer_notes", "TEXT DEFAULT ''"),
                         ("lat", "REAL"), ("lon", "REAL"),
                         ("cert_hash", "TEXT"), ("cert_by", "TEXT"), ("cert_at", "REAL"),
                         ("submitted_at", "REAL"),
                         ("routed_to", "TEXT"), ("routed_to_name", "TEXT"),
                         ("routing_reason", "TEXT"), ("routed_at", "REAL"),
                         ("ai_rescue_json", "TEXT"),
                         ("boundary_geojson", "TEXT"), ("boundary_source", "TEXT"),
                         ("boundary_at", "REAL")]:
        try:
            c.execute("ALTER TABLE documents ADD COLUMN %s %s" % (col, typedef))
        except sqlite3.OperationalError:
            pass
    for col, typedef in [("prev_hash", "TEXT"), ("entry_hash", "TEXT")]:
        try:
            c.execute("ALTER TABLE audit ADD COLUMN %s %s" % (col, typedef))
        except sqlite3.OperationalError:
            pass
    # Mutation (Namantaran) online application module
    c.executescript("""
    CREATE TABLE IF NOT EXISTS mutations (
        id TEXT PRIMARY KEY,
        app_no TEXT UNIQUE NOT NULL,
        applicant_id TEXT NOT NULL,
        applicant_email TEXT NOT NULL,
        applicant_name TEXT,
        transfer_type TEXT NOT NULL,
        previous_owner TEXT,
        new_owner TEXT,
        survey_number TEXT,
        khasra_number TEXT,
        khata_number TEXT,
        village TEXT,
        tehsil TEXT,
        district TEXT,
        state TEXT,
        deed_no TEXT,
        deed_date TEXT,
        notes TEXT,
        file_name TEXT,
        file_path TEXT,
        status TEXT NOT NULL DEFAULT 'received',
        reviewer_notes TEXT DEFAULT '',
        linked_doc_id TEXT,
        processed_by TEXT,
        created_at REAL,
        updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS mutation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mutation_id TEXT NOT NULL,
        ts REAL,
        user_id TEXT,
        username TEXT,
        status TEXT,
        note TEXT
    );
    CREATE TABLE IF NOT EXISTS encumbrances (
        id TEXT PRIMARY KEY,
        survey_number TEXT NOT NULL,
        khasra_number TEXT,
        village TEXT,
        tehsil TEXT,
        district TEXT,
        creditor TEXT NOT NULL,
        amount REAL,
        mortgage_date TEXT,
        reference_no TEXT,
        status TEXT NOT NULL DEFAULT 'active',
        settlement_date TEXT,
        notes TEXT,
        created_by TEXT,
        created_name TEXT,
        created_at REAL,
        updated_at REAL
    );
    CREATE TABLE IF NOT EXISTS ai_proposals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action_type TEXT NOT NULL,
        target_id TEXT,
        description TEXT,
        target_display TEXT,
        before_state TEXT,
        after_state TEXT,
        reason TEXT,
        created_by TEXT,
        created_name TEXT,
        created_at REAL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        executed_by TEXT,
        executed_at REAL,
        error TEXT
    );
    CREATE TABLE IF NOT EXISTS ai_tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        priority TEXT NOT NULL DEFAULT 'MEDIUM',
        status TEXT NOT NULL DEFAULT 'PENDING',
        assigned_role TEXT,
        assigned_name TEXT,
        record_id TEXT,
        created_by TEXT,
        created_name TEXT,
        created_at REAL,
        updated_at REAL,
        log TEXT
    );
    """)
    c.commit()
    c.close()
    _seed_admin(c=None)


def _seed_admin(c=None):
    """Create a default administrator account on first run."""
    c = c or _conn()
    row = c.execute("SELECT COUNT(*) n FROM users").fetchone()
    if row["n"] == 0:
        salt, pwh = auth.hash_password("Admin@123")
        c.execute("INSERT INTO users (id, email, password_hash, salt, full_name, role, created_at)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (uuid.uuid4().hex[:12], "admin@landrec.gov.in", pwh, salt,
                   "System Administrator", "admin", time.time()))
        c.commit()
    c.close()


# ---------- Users ----------
def create_user(email, password, full_name, role="operator"):
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Invalid email address")
    reason = auth.weak_password_reason(password or "")
    if reason:
        raise ValueError(reason)
    if role not in auth.ROLES:
        raise ValueError("Invalid role")
    salt, pwh = auth.hash_password(password)
    uid = uuid.uuid4().hex[:12]
    c = _conn()
    try:
        c.execute("INSERT INTO users (id, email, password_hash, salt, full_name, role, created_at)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (uid, email, pwh, salt, full_name.strip() or "User", role, time.time()))
        c.commit()
    except sqlite3.IntegrityError:
        c.close()
        raise ValueError("An account with this email already exists")
    c.close()
    return uid


def get_user(uid):
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_user_by_email(email):
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    c.close()
    return dict(row) if row else None


def list_users():
    c = _conn()
    rows = c.execute("SELECT id, email, full_name, role, is_active, created_at FROM users "
                     "ORDER BY created_at").fetchall()
    c.close()
    return [dict(r) for r in rows]


def update_user(uid, role=None, is_active=None):
    c = _conn()
    if role is not None:
        c.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    if is_active is not None:
        c.execute("UPDATE users SET is_active=? WHERE id=?", (1 if is_active else 0, uid))
    c.commit()
    c.close()


def change_password(uid, new_password):
    """Set a new password and invalidate all existing sessions."""
    reason = auth.weak_password_reason(new_password or "")
    if reason:
        raise ValueError(reason)
    salt, pwh = auth.hash_password(new_password)
    c = _conn()
    c.execute("UPDATE users SET password_hash=?, salt=?, token_version=token_version+1 "
              "WHERE id=?", (pwh, salt, uid))
    c.commit()
    c.close()


def bump_token_version(uid):
    c = _conn()
    c.execute("UPDATE users SET token_version=token_version+1 WHERE id=?", (uid,))
    c.commit()
    c.close()


def record_login_failure(email):
    c = _conn()
    c.execute("INSERT INTO login_attempts (email, failures, locked_until) VALUES (?,1,0) "
              "ON CONFLICT(email) DO UPDATE SET failures = failures + 1", (email,))
    row = c.execute("SELECT failures FROM login_attempts WHERE email=?", (email,)).fetchone()
    if row and row["failures"] >= 5:
        c.execute("UPDATE login_attempts SET locked_until=? WHERE email=?",
                  (time.time() + 300, email))  # 5 min lock
    c.commit()
    c.close()


def login_locked(email):
    c = _conn()
    row = c.execute("SELECT locked_until, failures FROM login_attempts WHERE email=?",
                    (email,)).fetchone()
    c.close()
    if row and row["locked_until"] > time.time():
        return True
    return False


def clear_login_failures(email):
    c = _conn()
    c.execute("DELETE FROM login_attempts WHERE email=?", (email,))
    c.commit()
    c.close()


def get_login_failures(email):
    c = _conn()
    row = c.execute("SELECT failures FROM login_attempts WHERE email=?", (email,)).fetchone()
    c.close()
    return row["failures"] if row else 0


# ---------- Account export / import (share accounts between computers) ----------
# Each computer has its own database, so this is the portable way to copy
# accounts from one machine to another (the real fix for "my teammate
# can't log in with the account I created").
def export_users() -> dict:
    """Export all accounts (with password hashes) for import on another PC."""
    c = _conn()
    rows = c.execute("SELECT id, email, password_hash, salt, full_name, role, "
                     "token_version, is_active, created_at FROM users").fetchall()
    c.close()
    return {"format": "landrec-users", "version": 1,
            "users": [dict(r) for r in rows]}


def import_users(payload: dict) -> dict:
    """Import accounts from an export file. Existing emails (matched by
    email) are updated; new emails are inserted. Returns counts.
    """
    if not isinstance(payload, dict) or payload.get("format") != "landrec-users":
        raise ValueError("Not a valid Land Record System account file.")
    users = payload.get("users")
    if not isinstance(users, list):
        raise ValueError("Not a valid Land Record System account file.")
    added, updated = 0, 0
    c = _conn()
    for u in users:
        if not isinstance(u, dict):
            continue
        email = (u.get("email") or "").strip().lower()
        if not email or not u.get("password_hash") or not u.get("salt"):
            continue
        role = u.get("role") or "operator"
        if role not in auth.ROLES:
            role = "operator"
        existing = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        full_name = u.get("full_name") or "User"
        token_version = int(u.get("token_version") or 0)
        is_active = 1 if u.get("is_active", 1) else 0
        created_at = u.get("created_at") or time.time()
        if existing:
            c.execute("UPDATE users SET password_hash=?, salt=?, full_name=?, role=?, "
                      "token_version=?, is_active=? WHERE id=?",
                      (u["password_hash"], u["salt"], full_name, role,
                       token_version, is_active, existing["id"]))
            updated += 1
        else:
            c.execute("INSERT INTO users (id, email, password_hash, salt, full_name, "
                      "role, token_version, is_active, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (u.get("id") or uuid.uuid4().hex[:12], email, u["password_hash"],
                       u["salt"], full_name, role, token_version, is_active, created_at))
            added += 1
    c.commit()
    c.close()
    return {"added": added, "updated": updated, "total": len(users)}


# ---------- Audit ----------
def _append_audit(c, doc_id, ts, user_id, username, action, detail=""):
    """Insert one hash-chained audit row using connection c. Each entry
    carries the previous entry's SHA-256 hash, so any later edit of an old
    entry breaks the chain and is detectable by verify_audit_chain()."""
    row = c.execute("SELECT entry_hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev_hash = row["entry_hash"] if (row and row["entry_hash"]) else "GENESIS"
    payload = "%s|%s|%s|%s|%s|%s" % (prev_hash, doc_id, ts, user_id, action, detail)
    entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    c.execute("INSERT INTO audit (doc_id, ts, user_id, username, action, detail, prev_hash, entry_hash) "
              "VALUES (?,?,?,?,?,?,?,?)",
              (doc_id, ts, user_id, username, action, detail, prev_hash, entry_hash))


def audit(doc_id, user_id, username, action, detail=""):
    """Append to the CRYPTOGRAPHIC audit trail (see _append_audit)."""
    c = _conn()
    _append_audit(c, doc_id, time.time(), user_id, username, action, detail)
    c.commit()
    c.close()


def verify_audit_chain():
    """Recompute the SHA-256 chain; report any broken link (tampering).

    The chain runs in SEGMENTS: it starts at the first hashed entry and
    restarts whenever an entry re-anchors at GENESIS (this happens right
    after legacy pre-hash entries, so old databases stay verifiable)."""
    c = _conn()
    rows = c.execute("SELECT id, doc_id, ts, user_id, action, detail, prev_hash, entry_hash "
                     "FROM audit ORDER BY id").fetchall()
    c.close()
    legacy = 0
    segments = 0
    prev = "GENESIS"
    for r in rows:
        if not r["entry_hash"]:
            legacy += 1
            continue
        if r["prev_hash"] == "GENESIS":
            segments += 1
            prev = "GENESIS"  # this row re-anchors a new segment
        if r["prev_hash"] != prev:
            return {"valid": False, "entries": len(rows), "legacy": legacy,
                    "segments": segments, "broken_at": r["id"]}
        payload = "%s|%s|%s|%s|%s|%s" % (prev, r["doc_id"], r["ts"],
                                         r["user_id"], r["action"], r["detail"])
        if hashlib.sha256(payload.encode("utf-8")).hexdigest() != r["entry_hash"]:
            return {"valid": False, "entries": len(rows), "legacy": legacy,
                    "segments": segments, "broken_at": r["id"]}
        prev = r["entry_hash"]
    return {"valid": True, "entries": len(rows), "legacy": legacy,
            "segments": segments, "broken_at": None}


# ---------- Documents ----------
def save_upload(filename, mime, size, stored_path, uploaded_by, ocr_result, fields,
                validation, dedup_key, doc_type="land_record", status=None):
    doc_id = uuid.uuid4().hex[:12]
    if status is None:
        status = "pending_review" if validation["low_confidence_fields"] else (
            "auto_approved" if validation["verdict"] == "valid" else "pending_review")
    if doc_type not in DOC_TYPES:
        doc_type = "land_record"
    c = _conn()
    cert_hash = cert_by = cert_at = None
    if status == "auto_approved":
        cert_at = time.time()
        cert_by = "system (auto-verified)"
        cert_hash = compute_cert_hash(doc_id, fields, cert_by, cert_at)
    c.execute("""INSERT INTO documents
        (id, filename, stored_path, mime_type, file_size, uploaded_by, uploaded_at,
         ocr_text, mean_conf, languages, extracted_json, validation_json, verdict,
         status, dedup_key, doc_type, reviewer_notes, cert_hash, cert_by, cert_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (doc_id, filename, stored_path, mime, size, uploaded_by, time.time(),
         ocr_result["full_text"], ocr_result["mean_conf"],
         json.dumps(ocr_result["detected_scripts"]), json.dumps(fields),
         json.dumps(validation), validation["verdict"], status, dedup_key,
         doc_type, "", cert_hash, cert_by, cert_at))
    c.commit()
    c.close()
    return doc_id


def get_document(doc_id):
    c = _conn()
    row = c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def set_location(doc_id, lat, lon):
    """Set (or clear, with lat/lon = None) the GIS pin for a record."""
    c = _conn()
    c.execute("UPDATE documents SET lat=?, lon=? WHERE id=?", (lat, lon, doc_id))
    c.commit()
    c.close()


def set_boundary(doc_id, coordinates, source, user):
    """Store a plot boundary polygon for a record.
    coordinates: [[lat, lon], ...] ring (>=4 points). source: digitized |
    estimated | imported. Returns True on success."""
    if source not in ("digitized", "estimated", "imported"):
        raise ValueError("Invalid boundary source")
    c = _conn()
    c.execute("UPDATE documents SET boundary_geojson=?, boundary_source=?, boundary_at=? WHERE id=?",
              (json.dumps(coordinates), source, time.time(), doc_id))
    c.commit()
    c.close()
    audit(doc_id, user["id"], user["email"], "boundary_set",
          "source=%s vertices=%d" % (source, len(coordinates)))
    return True


def clear_boundary(doc_id, user):
    c = _conn()
    c.execute("UPDATE documents SET boundary_geojson=NULL, boundary_source=NULL, "
              "boundary_at=NULL WHERE id=?", (doc_id,))
    c.commit()
    c.close()
    audit(doc_id, user["id"], user["email"], "boundary_cleared", "")
    return True


def compute_cert_hash(doc_id, fields, cert_by, cert_at):
    """SHA-256 over the CANONICAL snapshot of a certified record.

    This is the fingerprint printed on the Certified PDF (and in its QR
    code). If anyone later edits the stored fields, the recomputed hash
    will differ and the public Verify page flags the record as TAMPERED.
    """
    canon = json.dumps({"id": doc_id, "fields": fields, "cert_by": cert_by,
                        "cert_at": round(cert_at, 3)},
                       sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def certify_document(doc_id, cert_by):
    """Stamp a record with its certification fingerprint (idempotent)."""
    doc = get_document(doc_id)
    if not doc:
        return None
    fields = json.loads(doc.get("extracted_json") or "{}")
    ts = time.time()
    chash = compute_cert_hash(doc_id, fields, cert_by, ts)
    c = _conn()
    c.execute("UPDATE documents SET cert_hash=?, cert_by=?, cert_at=? WHERE id=?",
              (chash, cert_by, ts, doc_id))
    c.commit()
    c.close()
    return chash


def check_cert_hash(doc_id):
    """Recompute the certification hash from CURRENT stored fields.

    Returns (ok: bool, stored_hash, recomputed_hash)."""
    doc = get_document(doc_id)
    if not doc:
        return False, None, None
    stored = doc.get("cert_hash")
    if not stored:
        return False, None, None
    fields = json.loads(doc.get("extracted_json") or "{}")
    recomputed = compute_cert_hash(doc_id, fields, doc.get("cert_by") or "",
                                   doc.get("cert_at") or 0)
    return recomputed == stored, stored, recomputed


def list_documents(limit=500, status=None):
    c = _conn()
    sql = ("SELECT id, filename, uploaded_by, uploaded_at, submitted_at, mean_conf, "
           "verdict, status, lat, lon, extracted_json, doc_type, reviewer_notes, "
           "routed_to, routed_to_name, routing_reason, "
           "boundary_geojson, boundary_source FROM documents")
    args = []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY uploaded_at DESC LIMIT ?"
    args.append(limit)
    rows = c.execute(sql, tuple(args)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["fields"] = json.loads(d.pop("extracted_json") or "{}")
        out.append(d)
    return out


def search_public(q="", doc_type=None, limit=100):
    """Public repository: ONLY verified records, searched across the
    structured fields (owner, survey, khasra, khata, village, district)."""
    docs = list_documents(limit=1000)
    q = (q or "").strip().lower()
    out = []
    for d in docs:
        if d["status"] not in ("verified", "auto_approved"):
            continue
        if doc_type and doc_type in DOC_TYPES and d.get("doc_type") != doc_type:
            continue
        if q:
            f = d.get("fields", {})
            hay = " ".join(str(v.get("value", "")) for v in f.values()
                           if isinstance(v, dict)).lower()
            hay += " " + d["id"] + " " + (d.get("filename") or "")
            if q not in hay:
                continue
        f = d.get("fields", {})
        g = lambda k: str(f.get(k, {}).get("value", "")) if isinstance(f.get(k), dict) else ""
        out.append({"id": d["id"], "filename": d["filename"], "status": d["status"],
                    "doc_type": d.get("doc_type") or "land_record",
                    "owner": g("owner_name"), "survey": g("survey_number"),
                    "khasra": g("khasra_number"), "village": g("village"),
                    "district": g("district"), "tehsil": g("tehsil"),
                    "uploaded_at": d.get("uploaded_at")})
    return out[:limit]


# ---------- Verification workflow (draft -> pending -> verified / returned) ----------
def save_draft(doc_id, fields):
    c = _conn()
    c.execute("UPDATE documents SET extracted_json=?, status='draft' WHERE id=?",
              (json.dumps(fields), doc_id))
    c.commit()
    c.close()


def submit_document(doc_id):
    c = _conn()
    c.execute("UPDATE documents SET status='pending_review', submitted_at=? WHERE id=?",
              (time.time(), doc_id))
    c.commit()
    c.close()


def return_document(doc_id, notes, user):
    c = _conn()
    c.execute("UPDATE documents SET status='returned', reviewer_notes=? WHERE id=?",
              (notes or "", doc_id))
    c.commit()
    c.close()
    audit(doc_id, user["id"], user["email"], "record_returned", (notes or "")[:300])


def reject_document(doc_id, notes, user):
    """Terminal rejection: the record is closed permanently (reference
    portal's REJECTED status). Requires reviewer notes, which are kept
    on the record and in the audit trail."""
    c = _conn()
    c.execute("UPDATE documents SET status='rejected', reviewer_notes=? WHERE id=?",
              (notes or "", doc_id))
    c.commit()
    c.close()
    audit(doc_id, user["id"], user["email"], "record_rejected", (notes or "")[:300])


# ---------- Mutation (Namantaran) online applications ----------
MUTATION_TYPES = ["sale", "inheritance", "gift", "court_order", "partition"]
MUTATION_STATUS_FLOW = ["received", "under_review", "verified", "returned", "rejected"]


def _next_mutation_app_no(c):
    year = time.strftime("%Y")
    row = c.execute("SELECT app_no FROM mutations WHERE app_no LIKE ? ORDER BY app_no DESC LIMIT 1",
                    ("MUT-%s-%%" % year,)).fetchone()
    n = 1
    if row and row["app_no"]:
        try:
            n = int(row["app_no"].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            n = 1
    return "MUT-%s-%04d" % (year, n)


def create_mutation(data: dict, user: dict, file_name=None, file_path=None) -> dict:
    ttype = (data.get("transfer_type") or "sale").strip()
    if ttype not in MUTATION_TYPES:
        raise ValueError("Invalid transfer type")
    new_owner = (data.get("new_owner") or "").strip()
    if not new_owner:
        raise ValueError("New owner name is required")
    survey = (data.get("survey_number") or "").strip()
    if not survey:
        raise ValueError("Survey number is required")
    c = _conn()
    mid = uuid.uuid4().hex[:12]
    app_no = _next_mutation_app_no(c)
    ts = time.time()
    c.execute("""INSERT INTO mutations
        (id, app_no, applicant_id, applicant_email, applicant_name, transfer_type,
         previous_owner, new_owner, survey_number, khasra_number, khata_number,
         village, tehsil, district, state, deed_no, deed_date, notes,
         file_name, file_path, status, reviewer_notes, linked_doc_id,
         processed_by, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mid, app_no, user["id"], user["email"], (data.get("applicant_name") or "").strip(),
         ttype, (data.get("previous_owner") or "").strip(), new_owner, survey,
         (data.get("khasra_number") or "").strip(), (data.get("khata_number") or "").strip(),
         (data.get("village") or "").strip(), (data.get("tehsil") or "").strip(),
         (data.get("district") or "").strip(), (data.get("state") or "").strip(),
         (data.get("deed_no") or "").strip(), (data.get("deed_date") or "").strip(),
         (data.get("notes") or "").strip(), file_name, file_path, "received",
         "", (data.get("linked_doc_id") or "").strip(), None, ts, ts))
    _append_audit(c, None, ts, user["id"], user["email"], "mutation_created",
                  "%s survey %s by %s" % (app_no, survey, user["email"]))
    c.commit()
    c.close()
    _append_mutation_event(mid, user, "received", "Application submitted (online form)")
    return get_mutation(mid)


def _append_mutation_event(mid, user, status, note=""):
    c = _conn()
    c.execute("INSERT INTO mutation_events (mutation_id, ts, user_id, username, status, note) "
              "VALUES (?,?,?,?,?,?)",
              (mid, time.time(), user["id"], user["email"], status, note or ""))
    c.commit()
    c.close()


def list_mutations(status=None, mine_email=None, limit=300):
    c = _conn()
    sql = "SELECT * FROM mutations"
    conds, args = [], []
    if status:
        conds.append("status=?"); args.append(status)
    if mine_email:
        conds.append("applicant_email=?"); args.append(mine_email.lower())
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    for m in rows:
        c2 = _conn()
        last = c2.execute("SELECT status, note, ts, username FROM mutation_events "
                          "WHERE mutation_id=? ORDER BY id DESC LIMIT 1", (m["id"],)).fetchone()
        c2.close()
        m["last_event"] = dict(last) if last else None
    return rows


def get_mutation(mid):
    c = _conn()
    row = c.execute("SELECT * FROM mutations WHERE id=?", (mid,)).fetchone()
    if not row:
        c.close()
        return None
    m = dict(row)
    ev = c.execute("SELECT ts, username, status, note FROM mutation_events "
                   "WHERE mutation_id=? ORDER BY id", (mid,)).fetchall()
    c.close()
    m["events"] = [dict(e) for e in ev]
    if m.get("linked_doc_id"):
        d = get_document(m["linked_doc_id"])
        if d:
            f = json.loads(d.get("extracted_json") or "{}")
            g = lambda k: str(f.get(k, {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
            m["linked_doc"] = {"id": d["id"], "filename": d["filename"], "status": d["status"],
                               "owner": g("owner_name"), "survey": g("survey_number"),
                               "village": g("village")}
    return m


def set_mutation_status(mid, status, user, note="", linked_doc_id=None):
    if status not in MUTATION_STATUS_FLOW:
        raise ValueError("Invalid mutation status")
    c = _conn()
    c.execute("UPDATE mutations SET status=?, reviewer_notes=?, updated_at=?, "
              "processed_by=?, linked_doc_id=COALESCE(?, linked_doc_id) WHERE id=?",
              (status, note or "", time.time(), user["email"], linked_doc_id, mid))
    c.commit()
    c.close()
    _append_mutation_event(mid, user, status, note)
    if status == "verified" and linked_doc_id:
        # the heart of a mutation: the land record's owner is updated
        m = get_mutation(mid)
        if m:
            fields = json.loads(get_document(linked_doc_id).get("extracted_json") or "{}")
            old = str(fields.get("owner_name", {}).get("value", "") or "")
            fields["owner_name"] = {"value": m["new_owner"], "confidence": 1.0, "verified": True}
            c2 = _conn()
            c2.execute("UPDATE documents SET extracted_json=?, status='verified', verdict='verified' "
                       "WHERE id=?", (json.dumps(fields), linked_doc_id))
            c2.commit()
            c2.close()
            certify_document(linked_doc_id, user["email"])
            audit(linked_doc_id, user["id"], user["email"], "mutation_approved",
                  "owner '%s' -> '%s' per %s" % (old, m["new_owner"], m["app_no"]))
    return get_mutation(mid)


def mutation_counts():
    c = _conn()
    rows = c.execute("SELECT status, COUNT(*) n FROM mutations GROUP BY status").fetchall()
    c.close()
    return {r["status"]: r["n"] for r in rows}


# ---------- Encumbrances (loans / mortgages) ----------
ENCUMBRANCE_STATUSES = ("active", "settled", "foreclosed")


def _norm_text(v):
    return re.sub(r"\s+", " ", str(v or "").strip()).lower()


def create_encumbrance(data, user):
    """Record a loan/mortgage against a piece of land (keyed by survey +
    village, khasra optional). Returns the new row."""
    eid = uuid.uuid4().hex[:12]
    ts = time.time()
    status = (data.get("status") or "active").strip()
    if status not in ENCUMBRANCE_STATUSES:
        raise ValueError("Invalid encumbrance status (use active/settled/foreclosed)")
    c = _conn()
    c.execute("""INSERT INTO encumbrances
        (id, survey_number, khasra_number, village, tehsil, district,
         creditor, amount, mortgage_date, reference_no, status,
         settlement_date, notes, created_by, created_name, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (eid,
               str(data.get("survey_number") or "").strip(),
               str(data.get("khasra_number") or "").strip(),
               str(data.get("village") or "").strip(),
               str(data.get("tehsil") or "").strip(),
               str(data.get("district") or "").strip(),
               str(data.get("creditor") or "").strip(),
               _to_float(data.get("amount")),
               str(data.get("mortgage_date") or "").strip(),
               str(data.get("reference_no") or "").strip(),
               status,
               str(data.get("settlement_date") or "").strip() or None,
               str(data.get("notes") or "").strip(),
               user["id"], user.get("email") or user.get("full_name") or "",
               ts, ts))
    c.commit()
    c.close()
    audit(None, user["id"], user.get("email") or "", "encumbrance_created",
          "%s on survey %s %s (%s)" % (data.get("creditor"), data.get("survey_number"),
                                       data.get("khasra_number"), data.get("village")))
    return get_encumbrance(eid)


def _to_float(v):
    try:
        if v in (None, ""):
            return None
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def get_encumbrance(eid):
    c = _conn()
    r = c.execute("SELECT * FROM encumbrances WHERE id=?", (eid,)).fetchone()
    c.close()
    return dict(r) if r else None


def list_encumbrances(survey, village="", khasra=""):
    """All encumbrances on a piece of land (same survey + village; khasra
    applied when given), newest first."""
    c = _conn()
    q = "SELECT * FROM encumbrances WHERE survey_number=?"
    args = [str(survey or "").strip()]
    if village:
        # a loan registered without a village (survey-level) applies to the
        # whole survey number — matches any village on that survey
        q += " AND (village=? OR village IS NULL OR village='')"
        args.append(str(village).strip())
    if khasra:
        q += " AND (khasra_number=? OR khasra_number IS NULL OR khasra_number='')"
        args.append(str(khasra).strip())
    q += " ORDER BY COALESCE(mortgage_date,'') DESC, created_at DESC"
    rows = c.execute(q, args).fetchall()
    c.close()
    return [dict(r) for r in rows]


def settle_encumbrance(eid, user, settlement_date="", notes=""):
    """Mark a loan settled/foreclosed (the release the officer records after
    the bank NOC). Returns the updated row."""
    e = get_encumbrance(eid)
    if not e:
        return None
    if e["status"] != "active":
        raise ValueError("Only an ACTIVE encumbrance can be settled")
    c = _conn()
    c.execute("""UPDATE encumbrances SET status='settled', settlement_date=?,
                 notes=?, updated_at=? WHERE id=?""",
              (str(settlement_date or "").strip() or None,
               (e["notes"] or "") + ((" | " if e["notes"] else "") + str(notes or "").strip()),
               time.time(), eid))
    c.commit()
    c.close()
    audit(None, user["id"], user.get("email") or "", "encumbrance_settled",
          "%s on survey %s %s" % (e["creditor"], e["survey_number"], e["khasra_number"]))
    return get_encumbrance(eid)


def active_encumbrances(survey, village=""):
    """Active (unsettled) encumbrances on a piece of land — the ones that
    block an Encumbrance Certificate and flag a sale."""
    return [e for e in list_encumbrances(survey, village) if e["status"] == "active"]


# ---------- Year-wise history / SLA / reports ----------
def record_history(survey, village="", exclude_id=""):
    """All documents describing the SAME land (same survey number; village
    matched when given), oldest record year first — the 'passbook' view of
    how ownership/area changed over the years."""
    from . import common
    sv_c = common.normalize_numerals(survey or "").strip()
    if not sv_c:
        return []
    v_c = common.normalize_numerals(village or "").strip().lower()
    docs = list_documents(limit=5000)
    out = []
    for d in docs:
        if d["id"] == exclude_id:
            continue
        f = d.get("fields") or {}
        g = lambda k: str(f.get(k, {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        sv = common.normalize_numerals(g("survey_number")).strip()
        if sv != sv_c:
            continue
        if v_c and v_c != common.normalize_numerals(g("village")).strip().lower():
            continue
        out.append({"id": d["id"], "filename": d["filename"], "status": d["status"],
                    "year": g("khatauni_year"), "owner": g("owner_name"),
                    "father": g("father_name"), "area": g("area"),
                    "khasra": g("khasra_number"), "khata": g("khata_number"),
                    "village": g("village"), "doc_type": d.get("doc_type")})
    out.sort(key=lambda r: (str(r["year"] or "9999"), r["id"]))
    return out


def sla_stats():
    """Aging of the pending-verification queue: how long each record has
    been waiting (submitted_at, falling back to uploaded_at)."""
    now = time.time()
    c = _conn()
    rows = c.execute("SELECT id, submitted_at, uploaded_at FROM documents WHERE status='pending_review'").fetchall()
    c.close()
    fresh = warm = overdue = 0
    for r in rows:
        t = r["submitted_at"] or r["uploaded_at"] or now
        age = (now - t) / 86400.0
        if age <= 7:
            fresh += 1
        elif age <= 30:
            warm += 1
        else:
            overdue += 1
    return {"pending": len(rows), "fresh_7d": fresh, "warm_30d": warm, "overdue_30d": overdue}


def csv_export_rows():
    """One row per record, for the Excel/CSV report export."""
    docs = list_documents(limit=5000)
    rows = []
    for d in docs:
        f = d.get("fields") or {}
        g = lambda k: str(f.get(k, {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        rows.append({
            "id": d["id"], "filename": d.get("filename", ""), "doc_type": d.get("doc_type", ""),
            "owner_name": g("owner_name"), "father_name": g("father_name"),
            "survey_number": g("survey_number"), "khasra_number": g("khasra_number"),
            "khata_number": g("khata_number"), "plot_number": g("plot_number"),
            "area": g("area"), "village": g("village"), "tehsil": g("tehsil"),
            "district": g("district"), "state": g("state"), "year": g("khatauni_year"),
            "status": d.get("status", ""), "verdict": d.get("verdict", ""),
            "mean_conf": d.get("mean_conf") or 0, "uploaded_by": d.get("uploaded_by", ""),
            "uploaded_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(d.get("uploaded_at") or 0)),
        })
    return rows


# ---------- AI Approval Center (admin-approved proposals) ----------
PROPOSAL_ACTIONS = ["verify_document", "delete_document"]


def create_proposal(action_type, target_id, description, target_display,
                    before_state, after_state, reason, user):
    if action_type not in PROPOSAL_ACTIONS:
        raise ValueError("Unknown proposal action")
    c = _conn()
    c.execute("""INSERT INTO ai_proposals
        (action_type, target_id, description, target_display, before_state,
         after_state, reason, created_by, created_name, created_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?, 'PENDING')""",
        (action_type, target_id, description or "", target_display or "",
         json.dumps(before_state or {}, ensure_ascii=False),
         json.dumps(after_state or {}, ensure_ascii=False),
         reason or "", user["id"], user.get("full_name") or user.get("email", ""),
         time.time()))
    c.commit()
    pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.close()
    return get_proposal(pid)


def list_proposals(status=None):
    c = _conn()
    sql = "SELECT * FROM ai_proposals"
    if status:
        sql += " WHERE status=?"
        rows = c.execute(sql, (status,)).fetchall()
    else:
        rows = c.execute(sql).fetchall()
    c.close()
    return [dict(r) for r in rows][::-1]


def get_proposal(pid):
    c = _conn()
    r = c.execute("SELECT * FROM ai_proposals WHERE id=?", (pid,)).fetchone()
    c.close()
    return dict(r) if r else None


def decide_proposal(pid, decision, user):
    """Approve/reject an AI proposal. Approving re-authorizes the action
    server-side: the proposal must still be PENDING (executed exactly
    once) and the target's BEFORE-state must still match what the AI saw
    when it made the proposal — otherwise the execution fails safely."""
    p = get_proposal(pid)
    if not p:
        raise ValueError("Proposal not found")
    if decision == "reject":
        c = _conn()
        c.execute("UPDATE ai_proposals SET status='REJECTED', executed_by=?, executed_at=? WHERE id=?",
                  (user["email"], time.time(), pid))
        c.commit()
        c.close()
        audit(p.get("target_id"), user["id"], user["email"], "ai_proposal_rejected",
              "proposal #%s (%s)" % (pid, p["action_type"]))
        return get_proposal(pid)
    # approve
    if p["status"] != "PENDING":
        raise ValueError("Proposal already %s — it can be executed only once" % p["status"])
    before_expected = json.loads(p.get("before_state") or "{}")
    doc = get_document(p["target_id"] or "") if p["target_id"] else None
    stale = None
    if p["action_type"] == "verify_document":
        if not doc:
            stale = "the target record no longer exists"
        elif (doc.get("status") != before_expected.get("status")
              or json.loads(doc.get("extracted_json") or "{}") != before_expected.get("fields")):
            stale = "the record changed after the proposal was made (status/fields differ)"
    elif p["action_type"] == "delete_document":
        if not doc:
            stale = "the target record no longer exists (already deleted?)"
        elif doc.get("status") != before_expected.get("status"):
            stale = "the record's status changed after the proposal was made"
    if stale:
        c = _conn()
        c.execute("UPDATE ai_proposals SET status='FAILED', executed_by=?, executed_at=?, error=? WHERE id=?",
                  (user["email"], time.time(), stale, pid))
        c.commit()
        c.close()
        audit(p.get("target_id"), user["id"], user["email"], "ai_proposal_stale",
              "proposal #%s not executed: %s" % (pid, stale))
        return get_proposal(pid)
    # execute
    try:
        if p["action_type"] == "verify_document":
            fields = mark_verified(p["target_id"], {}, user)
            if fields is None:
                raise RuntimeError("record vanished during execution")
            certify_document(p["target_id"], user["email"])
        elif p["action_type"] == "delete_document":
            if not hard_delete_document(p["target_id"], user):
                raise RuntimeError("record vanished during execution")
    except Exception as e:  # noqa: BLE001
        c = _conn()
        c.execute("UPDATE ai_proposals SET status='FAILED', executed_by=?, executed_at=?, error=? WHERE id=?",
                  (user["email"], time.time(), str(e), pid))
        c.commit()
        c.close()
        raise ValueError("Execution failed: %s" % e)
    c = _conn()
    c.execute("UPDATE ai_proposals SET status='EXECUTED', executed_by=?, executed_at=? WHERE id=?",
              (user["email"], time.time(), pid))
    c.commit()
    c.close()
    audit(p.get("target_id"), user["id"], user["email"], "ai_proposal_executed",
          "proposal #%s (%s) approved & executed by admin" % (pid, p["action_type"]))
    return get_proposal(pid)


def hard_delete_document(doc_id, user):
    """Delete a document + its scan file, KEEPING the audit trail
    (the AI Approval Center path — deletions stay auditable)."""
    c = _conn()
    doc = c.execute("SELECT stored_path FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        c.close()
        return False
    c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    c.commit()
    c.close()
    if doc["stored_path"] and os.path.exists(doc["stored_path"]):
        try:
            os.remove(doc["stored_path"])
        except OSError:
            pass
    audit(doc_id, user["id"], user["email"], "record_deleted", "")
    return True


# ---------- AI Task Inbox (role-to-role workflow tasks) ----------
TASK_PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
TASK_TRANSITIONS = {
    "PENDING": ["ACCEPTED", "RETURNED"],
    "ACCEPTED": ["IN_PROGRESS", "RETURNED"],
    "IN_PROGRESS": ["COMPLETED", "BLOCKED", "ESCALATED"],
    "BLOCKED": ["IN_PROGRESS", "ESCALATED"],
    "ESCALATED": ["IN_PROGRESS", "COMPLETED"],
    "RETURNED": ["ACCEPTED"],
}


def _next_task_id(c):
    year = time.strftime("%Y")
    row = c.execute("SELECT id FROM ai_tasks WHERE id LIKE ? ORDER BY id DESC LIMIT 1",
                    ("TASK-%s-%%" % year,)).fetchone()
    n = 1
    if row and row["id"]:
        try:
            n = int(row["id"].rsplit("-", 1)[1]) + 1
        except (ValueError, IndexError):
            n = 1
    return "TASK-%s-%04d" % (year, n)


def create_task(title, description, priority, assigned_role, user, record_id=None):
    if not (title or "").strip():
        raise ValueError("Task title is required")
    if priority not in TASK_PRIORITIES:
        raise ValueError("Invalid priority")
    if assigned_role not in ("operator", "verifier", "admin"):
        raise ValueError("Tasks can only be assigned to staff roles")
    c = _conn()
    tid = _next_task_id(c)
    ts = time.time()
    c.execute("""INSERT INTO ai_tasks
        (id, title, description, priority, status, assigned_role, assigned_name,
         record_id, created_by, created_name, created_at, updated_at, log)
        VALUES (?,?,?,?,?,?,'',?,? ,?, ?,?,?)""",
        (tid, title.strip(), (description or "").strip(), priority, "PENDING",
         assigned_role, record_id, user["id"],
         user.get("full_name") or user.get("email", ""), ts, ts,
         json.dumps([{"ts": ts, "who": user.get("email", ""), "note": "task created",
                      "status": "PENDING"}], ensure_ascii=False)))
    c.commit()
    c.close()
    return get_task(tid)


def list_tasks(role):
    """Admins see every task; staff see tasks assigned to their role."""
    c = _conn()
    if role == "admin":
        rows = c.execute("SELECT * FROM ai_tasks ORDER BY created_at DESC LIMIT 200").fetchall()
    else:
        rows = c.execute("SELECT * FROM ai_tasks WHERE assigned_role=? ORDER BY created_at DESC LIMIT 200",
                         (role,)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["log"] = json.loads(d["log"] or "[]")
        out.append(d)
    return out


def get_task(tid):
    c = _conn()
    r = c.execute("SELECT * FROM ai_tasks WHERE id=?", (tid,)).fetchone()
    c.close()
    if not r:
        return None
    d = dict(r)
    d["log"] = json.loads(d["log"] or "[]")
    return d


def respond_task(tid, new_status, user):
    t = get_task(tid)
    if not t:
        raise ValueError("Task not found")
    allowed = TASK_TRANSITIONS.get(t["status"], [])
    if new_status not in allowed:
        raise ValueError("Invalid transition %s -> %s (allowed: %s)"
                         % (t["status"], new_status, ", ".join(allowed) or "none"))
    c = _conn()
    ts = time.time()
    log = t["log"]
    log.append({"ts": ts, "who": user.get("email", ""), "note": "", "status": new_status})
    c.execute("UPDATE ai_tasks SET status=?, updated_at=?, log=? WHERE id=?",
              (new_status, ts, json.dumps(log, ensure_ascii=False), tid))
    c.commit()
    c.close()
    return get_task(tid)


def task_counts(role):
    """Active (uncompleted) tasks for a role — for the inbox badge."""
    c = _conn()
    if role == "admin":
        row = c.execute("SELECT COUNT(*) n FROM ai_tasks WHERE status IN "
                        "('PENDING','ACCEPTED','IN_PROGRESS','BLOCKED','ESCALATED')").fetchone()
    else:
        row = c.execute("SELECT COUNT(*) n FROM ai_tasks WHERE assigned_role=? AND status IN "
                        "('PENDING','ACCEPTED','IN_PROGRESS','BLOCKED','ESCALATED')", (role,)).fetchone()
    c.close()
    return row["n"] if row else 0


def dashboard_stats():
    c = _conn()
    total = c.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
    verified = c.execute("SELECT COUNT(*) n FROM documents WHERE status='verified'").fetchone()["n"]
    auto = c.execute("SELECT COUNT(*) n FROM documents WHERE status='auto_approved'").fetchone()["n"]
    verified_total = verified + auto
    pending = c.execute("SELECT COUNT(*) n FROM documents WHERE status='pending_review'").fetchone()["n"]
    drafts = c.execute("SELECT COUNT(*) n FROM documents WHERE status='draft'").fetchone()["n"]
    returned = c.execute("SELECT COUNT(*) n FROM documents WHERE status='returned'").fetchone()["n"]
    rejected = c.execute("SELECT COUNT(*) n FROM documents WHERE status='rejected'").fetchone()["n"]
    flagged = c.execute("SELECT COUNT(*) n FROM documents WHERE verdict IN ('review','rejected')").fetchone()["n"]
    corr = c.execute("SELECT COUNT(*) n, COALESCE(SUM(count),0) s FROM corrections").fetchone()
    avg_conf = c.execute("SELECT AVG(mean_conf) a FROM documents").fetchone()["a"] or 0
    users = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
    rows = c.execute("SELECT extracted_json, doc_type FROM documents").fetchall()
    c.close()
    states, districts, by_type = {}, {}, {}
    for r in rows:
        f = json.loads(r["extracted_json"] or "{}")
        st = f.get("state", {}).get("value") if isinstance(f.get("state"), dict) else None
        dt = f.get("district", {}).get("value") if isinstance(f.get("district"), dict) else None
        if st:
            states[st] = states.get(st, 0) + 1
        if dt:
            districts[dt] = districts.get(dt, 0) + 1
        t = r["doc_type"] if r["doc_type"] else "land_record"
        by_type[t] = by_type.get(t, 0) + 1
    return {
        "total": total, "verified": verified_total, "pending_review": pending,
        "auto_approved": auto, "rejected": rejected, "users": users,
        "drafts": drafts, "returned": returned,
        "submissions": verified_total + pending,
        "avg_ocr_confidence": round(avg_conf, 1),
        "accuracy_estimate": round(verified_total / total * 100, 1) if total else 0,
        "ai_flagged": flagged,
        "ai_flag_rate": round(flagged / total * 100, 1) if total else 0,
        "human_corrections": corr["s"],
        "correction_rules": corr["n"],
        "by_type": by_type,
        "by_state": dict(sorted(states.items(), key=lambda x: -x[1])[:10]),
        "by_district": dict(sorted(districts.items(), key=lambda x: -x[1])[:10]),
    }


def check_duplicate(dedup_key):
    c = _conn()
    row = c.execute("SELECT id, filename FROM documents WHERE dedup_key=? AND dedup_key!=''",
                    (dedup_key,)).fetchone()
    c.close()
    return dict(row) if row else None


def find_transfer_candidate(survey, village, owner, limit_docs=3000):
    """Find an existing document on the SAME land (survey + village) for a
    new upload whose owner name differs. Returns (candidate, kind):

    kind "transfer" - the stored owner is genuinely different: the classic
      sale/mutation pattern (A sold to B; B's papers match A's record in
      every land detail, only the name changed).
    kind "variant"  - the stored owner is a close fuzzy match of the new
      name (same person, likely an OCR spelling variant): this is probably
      a RE-UPLOAD of the same record, not a transfer.

    A verified/auto-approved match is preferred (it is the record of title).
    Survey matching compares normalized raw strings (Devanagari/Arabic-Indic
    digits unified to ASCII, separators preserved), so "452" and "४५" match,
    but "45/2" is not confused with "452".
    """
    import difflib

    from . import common
    sv_c = common.normalize_numerals(survey or "").strip()
    v_n = common.normalize_numerals(village or "").strip().lower()
    o_n = common.normalize_numerals(owner or "").strip().lower()
    if not (sv_c and v_n and o_n):
        return None, None

    def _row(d, kind, g):
        return {"id": d["id"], "filename": d.get("filename"),
                "owner": g("owner_name"), "year": g("khatauni_year"),
                "status": d.get("status"), "kind": kind}

    best = {"verified_transfer": None, "verified_variant": None,
            "any_transfer": None, "any_variant": None}
    for d in list_documents(limit=limit_docs):
        f = d.get("fields") or {}
        g = lambda k: str((f.get(k) or {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        d_sv_c = common.normalize_numerals(g("survey_number")).strip()
        d_v = common.normalize_numerals(g("village")).strip().lower()
        d_o = common.normalize_numerals(g("owner_name")).strip().lower()
        if not (d_sv_c and d_v and d_o):
            continue
        if d_v != v_n or d_sv_c != sv_c:
            continue
        if d_o == o_n:
            continue  # exact same owner -> handled by the duplicate check
        ratio = difflib.SequenceMatcher(None, o_n, d_o).ratio()
        kind = "variant" if ratio >= 0.8 else "transfer"
        verified = d.get("status") in ("verified", "auto_approved")
        key = ("verified_" if verified else "any_") + kind
        if best[key] is None:
            best[key] = _row(d, kind, g)
    for key in ("verified_transfer", "verified_variant", "any_transfer", "any_variant"):
        if best[key]:
            return best[key], best[key]["kind"]
    return None, None


def mark_verified(doc_id, corrections, user):
    doc = get_document(doc_id)
    if not doc:
        return None
    fields = json.loads(doc["extracted_json"])
    c = _conn()
    ts = time.time()
    audit_rows = []
    for fid, newval in (corrections or {}).items():
        # Accept BOTH plain-string corrections (what the web UI sends) and
        # dict-shaped ones ({"value": ...}) from API clients — before this
        # fix a dict correction crashed mark_verified with AttributeError
        # and the whole verify request died with a 500.
        if isinstance(newval, dict):
            newval = newval.get("value", "")
        old = fields.get(fid, {}).get("value", "") if fid in fields else ""
        newval = (newval or "").strip()
        if fid in fields:
            fields[fid]["value"] = newval
            fields[fid]["confidence"] = 1.0
            fields[fid]["verified"] = True
        else:
            fields[fid] = {"value": newval, "confidence": 1.0, "verified": True}
        if newval != old and old:
            c.execute("""INSERT INTO corrections (field_id, wrong, right, count)
                         VALUES (?,?,?,1)
                         ON CONFLICT(field_id, wrong, right)
                         DO UPDATE SET count = count + 1""", (fid, old, newval))
            audit_rows.append((doc_id, ts, user["id"], user["email"], "correction",
                               f"{fid}: '{old}' -> '{newval}'"))
    cert_hash = compute_cert_hash(doc_id, fields, user["email"], ts)
    c.execute("UPDATE documents SET extracted_json=?, status='verified', verdict='verified', "
              "cert_hash=?, cert_by=?, cert_at=? WHERE id=?",
              (json.dumps(fields), cert_hash, user["email"], ts, doc_id))
    audit_rows.append((doc_id, ts, user["id"], user["email"], "verified",
                       f"{len(corrections or {})} fields confirmed"))
    audit_rows.append((doc_id, ts, user["id"], user["email"], "certified",
                       "certified copy issued, hash " + cert_hash[:16] + "…"))
    for (aid, ats, auid, aun, aact, adet) in audit_rows:
        _append_audit(c, aid, ats, auid, aun, aact, adet)
    c.commit()
    c.close()
    return fields


# Fields whose values are document-specific — learned corrections must
# NEVER touch these (a '45' -> '45/2' fix for one khatauni is always wrong
# for the next document).
NUMERIC_FIELD_IDS = {"survey_number", "khasra_number", "khata_number",
                     "plot_number", "mutation_no", "registration_no",
                     "khatauni_year"}


def get_learned_corrections(field_id=None):
    c = _conn()
    if field_id:
        rows = c.execute("SELECT id, wrong, right, count FROM corrections WHERE field_id=? "
                         "ORDER BY count DESC LIMIT 50", (field_id,)).fetchall()
    else:
        rows = c.execute("SELECT id, field_id, wrong, right, count FROM corrections "
                         "ORDER BY count DESC LIMIT 100").fetchall()
    c.close()
    return [dict(r) for r in rows]


def delete_correction(cid):
    """Remove a learned rule (the AI Feedback tab's per-row delete button)."""
    c = _conn()
    c.execute("DELETE FROM corrections WHERE id=?", (cid,))
    c.commit()
    c.close()


def apply_learned(fields):
    """Apply learned OCR-typo corrections — SAFELY.

    WHY THIS EXISTS: the old version did a blind substring replace of whole
    multi-word values across EVERY future upload. A legitimate correction on
    one document (owner 'रामस्वरूप शर्मा' -> 'रामस्वरूप शर्मा अग्रवाल') was then
    re-applied to every other document containing that substring — exactly
    the 'new upload shows the previous document's data' bug reported by the
    team (reproduced and verified before this fix).

    A rule is applied only when ALL of these hold:
      * the field is a TEXT field (numeric fields are never touched),
      * the 'wrong' string is ONE word of 4+ chars (a real OCR-typo pattern
        like 'Matation'->'Mutation' — never a full name or number),
      * the rule has been independently confirmed at least TWICE
        (count >= 2 — one verifier can be wrong, two is a pattern),
      * only the first occurrence is replaced.
    Applied fields are flagged auto_corrected=True so the UI can mark them
    for a human double-check.
    """
    c = _conn()
    rows = c.execute("SELECT field_id, wrong, right, count FROM corrections "
                     "WHERE count >= 2 ORDER BY count DESC").fetchall()
    c.close()
    for r in rows:
        fid = r["field_id"]
        if fid in NUMERIC_FIELD_IDS or fid not in fields:
            continue
        wrong = (r["wrong"] or "").strip()
        right = (r["right"] or "").strip()
        if not wrong or not right or wrong == right:
            continue
        if " " in wrong or len(wrong) < 4:
            continue  # multi-word values / short substrings never generalize
        val = fields[fid].get("value", "")
        if not val or wrong.lower() not in val.lower():
            continue
        i = val.lower().index(wrong.lower())
        fields[fid]["value"] = val[:i] + right + val[i + len(wrong):]
        fields[fid]["auto_corrected"] = True
    return fields


def get_audit(doc_id=None, limit=500):
    c = _conn()
    if doc_id:
        rows = c.execute("SELECT ts, user_id, username, action, detail, entry_hash FROM audit "
                         "WHERE doc_id=? ORDER BY ts", (doc_id,)).fetchall()
    else:
        rows = c.execute("SELECT doc_id, ts, user_id, username, action, detail, entry_hash FROM audit "
                         "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]

# --------------------------------------------------------------------------
# AI OCR rescue routing — unreadable docs go to the LEAST-LOADED officer
# --------------------------------------------------------------------------
def officer_loads():
    """Current workload of every verification officer.

    load = pending-verification documents assigned to them (routed_to)
           PLUS their own pending uploads (uploaded_by). The officer with
           the smallest load is the one new AI-routed documents go to.
    """
    c = _conn()
    users = c.execute("SELECT id, email, full_name, role FROM users WHERE is_active=1 "
                      "AND role IN ('verifier','admin') ORDER BY email").fetchall()
    if not users:
        c.close()
        return []
    out = []
    for u in users:
        row = c.execute("""SELECT
              (SELECT COUNT(*) FROM documents WHERE status='pending_review' AND routed_to=?) routed,
              (SELECT COUNT(*) FROM documents WHERE status='pending_review' AND uploaded_by=?) own
              """, (u["id"], u["id"])).fetchone()
        load = (row[0] or 0) + (row[1] or 0)
        out.append({"id": u["id"], "email": u["email"],
                    "name": u["full_name"] or u["email"],
                    "role": u["role"], "routed_pending": row[0] or 0,
                    "own_pending": row[1] or 0, "load": load})
    c.close()
    out.sort(key=lambda o: (o["load"], o["email"]))
    for o in out:
        o["least_loaded"] = o is out[0]
    return out


def pick_least_loaded_officer():
    """The verification officer with the smallest current load (or None)."""
    loads = officer_loads()
    return loads[0] if loads else None


def route_document(doc_id, officer, reason, uploaded_by=None, uploaded_by_email=None):
    """Mark an unreadable record as routed to a specific officer + open an
    AI task in their inbox. Keeps the record in the normal verification
    queue (status stays pending_review) so the officer can work on it."""
    if not officer:
        return False
    c = _conn()
    c.execute("UPDATE documents SET routed_to=?, routed_to_name=?, routing_reason=?, routed_at=? WHERE id=?",
              (officer["id"], officer.get("name") or officer.get("email", ""),
               (reason or "")[:500], time.time(), doc_id))
    c.commit()
    c.close()
    audit(doc_id, uploaded_by or officer["id"], uploaded_by_email or officer.get("email", ""),
          "ai_routed", "routed to %s (%s): %s" % (officer.get("name", ""), officer.get("email", ""), (reason or "")[:200]))
    # AI task in the officer's role inbox
    try:
        create_task("🤖 AI-unreadable record needs manual review",
                    "The AI could not read this document automatically: %s "
                    "Please open it, read the original scan, and enter the fields by hand." % (reason or ""),
                    "HIGH", "verifier",
                    {"id": officer["id"], "email": officer.get("email", ""),
                     "full_name": officer.get("name", "")},
                    record_id=doc_id)
    except Exception:  # noqa: BLE001 — the task is a nicety; routing itself is done
        pass
    return True


def clear_routing(doc_id, user):
    c = _conn()
    c.execute("UPDATE documents SET routed_to=NULL, routed_to_name=NULL, "
              "routing_reason=NULL, routed_at=NULL WHERE id=?", (doc_id,))
    c.commit()
    c.close()
    audit(doc_id, user["id"], user["email"], "ai_routing_cleared", "record processed — routing removed")


def set_ai_rescue(doc_id, info):
    c = _conn()
    c.execute("UPDATE documents SET ai_rescue_json=? WHERE id=?",
              (json.dumps(info, ensure_ascii=False), doc_id))
    c.commit()
    c.close()


def count_ai_routed():
    c = _conn()
    row = c.execute("SELECT COUNT(*) n FROM documents WHERE routed_to IS NOT NULL "
                    "AND status='pending_review'").fetchone()
    c.close()
    return row["n"] if row else 0
