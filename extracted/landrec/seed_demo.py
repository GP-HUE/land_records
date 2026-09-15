"""Demo-data seeder (importable module).

Seeds a rich demo state — demo accounts, land records (verified / pending /
AI-routed / draft) with REAL village/tehsil/district fields, a mutation
application, and sample scans — so every tab works out of the box:

  * Land Map → Village Sheet: the district/tehsil/village cascade has
    options and the plot grid renders.
  * Land Map → Real Map: villages are REAL OSM places, so the blue dots
    resolve even offline-of-internet is not required for the sheet.
  * Verification Queue / Comparison / Mutation / History: populated.

This is what makes a FRESH LOCAL INSTALL (start.bat / run.py / .exe) usable
immediately — previously the seeder only ran on Render's ephemeral disk
(hosted deployments), so local installs booted with an empty database and
the Village Sheet showed blank district/tehsil/village dropdowns.

Guards:
  * NEVER touches a database that already has documents (unless
    ``force=True``), so it is safe to call on every boot.
  * Opt out entirely with the environment variable ``LR_NO_DEMO_SEED=1``.
"""
import json
import os
import shutil
import time
import uuid

from . import store
from . import paths

SAMPLES = os.path.join(paths.resource_dir(), "samples")

DEMO_ACCOUNTS = [
    ("demo.admin@demo.local", "Demo@Admin1", "Demo Admin", "admin"),
    ("demo.operator@demo.local", "Demo@Operator1", "Demo Data Officer", "operator"),
    ("demo.verifier@demo.local", "Demo@Verifier1", "Demo Verification Officer", "verifier"),
]


def _f(value, conf=0.9, verified=False):
    return {"value": value, "confidence": conf, "verified": verified}


def _demo_docs():
    """(filename, sample_file, doc_type, status, fields, uploaded_by_email, extras)"""
    recs = []

    def add(filename, sample, doc_type, status, fields, owner_email, **extra):
        recs.append({"filename": filename, "sample": sample, "doc_type": doc_type,
                     "status": status, "fields": fields, "owner_email": owner_email,
                     "extra": extra})

    add("khatauni_barkheda_2024.pdf", "hindi_khatauni_sample.png", "land_record", "verified",
        {"owner_name": _f("रामस्वरूप शर्मा", 0.94, True), "father_name": _f("श्यामलाल शर्मा", 0.93, True),
         "survey_number": _f("312", 0.97, True), "khasra_number": _f("45/2", 0.92, True),
         "khata_number": _f("128", 0.95, True), "plot_number": _f("7", 0.88, True),
         "area": _f("2.5 एकड़", 0.9, True), "village": _f("Barkheda", 0.93, True),
         "tehsil": _f("Huzur", 0.9, True), "district": _f("Bhopal", 0.95, True),
         "state": _f("Madhya Pradesh", 0.92, True), "land_class": _f("Irrigated", 0.86, True),
         "ownership_type": _f("Private", 0.88, True), "mutation_no": _f("4471", 0.9, True),
         "registration_no": _f("एमपी/2021/8842", 0.89, True), "khatauni_year": _f("2023-24", 0.96, True)},
        "demo.operator@demo.local", mean_conf=91.2)

    add("khatauni_barkheda_2021.pdf", "hindi_khatauni_sample.png", "land_record", "verified",
        {"owner_name": _f("रामस्वरूप शर्मा", 0.9, True), "father_name": _f("श्यामलाल शर्मा", 0.9, True),
         "survey_number": _f("312", 0.96, True), "khasra_number": _f("45/2", 0.9, True),
         "khata_number": _f("128", 0.93, True), "area": _f("2.5 एकड़", 0.88, True),
         "village": _f("Barkheda", 0.92, True), "tehsil": _f("Huzur", 0.88, True),
         "district": _f("Bhopal", 0.94, True), "state": _f("Madhya Pradesh", 0.9, True),
         "land_class": _f("Irrigated", 0.84, True), "ownership_type": _f("Private", 0.86, True),
         "khatauni_year": _f("2020-21", 0.95, True)},
        "demo.operator@demo.local", mean_conf=88.4)

    add("pahani_arera_2023.pdf", "telugu_pahani_sample.png", "land_record", "pending_review",
        {"owner_name": _f("రాజేశ్వర్ రావు", 0.86), "father_name": _f("సుబ్బయ్య", 0.84),
         "survey_number": _f("452", 0.9), "khasra_number": _f("77", 0.85),
         "area": _f("3.20 ఎకరాలు", 0.8), "village": _f("Arera", 0.87),
         "tehsil": _f("Huzur", 0.82), "district": _f("Bhopal", 0.88),
         "state": _f("Madhya Pradesh", 0.85), "land_class": _f("Agricultural", 0.78),
         "khatauni_year": _f("2022-23", 0.9)},
        "demo.operator@demo.local", mean_conf=83.1)

    add("jamabandi_arera_2019.pdf", "xfer_old_2019.png", "land_record", "verified",
        {"owner_name": _f("Ram Bahadur Singh", 0.92, True), "father_name": _f("Kishan Singh", 0.9, True),
         "survey_number": _f("452", 0.95, True), "khasra_number": _f("77", 0.9, True),
         "area": _f("4.8 acre", 0.88, True), "village": _f("Arera", 0.92, True),
         "tehsil": _f("Huzur", 0.89, True), "district": _f("Bhopal", 0.93, True),
         "state": _f("Madhya Pradesh", 0.9, True), "land_class": _f("Agricultural", 0.85, True),
         "ownership_type": _f("Private", 0.87, True), "khatauni_year": _f("2018-19", 0.94, True)},
        "demo.operator@demo.local", mean_conf=89.7)

    add("ferfar_arera_2021.pdf", "xfer_mutation_2021.png", "mutation", "pending_review",
        {"owner_name": _f("Kamla Devi Singh", 0.88), "father_name": _f("Ram Bahadur Singh", 0.85),
         "survey_number": _f("452", 0.92), "khasra_number": _f("77", 0.86),
         "area": _f("4.8 acre", 0.83), "village": _f("Arera", 0.89),
         "tehsil": _f("Huzur", 0.84), "district": _f("Bhopal", 0.9),
         "state": _f("Madhya Pradesh", 0.87), "khatauni_year": _f("2020-21", 0.91)},
        "demo.operator@demo.local", mean_conf=85.9)

    # NOTE: villages (Barkheda / Arera / Sarangpur) are REAL places that
    # OpenStreetMap can resolve — so the hosted demo's Real Map shows blue
    # dots. The unreadable scan below has no fields and correctly gets none.
    # AI-routed unreadable scan (demonstrates the rescue + routing feature)
    add("unreadable_scan_001.jpg", "bad_blank_scan.png", "land_record", "pending_review",
        {}, "demo.operator@demo.local", mean_conf=0.0,
        routed_reason="No text detected — the scan may be blank, too dark, or the wrong page was scanned. (demo)")

    add("draft_khatauni_new.pdf", "hindi_khatauni_sample.png", "land_record", "draft",
        {"owner_name": _f("गोपाल वर्मा", 0.7), "survey_number": _f("118", 0.8),
         "village": _f("Sarangpur", 0.75), "tehsil": _f("Budar", 0.7),
         "district": _f("Shahdol", 0.8), "state": _f("Madhya Pradesh", 0.75)},
        "demo.operator@demo.local", mean_conf=71.3)

    return recs


def _doc_count():
    c = store._conn()
    try:
        return c.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
    finally:
        c.close()


def seed_demo_data(force=False):
    """Seed demo state. Returns a summary dict.

    * Skips (``seeded=False``) when the database already has documents and
      ``force`` is False — it never destroys user data.
    * ``force=True`` is only honoured by the admin "Load demo data" button
      when the database is EMPTY; with data present it still refuses.
    """
    store.init_db()  # idempotent — safe when called before/without main.py boot
    n = _doc_count()
    if n > 0:
        return {"seeded": False, "documents": n,
                "message": "Database already has %d document(s) — demo seed skipped." % n}

    print("[seed] empty database — seeding demo data…")

    # ---- accounts ----
    user_ids = {}
    for email, pw, name, role in DEMO_ACCOUNTS:
        try:
            user_ids[email] = store.create_user(email, pw, name, role=role)
        except ValueError:
            pass

    # ---- uploads dir + sample files ----
    os.makedirs(store.UPLOAD_DIR, exist_ok=True)
    uploaded_by_ids = user_ids.get("demo.operator@demo.local", "")
    admin_uid = user_ids.get("demo.admin@demo.local", "")
    verifier_uid = user_ids.get("demo.verifier@demo.local", "")

    demo_users = [store.get_user(uid) for uid in user_ids.values()]

    ts = time.time()
    created = 0
    for i, rec in enumerate(_demo_docs()):
        doc_id = uuid.uuid4().hex[:12]
        sample = rec["sample"]
        stored_path = os.path.join(store.UPLOAD_DIR, "seed_%s" % os.path.basename(sample))
        if sample and os.path.exists(os.path.join(SAMPLES, sample)):
            shutil.copy(os.path.join(SAMPLES, sample), stored_path)
        fields = rec["fields"]
        fjson = json.dumps(fields, ensure_ascii=False)
        validation = {"verdict": "valid" if rec["status"] == "verified" else "review",
                      "issues": [], "low_confidence_fields": []}
        mean_conf = rec["extra"].get("mean_conf", 85.0)
        c = store._conn()
        c.execute("""INSERT INTO documents
            (id, filename, stored_path, mime_type, file_size, uploaded_by, uploaded_at,
             ocr_text, mean_conf, languages, extracted_json, validation_json, verdict,
             status, dedup_key, doc_type, reviewer_notes, submitted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (doc_id, rec["filename"], stored_path if os.path.exists(stored_path) else "",
                   "application/pdf", 120000, uploaded_by_ids, ts + i,
                   "(demo record — created by the demo seeder)", mean_conf,
                   '["hin"]' if "hi" in (rec["filename"] or "") else '["eng"]',
                   fjson, json.dumps(validation),
                   "verified" if rec["status"] == "verified" else "review",
                   rec["status"], "", rec["doc_type"], "",
                   ts + i if rec["status"] != "draft" else None))
        c.commit()
        c.close()
        created += 1
        user = demo_users[0] if demo_users else {"id": admin_uid, "email": "seed", "full_name": "Seeder"}
        store.audit(doc_id, user["id"], user.get("email", "seed"), "document_created",
                    rec["filename"] + " (demo seed)")
        if rec["status"] == "verified":
            store.audit(doc_id, verifier_uid, "demo.verifier@demo.local", "verified", "demo seed")
            store.certify_document(doc_id, "demo.verifier@demo.local")
        if "routed_reason" in rec["extra"]:
            verifier = next((u for u in demo_users if u and u.get("role") == "verifier"), None)
            if verifier:
                store.route_document(doc_id,
                                     {"id": verifier["id"], "email": verifier["email"],
                                      "name": verifier.get("full_name", verifier["email"])},
                                     rec["extra"]["routed_reason"],
                                     uploaded_by=uploaded_by_ids,
                                     uploaded_by_email="demo.operator@demo.local")

    # ---- one mutation application ----
    verifier = next((u for u in demo_users if u and u.get("role") == "verifier"), None)
    operator = next((u for u in demo_users if u and u.get("role") == "operator"), None)
    if verifier and operator:
        store.create_mutation(
            {"transfer_type": "sale", "previous_owner": "Ram Bahadur Singh",
             "new_owner": "Kamla Devi Singh", "survey_number": "452", "khasra_number": "77",
             "village": "Arera", "district": "Bhopal", "state": "Madhya Pradesh",
             "deed_no": "SD/2021/118", "deed_date": "2021-06-14",
             "notes": "Sale per registered sale deed (demo application)."},
            operator)

    total = _doc_count()
    print("[seed] done — %d demo documents created." % total)
    return {"seeded": True, "documents": total,
            "message": "%d demo records created. Demo logins: demo.admin@demo.local / Demo@Admin1 · "
                       "demo.operator@demo.local / Demo@Operator1 · demo.verifier@demo.local / Demo@Verifier1" % total}


def seed_if_empty():
    """Boot-time entry point: seed only when the database is empty and the
    user has not opted out via LR_NO_DEMO_SEED=1."""
    if os.environ.get("LR_NO_DEMO_SEED", "").strip() in ("1", "true", "yes"):
        print("[seed] LR_NO_DEMO_SEED set — skipping demo seed")
        return {"seeded": False, "message": "disabled by LR_NO_DEMO_SEED"}
    try:
        return seed_demo_data(force=False)
    except Exception as e:  # noqa: BLE001 — a seed failure must never kill boot
        print("[seed] demo seed failed (non-fatal): %r" % e)
        return {"seeded": False, "message": "seed failed: %s" % e}
