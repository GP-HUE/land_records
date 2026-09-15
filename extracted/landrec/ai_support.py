"""Offline AI decision support and explanation engine.

Replicates the DILRMP reference portal's "AI" feature set:
  * AI decision support on document review (recommendation, summary,
    explanation, flags)            -> GET /api/documents/{id}
  * AI differences explanation      -> POST /api/records/compare
  * AI consistency explanation      -> POST /api/records/consistency
  * AI accuracy metrics             -> GET /api/dashboard

100% rule-based and local: no internet, no API keys, no document data
ever leaves the machine. The "AI" is a deterministic analysis layer over
the validation results, OCR confidence scores, learned corrections and
extracted fields - fast, explainable and audit-safe for government
records.
"""
from . import common

# OCR confidence (0-1 fraction in field dicts) below this needs human eyes.
LOW_CONF_FRACTION = 0.70
# Document mean_conf is 0-100 in the documents table.
CAUTION_MEAN_CONF = 50.0
REVIEW_MEAN_CONF = 85.0


def _label(fid: str) -> str:
    return common.FIELD_LABELS.get(fid, fid.replace("_", " ").title())


# --------------------------------------------------------------------------
# AI decision support (review screen)
# --------------------------------------------------------------------------
def decision_support(fields: dict, validation: dict, mean_conf: float,
                     status: str = "", doc_type: str = "") -> dict:
    """Analyse one document and produce a recommendation for the reviewer.

    recommendation: ROUTINE_CLEAR | REVIEW_REQUIRED | CAUTION_DISCREPANCY
    """
    fields = fields or {}
    validation = validation or {}
    issues = validation.get("issues", []) or []
    low_conf = validation.get("low_confidence_fields", []) or []
    mean_conf = mean_conf or 0.0

    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]
    reviews = [i for i in issues if i.get("severity") == "review"]

    dup_msg = next((i.get("msg", "") for i in issues if i.get("field") == "duplicate"), "")
    missing = [i for i in errors if i.get("msg", "").startswith("Missing required field")]
    auto_corrected = sorted(fid for fid, f in fields.items()
                            if isinstance(f, dict) and f.get("auto_corrected"))

    critical = bool(errors or dup_msg) or mean_conf < CAUTION_MEAN_CONF
    needs_review = bool(warnings or reviews or low_conf) or mean_conf < REVIEW_MEAN_CONF

    if critical:
        rec = "CAUTION_DISCREPANCY"
    elif needs_review:
        rec = "REVIEW_REQUIRED"
    else:
        rec = "ROUTINE_CLEAR"

    flags = []
    for i in errors:
        if i.get("field") == "duplicate":
            continue  # handled separately below with a cleaner label
        flags.append(i.get("msg", ""))
    for i in warnings:
        if i.get("field") == "duplicate":
            continue
        flags.append(i.get("msg", ""))
    for i in reviews:
        flags.append(i.get("msg", ""))
    for fid in low_conf:
        f = fields.get(fid) or {}
        conf_pct = round((f.get("confidence") or 0) * 100)
        flags.append("Low confidence: %s (%d%%)" % (_label(fid), conf_pct))
    if dup_msg:
        flags.append(dup_msg)
    if auto_corrected:
        flags.append("Auto-corrected by learning loop: " +
                     ", ".join(_label(f) for f in auto_corrected[:4]))

    n_fields = len([f for f in fields.values()
                    if isinstance(f, dict) and f.get("value")])
    conf = round(mean_conf)

    if rec == "ROUTINE_CLEAR":
        summary = ("All automated checks passed — %d field(s) extracted with %d%% "
                   "average OCR confidence. Suitable for routine approval." % (n_fields, conf))
        explanation = ("The extracted record is internally consistent: required fields are "
                       "present, no business-rule violations were found, and OCR confidence "
                       "is healthy across the board. A standard spot-check of the scan "
                       "against the key fields (owner, survey, area) is sufficient before "
                       "verifying.")
        if auto_corrected:
            explanation += (" Note: %d field(s) were auto-corrected using the "
                            "learned-corrections history — worth a glance." % len(auto_corrected))
    elif rec == "REVIEW_REQUIRED":
        summary = ("Mostly complete, but %d item(s) need manual confirmation before "
                   "approval." % len(flags))
        parts = []
        if low_conf:
            parts.append("low OCR confidence on " + ", ".join(_label(f) for f in low_conf[:4]))
        if warnings:
            parts.append("%d validation warning(s)" % len(warnings))
        if reviews and not low_conf:
            parts.append("review notes: " + "; ".join(i.get("msg", "") for i in reviews[:3]))
        explanation = ("The record is usable but not clean: " +
                       ("; ".join(parts) or "minor flags") +
                       ". Recommend the verifier opens each flagged field, compares it "
                       "with the scan, and corrects it if needed.")
    else:
        summary = "Possible data conflicts detected — do not approve without manual review."
        parts = []
        if missing:
            parts.append("missing required field(s): " + ", ".join(
                i.get("msg", "").replace("Missing required field: ", "") for i in missing))
        for i in errors:
            if not i.get("msg", "").startswith("Missing required field"):
                parts.append(i.get("msg", ""))
        if dup_msg:
            parts.append("this document looks like a duplicate of an already stored record")
        if mean_conf < CAUTION_MEAN_CONF:
            parts.append("very low OCR confidence (%d%%)" % conf)
        explanation = "Automatic analysis found: " + ("; ".join(parts) or "unexplained discrepancies") + ". " + (
            "If this is a re-upload of an already verified record, the duplicate flag can be "
            "dismissed after confirming; otherwise re-extract or enter the key fields manually."
            if dup_msg else
            "Compare against the original scan before making a decision.")

    return {
        "recommendation": rec,
        "summary": summary,
        "explanation": explanation,
        "flags": flags,
        "mean_conf": conf,
        "fields_extracted": n_fields,
        "status": status,
    }


# --------------------------------------------------------------------------
# AI differences explanation (version comparison)
# --------------------------------------------------------------------------
def compare_explanation(a: dict, b: dict, rows: list, highlights: list) -> str:
    """Plain-language explanation of what changed between two versions."""
    rows = rows or []
    changed = [r for r in rows if r.get("changed")]
    unchanged_n = len(rows) - len(changed)
    by_field = {r["field"]: r for r in changed}

    if not changed:
        return ("No differences detected between Document A (%s) and Document B (%s) — "
                "the two versions appear identical across all %d compared fields."
                % (a.get("filename", "A"), b.get("filename", "B"), len(rows)))

    sentences = []

    def emit(text: str):
        sentences.append("%d) %s" % (len(sentences) + 1, text))

    cf = by_field.get("owner_name")
    if cf:
        emit("Ownership changed from '%s' to '%s' — this indicates a transfer of the "
             "land (sale, mutation or inheritance) between the two versions."
             % (cf.get("a"), cf.get("b")))
    af = by_field.get("area")
    if af:
        emit("Area changed from '%s' to '%s' — consistent with a partition, "
             "consolidation or a correction of the plot extent." % (af.get("a"), af.get("b")))
    for fid in ("survey_number", "khasra_number", "khata_number", "plot_number"):
        r = by_field.get(fid)
        if r:
            emit("%s changed from '%s' to '%s' — the plot numbering differs, which may "
                 "be a renumbering, a correction, or a different parcel entirely."
                 % (_label(fid), r.get("a"), r.get("b")))
    for fid in ("village", "tehsil", "district", "state"):
        r = by_field.get(fid)
        if r:
            emit("Location field '%s' changed from '%s' to '%s'."
                 % (_label(fid), r.get("a"), r.get("b")))
    y = by_field.get("khatauni_year")
    if y:
        emit("Record year changed from '%s' to '%s' — the two versions appear to be "
             "from different record years." % (y.get("a"), y.get("b")))

    covered = {"owner_name", "area", "survey_number", "khasra_number", "khata_number",
               "plot_number", "village", "tehsil", "district", "state", "khatauni_year"}
    rest = [r for r in changed if r["field"] not in covered]
    if rest:
        detail = ", ".join("%s: '%s' to '%s'" % (_label(r["field"]), r.get("a"), r.get("b"))
                           for r in rest[:4])
        emit("Other changed field(s): %s." % detail)

    return ("Compared %d fields between Document A (%s) and Document B (%s): %d changed, "
            "%d unchanged. Material differences: %s"
            % (len(rows), a.get("filename", "A"), b.get("filename", "B"),
               len(changed), unchanged_n, " ".join(sentences)))


# --------------------------------------------------------------------------
# AI consistency explanation (cross-document check)
# --------------------------------------------------------------------------
def consistency_explanation(items: list, flags: list) -> str:
    """Plain-language summary of a cross-document consistency check."""
    items = items or []
    flags = flags or []
    n = len(items)

    errors = [f for f in flags if f.get("level") == "error"]
    warnings = [f for f in flags if f.get("level") == "warning"]
    infos = [f for f in flags if f.get("level") == "info"]
    oks = [f for f in flags if f.get("level") == "ok"]

    owners = sorted({it.get("owner", "") for it in items if it.get("owner")})
    surveys = sorted({it.get("survey", "") for it in items if it.get("survey")})
    villages = sorted({it.get("village", "") for it in items if it.get("village")})

    head = ("Compared %d document(s) (owners: %s; survey nos.: %s%s)."
            % (n, ", ".join(owners[:4]) or "n/a", ", ".join(surveys[:4]) or "n/a",
               ("; villages: " + ", ".join(villages[:3])) if villages else ""))

    parts = []
    for f in errors:
        parts.append("CRITICAL: " + f.get("msg", ""))
    for f in warnings:
        parts.append("Warning: " + f.get("msg", ""))
    for f in infos:
        parts.append("Note: " + f.get("msg", ""))
    if not errors and not warnings:
        parts.append("No conflicts or duplicates were found — the selected records are "
                     "factually consistent with each other.")
    elif oks:
        for f in oks[:2]:
            parts.append(f.get("msg", ""))

    if errors:
        verdict = ("Recommendation: verify the chain of title for the conflicting "
                   "documents before approving any of them.")
    elif warnings:
        verdict = ("Recommendation: resolve the flagged items — or confirm they are "
                   "expected (e.g. a partition across districts) — before approval.")
    else:
        verdict = ("Recommendation: these records can proceed to verification with a "
                   "normal spot-check.")

    return head + " " + " ".join(parts) + " " + verdict
