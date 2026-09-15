"""AI OCR Rescue Helper (100% offline — rule-based "AI", no external calls).

When the first OCR pass cannot read or extract a document, this module
tries to save it in two stages:

  STAGE 1 — adaptive OCR retry
      extra language-hint passes (the detected script, the user's hint,
      Hindi, English) and image-enhancement variants (grayscale +
      auto-contrast, 2x upscale + sharpen). The BEST pass wins.

  STAGE 2 — field recovery & merge
      every pass is field-extracted; the fields are merged, preferring
      values that are CORROBORATED by more passes, then higher OCR
      confidence. Learned corrections are re-applied.

  If the document is STILL unreadable, a human-readable DIAGNOSIS is
  produced and the caller routes it to a Verification Officer (the one
  with the least load).
"""
import io
import os

KEY_FIELDS = ("owner_name", "survey_number", "khasra_number", "village", "area")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")

# pass budget: keep uploads fast — at most this many EXTRA OCR passes
MAX_EXTRA_PASSES = 4


# --------------------------------------------------------------------------
# quality assessment
# --------------------------------------------------------------------------
def _filled(fields):
    n = 0
    for v in fields.values():
        if isinstance(v, dict) and str(v.get("value", "")).strip():
            n += 1
    return n


def _key_filled(fields):
    return sum(1 for k in KEY_FIELDS if str((fields.get(k) or {}).get("value", "") or "").strip())


def assess(ocr_result, fields):
    """Score an OCR pass + extraction. Returns a dict the UI can show."""
    words = 0
    for p in (ocr_result.get("pages") or []):
        words += len(p.get("words") or [])
    if not words:
        words = len((ocr_result.get("full_text") or "").split())
    mean_conf = float(ocr_result.get("mean_conf") or 0)
    total = _filled(fields)
    key = _key_filled(fields)
    owner = str((fields.get("owner_name") or {}).get("value", "") or "").strip()
    survey = str((fields.get("survey_number") or {}).get("value", "") or "").strip()

    diag = []
    # Language-pack hint (set by the OCR engine when the quick pass read
    # nothing and common Indic packs are missing from THIS machine):
    # this is the most likely cause of "OCR is broken" — show it FIRST.
    pack_hint = ocr_result.get("pack_hint") or ""
    if not pack_hint:
        for p in (ocr_result.get("pages") or []):
            if p.get("pack_hint"):
                pack_hint = p["pack_hint"]
                break
    if pack_hint:
        # The OCR engine only sets this when the page has visible ink but
        # produced almost no real words AND common Indic packs are missing
        # — show it FIRST: it is the most likely cause and the most
        # actionable one.
        diag.insert(0, pack_hint)
    if words == 0:
        diag.append("No text detected — the scan may be blank, too dark, or the wrong page was scanned.")
    elif words < 10:
        diag.append("Very little text detected (%d words) — the scan may be cropped, too small, or very low contrast." % words)
    if words >= 10 and key == 0:
        diag.append("Text was found, but no land-record labels matched — possibly handwritten, an unknown layout, or a script the OCR engine can't read well.")
    if key > 0 and key < 3 and mean_conf < 55:
        diag.append("OCR confidence is low (%.0f%%) — the image may be blurry; some fields were left empty." % mean_conf)
    missing = [k for k in KEY_FIELDS if not str((fields.get(k) or {}).get("value", "") or "").strip()]
    if words >= 10 and missing:
        diag.append("Missing key fields: %s." % ", ".join(missing))
    if not diag:
        diag.append("Extraction looks incomplete, but OCR itself ran.")

    ok = (key >= 3) or (words >= 20 and mean_conf >= 60 and total >= 4)
    # Text was found but the OCR is deeply uncertain (<35% confidence):
    # any "fields" are almost certainly hallucinations (pure noise, extreme
    # blur) — treat it as unreadable so a human verification officer gets it.
    garbage = (words >= 5 and mean_conf > 0 and mean_conf < 35)
    unreadable = (key == 0) or (words < 5) or (not owner and not survey and key <= 1) or garbage
    return {"words": words, "mean_conf": round(mean_conf, 1),
            "fields_filled": total, "key_fields_filled": key,
            "missing_key": missing, "ok": ok, "unreadable": unreadable,
            "diagnosis": diag}


# --------------------------------------------------------------------------
# image enhancement variants (images only — PDFs get hint-only retries)
# --------------------------------------------------------------------------
def enhance_variants(data, filename):
    """Return [(label, new_bytes)] enhanced variants, best-effort.

    PIL-only (no OpenCV) — the web-server process stays light, which is
    what keeps total RAM under 512MB hosts such as Render's free tier."""
    import io
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in _IMAGE_EXTS:
        return []
    try:
        from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps
    except Exception:  # noqa: BLE001
        return []
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
    except Exception:  # noqa: BLE001
        return []
    if max(img.size) < 40:
        return []
    fmt = (ext.lstrip(".") or "png").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    out = []
    try:
        # variant 1: grayscale + auto-contrast (helps dark/flat scans)
        v1 = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        buf = io.BytesIO()
        v1.save(buf, format=fmt)
        out.append(("grayscale + auto-contrast", buf.getvalue()))
    except Exception:  # noqa: BLE001
        pass
    try:
        # variant 2: 2x upscale + grayscale + contrast + light sharpen
        big = img.resize((img.width * 2, img.height * 2), Image.BICUBIC)
        v2 = ImageOps.autocontrast(big.convert("L"), cutoff=1)
        v2 = ImageEnhance.Sharpness(v2).enhance(2.0)
        buf = io.BytesIO()
        v2.save(buf, format=fmt)
        out.append(("2x upscale + sharpen", buf.getvalue()))
    except Exception:  # noqa: BLE001
        pass
    return out


# --------------------------------------------------------------------------
# the rescue itself
# --------------------------------------------------------------------------
def _pass_score(ocr, fields):
    a = assess(ocr, fields)
    return (a["key_fields_filled"], a["fields_filled"],
            a["mean_conf"], a["words"])


def rescue(data, filename, langs_hint, first_ocr, first_fields,
           run_ocr, extract_fields, apply_learned):
    """Try to rescue a poorly-read document.

    `run_ocr(bytes, name, langs)`  — the worker OCR entry point
    `extract_fields(ocr)`          — the NLP field extractor
    `apply_learned(fields)`        — learned-correction applier

    Returns None when nothing extra is possible, otherwise a dict:
      {rescued, before, after, passes, tried, diagnosis, ocr, fields}
    """
    before = assess(first_ocr, first_fields)
    tried = []

    # candidate extra language hints (max 2 beyond the first pass)
    hints = []
    for h in list(langs_hint or []) + list(first_ocr.get("detected_scripts") or []) + ["hin", "eng"]:
        if h and h not in hints:
            hints.append(h)
    hints = hints[:2]

    # image variants (images only)
    variants = enhance_variants(data, filename)

    # build the pass plan: (bytes, hint, label)
    plan = []
    for i, h in enumerate(hints, 1):
        plan.append((data, [h], "language pass %d (%s)" % (i, h)))
    if variants:
        for i, (vlabel, vdata) in enumerate(variants, 1):
            h = (hints[0] if hints else None)
            plan.append((vdata, h, "enhanced scan %d: %s%s" % (i, vlabel,
                       (" + " + hints[0] if h else ""))))
    plan = plan[:MAX_EXTRA_PASSES]
    if not plan:
        return None

    best_ocr, best_fields, best_score = first_ocr, first_fields, _pass_score(first_ocr, first_fields)
    all_fields = [first_fields]
    for blob, hint, label in plan:
        ocr = None
        for _attempt in (1, 2):  # one retry: a fresh worker can be mid-warmup
            try:
                ocr = run_ocr(blob, filename, langs=hint)
                break
            except Exception:  # noqa: BLE001 — a bad variant must not kill the upload
                ocr = None
        if ocr is None:
            tried.append(label + " — failed")
            continue
        try:
            fields = extract_fields(ocr)
        except Exception:  # noqa: BLE001
            fields = {}
        tried.append("%s — %d words, %d fields" % (label,
                     len((ocr.get("full_text") or "").split()), _filled(fields)))
        all_fields.append(fields)
        sc = _pass_score(ocr, fields)
        if sc > best_score:
            best_ocr, best_fields, best_score = ocr, fields, sc

    # ---- STAGE 2: merge — a field value seen in more passes wins,
    #      then higher confidence, then the longer (more complete) value
    merged = {}
    for k in {k for f in all_fields for k in f.keys()}:
        cands = []
        for f in all_fields:
            v = f.get(k)
            if isinstance(v, dict) and str(v.get("value", "")).strip():
                cands.append(v)
        if not cands:
            if k in best_fields:
                merged[k] = best_fields[k]
            continue
        counts = {}
        for v in cands:
            counts[str(v["value"]).strip()] = counts.get(str(v["value"]).strip(), 0) + 1
        def sortkey(v):
            return (counts[str(v["value"]).strip()],
                    float(v.get("confidence") or 0),
                    len(str(v.get("value", ""))))
        winner = max(cands, key=sortkey)
        winner = dict(winner)
        winner["corroborated"] = counts[str(winner["value"]).strip()] > 1
        merged[k] = winner
    try:
        merged = apply_learned(merged)
    except Exception:  # noqa: BLE001
        pass

    after = assess(best_ocr, merged)
    rescued = (after["key_fields_filled"] > before["key_fields_filled"]
               or after["fields_filled"] > before["fields_filled"]
               or after["mean_conf"] > before["mean_conf"] + 5)
    if rescued and merged:
        best_fields = merged
    return {"rescued": rescued,
            "before": before, "after": assess(best_ocr, best_fields),
            "passes": len(tried), "tried": tried,
            "diagnosis": assess(best_ocr, best_fields)["diagnosis"],
            "ocr": best_ocr, "fields": best_fields}
