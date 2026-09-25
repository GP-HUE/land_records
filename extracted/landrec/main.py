"""FastAPI application: Intelligent Land Record Digitization & Validation System.

Multi-user with signup/sign-in, role-based access control, document upload,
verification workflow, learning loop, dashboards, and audit trail.
"""
import json
import logging
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

# NOTE: landrec.ocr is intentionally NOT imported here. All native OCR work
# (OpenCV / Tesseract / PyMuPDF) runs in the disposable worker process via
# ocrpool, so a bad file can never crash the web-server process itself.
from . import ai_rescue, auth, cert_pdf, common, extractor, ocrpool, paths, store, validator
from . import ai_support, ai_assistant, sa_admin, courtlink

PKG = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(paths.resource_dir(), "landrec", "static")
SAMPLES_DIR = os.path.join(paths.resource_dir(), "samples")

SESSION_COOKIE = "lr_session"

app = FastAPI(title="Intelligent Land Record Digitization & Validation System")
store.init_db()
courtlink.init_db()   # demo court database (SA CourtLink) — table + seed
log = logging.getLogger("landrec")
_START_TIME = time.time()

# Build version — shown in the UI footer and the System Status panel.
# Bump this every time a new zip is released so users can instantly tell
# whether their local .exe is the current build or an old one.
APP_VERSION = "3.11.0"


def _warmup_ocr_worker():
    """Pre-start the OCR engine so the FIRST upload doesn't pay the cost.

    Starting the worker means re-extracting the frozen bundle and importing
    OpenCV/Tesseract — tens of seconds on a slow laptop, and it used to land
    right in the middle of the user's first upload (the "spinner does
    nothing for a minute" complaint). Failures are fine: the worker is
    lazily re-spawned on the first real OCR request.
    """
    try:
        ocrpool.ensure_worker()
        log.info("OCR worker pre-warmed at startup")
    except Exception:  # noqa: BLE001 — retried lazily on first upload
        pass


threading.Thread(target=_warmup_ocr_worker, daemon=True,
                 name="ocr-warmup").start()


def _first_boot_demo_seed():
    """Seed demo data on a FRESH install so the app is usable immediately.

    Historically the demo seeder only ran on Render (ephemeral disk), so a
    fresh LOCAL install booted with an empty database: the Village Sheet
    showed blank district/tehsil/village dropdowns and no plot grid, the
    dashboard/queue were empty, etc. Now any boot with zero documents
    (local start.bat / run.py / .exe, or a wiped hosted disk) gets the
    demo state automatically. Existing databases are never touched, and
    the whole thing is opt-out-able with LR_NO_DEMO_SEED=1.
    """
    try:
        from landrec import seed_demo
        res = seed_demo.seed_if_empty()
        if res.get("seeded"):
            log.info("First boot: demo data seeded (%d documents)", res.get("documents", 0))
    except Exception:  # noqa: BLE001 — a seed failure must never block boot
        log.exception("First-boot demo seed failed (non-fatal)")


_first_boot_demo_seed()

# --------------------------------------------------------------------------
# Security: simple in-memory per-IP rate limiter (login + expensive OCR)
# --------------------------------------------------------------------------
_RATE_BUCKETS: "defaultdict" = defaultdict(deque)


def _rate_limit(key: str, limit: int, window: int = 60):
    now = time.time()
    q = _RATE_BUCKETS[key]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "बहुत अधिक अनुरोध — कृपया 1 मिनट रुकें। (Too many requests — please wait a minute.)")
    q.append(now)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    # frame-ancestors only (a full CSP would break the inline-UI build).
    # *.e2b.app is the Arena live-preview host; everything else is blocked.
    resp.headers.setdefault(
        "Content-Security-Policy", "frame-ancestors 'self' https://*.e2b.app")
    resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    return resp


def _set_session_cookie(response: Response, token: str, secure: bool = False):
    # HttpOnly keeps the token out of reach of JS; SameSite=Lax mitigates CSRF.
    # secure=True whenever the request arrived over HTTPS (reverse proxy).
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=auth.TOKEN_TTL, path="/", secure=secure)


def _clear_session_cookie(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")


# --------------------------------------------------------------------------
# Auth dependencies
# --------------------------------------------------------------------------
def get_current_user(request: Request, authorization: str = Header(None)) -> dict:
    # Session token channels (in priority order):
    #   1. session cookie (HttpOnly)     — normal browser use (secure)
    #   2. Authorization: Bearer header  — SPA fetch() / direct connections (secure)
    #   3. ?token= query parameter       — LAST-RESORT fallback for the
    #      sandboxed live-preview iframe, which strips the Authorization
    #      header and does not persist cookies. In the real .exe (localhost)
    #      the secure cookie/header channels are used and the query param is
      # never needed.
    token = request.cookies.get(SESSION_COOKIE)
    if not token and authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        token = request.query_params.get("token")
    if not token:
        raise HTTPException(401, "Not authenticated")
    payload = auth.verify_token(token)
    if not payload:
        raise HTTPException(401, "Invalid or expired session — please sign in again")
    user = store.get_user(payload["uid"])
    if (not user or not user["is_active"]
            or user["token_version"] != payload["ver"]):
        raise HTTPException(401, "Invalid session")
    return user


def require_role(min_role: str):
    def dep(user: dict = Depends(get_current_user)) -> dict:
        if auth.ROLE_LEVELS[user["role"]] < auth.ROLE_LEVELS[min_role]:
            raise HTTPException(403, "Insufficient permissions for this action")
        return user
    return dep


def _public_user(user: dict) -> dict:
    return {k: user[k] for k in ("id", "email", "full_name", "role", "created_at")}


# --------------------------------------------------------------------------
# Authentication endpoints
# --------------------------------------------------------------------------
@app.post("/api/auth/signup")
def signup(payload: dict, request: Request, response: Response):
    # Public self-registration is allowed with a ROLE CHOICE (like the
    # reference portal): Viewer (search only) or Data Operator (upload &
    # extract). Verifier/Admin can only be granted by the System
    # Administrator from the Users tab.
    role = (payload.get("role") or "operator").strip().lower()
    if role not in ("viewer", "operator"):
        role = "operator"
    try:
        uid = store.create_user(payload.get("email", ""), payload.get("password", ""),
                                payload.get("full_name", ""), role=role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    user = store.get_user(uid)
    token = auth.create_token(user["id"], user["role"], user["token_version"])
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    store.audit(None, user["id"], user["email"], "signup", user["email"])
    return {"token": token, "user": _public_user(user)}


@app.post("/api/auth/login")
def login(payload: dict, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    _rate_limit("login:" + ip, 10, 60)   # max 10 logins / minute / IP
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""
    if store.login_locked(email):
        raise HTTPException(429, "5 गलत प्रयास — अकाउंट 5 मिनट के लिए लॉक है। (Too many failed attempts — locked for 5 minutes.)")
    user = store.get_user_by_email(email)
    if not user or not auth.verify_password(password, user["salt"], user["password_hash"]):
        store.record_login_failure(email)
        if not user:
            # Clear message (issue #1): the account simply does not exist on
            # THIS machine — accounts are created per-installation.
            raise HTTPException(401, "इस ईमेल आईडी से कोई अकाउंट नहीं मिला। (No account with this email on this installation. Each computer has its own database — the System Administrator creates officer accounts from the Users tab ON THIS COMPUTER.)")
        left = 5 - store.get_login_failures(email)
        raise HTTPException(401, f"पासवर्ड गलत है। (Password is incorrect. {max(left, 0)} attempt(s) left before a 5-minute lock.)")
    if not user["is_active"]:
        raise HTTPException(403, "Account is deactivated. Contact an administrator.")
    store.clear_login_failures(email)
    token = auth.create_token(user["id"], user["role"], user["token_version"])
    _set_session_cookie(response, token, secure=request.url.scheme == "https")
    store.audit(None, user["id"], user["email"], "login", "")
    return {"token": token, "user": _public_user(user)}


@app.get("/api/auth/me")
def me(user: dict = Depends(get_current_user)):
    out = _public_user(user)
    # Drives the "default admin password still active" warning banner.
    out["default_password"] = auth.verify_password(
        auth.DEFAULT_ADMIN_PASSWORD, user["salt"], user["password_hash"])
    return {"user": out}


@app.post("/api/auth/logout")
def logout(response: Response, user: dict = Depends(get_current_user)):
    store.bump_token_version(user["id"])
    sa_admin.end_sessions_for_user(user["id"])  # SA mode never survives logout
    store.audit(None, user["id"], user["email"], "logout", "")
    _clear_session_cookie(response)
    return {"ok": True}


@app.post("/api/auth/change-password")
def change_password(payload: dict, user: dict = Depends(get_current_user)):
    current = payload.get("current_password", "")
    new = payload.get("new_password", "")
    if not auth.verify_password(current, user["salt"], user["password_hash"]):
        raise HTTPException(400, "Current password is incorrect")
    try:
        store.change_password(user["id"], new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.audit(None, user["id"], user["email"], "change_password", "")
    return {"ok": True}


# --------------------------------------------------------------------------
# User management (admin only)
# --------------------------------------------------------------------------
@app.get("/api/users")
def list_users(user: dict = Depends(require_role("admin"))):
    return {"users": store.list_users()}


@app.post("/api/users")
def create_user(payload: dict, user: dict = Depends(require_role("admin"))):
    role = payload.get("role", "operator")
    if role not in auth.ROLES:
        raise HTTPException(400, "Invalid role")
    try:
        uid = store.create_user(payload.get("email", ""), payload.get("password", ""),
                                payload.get("full_name", ""), role=role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.audit(None, user["id"], user["email"], "create_user",
                f"{payload.get('email')} as {role}")
    return {"ok": True, "id": uid}


@app.patch("/api/users/{uid}")
def update_user(uid: str, payload: dict, user: dict = Depends(require_role("admin"))):
    if uid == user["id"] and payload.get("role") not in (None, "admin"):
        raise HTTPException(400, "You cannot demote your own account")
    role = payload.get("role")
    if role is not None and role not in auth.ROLES:
        raise HTTPException(400, "Invalid role")
    store.update_user(uid, role=role, is_active=payload.get("is_active"))
    store.audit(None, user["id"], user["email"], "update_user", f"{uid} role={role}")
    return {"ok": True}


@app.post("/api/users/{uid}/reset-password")
def reset_password(uid: str, payload: dict, user: dict = Depends(require_role("admin"))):
    """Admin resets a teammate's forgotten password (fixes issue #1)."""
    if not store.get_user(uid):
        raise HTTPException(404, "User not found")
    new = payload.get("password") or ""
    reason = auth.weak_password_reason(new)
    if reason:
        raise HTTPException(400, reason)
    try:
        store.change_password(uid, new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.audit(None, user["id"], user["email"], "reset_password", f"for user {uid}")
    return {"ok": True}


# --------------------------------------------------------------------------
# Account export / import — share accounts between computers (issue #1).
# Each computer has its own database, so this is how a team copies accounts
# from one PC to another.
# --------------------------------------------------------------------------
@app.get("/api/users/export")
def export_users(user: dict = Depends(require_role("admin"))):
    data = store.export_users()
    store.audit(None, user["id"], user["email"], "users_export",
                f"{len(data['users'])} accounts exported")
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="landrec-accounts.json"'})


@app.post("/api/users/import")
def import_users(payload: dict, user: dict = Depends(require_role("admin"))):
    try:
        result = store.import_users(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.audit(None, user["id"], user["email"], "users_import",
                f"added={result['added']} updated={result['updated']} total={result['total']}")
    return result


# --------------------------------------------------------------------------
# Processing pipeline
# --------------------------------------------------------------------------
# Tesseract language codes the UI may hint (skips the auto-detect pass).
_VALID_LANGS = {"eng", "hin", "ben", "guj", "pan", "ori", "tam", "tel",
                "kan", "mal", "urd"}


def _parse_lang(lang: str):
    """Optional language hint from the UI -> [code] or None (auto-detect)."""
    code = (lang or "").strip().lower()
    return [code] if code in _VALID_LANGS else None


def _check_transfer_hint(fields: dict, validation: dict):
    """Detect what a same-land / different-name upload actually is:

    * transfer - the stored owner is genuinely different: the classic
      sale/mutation pattern (A sold to B; B's papers match A's record in
      every land detail). Routed for a quick human check (severity
      'review' -> pending_review, never auto-approved) with a clear hint
      to verify against the mutation record. NOT flagged as a conflict.
    * variant  - the stored owner is a close fuzzy match of the new name
      (same person, likely an OCR spelling variant): probably a RE-UPLOAD
      of the same record. Flagged as a possible duplicate so the verifier
      confirms it is not a double entry.
    """
    g = lambda k: str((fields.get(k) or {}).get("value", "") or "") \
        if isinstance(fields.get(k), dict) else ""
    owner, survey, village = g("owner_name"), g("survey_number"), g("village")
    if not (owner and survey and village):
        return
    cand, kind = store.find_transfer_candidate(survey, village, owner)
    if not cand:
        return
    if kind == "variant":
        validation["issues"].append(
            {"field": "duplicate", "severity": "warning",
             "msg": f"Possible duplicate of record {cand['id']} ({cand.get('filename') or 'record'}): "
                    f"same land, and the owner name differs only slightly "
                    f"('{cand.get('owner') or '?'}' vs '{owner}') - may be an OCR "
                    "variation of the same person. Confirm it is not a double entry."})
        validation["duplicate_of"] = cand["id"]
    else:
        status_note = "" if cand.get("status") in ("verified", "auto_approved") \
            else f" (status: {cand.get('status')} - not yet verified itself)"
        validation["issues"].append(
            {"field": "transfer", "severity": "review",
             "msg": "Possible ownership transfer: same survey + village as record "
                    f"{cand['id']} ({cand.get('filename') or 'record'}) where the owner is "
                    f"'{cand.get('owner') or '?'}'{status_note}, but a different owner here "
                    "- verify with the mutation/sale record before approving."})
        validation["transfer_of"] = cand["id"]
    # This hint arrives after validator.validate() set the verdict, so a
    # 'valid' verdict must be downgraded - otherwise the record would be
    # auto-approved without anyone checking the transfer / re-upload.
    if validation.get("verdict") == "valid":
        validation["verdict"] = "review"


def _run_pipeline(data: bytes, filename: str, uploaded_by: dict,
                  stored_path: str = None, mime: str = None, size: int = None,
                  langs: list = None, doc_type: str = "land_record") -> dict:
    ocr_result = ocrpool.run_ocr(data, filename, langs=langs)
    fields = extractor.extract_fields(ocr_result)
    fields = store.apply_learned(fields)

    # ---- AI OCR RESCUE: if the first pass reads poorly, the AI retries
    # (language hints + enhanced scans) and merges the best fields
    quality = ai_rescue.assess(ocr_result, fields)
    rescue_info = None
    if not quality["ok"]:
        try:
            rescue_info = ai_rescue.rescue(
                data, filename, langs or [], ocr_result, fields,
                run_ocr=ocrpool.run_ocr, extract_fields=extractor.extract_fields,
                apply_learned=store.apply_learned)
        except Exception:  # noqa: BLE001 — rescue must never kill an upload
            log.exception("AI rescue failed for %s", filename)
            rescue_info = None
        if rescue_info:
            ocr_result = rescue_info["ocr"]
            fields = rescue_info["fields"]
            quality = rescue_info["after"]

    validation = validator.validate(fields, scripts=ocr_result["detected_scripts"])
    dedup_key = validator.duplicate_key(fields)
    dup = store.check_duplicate(dedup_key) if dedup_key else None
    if dup:
        validation["issues"].append(
            {"field": "duplicate", "severity": "warning",
             "msg": f"Possible duplicate of record {dup['id']} ({dup['filename']})"})
        validation["duplicate_of"] = dup["id"]
    if not dup:
        _check_transfer_hint(fields, validation)

    # ---- AI ROUTING: a document the AI still cannot read is NEVER
    # auto-approved — it is forced into the verification queue and sent
    # to the LEAST-LOADED verification officer with a diagnosis
    forced_status = None
    routed_officer = None
    if quality["unreadable"]:
        forced_status = "pending_review"
        routed_officer = store.pick_least_loaded_officer()
    doc_id = store.save_upload(filename, mime, size, stored_path, uploaded_by["id"],
                               ocr_result, fields, validation, dedup_key,
                               doc_type=doc_type, status=forced_status)
    store.audit(doc_id, uploaded_by["id"], uploaded_by["email"],
                "document_created", filename)
    ai_out = None
    if rescue_info is not None or quality["unreadable"]:
        ai_out = {"rescued": bool(rescue_info and rescue_info["rescued"]),
                  "passes": (rescue_info or {}).get("passes", 0),
                  "tried": (rescue_info or {}).get("tried", []),
                  "before": (rescue_info or {}).get("before"),
                  "diagnosis": quality["diagnosis"],
                  "unreadable": quality["unreadable"],
                  "key_before": ((rescue_info or {}).get("before") or {}).get("key_fields_filled"),
                  "key_after": quality["key_fields_filled"]}
        if quality["unreadable"]:
            reason = " ".join(quality["diagnosis"])[:500]
            if routed_officer:
                store.route_document(doc_id, routed_officer, reason,
                                     uploaded_by=uploaded_by["id"],
                                     uploaded_by_email=uploaded_by["email"])
                ai_out["routed_to"] = routed_officer.get("name") or routed_officer.get("email")
                ai_out["routed_to_email"] = routed_officer.get("email")
                ai_out["routed_load"] = routed_officer.get("load", 0)
            else:
                ai_out["routed_to"] = None
                ai_out["no_officer"] = True
                store.audit(doc_id, uploaded_by["id"], uploaded_by["email"],
                            "ai_unreadable", "unreadable but NO verification officer exists to receive it")
        store.set_ai_rescue(doc_id, ai_out)
        store.audit(doc_id, uploaded_by["id"], uploaded_by["email"],
                    "ai_rescue", "rescued=%s unreadable=%s key %s->%s" % (
                        ai_out["rescued"], ai_out["unreadable"],
                        ai_out.get("key_before"), ai_out.get("key_after")))
    return {"id": doc_id, "filename": filename,
            "ocr": {"mean_conf": ocr_result["mean_conf"],
                    "languages": ocr_result["detected_scripts"],
                    "pages": ocr_result["num_pages"],
                    "text_preview": ocr_result["full_text"][:1500]},
            "fields": fields, "validation": validation,
            "ai": ai_out}


def _maybe_sa_court_screen(result: dict, user: dict):
    """SA CourtLink (v3.11): when the ADMIN uploads a document, the Superior
    Administrator layer immediately screens the OCR-extracted land details
    against the demo court database and rides the findings back in the
    upload response.  Non-admin uploads are NOT screened — only admins can
    use this feature.  Screening failure must never break an upload."""
    try:
        if user and user.get("role") == "admin":
            res = courtlink.screen_fields(result.get("fields") or {})
            res["document_id"] = result.get("id")
            result["sa_screening"] = res
    except Exception:  # noqa: BLE001
        log.exception("SA CourtLink screening failed (non-fatal)")


@app.post("/api/process")
async def process(request: Request, file: UploadFile = File(...),
                  lang: str = Form(""), doc_type: str = Form("land_record"),
                  user: dict = Depends(require_role("operator"))):
    ip = request.client.host if request.client else "?"
    _rate_limit("ocr:" + ip, 10, 60)   # OCR is CPU-heavy — max 10/min/IP
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in store.ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{ext}'. "
                                 f"Allowed: {', '.join(sorted(store.ALLOWED_EXTENSIONS))}")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > store.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File exceeds {store.MAX_UPLOAD_BYTES // (1024*1024)} MB limit")
    stored_path = os.path.join(store.UPLOAD_DIR, uuid.uuid4().hex + ext)
    with open(stored_path, "wb") as fh:
        fh.write(data)
    try:
        result = _run_pipeline(data, file.filename or "upload", user,
                               stored_path=stored_path, mime=file.content_type,
                               size=len(data), langs=_parse_lang(lang),
                               doc_type=doc_type)
    except Exception:  # noqa: BLE001
        log.exception("Processing failed for %s", file.filename)
        if os.path.exists(stored_path):
            os.remove(stored_path)
        # No internals in the client-facing message (stack traces leak paths).
        raise HTTPException(500, "Processing failed. Please try again or contact the administrator.")
    _maybe_sa_court_screen(result, user)   # SA CourtLink: instant for admin
    return result


# Security: only allow plain filenames — blocks path traversal
# (e.g. '../../landrec/store.py' or Windows '..\..\windows\...').
SAMPLE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*\.(pdf|jpg|jpeg|png|tif|tiff)$", re.I)


@app.get("/api/ocr-progress")
def ocr_progress(user: dict = Depends(get_current_user)):
    """Live OCR stage for the processing screen (polled by the UI)."""
    return ocrpool.progress()


@app.post("/api/process/sample/{name}")
def process_sample(name: str, lang: str = Query(""), doc_type: str = Query("land_record"),
                   request: Request = None,
                   user: dict = Depends(require_role("operator"))):
    # (request is auto-injected by FastAPI; default only for optional safety)
    ip = request.client.host if request else "?"
    _rate_limit("ocr:" + ip, 10, 60)   # same OCR budget as uploads (v3.5.1)
    if not SAMPLE_NAME_RE.fullmatch(name):
        raise HTTPException(400, "Invalid sample name")
    base = os.path.realpath(SAMPLES_DIR)
    path = os.path.realpath(os.path.join(SAMPLES_DIR, name))
    if not (path == base or path.startswith(base + os.sep)):
        raise HTTPException(400, "Invalid sample name")
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(404, "Sample not found")
    with open(path, "rb") as fh:
        data = fh.read()
    result = _run_pipeline(data, name, user, langs=_parse_lang(lang), doc_type=doc_type)
    _maybe_sa_court_screen(result, user)   # SA CourtLink: instant for admin
    return result


@app.get("/api/samples")
def list_samples(user: dict = Depends(get_current_user)):
    d = SAMPLES_DIR
    if not os.path.exists(d):
        return {"samples": []}
    return {"samples": sorted(f for f in os.listdir(d) if not f.startswith("."))}


# --------------------------------------------------------------------------
# Documents, verification, dashboards
# --------------------------------------------------------------------------
@app.get("/api/documents")
def documents(status: str = Query(""), user: dict = Depends(get_current_user)):
    return {"documents": store.list_documents(status=status or None)}


@app.get("/api/documents/{doc_id}")
def document(doc_id: str, user: dict = Depends(get_current_user)):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    doc["fields"] = json.loads(doc.pop("extracted_json") or "{}")
    doc["validation"] = json.loads(doc["validation_json"] or "{}")
    doc["boundary"] = _parse_boundary(doc.pop("boundary_geojson", None))
    doc.pop("ocr_text", None)
    doc["ai_decision_support"] = ai_support.decision_support(
        doc["fields"], doc["validation"], doc.get("mean_conf") or 0,
        doc.get("status") or "", doc.get("doc_type") or "")
    return doc


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: str, user: dict = Depends(get_current_user)):
    doc = store.get_document(doc_id)
    if not doc or not doc.get("stored_path") or not os.path.exists(doc["stored_path"]):
        raise HTTPException(404, "File not found")
    return FileResponse(doc["stored_path"], filename=doc["filename"])


@app.get("/api/documents/{doc_id}/certified-pdf")
def certified_pdf(doc_id: str, request: Request, user: dict = Depends(get_current_user)):
    """Download the official CERTIFIED COPY (PDF + QR) of a VERIFIED
    record. The QR points at the public /verify/<id> page, which checks
    the certification hash live — scan it with a phone to prove the
    paper is genuine and unaltered."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") not in ("verified", "auto_approved"):
        raise HTTPException(403, "Certified copies are issued only for VERIFIED records "
                                 "(current status: %s)" % doc.get("status"))
    fields = json.loads(doc.get("extracted_json") or "{}")
    if not doc.get("cert_hash"):
        # legacy record verified before this feature existed: certify now
        store.certify_document(doc_id, doc.get("cert_by") or "system (re-certified)")
        doc = store.get_document(doc_id)
    host = request.headers.get("host") or "localhost:8000"
    base = "http://" + host
    data = cert_pdf.render_certified_pdf(doc, fields, base + "/verify/" + doc_id)
    fname = "certified_record_%s.pdf" % doc_id
    store.audit(doc_id, user["id"], user["email"], "certified_copy_downloaded", fname)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=%s" % fname})


@app.get("/api/documents/{doc_id}/history")
def record_history(doc_id: str, user: dict = Depends(get_current_user)):
    """Year-wise 'passbook' of one land: every record with the same survey
    number (and village, when known), oldest year first."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    f = json.loads(doc.get("extracted_json") or "{}")
    g = lambda k: str((f.get(k) or {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
    # the current record is INCLUDED (the UI highlights it as "THIS RECORD"),
    # so the passbook shows the complete story including the latest year
    items = store.record_history(g("survey_number"), g("village"))
    return {"survey": g("survey_number"), "village": g("village"),
            "current_id": doc_id,
            "items": items, "including_current": True}


# --------------------------------------------------------------------------
# ENCUMBRANCE (loan) + FRAUD-RISK checks — answers "is this land free of
# loans and free of fraud?" for the piece of land behind a record
# (same survey + village key as the year-wise history).
# --------------------------------------------------------------------------
def _land_key_from_doc(doc):
    f = json.loads(doc.get("extracted_json") or "{}")
    g = lambda k: str((f.get(k) or {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
    return g("survey_number"), g("village"), g("khasra_number")


def _land_risk_payload(doc, user=None):
    """Full risk answer for the land behind a document: encumbrances,
    mutation history and the computed flags."""
    survey, village, khasra = _land_key_from_doc(doc)
    if not survey:
        return {"survey": "", "village": village, "khasra": khasra,
                "encumbrances": [], "mutations": [], "flags": [],
                "active": 0, "verdict": "no_survey"}
    encs = store.list_encumbrances(survey, village)
    muts = [m for m in store.list_mutations(limit=300)
            if _norm_s(m.get("survey_number")) == _norm_s(survey)
            and (not village or _norm_s(m.get("village")) == _norm_s(village)
                 or not m.get("village"))]
    hist = store.record_history(survey, village)
    rec_rows = []
    for h in hist:
        rec_rows.append({"year": h.get("year"), "owner": h.get("owner"),
                         "area": h.get("area"), "khasra": h.get("khasra"),
                         "doc_id": h.get("id"), "filename": h.get("filename"),
                         "status": h.get("status"), "doc_type": h.get("doc_type")})
    from landrec import risk
    cases = store.list_court_cases(survey, village)
    flags = risk.land_risk(rec_rows, encs, muts, cases)
    active = sum(1 for e in encs if e.get("status") == "active")
    active_cases = sum(1 for c in cases if c.get("status") == "active")
    if active:
        verdict = "encumbered"
    elif active_cases:
        verdict = "litigation"
    elif any(f["severity"] == "critical" for f in flags):
        verdict = "risk"
    elif any(f["severity"] == "warning" for f in flags):
        verdict = "review"
    else:
        verdict = "clear"
    return {"survey": survey, "village": village, "khasra": khasra,
            "encumbrances": encs, "mutations": muts, "court_cases": cases,
            "flags": flags, "active": active, "active_cases": active_cases,
            "verdict": verdict}


def _norm_s(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


@app.get("/api/encumbrances")
def list_encumbrances(survey: str = Query(""), village: str = Query(""),
                      khasra: str = Query(""),
                      user: dict = Depends(get_current_user)):
    if not survey:
        raise HTTPException(400, "survey number is required")
    encs = store.list_encumbrances(survey, village, khasra)
    return {"encumbrances": encs,
            "active": sum(1 for e in encs if e["status"] == "active")}


@app.post("/api/encumbrances")
def create_encumbrance(payload: dict, user: dict = Depends(require_role("operator"))):
    """Record a loan/mortgage against a piece of land (Data Operator+)."""
    try:
        e = store.create_encumbrance(payload, user)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return e


@app.post("/api/encumbrances/{eid}/settle")
def settle_encumbrance(eid: str, payload: dict, user: dict = Depends(require_role("verifier"))):
    """Record the loan release (bank NOC) — Verification Officer/Admin."""
    try:
        e = store.settle_encumbrance(eid, user,
                                     settlement_date=payload.get("settlement_date", ""),
                                     notes=payload.get("notes", ""))
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    if not e:
        raise HTTPException(404, "Not found")
    return e


# --------------------------------------------------------------------------
# ⚖️ COURT CASES / LITIGATION on a piece of land
# --------------------------------------------------------------------------
@app.get("/api/court-cases")
def list_court_cases(survey: str = Query(""), village: str = Query(""),
                     user: dict = Depends(get_current_user)):
    """All court cases registered against a piece of land (survey + village)."""
    if not survey:
        raise HTTPException(400, "survey number is required")
    cases = store.list_court_cases(survey, village)
    return {"court_cases": cases,
            "active": sum(1 for c in cases if c["status"] == "active")}


@app.post("/api/court-cases")
def create_court_case(payload: dict, user: dict = Depends(require_role("operator"))):
    """Register a court case / litigation against a piece of land."""
    try:
        c = store.create_court_case(payload, user)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return c


@app.post("/api/court-cases/{cid}/close")
def close_court_case(cid: str, payload: dict, user: dict = Depends(require_role("verifier"))):
    """Record the outcome of a case (decided / withdrawn / settled) with the
    decision summary — Verification Officer/Admin."""
    try:
        c = store.close_court_case(cid, user,
                                   status=payload.get("status", "decided"),
                                   closed_date=payload.get("closed_date", ""),
                                   decision_summary=payload.get("decision_summary", ""))
    except ValueError as ex:
        raise HTTPException(409, str(ex))
    if not c:
        raise HTTPException(404, "Not found")
    return c


@app.get("/api/documents/{doc_id}/risk")
def document_risk(doc_id: str, user: dict = Depends(get_current_user)):
    """Encumbrance + fraud-risk report for the land behind this record:
    active/settled loans, the owner chain, and rule-based risk flags with
    evidence. Local computation only — no external calls."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    return _land_risk_payload(doc)


@app.get("/api/documents/{doc_id}/encumbrance-pdf")
def encumbrance_pdf(doc_id: str, request: Request,
                    years: int = Query(13, ge=1, le=30),
                    user: dict = Depends(get_current_user)):
    """Encumbrance Certificate (EC) style report for the land: 'free of
    encumbrances in the last N years' — or the list of encumbrances found.
    Internal risk-check report; the legally conclusive EC is issued by the
    Sub-Registrar (stated on the document)."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    data = cert_pdf.render_encumbrance_pdf(doc, _land_risk_payload(doc), years,
                                           issued_by=user.get("email") or "")
    fname = "encumbrance_report_%s.pdf" % doc_id
    store.audit(doc_id, user["id"], user["email"], "encumbrance_report_downloaded", fname)
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=%s" % fname})


@app.post("/api/documents/{doc_id}/verify")
def verify(doc_id: str, payload: dict, user: dict = Depends(require_role("verifier"))):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") == "rejected":
        raise HTTPException(409, "Record was rejected and is permanently closed")
    corrections = payload.get("corrections", {})
    fields = store.mark_verified(doc_id, corrections, user)
    if fields is None:
        raise HTTPException(404, "Not found")
    if doc.get("routed_to"):
        store.clear_routing(doc_id, user)
    return {"id": doc_id, "fields": fields, "status": "verified"}


@app.get("/api/queue/loads")
def queue_loads(user: dict = Depends(require_role("verifier"))):
    """Workload of every verification officer — used to show who is the
    LEAST LOADED (AI-routed unreadable documents always go there)."""
    loads = store.officer_loads()
    return {"officers": loads,
            "least_loaded": loads[0]["name"] if loads else None}


@app.post("/api/documents/{doc_id}/save-draft")
def save_draft(doc_id: str, payload: dict, user: dict = Depends(require_role("operator"))):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") == "rejected":
        raise HTTPException(409, "Record was rejected and is permanently closed")
    fields = payload.get("fields") or json.loads(doc["extracted_json"] or "{}")
    store.save_draft(doc_id, fields)
    store.audit(doc_id, user["id"], user["email"], "draft_saved", "")
    return {"id": doc_id, "status": "draft"}


@app.post("/api/documents/{doc_id}/submit")
def submit_document(doc_id: str, payload: dict, user: dict = Depends(require_role("operator"))):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") == "rejected":
        raise HTTPException(409, "Record was rejected and is permanently closed")
    fields = payload.get("fields")
    if fields:
        store.save_draft(doc_id, fields)
    store.submit_document(doc_id)
    store.audit(doc_id, user["id"], user["email"], "submitted_for_verification", "")
    return {"id": doc_id, "status": "pending_review"}


@app.post("/api/documents/{doc_id}/return")
def return_document(doc_id: str, payload: dict, user: dict = Depends(require_role("verifier"))):
    notes = (payload.get("notes") or "").strip()
    if not notes:
        raise HTTPException(400, "Reviewer notes are required when returning a record")
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") == "rejected":
        raise HTTPException(409, "Record was rejected and is permanently closed")
    store.return_document(doc_id, notes, user)
    return {"id": doc_id, "status": "returned", "notes": notes}


@app.post("/api/documents/{doc_id}/reject")
def reject_document(doc_id: str, payload: dict, user: dict = Depends(require_role("verifier"))):
    """Terminal rejection (reference portal's Reject action): closes the
    record permanently with mandatory reviewer notes."""
    notes = (payload.get("notes") or "").strip()
    if not notes:
        raise HTTPException(400, "Reviewer notes are required when rejecting a record")
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    if doc.get("status") not in ("pending_review", "auto_approved"):
        raise HTTPException(409, "Only pending or auto-approved records can be rejected "
                                 "(current status: %s)" % doc.get("status"))
    store.reject_document(doc_id, notes, user)
    return {"id": doc_id, "status": "rejected", "notes": notes}


@app.get("/api/doc-types")
def doc_types(user: dict = Depends(get_current_user)):
    return {"doc_types": store.DOC_TYPES}


@app.post("/api/assistant/ask")
def assistant_ask(payload: dict, user: dict = Depends(get_current_user)):
    """Offline AI assistant: answers website-help, stats and document-search
    questions. All processing is local (rule-based) - no external calls."""
    q = (payload.get("q") or "").strip()
    if not q:
        raise HTTPException(400, "Type a question first")
    if len(q) > 400:
        raise HTTPException(400, "Question too long (max 400 characters)")
    return ai_assistant.answer(q, user_id=user["id"], role=user.get("role"), user=user)


@app.post("/api/assistant/briefing")
def assistant_briefing(user: dict = Depends(get_current_user)):
    """One-click system briefing for the AI assistant panel."""
    return ai_assistant.briefing(user_id=user["id"], role=user.get("role"))


# ==========================================================================
# SA — Superior Administrator mode (hidden: type 'SA' in the AI assistant)
# ==========================================================================
def _sa_error(e: sa_admin.SAError):
    return HTTPException(e.status, str(e))


@app.post("/api/admin/sa/activate-options")
def sa_activate_options(payload: dict, user: dict = Depends(require_role("admin"))):
    """SA step 1: validate the activation code, return admin identities."""
    try:
        return sa_admin.activate_options(payload.get("code") or "", user)
    except sa_admin.SAError as e:
        raise _sa_error(e)


@app.post("/api/admin/sa/activate")
def sa_activate(payload: dict, user: dict = Depends(require_role("admin"))):
    """SA step 2: re-authenticate the chosen administrator (password checked
    server-side only) and open a short-lived SA session."""
    try:
        return sa_admin.activate(payload.get("code") or "",
                                 payload.get("administrator") or "",
                                 payload.get("password") or "", user)
    except sa_admin.SAError as e:
        raise _sa_error(e)


@app.post("/api/admin/sa/query")
def sa_query(payload: dict, user: dict = Depends(require_role("admin"))):
    """One SA-mode message: plan/coordinate across features; consequential
    actions become AI Approval Center proposals."""
    try:
        return sa_admin.query(payload.get("session_id") or "",
                              payload.get("query") or payload.get("q") or "", user)
    except sa_admin.SAError as e:
        raise _sa_error(e)


@app.post("/api/admin/sa/end")
def sa_end(payload: dict, user: dict = Depends(require_role("admin"))):
    """End the SA session — back to normal assistant mode."""
    try:
        return sa_admin.end(payload.get("session_id") or "", user)
    except sa_admin.SAError as e:
        raise _sa_error(e)


@app.get("/api/admin/sa/report")
def sa_report(session_id: str = Query(""), user: dict = Depends(require_role("admin"))):
    """The SA Activity Report: timestamped log of everything the session did."""
    try:
        return sa_admin.report(session_id, user)
    except sa_admin.SAError as e:
        raise _sa_error(e)


# --------------------------------------------------------------------------
# SA CourtLink — demo court database + SA court-history screening (v3.11)
# All endpoints are ADMIN-ONLY: data operators and verification officers
# cannot view the court database, add/edit cases, scan, or attach.
# --------------------------------------------------------------------------
def _cl_error(e: courtlink.CourtLinkError):
    return HTTPException(e.status, str(e))


@app.get("/api/admin/court-db")
def court_db_list(user: dict = Depends(require_role("admin"))):
    """List every predefined case in the demo court database."""
    return {"cases": courtlink.list_cases()}


@app.post("/api/admin/court-db")
def court_db_create(payload: dict, user: dict = Depends(require_role("admin"))):
    """Add a predefined case to the court database."""
    try:
        return courtlink.create_case(payload or {}, user)
    except courtlink.CourtLinkError as e:
        raise _cl_error(e)


@app.put("/api/admin/court-db/{cid}")
def court_db_update(cid: str, payload: dict, user: dict = Depends(require_role("admin"))):
    """Edit a predefined case in the court database."""
    try:
        return courtlink.update_case(cid, payload or {}, user)
    except courtlink.CourtLinkError as e:
        raise _cl_error(e)


@app.delete("/api/admin/court-db/{cid}")
def court_db_delete(cid: str, user: dict = Depends(require_role("admin"))):
    """Remove a predefined case from the court database."""
    try:
        return courtlink.delete_case(cid, user)
    except courtlink.CourtLinkError as e:
        raise _cl_error(e)


@app.post("/api/admin/court-db/scan/{doc_id}")
def court_db_scan(doc_id: str, user: dict = Depends(require_role("admin"))):
    """SA scans this record's OCR details against the court database and
    reports prior court-case history (recorded in the record's audit trail)."""
    try:
        return courtlink.screen_document(doc_id, user, source="record_tab")
    except courtlink.CourtLinkError as e:
        raise _cl_error(e)


@app.post("/api/admin/court-db/attach")
def court_db_attach(payload: dict, user: dict = Depends(require_role("admin"))):
    """Import a matched court case into the record's internal litigation
    ledger (court_cases), so the record's risk view and PDFs reflect it."""
    try:
        return courtlink.attach_case(payload.get("case_id") or "",
                                     payload.get("document_id") or "", user)
    except courtlink.CourtLinkError as e:
        raise _cl_error(e)


@app.get("/api/documents/{doc_id}/audit")
def document_audit(doc_id: str, user: dict = Depends(get_current_user)):
    """Per-record audit trail: the hash-chained, tamper-evident history of
    THIS one record (upload → draft → verification → gate stamps → PDF
    downloads), oldest first. Read-only — like the land history, but for
    actions taken ON the record."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    rows = store.get_audit(doc_id)
    return {"document_id": doc_id, "audit": [
        {"ts": r.get("ts"), "username": r.get("username") or "",
         "action": r.get("action") or "", "detail": r.get("detail") or "",
         "hash": (r.get("entry_hash") or "")[:16]} for r in rows]}


def _diff_fields(fa: dict, fb: dict):
    """Side-by-side field diff of two extracted-field dicts with
    highlights for ownership transfer, area variance, survey modification."""
    rows, highlights = [], []
    for fid, disp, _labels in common.FIELD_DEFS:
        va = str(fa.get(fid, {}).get("value", "") or "") if isinstance(fa.get(fid), dict) else ""
        vb = str(fb.get(fid, {}).get("value", "") or "") if isinstance(fb.get(fid), dict) else ""
        rows.append({"field": fid, "label": disp, "a": va, "b": vb, "changed": va != vb})
        if va and vb and va != vb:
            if fid == "owner_name":
                highlights.append("⚖️ Ownership change: %s → %s" % (va, vb))
            if fid == "area":
                highlights.append("📐 Area variance: %s → %s" % (va, vb))
            if fid in ("survey_number", "khasra_number", "khata_number", "plot_number"):
                highlights.append("🔢 %s modified: %s → %s" % (disp, va, vb))
    return rows, highlights


@app.post("/api/records/compare")
def records_compare(payload: dict, user: dict = Depends(get_current_user)):
    """Version comparison: side-by-side field diff of two records with
    highlights for ownership transfer, area variance, survey modification."""
    a, b = store.get_document(payload.get("id_a") or ""), store.get_document(payload.get("id_b") or "")
    if not a or not b:
        raise HTTPException(404, "One or both records not found")
    fa = json.loads(a["extracted_json"] or "{}")
    fb = json.loads(b["extracted_json"] or "{}")
    rows, highlights = _diff_fields(fa, fb)
    return {
        "a": {"id": a["id"], "filename": a["filename"],
              "uploaded_at": a["uploaded_at"], "doc_type": a.get("doc_type")},
        "b": {"id": b["id"], "filename": b["filename"],
              "uploaded_at": b["uploaded_at"], "doc_type": b.get("doc_type")},
        "rows": rows, "highlights": highlights,
        "ai_explanation": ai_support.compare_explanation(
            {"filename": a["filename"]}, {"filename": b["filename"]}, rows, highlights),
    }


@app.post("/api/records/compare-files")
async def records_compare_files(request: Request,
                                file_a: UploadFile = File(...),
                                file_b: UploadFile = File(...),
                                user: dict = Depends(require_role("operator"))):
    """Compare two uploaded document scans side-by-side.

    Both files are OCR-processed and field-extracted transiently — they are
    NOT saved to the repository. Returns the same diff shape as
    /api/records/compare so the UI can render both flows identically.
    """
    ip = request.client.host if request.client else "?"
    _rate_limit("ocr:" + ip, 10, 60)   # same OCR budget as uploads
    docs = []
    for up in (file_a, file_b):
        ext = os.path.splitext(up.filename or "")[1].lower()
        if ext not in store.ALLOWED_EXTENSIONS:
            raise HTTPException(400, "Unsupported file type '%s'. "
                                     "Allowed: %s" % (ext, ", ".join(sorted(store.ALLOWED_EXTENSIONS))))
        data = await up.read()
        if not data:
            raise HTTPException(400, "Empty file")
        if len(data) > store.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "File exceeds %d MB limit" % (store.MAX_UPLOAD_BYTES // (1024 * 1024)))
        try:
            ocr_result = ocrpool.run_ocr(data, up.filename or "upload")
        except Exception:  # noqa: BLE001
            log.exception("Compare-scans OCR failed for %s", up.filename)
            raise HTTPException(500, "OCR failed for %s. Please try again." % (up.filename or "one of the files"))
        fields = extractor.extract_fields(ocr_result)
        fields = store.apply_learned(fields)
        docs.append({"filename": up.filename or "upload",
                     "mean_conf": ocr_result["mean_conf"], "fields": fields})
    rows, highlights = _diff_fields(docs[0]["fields"], docs[1]["fields"])
    store.audit(None, user["id"], user["email"], "compare_scans",
                "%s vs %s" % (docs[0]["filename"], docs[1]["filename"]))
    return {
        "a": {"id": "A", "filename": docs[0]["filename"], "mean_conf": docs[0]["mean_conf"]},
        "b": {"id": "B", "filename": docs[1]["filename"], "mean_conf": docs[1]["mean_conf"]},
        "rows": rows, "highlights": highlights, "transient": True,
    }


@app.post("/api/records/consistency")
def records_consistency(payload: dict, user: dict = Depends(get_current_user)):
    """Cross-document consistency audit (chain of title): cross-verifies
    factual consistency across 2+ related records."""
    ids = payload.get("ids") or []
    if len(ids) < 2:
        raise HTTPException(400, "Select at least 2 documents to audit")
    docs = [store.get_document(i) for i in ids[:6]]
    docs = [d for d in docs if d]
    if len(docs) < 2:
        raise HTTPException(404, "Need at least 2 valid documents")
    items, flags = [], []
    for d in docs:
        f = json.loads(d["extracted_json"] or "{}")
        g = lambda k: str(f.get(k, {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        items.append({"id": d["id"], "filename": d["filename"], "status": d["status"],
                      "doc_type": d.get("doc_type") or "land_record",
                      "owner": g("owner_name"), "father": g("father_name"),
                      "survey": g("survey_number"), "khasra": g("khasra_number"),
                      "area": g("area"), "village": g("village"),
                      "district": g("district"), "tehsil": g("tehsil"),
                      "land_class": g("land_class"), "year": g("khatauni_year")})
    # same survey number, different owners - but a CHANGE of owner is often a
    # legitimate sale/mutation (the new owner's papers match the old record in
    # every land detail and only the name differs), so distinguish:
    #   * a mutation/sale document in the set        -> recorded transfer (info)
    #   * different record years + matching land     -> likely transfer (warning)
    #   * same year, or land details differ          -> genuine conflict (error)
    by_survey = {}
    for it in items:
        if it["survey"]:
            by_survey.setdefault(it["survey"], []).append(it)
    for sv, sv_docs in by_survey.items():
        owners = {d["owner"] or "?" for d in sv_docs}
        if len(owners) <= 1:
            continue
        names = ", ".join(sorted(o for o in owners if o != "?")) or ", ".join(sorted(owners))
        # Edge case: the same survey number in DIFFERENT villages is simply
        # two different lands that share a number - not a conflict.
        villages = {d["village"] for d in sv_docs if d.get("village")}
        if len(villages) > 1:
            flags.append({"level": "info",
                          "msg": "Survey no. %s appears in different villages (%s) with different owners - these are different lands that merely share a survey number, not a conflict." % (sv, ", ".join(sorted(villages)))})
            continue
        has_transfer_doc = any(d.get("doc_type") in ("mutation", "sale_deed") for d in sv_docs)
        years = {d["year"] for d in sv_docs if d.get("year")}
        # Area / khasra may legitimately CHANGE in a partition sale, so they
        # do not block the transfer pattern - just note the change.
        extras = []
        areas = {d["area"] for d in sv_docs if d.get("area")}
        if len(areas) > 1:
            extras.append("Area also differs (%s) - consistent with a partition." % ", ".join(sorted(areas)))
        khasras = {d["khasra"] for d in sv_docs if d.get("khasra")}
        if len(khasras) > 1:
            extras.append("Khasra also differs (%s)." % ", ".join(sorted(khasras)))
        extra = " ".join(extras)
        if has_transfer_doc:
            flags.append({"level": "info",
                          "msg": "Survey no. %s shows different owners (%s) and the set includes a mutation/sale document - this matches a recorded ownership transfer, not a conflict. Verify the transfer details match the records." % (sv, names)})
        elif len(years) > 1:
            flags.append({"level": "warning",
                          "msg": "Survey no. %s shows different owners (%s) in different record years (%s) in the same village - consistent with a legitimate sale/transfer between those years.%s Confirm with the mutation record." % (sv, names, ", ".join(sorted(years)), extra)})
        else:
            flags.append({"level": "error",
                          "msg": "Same survey no. %s in the same village shows different owners across documents with the same (or no) record year: %s - a genuine conflict, verify the chain of title." % (sv, names)})
    # identical survey+khasra+owner -> possible duplicate
    seen = {}
    for it in items:
        if it["survey"] and it["owner"]:
            k = (it["survey"], it["khasra"], it["owner"])
            seen.setdefault(k, []).append(it["id"])
    for k, ids2 in seen.items():
        if len(ids2) > 1:
            flags.append({"level": "warning", "msg": "Identical survey+khasra+owner in documents %s — possible duplicate entry." % ", ".join(ids2)})
    # context consistency
    villages = {it["village"] for it in items if it["village"]}
    districts = {it["district"] for it in items if it["district"]}
    if len(districts) > 1:
        flags.append({"level": "warning", "msg": "Documents span different districts: %s — expected for a chain of title across partitions, otherwise review." % ", ".join(sorted(districts))})
    elif len(districts) == 1:
        flags.append({"level": "ok", "msg": "All documents belong to the same district (%s) — consistent land context." % next(iter(districts))})
    # ownership chain hints
    owners_all = [it["owner"] for it in items if it["owner"]]
    fathers_all = [it["father"] for it in items if it["father"]]
    for o in owners_all:
        for fath in fathers_all:
            if o and fath and o != fath and (o in fath or fath in o):
                flags.append({"level": "info", "msg": "Possible family/chain link: owner '%s' appears in a father's name field ('%s') across documents." % (o, fath)})
    if not any(f["level"] in ("error", "warning") for f in flags):
        flags.append({"level": "ok", "msg": "No factual inconsistencies detected across the selected records."})
    return {"items": items, "flags": flags,
            "consistent": not any(f["level"] == "error" for f in flags),
            "ai_explanation": ai_support.consistency_explanation(items, flags)}


# --------------------------------------------------------------------------
# Map / GIS (village-level locator + optional per-record exact pin)
# --------------------------------------------------------------------------
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_UA = "LandRecordDigitizationSystem/3.5 (government land-records GIS locator)"
_geocode_cache = {}
_geocode_last_call = [0.0]


@app.post("/api/demo/seed")
def demo_seed(user: dict = Depends(require_role("admin"))):
    """(Re)load the demo dataset. Refuses to run when the database already
    has documents, so it can never destroy real data — use it from the
    Land Map tab to make a fresh install usable immediately."""
    try:
        from landrec import seed_demo
        res = seed_demo.seed_demo_data(force=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, "Demo seed failed: %s" % e)
    return res


@app.get("/api/map/records")
def map_records(user: dict = Depends(get_current_user)):
    """All records with their location data (exact pin and/or village)
    for the Map/GIS tab."""
    out = []
    for d in store.list_documents(limit=5000):
        f = d.get("fields") or {}
        g = lambda k: str(f.get(k, {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        out.append({
            "id": d["id"], "filename": d.get("filename", ""),
            "doc_type": d.get("doc_type") or "land_record",
            "status": d.get("status", ""),
            "owner": g("owner_name"), "father": g("father_name"),
            "survey": g("survey_number"), "khasra": g("khasra_number"),
            "khata": g("khata_number"), "plot": g("plot_number"),
            "area": g("area"), "village": g("village"),
            "tehsil": g("tehsil"), "district": g("district"),
            "state": g("state"), "year": g("khatauni_year"),
            "lat": d.get("lat"), "lon": d.get("lon"),
            "boundary": _parse_boundary(d.get("boundary_geojson")),
            "boundary_source": d.get("boundary_source"),
        })
    return {"records": out}


@app.put("/api/map/records/{doc_id}/location")
def map_set_location(doc_id: str, payload: dict,
                     user: dict = Depends(require_role("verifier"))):
    """Set (or clear, with lat/lon null) the exact GIS pin for a record."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Record not found")
    lat, lon = payload.get("lat"), payload.get("lon")
    if lat is None and lon is None:
        store.set_location(doc_id, None, None)
        store.audit(doc_id, user["id"], user["email"], "location_cleared", "GIS pin removed")
        return {"ok": True, "lat": None, "lon": None}
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise HTTPException(400, "lat/lon must be numbers (or null to clear)")
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise HTTPException(400, "lat must be -90..90 and lon -180..180")
    store.set_location(doc_id, lat, lon)
    store.audit(doc_id, user["id"], user["email"], "location_set",
                "lat=%.6f lon=%.6f" % (lat, lon))
    return {"ok": True, "lat": lat, "lon": lon}


# --------------------------------------------------------------------------
# Plot boundary polygons (digitized / estimated / imported) + coordinates
# --------------------------------------------------------------------------
def _parse_boundary(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _validate_ring(coords):
    """Validate a [[lat, lon], ...] ring. Raises ValueError with a message."""
    if not isinstance(coords, list) or len(coords) < 4:
        raise ValueError("A boundary needs at least 4 corner points")
    if len(coords) > 64:
        raise ValueError("Boundary too complex (max 64 vertices)")
    ring = []
    for pt in coords:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError("Each vertex must be [lat, lon]")
        try:
            lat, lon = float(pt[0]), float(pt[1])
        except (TypeError, ValueError):
            raise ValueError("Vertex coordinates must be numbers")
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValueError("Vertex out of range (lat -90..90, lon -180..180)")
        ring.append([round(lat, 7), round(lon, 7)])
    return ring


def _extract_first_polygon(geo):
    """Pull the first exterior ring (as [lon, lat] pairs) from any of:
    FeatureCollection / Feature / Polygon / MultiPolygon / raw ring."""
    def geom(g):
        if isinstance(g, dict) and g.get("type") in ("Polygon", "MultiPolygon"):
            return g
        if isinstance(g, dict) and g.get("type") == "Feature":
            return g.get("geometry")
        if isinstance(g, dict) and g.get("type") == "FeatureCollection":
            feats = g.get("features") or []
            if feats:
                return geom(feats[0])
        return None
    g = geom(geo)
    if isinstance(geo, list):  # raw ring [[lon,lat],...]
        return geo
    if not g:
        return None
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    for poly in polys:
        if poly and len(poly[0]) >= 4:
            return poly[0]
    return None


def _area_str_to_m2(area_str):
    """Parse a recorded area value like '2.5 एकड़' / '3.20 Ac 12 Gts' /
    '48 cents' into square metres. Unknown/absent unit -> assume acres."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z\u0900-\u097F\u0B80-\u0BFF]*)",
                  str(area_str or ""))
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if not unit or "acre" in unit or unit in ("ac", "acres", "acre.") \
       or "एकड़" in unit or "एकड" in unit or "ekad" in unit:
        return val * 4046.86
    if "cent" in unit or "सेंट" in unit:
        return val * 40.4686
    if "bigha" in unit or "बिगा" in unit or "बिगहा" in unit:
        return val * 2529.29
    return val * 4046.86  # default assumption for Indian land records


def _estimate_boundary_ring(lat, lon, area_m2):
    """Deterministic square of the right AREA, centred on (lat, lon).
    Clearly an ESTIMATE — labelled as such everywhere in the UI."""
    import math
    side = math.sqrt(max(area_m2, 1.0))
    dlat = (side / 2.0) / 111320.0
    dlon = (side / 2.0) / (111320.0 * max(math.cos(math.radians(lat)), 0.01))
    return [[round(lat - dlat, 7), round(lon - dlon, 7)],
            [round(lat - dlat, 7), round(lon + dlon, 7)],
            [round(lat + dlat, 7), round(lon + dlon, 7)],
            [round(lat + dlat, 7), round(lon - dlon, 7)]]


@app.post("/api/map/records/{doc_id}/boundary")
def map_set_boundary(doc_id: str, payload: dict,
                     user: dict = Depends(require_role("verifier"))):
    """Store a DIGITIZED plot boundary (officer traced it on the map)."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Record not found")
    try:
        ring = _validate_ring(payload.get("coordinates"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.set_boundary(doc_id, ring, "digitized", user)
    return {"ok": True, "boundary": ring, "source": "digitized"}


@app.post("/api/map/records/{doc_id}/boundary/estimate")
def map_estimate_boundary(doc_id: str,
                          user: dict = Depends(require_role("verifier"))):
    """Generate an ESTIMATED boundary: a square with the recorded AREA,
    centred on the record's exact pin (or its geocoded village when no pin
    exists). The UI always labels this 'estimated — not a legal boundary'."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Record not found")
    f = json.loads(doc.get("extracted_json") or "{}")
    g = lambda k: str((f.get(k) or {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
    area_m2 = _area_str_to_m2(g("area"))
    if not area_m2:
        raise HTTPException(400, "No readable area value on this record to size the boundary")
    lat, lon = doc.get("lat"), doc.get("lon")
    if lat is None or lon is None:
        # fall back to the geocode cascade (village → district)
        cands = _nominatim_search(
            "%s, %s%s, India" % (g("village"), g("district"),
                                 (", " + g("state")) if g("state") else ""), 5) \
            if g("village") else None
        if cands is None:
            raise HTTPException(503, "Geocoding unavailable right now — set an exact pin first")
        c = _pick_candidate(cands, g("state"), g("district"), allow_any_state=True)
        if not c:
            raise HTTPException(400, "Could not locate this record's village — "
                                     "set an exact pin (📍) first, then estimate")
        lat, lon = c["lat"], c["lon"]
    ring = _estimate_boundary_ring(lat, lon, area_m2)
    store.set_boundary(doc_id, ring, "estimated", user)
    return {"ok": True, "boundary": ring, "source": "estimated",
            "center": [lat, lon], "area_m2": round(area_m2, 1)}


@app.post("/api/map/records/{doc_id}/boundary/import")
def map_import_boundary(doc_id: str, payload: dict,
                        user: dict = Depends(require_role("verifier"))):
    """Import a REAL boundary from a GeoJSON document (surveyor / government
    shapefile export). Accepts FeatureCollection / Feature / Polygon /
    MultiPolygon / raw ring. GeoJSON [lon, lat] order is converted."""
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Record not found")
    raw = _extract_first_polygon(payload.get("geojson"))
    if not raw:
        raise HTTPException(400, "No polygon found in the GeoJSON "
                                 "(expected FeatureCollection / Feature / Polygon / ring)")
    try:
        ring = _validate_ring([[pt[1], pt[0]] for pt in raw])
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.set_boundary(doc_id, ring, "imported", user)
    return {"ok": True, "boundary": ring, "source": "imported"}


@app.post("/api/map/records/{doc_id}/boundary/clear")
def map_clear_boundary(doc_id: str, user: dict = Depends(require_role("verifier"))):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Record not found")
    store.clear_boundary(doc_id, user)
    return {"ok": True}


def _nominatim_search(query, limit=5):
    """One throttled Nominatim call returning a LIST of candidates.
    Returns None on network error (so the caller can try the next level).
    Results cached 7 days; upstream throttled to ~1 call/1.1 s per
    Nominatim's usage policy."""
    import urllib.parse
    import urllib.request

    key = query.lower()
    now = time.time()
    if key in _geocode_cache:
        ts, cands = _geocode_cache[key]
        if now - ts < 7 * 86400:
            return cands
    if len(_geocode_cache) > 500:
        _geocode_cache.clear()
    wait = 1.1 - (now - _geocode_last_call[0])
    if wait > 0:
        time.sleep(wait)
    _geocode_last_call[0] = time.time()
    url = _NOMINATIM_URL + "?" + urllib.parse.urlencode(
        {"format": "jsonv2", "limit": limit, "q": query, "countrycodes": "in"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _NOMINATIM_UA})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001 - network/parse problems are not fatal
        return None
    cands = []
    for item in (data or [])[:limit]:
        try:
            cands.append({"lat": float(item["lat"]), "lon": float(item["lon"]),
                          "display_name": item.get("display_name", ""),
                          "address": item.get("address") or {}})
        except (KeyError, ValueError, TypeError):
            continue
    _geocode_cache[key] = (time.time(), cands)
    return cands


def _pick_candidate(cands, state, district, allow_any_state=False):
    """Pick the best candidate. Prefers a match in the requested
    state/district (structured address fields); avoids returning a
    same-named village from ANOTHER state when a state was requested."""
    if not cands:
        return None
    st = (state or "").strip().lower()
    dt = (district or "").strip().lower()
    if st or dt:
        for c in cands:
            a = c.get("address") or {}
            c_state = (a.get("state") or "").lower()
            c_county = (a.get("county") or a.get("state_district")
                        or a.get("city_district") or "").lower()
            if (st and c_state and (st in c_state or c_state in st)) \
               or (dt and c_county and (dt in c_county or c_county in dt)):
                return c
    if allow_any_state:
        return cands[0]
    return None


@app.post("/api/map/geocode")
def map_geocode(payload: dict, user: dict = Depends(get_current_user)):
    """Geocode a place via OpenStreetMap Nominatim (free, no API key).

    Two modes:
      * structured: {village, district, state} — runs a smart cascade
        (village+district+state → village+district → village → district),
        preferring candidates in the requested state/district so a village
        that OSM can't find with its district still resolves, and a
        same-named village in another state is NOT returned by mistake.
      * legacy: {query: "..."} — single raw query, first match.
    """
    village = (payload.get("village") or "").strip()
    district = (payload.get("district") or "").strip()
    state = (payload.get("state") or "").strip()
    query = (payload.get("query") or "").strip()

    if query:
        if len(query) > 200:
            raise HTTPException(400, "query too long (max 200 chars)")
        cands = _nominatim_search(query, 5)
        if cands is None:
            return {"query": query, "lat": None, "lon": None,
                    "error": "geocoding unavailable (offline?) - try again later"}
        if not cands:
            return {"query": query, "lat": None, "lon": None,
                    "error": "no match found for '%s'" % query}
        c = cands[0]
        return {"query": query, "lat": c["lat"], "lon": c["lon"],
                "display_name": c["display_name"], "match_level": "query"}

    if not (village or district):
        raise HTTPException(400, "village/district (or legacy 'query') required")
    if len(village) + len(district) + len(state) > 200:
        raise HTTPException(400, "query too long (max 200 chars)")

    # cascade levels, most specific first
    levels = []
    if village and district:
        if state:
            levels.append(("%s, %s, %s, India" % (village, district, state),
                           "village_district", False))
        levels.append(("%s, %s, India" % (village, district),
                       "village_district", False))
    if village:
        levels.append(("%s, India" % village, "village", False))
    if district:
        levels.append(("%s%s, India" % (district, (", " + state) if state else ""),
                       "district", True))

    last_error = None
    for q, level, allow_any in levels:
        cands = _nominatim_search(q, 5)
        if cands is None:
            last_error = "geocoding unavailable (offline?) - try again later"
            continue
        c = _pick_candidate(cands, state, district, allow_any_state=allow_any)
        if c:
            return {"query": q, "lat": c["lat"], "lon": c["lon"],
                    "display_name": c["display_name"], "match_level": level}
    return {"query": village or district, "lat": None, "lon": None,
            "error": last_error or "no match found for '%s%s'"
                     % (village, (", " + district) if district else "")}


# --------------------------------------------------------------------------
# Mutation (Namantaran) online application module
# --------------------------------------------------------------------------
MUTATION_FILE_EXTS = store.ALLOWED_EXTENSIONS


@app.post("/api/mutations")
def create_mutation(payload: dict, user: dict = Depends(get_current_user)):
    """Citizen submits a mutation (name-change) application online.
    Any logged-in user can apply EXCEPT read-only Viewers; returns the
    application with its official app number (MUT-YYYY-NNNN)."""
    if user.get("role") == "viewer":
        raise HTTPException(403, "Viewers have search-only access — use a Data Operator account to apply")
    try:
        m = store.create_mutation(payload, user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return m


@app.post("/api/mutations/{mid}/file")
async def mutation_attach_file(mid: str, request: Request,
                               file: UploadFile = File(...),
                               user: dict = Depends(get_current_user)):
    """Attach the supporting document (sale deed / inheritance papers)
    to an application — allowed for the applicant while 'received'."""
    if user.get("role") == "viewer":
        raise HTTPException(403, "Viewers have search-only access")
    m = store.get_mutation(mid)
    if not m:
        raise HTTPException(404, "Application not found")
    if m["applicant_email"] != user["email"] and user.get("role") not in ("verifier", "admin"):
        raise HTTPException(403, "Only the applicant (or staff) can attach the document")
    if m["status"] != "received":
        raise HTTPException(409, "Documents can only be attached while the application is 'received'")
    if m.get("file_path"):
        raise HTTPException(409, "A supporting document is already attached")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in MUTATION_FILE_EXTS:
        raise HTTPException(400, "Unsupported file type '%s'" % ext)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > store.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File too large")
    path = os.path.join(store.UPLOAD_DIR, "mut_" + uuid.uuid4().hex + ext)
    with open(path, "wb") as fh:
        fh.write(data)
    c = store._conn()
    c.execute("UPDATE mutations SET file_name=?, file_path=? WHERE id=?",
              (file.filename or "supporting_document", path, mid))
    c.commit()
    c.close()
    store._append_mutation_event(mid, user, "received",
                                 "Supporting document attached: %s" % (file.filename or ""))
    return store.get_mutation(mid)


@app.get("/api/mutations")
def list_mutations(status: str = Query(""), user: dict = Depends(get_current_user)):
    """Staff see every application; a citizen sees their OWN."""
    if user.get("role") in ("verifier", "admin"):
        return {"mutations": store.list_mutations(status=status or None),
                "counts": store.mutation_counts(), "staff": True}
    return {"mutations": store.list_mutations(status=status or None,
                                              mine_email=user["email"]),
            "counts": None, "staff": False}


@app.get("/api/mutations/{mid}")
def get_mutation(mid: str, user: dict = Depends(get_current_user)):
    m = store.get_mutation(mid)
    if not m:
        raise HTTPException(404, "Application not found")
    is_staff = user.get("role") in ("verifier", "admin")
    if not is_staff and m["applicant_email"] != user["email"]:
        raise HTTPException(403, "You can only view your own applications")
    return m


@app.get("/api/mutations/{mid}/file")
def mutation_file(mid: str, user: dict = Depends(get_current_user)):
    m = store.get_mutation(mid)
    if not m or not m.get("file_path") or not os.path.exists(m.get("file_path") or ""):
        raise HTTPException(404, "No supporting document attached")
    is_staff = user.get("role") in ("verifier", "admin")
    if not is_staff and m["applicant_email"] != user["email"]:
        raise HTTPException(403, "You can only view your own applications")
    return FileResponse(m["file_path"], filename=m.get("file_name") or "document")


@app.post("/api/mutations/{mid}/review")
def review_mutation(mid: str, payload: dict, user: dict = Depends(require_role("verifier"))):
    """Officer decision: approve (updates the linked record's owner),
    return (with notes) or reject (with notes)."""
    m = store.get_mutation(mid)
    if not m:
        raise HTTPException(404, "Application not found")
    if m["status"] not in ("received", "under_review"):
        raise HTTPException(409, "This application has already been processed (%s)" % m["status"])
    action = (payload.get("action") or "").strip()
    notes = (payload.get("notes") or "").strip()
    linked = (payload.get("linked_doc_id") or "").strip() or None

    if action == "start":
        try:
            m = store.set_mutation_status(mid, "under_review", user, notes or "Review started")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return m
    if action == "approve":
        if not linked:
            # auto-resolve: latest existing record on the same land
            cand, kind = store.find_transfer_candidate(
                m["survey_number"], m.get("village") or "", m["new_owner"])
            if cand and kind == "transfer":
                linked = cand["id"]
        if not linked or not store.get_document(linked or ""):
            raise HTTPException(400, "Select the existing record to update (link) first")
        # ENCBUMBRANCE GATE (non-blocking): if the land has a live loan the
        # officer is approving a transfer of mortgaged land — allowed, but
        # the decision (and the live loan) are permanently noted.
        act = store.active_encumbrances(m["survey_number"], m.get("village") or "")
        gate_note = ""
        if act:
            gate_note = (" | ⚠ APPROVED WITH ACTIVE ENCUMBRANCE: %s on this land "
                         "(%s) — bank release must be verified."
                         % ("; ".join("%s (ref. %s)" % (e.get("creditor"), e.get("reference_no") or "n/a")
                                      for e in act),
                            ", ".join(e.get("mortgage_date") or "?" for e in act)))
            store.audit(linked, user["id"], user["email"], "mutation_approved_with_encumbrance",
                        "%s — %d active encumbrance(s) on the land" % (m.get("app_no"), len(act)))
        acs = store.active_court_cases(m["survey_number"], m.get("village") or "")
        if acs:
            gate_note += (" | \u26a0 APPROVED WITH ACTIVE LITIGATION: %s on this land "
                          "(filed %s) \u2014 the court outcome must be verified."
                          % ("; ".join("%s (%s)" % (c.get("case_number") or "case",
                                                   c.get("court_name") or "court") for c in acs),
                             ", ".join(c.get("filed_date") or "?" for c in acs)))
            store.audit(linked, user["id"], user["email"], "mutation_approved_with_litigation",
                        "%s \u2014 %d active court case(s) on the land" % (m.get("app_no"), len(acs)))
        try:
            m = store.set_mutation_status(mid, "verified", user, (notes or "") + gate_note,
                                          linked_doc_id=linked)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return m
    if action in ("return", "reject"):
        if not notes:
            raise HTTPException(400, "Reviewer notes are required to %s an application" % action)
        status = "returned" if action == "return" else "rejected"
        try:
            m = store.set_mutation_status(mid, status, user, notes)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return m
    raise HTTPException(400, "Unknown action '%s' (use start/approve/return/reject)" % action)


# --------------------------------------------------------------------------
# Reports (CSV export)
# --------------------------------------------------------------------------
@app.get("/api/reports/export")
def export_report(user: dict = Depends(require_role("verifier"))):
    """Full records register as CSV (Excel-compatible) — the monthly
    office report in one click."""
    import csv
    import io as _io
    rows = store.csv_export_rows()
    buf = _io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens Hindi correctly
    fname = "land_records_report_%s.csv" % time.strftime("%Y%m%d")
    return Response(content=data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=%s" % fname})


# --------------------------------------------------------------------------
# Search (Viewer workspace — verified records only)
# --------------------------------------------------------------------------
@app.get("/api/search")
def search(q: str = Query(""), village: str = Query(""), district: str = Query(""),
           doc_type: str = Query(""), user: dict = Depends(get_current_user)):
    """The Viewer workspace's search: ONLY verified / auto-approved
    records, searched across owner, survey, khasra, khata, plot, village,
    district, filename and document ID."""
    docs = store.list_documents(limit=5000)
    out = []
    for d in docs:
        if d["status"] not in ("verified", "auto_approved"):
            continue
        f = d.get("fields") or {}
        g = lambda k: str((f.get(k) or {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        if village and village.lower() not in g("village").lower():
            continue
        if district and district.lower() not in g("district").lower():
            continue
        if doc_type and (d.get("doc_type") or "") != doc_type:
            continue
        if q:
            hay = " ".join([d["id"], d.get("filename") or "", g("owner_name"), g("father_name"),
                            g("survey_number"), g("khasra_number"), g("khata_number"),
                            g("plot_number"), g("village"), g("district")]).lower()
            if q.lower() not in hay:
                continue
        out.append({"id": d["id"], "filename": d.get("filename", ""),
                    "doc_type": d.get("doc_type", ""), "status": d["status"],
                    "mean_conf": d.get("mean_conf") or 0,
                    "owner": g("owner_name"), "survey": g("survey_number"),
                    "khasra": g("khasra_number"), "village": g("village"),
                    "district": g("district")})
    return {"records": out, "count": len(out)}


# --------------------------------------------------------------------------
# AI Approval Center — AI proposes, a human re-authorizes, server executes
# --------------------------------------------------------------------------
@app.get("/api/ai-approvals")
def ai_approvals(status: str = Query(""), user: dict = Depends(require_role("verifier"))):
    return {"proposals": store.list_proposals(status=status or None),
            "pending": len(store.list_proposals(status="PENDING"))}


@app.post("/api/ai-approvals/{pid}/decide")
def decide_ai_approval(pid: int, payload: dict, user: dict = Depends(require_role("verifier"))):
    decision = (payload.get("decision") or "").strip().lower()
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be 'approve' or 'reject'")
    try:
        p = store.decide_proposal(pid, decision, user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return p


# --------------------------------------------------------------------------
# AI Task Inbox — role-to-role workflow tasks
# --------------------------------------------------------------------------
@app.get("/api/ai-tasks")
def ai_tasks(user: dict = Depends(get_current_user)):
    tasks = store.list_tasks(user.get("role"))
    return {"tasks": tasks, "active_count": store.task_counts(user.get("role")),
            "role": user.get("role")}


@app.post("/api/ai-tasks")
def create_ai_task(payload: dict, user: dict = Depends(require_role("verifier"))):
    try:
        t = store.create_task(payload.get("title", ""), payload.get("description", ""),
                              (payload.get("priority") or "MEDIUM").upper(),
                              payload.get("assigned_role", "operator"), user,
                              record_id=payload.get("record_id") or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return t


@app.post("/api/ai-tasks/{tid}/respond")
def respond_ai_task(tid: str, payload: dict, user: dict = Depends(get_current_user)):
    t = store.get_task(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    if user.get("role") != "admin" and t.get("assigned_role") != user.get("role"):
        raise HTTPException(403, "You can only respond to tasks assigned to your role")
    try:
        t = store.respond_task(tid, (payload.get("status") or "").upper(), user)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return t


# --------------------------------------------------------------------------
# Public verification page (QR target) — no login, certified records only
# --------------------------------------------------------------------------
@app.get("/verify/{doc_id}")
def verify_page(doc_id: str):
    doc = store.get_document(doc_id)
    certified = bool(doc and doc.get("status") in ("verified", "auto_approved"))
    if certified:
        if not doc.get("cert_hash"):
            # legacy record (verified before the certification feature):
            # stamp its fingerprint now so the QR flow works
            store.certify_document(doc_id, doc.get("cert_by") or "system (re-certified)")
            doc = store.get_document(doc_id)
        f = json.loads(doc.get("extracted_json") or "{}")
        g = lambda k: str((f.get(k) or {}).get("value", "") or "") if isinstance(f.get(k), dict) else ""
        ok, stored, recomputed = store.check_cert_hash(doc_id)
        chain = store.verify_audit_chain()
        ts = doc.get("cert_at") or 0
        import time as _t
        v_when = _t.strftime("%d %b %Y %H:%M", _t.localtime(ts)) if ts else "—"
        fields_html = ""
        for label, key in [("Owner", "owner_name"), ("Survey No.", "survey_number"),
                           ("Khasra", "khasra_number"), ("Khata", "khata_number"),
                           ("Area", "area"), ("Village", "village"),
                           ("Tehsil", "tehsil"), ("District", "district"),
                           ("State", "state"), ("Year", "khatauni_year")]:
            v = g(key)
            if v:
                fields_html += '<div class="row"><span class="k">%s</span><span class="v">%s</span></div>' % (label, v)
        verdict_html = (
            '<div class="verdict ok">✓<div><b>VERIFIED — GENUINE &amp; UNALTERED</b>'
            '<div class="sub">Certification hash matches and the audit trail is intact.</div></div></div>'
            if (ok and chain.get("valid")) else
            '<div class="verdict bad">⚠<div><b>%s</b>'
            '<div class="sub">%s</div></div></div>'
            % ("TAMPERED — CERTIFICATION FAILED" if not ok else "INTEGRITY CHECK FAILED",
               "The stored record no longer matches its certification hash — "
               "it was altered after the certified copy was issued." if not ok
               else "The document audit trail has a broken link (entry #%s)." % chain.get("broken_at"))
        )
        body = """
        <div class="card">
          <div class="head">📄 Certified Land Record — Verification</div>
          <div class="meta">Record ID <b>%s</b> · File <b>%s</b> · Status <b>%s</b></div>
          %s
          %s
          <div class="verblock">
            <div class="row"><span class="k">Certified By</span><span class="v">%s</span></div>
            <div class="row"><span class="k">Certified On</span><span class="v">%s</span></div>
            <div class="row"><span class="k">Certification Hash</span><span class="v mono">%s</span></div>
            <div class="row"><span class="k">Audit Trail</span><span class="v">%s entries, chain %s</span></div>
          </div>
          <div class="foot">This page is publicly accessible and only displays CERTIFIED (verified)
          records. For legal purposes the certified PDF issued by the department prevails.</div>
        </div>""" % (doc_id, doc.get("filename") or "—", (doc.get("status") or "").upper(),
                      fields_html, verdict_html, doc.get("cert_by") or "—", v_when,
                      doc.get("cert_hash") or "—",
                      chain.get("entries", 0), "valid" if chain.get("valid") else "BROKEN")
    else:
        body = """
        <div class="card">
          <div class="head">📄 Land Record Verification</div>
          <div class="verdict bad">✕<div><b>NO CERTIFIED RECORD FOUND</b>
          <div class="sub">No verified land record exists for this ID (or it has not been
          certified yet). Check the ID on the QR code and try again.</div></div></div>
        </div>"""
    html = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Record Verification — Land Record System</title>
<style>
 body{font-family:'Segoe UI',system-ui,Arial,sans-serif;background:#eef2f7;margin:0;padding:24px 14px;color:#0f172a}
 .wrap{max-width:640px;margin:0 auto}
 .card{background:#fff;border:1px solid #cbd5e1;border-radius:12px;padding:22px;box-shadow:0 2px 10px rgba(15,43,72,.08)}
 .head{font-size:17px;font-weight:800;color:#0f2b48;border-bottom:2px solid #0f2b48;padding-bottom:10px;margin-bottom:14px}
 .meta{font-size:12.5px;color:#475569;margin-bottom:12px}
 .row{display:flex;gap:10px;font-size:13px;padding:4px 0;border-bottom:1px dashed #e2e8f0}
 .k{width:150px;flex:none;color:#64748b;font-weight:600}
 .v{font-weight:700;word-break:break-all}
 .mono{font-family:Consolas,monospace;font-size:11px}
 .verblock{background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:10px 12px;margin:12px 0}
 .verdict{display:flex;gap:12px;align-items:flex-start;padding:14px;border-radius:10px;margin-top:12px;font-size:14px}
 .verdict.ok{background:#dcfce7;color:#166534;border:1px solid #86efac;font-size:26px}
 .verdict.ok div{font-size:14px}
 .verdict.bad{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;font-size:26px}
 .verdict.bad div{font-size:14px}
 .sub{font-size:12px;color:#7f1d1d;font-weight:400;margin-top:4px}
 .foot{font-size:11px;color:#94a3b8;margin-top:14px;line-height:1.5}
 .topbar{text-align:center;font-weight:800;color:#0f2b48;margin-bottom:14px;font-size:14px}
</style></head><body><div class="wrap">
<div class="topbar">🏛️ Intelligent Land Record Digitization &amp; Validation System</div>
%s
</div></body></html>""" % body
    return HTMLResponse(html)


@app.get("/api/audit/verify")
def audit_verify(user: dict = Depends(require_role("verifier"))):
    """Recompute the SHA-256 chain; any tampered entry breaks the chain."""
    return store.verify_audit_chain()


# --------------------------------------------------------------------------
# 💾 DATA BACKUP & RESTORE (ADMIN) — the whole portal in one ZIP
# --------------------------------------------------------------------------
@app.get("/api/backup/export")
def backup_export(user: dict = Depends(require_role("admin"))):
    """Download the complete dataset as a ZIP: a CONSISTENT snapshot of the
    SQLite database (online backup, safe while the app runs) + every
    uploaded scan + a manifest. This is the one-click data backup: save it,
    and the entire portal (records, workflow state, mutations, encumbrances,
    audit trail, uploads) can be restored anywhere from this file."""
    import io
    import sqlite3
    import tempfile
    import zipfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    src = sqlite3.connect(store.DB_PATH)
    dst = sqlite3.connect(tmp.name)
    try:
        with dst:
            src.backup(dst)  # online backup: consistent snapshot while live
    finally:
        src.close()
        dst.close()
    with open(tmp.name, "rb") as fh:
        db_bytes = fh.read()
    os.remove(tmp.name)

    n_docs = n_muts = n_enc = 0
    con = sqlite3.connect(store.DB_PATH)
    try:
        n_docs = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        n_muts = con.execute("SELECT COUNT(*) FROM mutations").fetchone()[0]
        n_enc = con.execute("SELECT COUNT(*) FROM encumbrances").fetchone()[0]
    finally:
        con.close()

    up_count = 0
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("landrec.db", db_bytes)
        z.writestr("manifest.json", json.dumps({
            "app": "Intelligent Land Record Digitization & Validation System",
            "version": APP_VERSION,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "documents": n_docs, "mutations": n_muts, "encumbrances": n_enc,
        }, indent=1))
        if os.path.isdir(store.UPLOAD_DIR):
            for f in sorted(os.listdir(store.UPLOAD_DIR)):
                fp = os.path.join(store.UPLOAD_DIR, f)
                if os.path.isfile(fp):
                    z.write(fp, "uploads/" + f)
                    up_count += 1
    fname = "landrec_backup_%s.zip" % time.strftime("%Y%m%d_%H%M")
    store.audit(None, user["id"], user["email"], "backup_exported",
                "%d docs, %d mutations, %d encumbrances, %d uploads" % (n_docs, n_muts, n_enc, up_count))
    return Response(zbuf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": "attachment; filename=%s" % fname})


@app.post("/api/backup/import")
async def backup_import(request: Request, user: dict = Depends(require_role("admin"))):
    """Restore the portal from a backup ZIP (replaces the current data).
    Before overwriting anything, a SAFETY COPY of the current data is kept
    at data/backup_before_restore_<ts>/, so a wrong restore is always undoable."""
    import io
    import shutil
    import sqlite3
    import zipfile
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        form = await request.form()
        up = form.get("file")
        if up is None:
            raise HTTPException(400, "Missing 'file' field (the backup ZIP)")
        data = await up.read()
    else:
        data = await request.body()
    if not data:
        raise HTTPException(400, "Empty upload")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Not a valid ZIP file")
    names = zf.namelist()
    if "landrec.db" not in names:
        raise HTTPException(400, "Not a landrec backup: 'landrec.db' missing from the ZIP")
    db_bytes = zf.read("landrec.db")
    # Validate: the bytes must be a readable SQLite DB with our tables
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.write(db_bytes)
    tmp.close()
    try:
        t3 = sqlite3.connect(tmp.name)
        n_docs = t3.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        t3.close()
    except Exception:
        os.remove(tmp.name)
        raise HTTPException(400, "landrec.db in the backup is not a readable database")
    os.remove(tmp.name)

    ts = time.strftime("%Y%m%d_%H%M%S")
    safe_dir = os.path.join(os.path.dirname(store.DB_PATH),
                            "backup_before_restore_%s" % ts)
    os.makedirs(safe_dir, exist_ok=True)
    shutil.copy2(store.DB_PATH, os.path.join(safe_dir, "landrec.db"))
    if os.path.isdir(store.UPLOAD_DIR):
        shutil.copytree(store.UPLOAD_DIR, os.path.join(safe_dir, "uploads"),
                        dirs_exist_ok=True)

    # apply the backup
    with open(store.DB_PATH, "wb") as fh:
        fh.write(db_bytes)
    if os.path.isdir(store.UPLOAD_DIR):
        for f in os.listdir(store.UPLOAD_DIR):
            fp = os.path.join(store.UPLOAD_DIR, f)
            if os.path.isfile(fp):
                os.remove(fp)
    n_up = 0
    for n in names:
        if n.startswith("uploads/") and not n.endswith("/"):
            os.makedirs(store.UPLOAD_DIR, exist_ok=True)
            target = os.path.join(store.UPLOAD_DIR, os.path.basename(n))
            with zf.open(n) as src_f, open(target, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)
            n_up += 1
    store.audit(None, user["id"], user["email"], "backup_restored",
                "%d documents restored (safety copy: %s)" % (n_docs, os.path.basename(safe_dir)))
    return {"ok": True, "documents": n_docs, "uploads_restored": n_up,
            "safety_copy": safe_dir,
            "note": "Data restored. The previous data was kept as a safety copy at: %s" % safe_dir}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(require_role("admin"))):
    doc = store.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Not found")
    c = store._conn()
    c.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    c.execute("DELETE FROM audit WHERE doc_id=?", (doc_id,))
    c.commit()
    c.close()
    if doc.get("stored_path") and os.path.exists(doc["stored_path"]):
        os.remove(doc["stored_path"])
    store.audit(None, user["id"], user["email"], "delete_document", doc_id)
    return {"ok": True}


@app.get("/api/dashboard")
def dashboard(user: dict = Depends(get_current_user)):
    d = store.dashboard_stats()
    d["sla"] = store.sla_stats()
    d["mutations"] = store.mutation_counts()
    d["ai_routed"] = store.count_ai_routed()
    return d


def _find_tesseract():
    """Locate the Tesseract binary in priority order:
    pytesseract setting -> PYTESSERACT_PATH -> bundled copy -> well-known
    Windows install locations -> system PATH.
    Returns (path_or_empty, error_or_None, candidates_list)."""
    import shutil
    import pytesseract
    candidates = []
    tc = pytesseract.pytesseract.tesseract_cmd
    if tc:
        candidates.append(tc)
    env_tc = os.environ.get("PYTESSERACT_PATH")
    if env_tc:
        candidates.append(env_tc)
    bundled = os.path.join(
        paths.resource_dir(), "tesseract",
        "tesseract.exe" if os.name == "nt" else "tesseract")
    if os.path.exists(bundled):
        candidates.append(bundled)
    candidates.append("tesseract")
    for c in candidates:
        rp = c if os.path.isabs(c) else (shutil.which(c) or "")
        if rp and os.path.exists(rp):
            return rp, None, candidates
    # Last resort: the shared locator (well-known Windows install paths
    # such as C:\Program Files\Tesseract-OCR, which are NOT on PATH).
    try:
        from landrec import ocr as _ocr
        found = _ocr.locate_tesseract()
        if found:
            return found, None, candidates + [found]
    except Exception:  # noqa: BLE001 — detection helper must never 500
        pass
    return "", ("tesseract binary not found (looked in: %s)"
                % ", ".join(candidates)), candidates


def _tesseract_info():
    """{cmd, version, error} — used by System Status panel and self-test."""
    import subprocess as _sp
    tess = {"cmd": "", "version": None, "error": None}
    try:
        resolved, err, _cand = _find_tesseract()
        if resolved:
            tess["cmd"] = resolved
            r = _sp.run([resolved, "--version"], capture_output=True, timeout=15)
            first = (r.stderr or r.stdout).decode(errors="replace").splitlines()
            tess["version"] = first[0].strip() if first else "ok"
        else:
            tess["error"] = err
    except Exception as e:  # noqa: BLE001
        tess["error"] = "%s: %s" % (type(e).__name__, e)
    return tess


_avail_langs_cache = None


@app.get("/api/languages")
def available_languages(user: dict = Depends(get_current_user)):
    """Which Tesseract language packs are actually installed on THIS
    machine (the UI uses this to disable dropdown options that would
    otherwise silently fall back to English OCR)."""
    global _avail_langs_cache
    if _avail_langs_cache is None:
        import subprocess as _sp
        langs = set()
        try:
            resolved, _e, _c = _find_tesseract()
            if resolved:
                r = _sp.run([resolved, "--list-langs"], capture_output=True,
                            timeout=20)
                txt = (r.stdout or r.stderr).decode(errors="replace")
                langs = {ln.strip().lower() for ln in txt.splitlines()[1:]
                         if ln.strip()}
        except Exception:  # noqa: BLE001
            pass
        _avail_langs_cache = langs
    return {"installed": sorted(_avail_langs_cache)}


@app.post("/api/system/selftest")
def system_selftest(user: dict = Depends(require_role("admin"))):
    """ONE-CLICK FULL DIAGNOSTIC: runs the entire OCR pipeline end-to-end
    on THIS machine and reports every stage pass/fail with timings.
    This is how problems get reported in ONE paste instead of five
    screenshots: run the self-test, press Copy Report, send the text.

    Checks: tesseract -> language packs -> worker handshake -> OCR
    auto-detect -> OCR with hint -> PDF -> field extraction.
    """
    import subprocess as _sp
    import time as _time
    from landrec import ocrpool

    checks = []

    def _check(cid, name):
        return {"id": cid, "name": name, "ok": False, "detail": "", "ms": 0}

    # ---- 1. Tesseract ----
    t0 = _time.time()
    c = _check("tesseract", "Tesseract engine")
    tess = _tesseract_info()
    c["ms"] = int((_time.time() - t0) * 1000)
    if tess["version"]:
        c["ok"], c["detail"] = True, "%s — %s" % (tess["version"], tess["cmd"])
    else:
        c["detail"] = tess.get("error") or "unknown error"
    checks.append(c)

    # ---- 2. Languages ----
    t0 = _time.time()
    c = _check("langs", "Language packs")
    try:
        resolved, _e, _cand = _find_tesseract()
        if resolved:
            r = _sp.run([resolved, "--list-langs"], capture_output=True, timeout=20)
            txt = (r.stdout or r.stderr).decode(errors="replace")
            langs = [ln.strip() for ln in txt.splitlines()[1:]
                     if ln.strip() and ln.strip().lower() != "osd"]
            c["detail"] = ", ".join(langs) if langs else "NONE FOUND"
            c["ok"] = any(l.lower() == "eng" for l in langs)
        else:
            c["detail"] = "skipped (no tesseract found)"
    except Exception as e:  # noqa: BLE001
        c["detail"] = "%s: %s" % (type(e).__name__, e)
    c["ms"] = int((_time.time() - t0) * 1000)
    checks.append(c)

    # ---- 3. Worker handshake ----
    t0 = _time.time()
    c = _check("worker", "OCR worker start (handshake)")
    try:
        ocrpool.ensure_worker()
        st = ocrpool.status()
        c["ok"] = (st["state"] == "ready" and st["worker_alive"])
        c["detail"] = ("pid %s, state %s" % (st["worker_pid"], st["state"])
                       if c["ok"] else
                       "state=%s error=%s" % (st["state"], st["last_spawn_error"]))
    except Exception as e:  # noqa: BLE001
        c["detail"] = "%s: %s" % (type(e).__name__, e)
    c["ms"] = int((_time.time() - t0) * 1000)
    checks.append(c)

    # pick bundled samples for the OCR checks
    img_name = None
    for cand in ("hindi_khatauni_sample.png", "english_jamabandi_sample.png",
                 "tamil_patta_sample.png", "telugu_pahani_sample.png"):
        if os.path.exists(os.path.join(SAMPLES_DIR, cand)):
            img_name = cand
            break
    if not img_name and os.path.isdir(SAMPLES_DIR):
        for f in sorted(os.listdir(SAMPLES_DIR)):
            if f.lower().endswith((".png", ".jpg", ".jpeg")):
                img_name = f
                break
    pdf_name = None
    if os.path.isdir(SAMPLES_DIR):
        for f in sorted(os.listdir(SAMPLES_DIR)):
            if f.lower().endswith(".pdf"):
                pdf_name = f
                break

    ocr_result = None
    if not img_name:
        checks.append({"id": "ocr_auto", "name": "OCR (auto-detect)", "ok": False,
                       "detail": "no sample image found in bundled samples/", "ms": 0})
        checks.append({"id": "ocr_hint", "name": "OCR (language hint)", "ok": False,
                       "detail": "skipped (no sample image)", "ms": 0})
        checks.append({"id": "pdf", "name": "PDF processing", "ok": False,
                       "detail": "skipped (no sample image)", "ms": 0})
        checks.append({"id": "fields", "name": "Field extraction (NLP)", "ok": False,
                       "detail": "skipped (no OCR output)", "ms": 0})
    else:
        img_bytes = open(os.path.join(SAMPLES_DIR, img_name), "rb").read()

        # ---- 4. OCR auto-detect ----
        t0 = _time.time()
        c = _check("ocr_auto", "OCR end-to-end (auto-detect: %s)" % img_name)
        try:
            ocr_result = ocrpool.run_ocr(img_bytes, img_name, langs=None)
            words = len(ocr_result["pages"][0]["words"]) if ocr_result["pages"] else 0
            c["ok"] = words > 0
            c["detail"] = ("%d words, conf %s, scripts %s"
                           % (words, ocr_result["mean_conf"],
                              ocr_result["detected_scripts"] or ["eng?"]))
        except Exception as e:  # noqa: BLE001
            c["detail"] = "%s: %s" % (type(e).__name__, e)
        c["ms"] = int((_time.time() - t0) * 1000)
        checks.append(c)

        # ---- 5. OCR with hint ----
        t0 = _time.time()
        c = _check("ocr_hint", "OCR end-to-end (language hint)")
        try:
            hint = (ocr_result["detected_scripts"] or ["eng"])[0] if ocr_result else "eng"
            r2 = ocrpool.run_ocr(img_bytes, img_name, langs=[hint])
            words = len(r2["pages"][0]["words"]) if r2["pages"] else 0
            c["ok"] = words > 0
            c["detail"] = ("hint=[%s] -> %d words, conf %s (fast path, ~2x quicker than auto)"
                           % (hint, words, r2["mean_conf"]))
        except Exception as e:  # noqa: BLE001
            c["detail"] = "%s: %s" % (type(e).__name__, e)
        c["ms"] = int((_time.time() - t0) * 1000)
        checks.append(c)

    # ---- 6. PDF ----
    if pdf_name:
        t0 = _time.time()
        c = _check("pdf", "PDF processing (%s)" % pdf_name)
        try:
            rb = open(os.path.join(SAMPLES_DIR, pdf_name), "rb").read()
            rp = ocrpool.run_ocr(rb, pdf_name, langs=None)
            c["ok"] = rp["num_pages"] > 0
            c["detail"] = "%d page(s) OK, conf %s" % (rp["num_pages"], rp["mean_conf"])
        except Exception as e:  # noqa: BLE001
            c["detail"] = "%s: %s" % (type(e).__name__, e)
        c["ms"] = int((_time.time() - t0) * 1000)
        checks.append(c)

    # ---- 7. Field extraction ----
    if ocr_result:
        t0 = _time.time()
        c = _check("fields", "Field extraction (NLP)")
        try:
            fields = extractor.extract_fields(ocr_result)
            filled = [k for k, v in fields.items()
                      if v and str(v).strip() and str(v).strip().lower() != "not found"]
            c["ok"] = len(filled) >= 3
            c["detail"] = ("%d fields filled: %s"
                           % (len(filled),
                              ", ".join(filled[:6]) + ("…" if len(filled) > 6 else "")))
        except Exception as e:  # noqa: BLE001
            c["detail"] = "%s: %s" % (type(e).__name__, e)
        c["ms"] = int((_time.time() - t0) * 1000)
        checks.append(c)

    total_ms = sum(c["ms"] for c in checks)
    passed = sum(1 for c in checks if c["ok"])
    return {
        "version": APP_VERSION,
        "checks": checks,
        "summary": "%d/%d checks passed in %.1fs" % (passed, len(checks), total_ms / 1000.0),
        "all_ok": passed == len(checks),
    }


@app.get("/api/system")
def system_status(user: dict = Depends(require_role("admin"))):
    """Diagnostics: what the System Status panel shows, plus the log tail
    so a problem can be diagnosed from one screenshot."""
    import platform
    import subprocess as _sp
    import sqlite3 as _sqlite
    from landrec import ocrpool

    # Tesseract location/version (the server never imports cv2; pytesseract
    # is pure Python, and running tesseract.exe as a subprocess is safe).
    tess = _tesseract_info()

    # database quick stats
    db = {"path": os.path.join(paths.data_dir(), "data", "landrec.db"),
          "documents": None, "users": None, "error": None}
    try:
        c = _sqlite.connect(db["path"], timeout=5)
        db["documents"] = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        db["users"] = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        c.close()
    except Exception as e:  # noqa: BLE001
        db["error"] = str(e)

    # log tail (last 40 lines)
    log_file = os.path.join(paths.data_dir(), "data", "server.log")
    log_tail = []
    try:
        with open(log_file, errors="replace") as f:
            log_tail = f.readlines()[-40:]
    except Exception:
        pass

    return {
        "app": "Land Record System (DILRMP-style)",
        "version": APP_VERSION,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "frozen": paths.is_frozen(),
        "server_pid": os.getpid(),
        "uptime_sec": round(time.time() - _START_TIME),
        "port": int(os.environ.get("PORT", "8000")),
        "data_dir": paths.data_dir(),
        "ocr_worker": ocrpool.status(),
        "tesseract": tess,
        "database": db,
        "log_file": log_file,
        "log_tail": log_tail,
    }


@app.get("/api/corrections")
def corrections(user: dict = Depends(require_role("verifier"))):
    return {"corrections": store.get_learned_corrections()}


@app.delete("/api/corrections/{cid}")
def delete_correction(cid: int, user: dict = Depends(require_role("verifier"))):
    """Remove a learned rule (AI Feedback tab). Rules with count < 2 are
    record-only and never applied; deleting any rule is always safe."""
    store.delete_correction(cid)
    store.audit(None, user["id"], user["email"], "correction_deleted", str(cid))
    return {"ok": True}


@app.get("/api/audit")
def audit_all(user: dict = Depends(require_role("verifier"))):
    return {"audit": store.get_audit()}


@app.get("/api/audit/{doc_id}")
def audit_doc(doc_id: str, user: dict = Depends(require_role("verifier"))):
    return {"audit": store.get_audit(doc_id=doc_id)}


@app.get("/api/fields")
def field_metadata(user: dict = Depends(get_current_user)):
    return {"fields": [{"id": f[0], "label": f[1]} for f in common.FIELD_DEFS],
            "verify_threshold": validator.VERIFY_THRESHOLD,
            "roles": {r: auth.ROLE_LABELS[r] for r in auth.ROLES}}


# --------------------------------------------------------------------------
# Keep-alive / auto-stop: every open browser tab announces itself with a
# unique tab id (ping every 5 s) and sends a "bye" the moment the tab
# closes. The .exe launcher's watchdog shuts the server down as soon as
# NO tabs remain — closing the website stops the background server almost
# immediately (~10 s grace so a page refresh doesn't kill it).
# Tabs that vanish without a bye (browser crash) are swept after 30 s.
# --------------------------------------------------------------------------
_ACTIVE_TABS = {}          # tab_id -> last_seen timestamp
_TAB_STALE_SECONDS = 30.0


def _parse_tab_id(body: bytes):
    try:
        payload = json.loads(body or b"{}")
    except (ValueError, TypeError):
        return None
    if isinstance(payload, dict):
        tid = payload.get("tab_id")
        if isinstance(tid, str) and 0 < len(tid) <= 64:
            return tid
    return None


def _prune_stale_tabs() -> None:
    now = time.time()
    for tid in [t for t, ts in _ACTIVE_TABS.items() if now - ts > _TAB_STALE_SECONDS]:
        _ACTIVE_TABS.pop(tid, None)


@app.post("/api/keepalive")
async def keepalive(request: Request):
    global _last_ping_ts
    _last_ping_ts = time.time()
    tid = _parse_tab_id(await request.body())
    if tid:
        _ACTIVE_TABS[tid] = time.time()
        _prune_stale_tabs()
    return {"ok": True, "tabs": len(_ACTIVE_TABS)}


@app.post("/api/keepalive/bye")
async def keepalive_bye(request: Request):
    """Tab closing (browser fires this via sendBeacon on pagehide)."""
    global _last_ping_ts
    _last_ping_ts = time.time()
    tid = _parse_tab_id(await request.body())
    if tid:
        _ACTIVE_TABS.pop(tid, None)
    return {"ok": True, "tabs": len(_ACTIVE_TABS)}


def last_ping() -> float:
    return _last_ping_ts


def active_tab_count() -> int:
    _prune_stale_tabs()
    return len(_ACTIVE_TABS)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"),
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                                 "Pragma": "no-cache", "Expires": "0"})


@app.get("/api/health")
def health():
    """Lightweight liveness probe (no auth). The web UI polls this to
    auto-reconnect when the server process has crashed and restarted."""
    return {"ok": True, "ts": time.time()}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
