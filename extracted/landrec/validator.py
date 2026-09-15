"""Business-rule validation, duplicate detection, confidence thresholds."""
import re

from . import common

# Mandatory fields for a valid land record
REQUIRED_FIELDS = ["owner_name", "survey_number", "village", "district"]

# Confidence below which a field is flagged for manual verification
VERIFY_THRESHOLD = 0.75


DEVA_RANGE = (0x0900, 0x097F)
TEXT_FIELDS = ["owner_name", "father_name", "village", "tehsil", "district", "state"]


def _has_devanagari(s: str) -> bool:
    return any(DEVA_RANGE[0] <= ord(c) <= DEVA_RANGE[1] for c in s)


def validate(fields: dict, scripts: list = None) -> dict:
    """Apply business rules to extracted fields. Returns issues + verdict."""
    issues = []
    present = {fid: f for fid, f in fields.items() if f.get("value")}
    scripts = scripts or []

    # 0. Cross-language consistency: Latin-only value in a Devanagari document
    if "hin" in scripts:
        for fid in TEXT_FIELDS:
            v = present.get(fid, {}).get("value", "")
            if v and not _has_devanagari(v) and re.search(r"[A-Za-z]", v):
                issues.append({"field": fid, "severity": "review",
                               "msg": f"{common.FIELD_LABELS[fid]} '{v}' is Latin text in a "
                                      f"Devanagari document — possible OCR error"})

    # 1. Required fields
    for fid in REQUIRED_FIELDS:
        if fid not in present:
            issues.append({"field": fid, "severity": "error",
                           "msg": f"Missing required field: {common.FIELD_LABELS[fid]}"})

    # 2. Numeric checks
    for fid in ["survey_number", "khasra_number", "khata_number", "plot_number",
                "mutation_no", "registration_no"]:
        f = present.get(fid)
        if f and f.get("digits"):
            if len(f["digits"]) > 12:
                issues.append({"field": fid, "severity": "warning",
                               "msg": f"Unusually long {common.FIELD_LABELS[fid]}"})

    # 3. Area sanity
    a = present.get("area")
    if a:
        v = a.get("num_value")
        if v is not None and v <= 0:
            issues.append({"field": "area", "severity": "error",
                           "msg": "Area must be positive"})
        elif v is not None and v > 10000:
            issues.append({"field": "area", "severity": "warning",
                           "msg": "Area value looks abnormally large"})

    # 4. Year sanity (use first 4 digits, so '2023-24' -> 2023)
    y = present.get("khatauni_year")
    if y and y.get("digits"):
        first4 = y["digits"][:4]
        yr = int(first4) if first4.isdigit() and len(first4) == 4 else None
        if yr is not None and (yr < 1900 or yr > 2100):
            issues.append({"field": "khatauni_year", "severity": "warning",
                           "msg": "Year outside plausible range"})

    # 5. Confidence-based verification flags
    low_conf = [fid for fid, f in fields.items() if f.get("confidence", 0) < VERIFY_THRESHOLD]
    for fid in low_conf:
        issues.append({"field": fid, "severity": "review",
                       "msg": f"Low confidence on {common.FIELD_LABELS[fid]}"})

    has_error = any(i["severity"] == "error" for i in issues)
    has_review = bool(low_conf) or any(i["severity"] == "review" for i in issues)
    # collect fields flagged by review-severity issues too
    for i in issues:
        if i["severity"] == "review" and i.get("field") and i["field"] not in low_conf:
            low_conf.append(i["field"])
    verdict = "rejected" if has_error else ("review" if has_review else "valid")
    return {"issues": issues, "verdict": verdict, "low_confidence_fields": low_conf}


def duplicate_key(fields: dict) -> str:
    """Build a dedup key: owner + survey + village (normalized)."""
    owner = common.normalize_numerals(fields.get("owner_name", {}).get("value", ""))
    survey = common.digits_only(fields.get("survey_number", {}).get("value", ""))
    village = common.normalize_numerals(fields.get("village", {}).get("value", ""))
    return "|".join([owner.strip().lower(), survey, village.strip().lower()])
