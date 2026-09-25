"""Bulk OCR (v3.12) — multi-scan intake queue with background processing.

One drag of up to 25 scanned land-record files creates a BATCH.  A single
background worker (OCR is CPU-heavy) processes the items one by one through
the SAME pipeline a single upload uses (main._analyze_document), so a bulk
batch and a manual upload are read, rescued, validated and deduplicated
identically.  Per item the batch table shows extracted fields, confidence,
validation verdict, duplicate warnings — and for ADMIN batches, an instant
SA CourtLink screening.

Nothing bypasses verification: importing a processed item creates the land
record with status "pending_review" — a Verification Officer still approves
every imported row.  Batches live in SQLite (bulk dir on disk), so a browser
refresh or app restart never loses a batch; on boot, items stuck in
'processing' are re-queued automatically.

100% local & offline: Tesseract OCR, rule-based checks, no internet.
"""
import json
import logging
import os
import threading
import time
import uuid

from . import courtlink, store

log = logging.getLogger("landrec.bulk")

MAX_BATCH_FILES = 25
ITEM_STATUSES = ("queued", "processing", "done", "failed")
BULK_DIR = os.path.join(store.DATA_DIR, "bulk")

_ANALYZER = None          # injected by main.py: _analyze_document
_worker = None
_worker_lock = threading.Lock()


def set_analyzer(fn):
    """main.py injects its pipeline (avoids a circular import)."""
    global _ANALYZER
    _ANALYZER = fn


def init_db():
    os.makedirs(BULK_DIR, exist_ok=True)
    c = store._conn()
    c.execute("""CREATE TABLE IF NOT EXISTS ocr_batches (
        id TEXT PRIMARY KEY, owner_id TEXT, owner_email TEXT,
        lang TEXT, doc_type TEXT, status TEXT DEFAULT 'queued',
        total INT, done INT DEFAULT 0, failed INT DEFAULT 0,
        imported INT DEFAULT 0, created_at REAL, updated_at REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS ocr_batch_items (
        id TEXT PRIMARY KEY, batch_id TEXT, filename TEXT, stored_path TEXT,
        size INT, status TEXT DEFAULT 'queued', lang TEXT,
        langs_detected TEXT, mean_conf REAL, fields_json TEXT,
        validation_json TEXT, ocr_text TEXT, dedup_key TEXT,
        screening_json TEXT, error TEXT, imported_doc_id TEXT,
        created_at REAL, updated_at REAL)""")
    # resume: an item that was mid-flight when the app stopped re-queues
    c.execute("UPDATE ocr_batch_items SET status='queued', updated_at=? "
              "WHERE status='processing'", (time.time(),))
    c.commit()
    c.close()


# --------------------------------------------------------------------------
# batch / item CRUD
# --------------------------------------------------------------------------
def _batch_row(bid):
    c = store._conn()
    r = c.execute("SELECT * FROM ocr_batches WHERE id=?", (bid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _item_row(iid):
    c = store._conn()
    r = c.execute("SELECT * FROM ocr_batch_items WHERE id=?", (iid,)).fetchone()
    c.close()
    return dict(r) if r else None


def _bump_batch(bid):
    c = store._conn()
    cnt = c.execute("""SELECT
            SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN imported_doc_id IS NOT NULL AND imported_doc_id!=''
                THEN 1 ELSE 0 END) AS imported,
            COUNT(*) AS total
        FROM ocr_batch_items WHERE batch_id=?""", (bid,)).fetchone()
    done, failed = cnt["done"] or 0, cnt["failed"] or 0
    status = "done" if done + failed >= (cnt["total"] or 0) else "processing"
    c.execute("UPDATE ocr_batches SET done=?, failed=?, imported=?, status=?, "
              "updated_at=? WHERE id=?",
              (done, failed, cnt["imported"] or 0, status, time.time(), bid))
    c.commit()
    c.close()


def create_batch(files, lang, doc_type, user):
    """files: list of (filename, bytes, ext).  Returns the created batch."""
    bid = uuid.uuid4().hex[:12]
    ts = time.time()
    bdir = os.path.join(BULK_DIR, bid)
    os.makedirs(bdir, exist_ok=True)
    c = store._conn()
    c.execute("""INSERT INTO ocr_batches
        (id, owner_id, owner_email, lang, doc_type, status, total,
         done, failed, imported, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
              (bid, user["id"], user.get("email") or "", (lang or "").strip(),
               doc_type or "land_record", "processing", len(files), 0, 0, 0, ts, ts))
    for fn, data, ext in files:
        iid = uuid.uuid4().hex[:12]
        path = os.path.join(bdir, iid + ext)
        with open(path, "wb") as fh:
            fh.write(data)
        c.execute("""INSERT INTO ocr_batch_items
            (id, batch_id, filename, stored_path, size, status, lang,
             created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)""",
                  (iid, bid, fn, path, len(data), "queued",
                   (lang or "").strip(), ts, ts))
    c.commit()
    c.close()
    start_worker()
    return get_batch(bid, user)


def list_batches(user):
    c = store._conn()
    if user.get("role") == "admin":
        rows = c.execute("SELECT * FROM ocr_batches ORDER BY created_at DESC "
                         "LIMIT 100").fetchall()
    else:
        rows = c.execute("SELECT * FROM ocr_batches WHERE owner_id=? "
                         "ORDER BY created_at DESC LIMIT 100",
                         (user["id"],)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def _check_access(batch, user):
    if not batch:
        raise ValueError("Batch not found")
    if user.get("role") != "admin" and batch["owner_id"] != user["id"]:
        raise PermissionError("You can only see your own batches")


def get_batch(bid, user, with_items=True):
    batch = dict(_batch_row(bid) or {})
    if not batch:
        raise ValueError("Batch not found")
    _check_access(batch, user)
    batch["batch_no"] = "BATCH-" + bid[:6].upper()
    if with_items:
        c = store._conn()
        rows = c.execute("SELECT * FROM ocr_batch_items WHERE batch_id=? "
                         "ORDER BY created_at, filename", (bid,)).fetchall()
        c.close()
        items = []
        for r in rows:
            it = dict(r)
            fields = json.loads(it.pop("fields_json") or "{}")
            val = json.loads(it.pop("validation_json") or "{}")
            scr = json.loads(it.pop("screening_json") or "null")
            it["fields"] = fields
            it["validation"] = val
            it["screening"] = scr
            it.pop("ocr_text", None)      # heavy; not needed in the UI list
            items.append(it)
        batch["items"] = items
        batch["clean"] = sum(1 for it in items if _is_clean(it))
    return batch


def _is_clean(item):
    """Importable in one click: processed OK, verdict valid, not a duplicate,
    not already imported."""
    if item.get("status") != "done" or item.get("imported_doc_id"):
        return False
    val = item.get("validation") or {}
    return val.get("verdict") == "valid" and not val.get("duplicate_of")


# --------------------------------------------------------------------------
# background worker
# --------------------------------------------------------------------------
def start_worker():
    global _worker
    with _worker_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, daemon=True,
                                   name="bulk-ocr-worker")
        _worker.start()


def _worker_loop():
    while True:
        c = store._conn()
        row = c.execute("SELECT * FROM ocr_batch_items WHERE status='queued' "
                        "ORDER BY created_at LIMIT 1").fetchone()
        c.close()
        if not row:
            time.sleep(1.2)
            continue
        _process_item(dict(row))


def _process_item(item):
    iid = item["id"]
    c = store._conn()
    c.execute("UPDATE ocr_batch_items SET status='processing', updated_at=? "
              "WHERE id=?", (time.time(), iid))
    c.commit()
    c.close()
    result = {}
    try:
        with open(item["stored_path"], "rb") as fh:
            data = fh.read()
        langs = [item["lang"]] if item.get("lang") else None
        a = _ANALYZER(data, item["filename"], langs=langs)
        unreadable = a["quality"].get("unreadable")
        # SA CourtLink instant screen for ADMIN-owned batches (admin-only
        # feature — operator batches are never screened)
        screening = None
        try:
            batch = _batch_row(item["batch_id"]) or {}
            owner = store.get_user(batch.get("owner_id") or "")
            if owner and owner.get("role") == "admin" and not unreadable:
                screening = courtlink.screen_fields(a["fields"])
        except Exception:  # noqa: BLE001 — screening must never kill an item
            log.exception("bulk screening failed for %s", iid)
        result = {
            "status": "failed" if unreadable else "done",
            "mean_conf": a["ocr"].get("mean_conf"),
            "langs_detected": json.dumps(a["ocr"].get("detected_scripts") or []),
            "fields_json": json.dumps(a["fields"], ensure_ascii=False),
            "validation_json": json.dumps(a["validation"], ensure_ascii=False),
            "ocr_text": (a["ocr"].get("full_text") or "")[:60000],
            "dedup_key": a.get("dedup_key") or "",
            "screening_json": (json.dumps(screening, ensure_ascii=False)
                               if screening else None),
            "error": (" ".join(a["quality"].get("diagnosis") or [])[:400]
                      if unreadable else ""),
        }
    except Exception as e:  # noqa: BLE001 — a bad file must not kill the batch
        log.exception("bulk OCR failed for %s", item.get("filename"))
        result = {"status": "failed", "error": "Processing failed: %s" % str(e)[:300]}
    c = store._conn()
    c.execute("""UPDATE ocr_batch_items SET status=?, mean_conf=?,
                 langs_detected=?, fields_json=?, validation_json=?,
                 ocr_text=?, dedup_key=?, screening_json=?, error=?,
                 updated_at=? WHERE id=?""",
              (result.get("status", "failed"), result.get("mean_conf"),
               result.get("langs_detected"), result.get("fields_json"),
               result.get("validation_json"), result.get("ocr_text"),
               result.get("dedup_key"), result.get("screening_json"),
               result.get("error"), time.time(), iid))
    c.commit()
    c.close()
    _bump_batch(item["batch_id"])


# --------------------------------------------------------------------------
# actions: retry / import
# --------------------------------------------------------------------------
def retry_item(iid, lang, user):
    item = _item_row(iid)
    if not item:
        raise ValueError("Item not found")
    batch = _batch_row(item["batch_id"])
    _check_access(batch, user)
    if item["status"] != "failed":
        raise ValueError("Only a FAILED item can be retried")
    c = store._conn()
    c.execute("UPDATE ocr_batch_items SET status='queued', lang=?, error='', "
              "updated_at=? WHERE id=?",
              ((lang if lang is not None else item.get("lang")) or "",
               time.time(), iid))
    c.commit()
    c.close()
    _bump_batch(item["batch_id"])
    start_worker()
    return {"requeued": True, "id": iid, "lang": lang if lang is not None else item.get("lang")}


def _field_value(fields, key):
    v = (fields or {}).get(key)
    return str((v or {}).get("value") or "").strip() if isinstance(v, dict) else str(v or "").strip()


def import_item(iid, user, force=False):
    """Turn one processed item into a real land record.

    Always lands in 'pending_review' — bulk intake is high-volume, so a
    human Verification Officer approves every imported row before it
    becomes an official record.  clean-only unless force=True."""
    item = _item_row(iid)
    if not item:
        raise ValueError("Item not found")
    batch = _batch_row(item["batch_id"])
    _check_access(batch, user)
    if item["status"] != "done":
        raise ValueError("Item is not processed yet (status: %s)" % item["status"])
    if item.get("imported_doc_id"):
        raise ValueError("Item already imported as record %s" % item["imported_doc_id"])
    fields = json.loads(item["fields_json"] or "{}")
    validation = json.loads(item["validation_json"] or "{}")
    if not force and not _is_clean({**item, "validation": validation}):
        raise ValueError("Item is not clean (verdict %s) — import it row-by-row "
                         "with the per-item Import button if you accept it"
                         % validation.get("verdict"))
    ocr_result = {"full_text": item.get("ocr_text") or "",
                  "mean_conf": item.get("mean_conf") or 0,
                  "detected_scripts": json.loads(item.get("langs_detected") or "[]"),
                  "num_pages": 1}
    doc_id = store.save_upload(
        item["filename"], None, item.get("size"), item["stored_path"], user["id"],
        ocr_result, fields, validation, item.get("dedup_key") or None,
        doc_type=batch.get("doc_type") or "land_record", status="pending_review")
    store.audit(doc_id, user["id"], user.get("email") or "", "bulk_import",
                "imported via %s (bulk OCR batch %s)" %
                ("BATCH-" + (item["batch_id"] or "")[:6].upper(), item["batch_id"]))
    c = store._conn()
    c.execute("UPDATE ocr_batch_items SET imported_doc_id=?, updated_at=? WHERE id=?",
              (doc_id, time.time(), iid))
    c.commit()
    c.close()
    _bump_batch(item["batch_id"])
    return {"imported": True, "doc_id": doc_id, "status": "pending_review"}


def import_clean(bid, user):
    batch = _batch_row(bid)
    _check_access(batch, user)
    data = get_batch(bid, user)
    imported, skipped, failed = [], 0, []
    for it in data["items"]:
        if _is_clean(it):
            try:
                r = import_item(it["id"], user)
                imported.append(r["doc_id"])
            except Exception as e:  # noqa: BLE001
                failed.append({"item": it["id"], "error": str(e)[:200]})
        elif it.get("status") in ("done",) and not it.get("imported_doc_id"):
            skipped += 1
    return {"imported": len(imported), "doc_ids": imported,
            "skipped_not_clean": skipped, "failed": failed}


# --------------------------------------------------------------------------
# CSV report
# --------------------------------------------------------------------------
def report_csv(bid, user):
    data = get_batch(bid, user)

    def _esc(v):
        s = str(v if v is not None else "")
        return '"' + s.replace('"', '""') + '"'
    lines = ["batch,file,status,verdict,confidence,survey,village,owner,"
             "warnings,duplicate_of,sa_screening_matches,imported_doc_id,error"]
    for it in data["items"]:
        f, v = it.get("fields") or {}, it.get("validation") or {}
        scr = it.get("screening") or {}
        lines.append(",".join(_esc(x) for x in [
            data.get("batch_no"), it.get("filename"), it.get("status"),
            v.get("verdict") or "", it.get("mean_conf") or "",
            _field_value(f, "survey_number"), _field_value(f, "village"),
            _field_value(f, "owner_name"), len(v.get("issues") or []),
            v.get("duplicate_of") or "", scr.get("count") if scr else "",
            it.get("imported_doc_id") or "", (it.get("error") or "")[:120]]))
    return "\n".join(lines) + "\n"
