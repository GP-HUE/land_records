# Intelligent Land Record Digitization & Validation System

An AI-powered, **multi-user** web application that automatically extracts structured
information from scanned land records, handwritten documents, and legacy PDFs — then
validates it, scores confidence, and routes uncertain records for human verification.

Built around the study brief: *"Develop an AI-powered Intelligent Land Record
Digitization and Validation System"* (OCR + Computer Vision + NLP, multilingual,
RBAC, audit, DILRMP/LRMS/GIS-aligned).

---

## Features (mapped to the brief)

| Brief requirement | Implementation |
|---|---|
| Multilingual OCR (printed + handwritten) | Tesseract with **10 languages** + automatic script detection |
| Extract from PDFs, images, historical docs | PyMuPDF (PDF→image) + OpenCV denoise/adaptive-threshold |
| Classify into predefined land-record fields | 16 fields (owner, survey/khasra/khata no., area, village, tehsil, district, land class, mutation, registration…) + Devanagari-numeral normalization |
| Automated validation | Business rules + cross-language consistency + duplicate detection |
| Confidence scoring | Per-field score from word-level OCR confidence; low-confidence fields auto-flagged |
| Human verification workflow | Editable review form; corrections recorded with audit trail |
| AI learning mechanism | Corrections stored as OCR→truth mappings, auto-applied to future extractions |
| **User signup / sign-in** | Secure accounts with PBKDF2 password hashing + HMAC-signed session tokens |
| **Role-based access control** | 4 roles: Admin, Verifier, Operator, Viewer — enforced server-side |
| Secure repository + audit trail | Original files stored & downloadable; every action logged with the acting user |
| Dashboards / APIs | Docs processed, accuracy, pending cases, state/district progress + REST API |
| Security | Login throttling (lock after 5 failures), deactivation, password change (invalidates sessions), session expiry |

---

## Roles

| Role | Upload | View | Verify/Correct | Manage users | See learning/audit |
|---|---|---|---|---|---|
| **Admin** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Verification Officer** | ✅ | ✅ | ✅ | — | ✅ |
| **Data Operator** | ✅ | ✅ | — | — | — |
| **Viewer** | — | ✅ | — | — | — |

---

## Getting started

### Default administrator
On first run the system seeds an admin account:

- **Email:** `admin@landrec.gov.in`
- **Password:** `Admin@123`

**Change this password immediately** (Account → Change password). New users can also
self-register (as Data Operators) from the sign-in screen, or the admin can create
accounts with any role from the **Users** tab.

### Demo data (automatic on first run)

A **fresh install seeds demo data automatically on first boot** (empty database
→ 7 demo land records with real village/tehsil/district fields, a demo mutation
application, and demo accounts such as `demo.admin@demo.local / Demo@Admin1`).
This is what makes the **Land Map → Village Sheet** work immediately: the
district/tehsil/village dropdowns are populated and the plot grid renders.
An existing database is NEVER touched. To skip first-boot seeding, start with
`LR_NO_DEMO_SEED=1`; as admin you can also click **🧪 Load Demo Data** in the
Land Map tab (it refuses to run while the database already has documents).

### Run

```bash
pip install -r requirements.txt
# system tesseract + language packs (see requirements below)

python3 -m uvicorn landrec.main:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

System Tesseract (required for OCR):

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-hin tesseract-ocr-mar \
  tesseract-ocr-tam tesseract-ocr-tel tesseract-ocr-kan tesseract-ocr-guj \
  tesseract-ocr-ben tesseract-ocr-urd
```

---

## Architecture

```
landrec/
├── auth.py         # PBKDF2 password hashing + HMAC session tokens (stdlib, no deps)
├── common.py       # numeral conversion, script detection, label dictionaries
├── ocr.py          # image preprocessing + multilingual OCR (images & PDFs)
├── extractor.py    # field extraction, fuzzy label matching, confidence scoring
├── validator.py    # business rules, cross-language checks, duplicate key
├── store.py        # SQLite: users, documents, audit, corrections, login throttling
├── main.py         # FastAPI app: auth, RBAC, processing, dashboards
└── static/index.html   # single-page UI (sign-in, upload, verify, dashboards, admin)
samples/            # generated test records
data/               # SQLite DB, uploads, signing secret (created at runtime)
```

---

## REST API

Authentication: send `Authorization: Bearer <token>` on every request (token returned
by `/api/auth/login` or `/api/auth/signup`).

| Endpoint | Access | Description |
|---|---|---|
| `POST /api/auth/signup` | public | Create account (operator role) |
| `POST /api/auth/login` | public | Sign in → token + user |
| `GET /api/auth/me` | any user | Current user |
| `POST /api/auth/change-password` | any user | Change password (signs out other sessions) |
| `POST /api/auth/logout` | any user | Invalidate sessions |
| `POST /api/process` | operator+ | Upload PDF/image, run pipeline |
| `POST /api/process/sample/{name}` | operator+ | Process a built-in sample |
| `GET /api/documents` | any user | List documents |
| `GET /api/documents/{id}` | any user | Document + extracted fields |
| `GET /api/documents/{id}/file` | any user | Download original file |
| `POST /api/documents/{id}/verify` | verifier+ | Submit corrections (learning) |
| `DELETE /api/documents/{id}` | admin | Delete document |
| `GET /api/dashboard` | any user | Aggregate statistics |
| `GET /api/corrections` | verifier+ | Learned corrections |
| `GET /api/audit` | verifier+ | Full audit trail |
| `GET /api/users` | admin | List users |
| `POST /api/users` | admin | Create user (any role) |
| `PATCH /api/users/{id}` | admin | Change role / activate-deactivate |

---

## Notes & production roadmap

This is a **fully functional multi-user prototype** — real authentication, RBAC,
audit, file storage, and the complete digitization/validation/learning pipeline.

For a hardened production deployment, the natural next steps are:
- **Database**: move SQLite → PostgreSQL (the `store.py` layer is isolated for this).
- **HTTPS + reverse proxy**: run behind nginx/Caddy (or a PaaS like Render/Railway).
- **Async queue** for large batch scans (Celery/RQ + Redis).
- **Fine-tuned NER/ICR model** to replace rule-based extraction (the interface is model-agnostic).
- **Integration connectors** for LRMS / DILRMP / GIS (export GeoJSON/CSV, push APIs).
- **Email verification & password reset** flows.
- **Object storage** (S3) for document files at scale.
