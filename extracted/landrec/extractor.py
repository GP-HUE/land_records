"""Field extraction: map OCR text to structured land-record fields with confidence."""
import re

from . import common

# words that appear right after a label but are not the actual value
JUNK_WORDS = {
    "no", "no.", "number", "num", "नंबर", "नं.", "संख्या", "क्र.", "क्र",
    "सं.", "नाम", "का", "name", ":", "=", "-",
}

# fields whose value must contain a digit
NUMERIC_FIELDS = {"survey_number", "khasra_number", "khata_number", "plot_number",
                  "mutation_no", "registration_no", "khatauni_year"}

# characters that signal OCR failure / uncertainty
GARBAGE_CHARS = "?॥|@#$%^&*[]{}"

DEVA_RANGE = (0x0900, 0x097F)


def _has_devanagari(s: str) -> bool:
    return any(DEVA_RANGE[0] <= ord(c) <= DEVA_RANGE[1] for c in s)


def _clean_value(raw: str) -> str:
    raw = raw.replace("|", " ")
    # strip zero-width joiners/non-joiners that Indic OCR often emits
    for zw in ("\u200c", "\u200d", "\ufeff", "\u200b"):
        raw = raw.replace(zw, "")
    raw = re.sub(r"\s+", " ", raw.strip())
    return raw.strip(" .,-—–:;()[]{}").strip()


def _strip_junk(value: str) -> str:
    """Remove leading junk words like 'नंबर:' / 'number' from an extracted value."""
    tokens = value.split()
    while tokens and tokens[0].strip(".:-—= ") .lower() in JUNK_WORDS:
        tokens.pop(0)
    return " ".join(tokens).strip(" :.-—–")


def _label_regex(label: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(label) + r"(?!\w)", re.IGNORECASE)


def _lev(a: str, b: str) -> int:
    """Levenshtein edit distance between two strings."""
    a, b = a.lower(), b.lower()
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_token(tok: str, field_id: str) -> bool:
    """True if token is a small OCR-typo of any label's first word.
    Requires same length, same first char, and <=2 edits (1 for short words)."""
    # OCR of Indic scripts often leaves zero-width joiners stuck to tokens
    t = tok.strip(" :.,").lower()
    for zw in ("\u200c", "\u200d", "\ufeff", "\u200b"):
        t = t.replace(zw, "")
    if len(t) < 3:
        return False
    for lbl in common.LABEL_PATTERNS[field_id]:
        first = lbl.split()[0].lower()
        if len(first) < 3 or abs(len(first) - len(t)) > 1:
            continue
        # short labels: first char must match (prevents junk matches);
        # OCR often adds/drops one glyph and transposes neighbours,
        # so allow distance 2 for 4+ char words, 1 for 3-char words
        if len(t) <= 5 and first[0] != t[0]:
            continue
        maxd = 1 if len(t) <= 3 else 2
        if _lev(t, first) <= maxd:
            return True
    return False


def _next_label_pos(remainder: str, current_field: str) -> int:
    """Position of the NEXT known field label (any other field) inside the
    remainder text, or -1 if none.

    Real-world forms are dense: 'स्वामी रामस्वरूप शर्मा पिता शंकरलाल' is
    ONE line. Without this cut, the owner value swallows the father's name
    ('रामस्वरूप शर्मा पिता शंकरलाल') — a major source of 'wrong values'.
    """
    best = -1
    for fid, labels in common.LABEL_PATTERNS.items():
        if fid == current_field:
            continue
        for lbl in labels:
            m = _label_regex(common.normalize_numerals(lbl)).search(remainder)
            if m and (best == -1 or m.start() < best):
                best = m.start()
    return best


def _find_value(text: str, field_id: str, rtl: bool = False):
    """Find label, return (remainder, quality). quality reflects match confidence.

    rtl=True (Urdu/Arabic documents): after bidi restoration the VALUE
    precedes the LABEL on the line, so an empty remainder falls back to
    the text BEFORE the label (never used for LTR documents, where the
    preceding text is usually header junk)."""
    lines = [l for l in text.splitlines()]
    # ---- pass 1: exact label match ----
    best = (None, 0.0)
    for i, line in enumerate(lines):
        for lbl in common.LABEL_PATTERNS[field_id]:
            lbl_n = common.normalize_numerals(lbl)
            m = _label_regex(lbl_n).search(line)
            if not m:
                continue
            remainder = line[m.end():]
            if not remainder.strip() and rtl:
                remainder = line[:m.start()]
            if not remainder.strip():
                remainder = lines[i + 1] if i + 1 < len(lines) else ""
            remainder = _clean_value(remainder)
            if not remainder:
                continue
            # dense-line guard: cut the value where the next field's label
            # begins (fixes values swallowing the following field)
            pos = _next_label_pos(remainder, field_id)
            if pos > 0:
                remainder = _clean_value(remainder[:pos])
                if not remainder:
                    continue
            quality = 0.9  # same-line exact label match
            if quality > best[1]:
                best = (remainder, quality)
    if best[0]:
        return best

    # ---- pass 2: fuzzy label match (handles OCR typos like 'Matation' -> 'mutation') ----
    for i, line in enumerate(lines):
        tokens = line.split()
        for j, tok in enumerate(tokens):
            if _fuzzy_token(tok, field_id):
                remainder = _clean_value(" ".join(tokens[j + 1:]))
                if not remainder and rtl:
                    remainder = _clean_value(" ".join(tokens[:j]))
                if remainder:
                    return remainder, 0.72
    return None, 0.0


def extract_area(text: str):
    """Extract composite area value + primary unit.

    Captures the full "number unit [number unit]" span so subdivisions
    (cent/सेंटी/gts/गट/guns) are preserved, e.g. "2.45 एकड़ 34 सेंटी" or
    "3.20 Ac 12 Gts". `value`/`unit` reflect the leading number+unit;
    `display` preserves the whole composite value.
    """
    lines = [l for l in text.splitlines() if l.strip()]
    # A unit token: Latin word or an Indic word (Devanagari/Bengali/Tamil/
    # Telugu/Kannada/Malayalam/Gujarati/Gurmukhi/Odia ranges), optionally
    # followed by a 1-2 letter Latin qualifier (ft/m/ft./m.).
    # A unit token: optional "sq." prefix + a single word (Latin or any
    # Indic script). Kept as one token so the trailing " number unit"
    # subdivision still matches.
    UNIT = (r"(?:sq\.?\s*)?[A-Za-z\u0900-\u097F\u0980-\u09FF\u0A80-\u0AFF"
            r"\u0B80-\u0BFF\u0C00-\u0C7F\u0C80-\u0CFF\u0D00-\u0D7F"
            r"\u0A00-\u0A7F\u0B00-\u0B7F]+")
    PAT = re.compile(
        r"(\d+(?:\.\d+)?)\s*(" + UNIT + r")"
        r"(?:\s+(\d+(?:\.\d+)?)\s*" + UNIT + r")?",
        re.IGNORECASE)
    for i, line in enumerate(lines):
        exact = any(_label_regex(lbl).search(line) for lbl in common.LABEL_PATTERNS["area"])
        fuzzy = any(_fuzzy_token(t, "area") for t in line.split())
        if not (exact or fuzzy):
            continue
        for cand in [line, lines[i + 1] if i + 1 < len(lines) else ""]:
            c = common.normalize_numerals(cand)
            m = PAT.search(c)
            if m:
                value = float(m.group(1))
                unit_raw = (m.group(2) or "").lower().strip(" .")
                unit = "acre"
                for uname, synonyms in common.AREA_UNITS.items():
                    if any(s in unit_raw for s in synonyms):
                        unit = uname
                        break
                disp = re.sub(r"\s+", " ", m.group(0)).strip()
                return {"value": value, "unit": unit, "display": disp}
    return None


def extract_class(text: str, kind: str, field_id: str) -> str:
    """Find the land-class / ownership category.

    Scoped to the line carrying the field's LABEL — the old whole-text scan
    let the document header ("GOVERNMENT OF ...") masquerade as the
    ownership value on every English record.
    """
    lowered = text.lower()
    vocab = common.LAND_CLASSES if kind == "land" else common.OWNERSHIP_TYPES
    scope = lowered
    for l in [l for l in text.splitlines() if l.strip()]:
        if any(_label_regex(common.normalize_numerals(lbl)).search(l)
               for lbl in common.LABEL_PATTERNS[field_id]):
            scope = l.lower()
            break
    for canonical, synonyms in vocab:
        if any(_label_regex(s).search(scope) for s in synonyms):
            return canonical
    return None


def _value_word_conf(value: str, ocr_result: dict) -> float:
    """Mean tesseract word-confidence of the words overlapping the extracted value."""
    val_norm = common.normalize_numerals(value).lower().strip()
    if not val_norm:
        return 0.0
    confs = []
    for page in ocr_result["pages"]:
        for w, c in zip(page["words"], page["conf"]):
            wn = common.normalize_numerals(w).lower().strip()
            if not wn:
                continue
            if wn in val_norm or val_norm in wn:
                confs.append(c)
    if not confs:
        return 0.0
    return sum(confs) / len(confs) / 100.0


# Zero-width characters Indic OCR sticks inside/between words
ZW_CHARS = ("\u200c", "\u200d", "\ufeff", "\u200b")


def _norm_text(text: str) -> str:
    for zw in ZW_CHARS:
        text = text.replace(zw, "")
    return text


def _maybe_bidi_normalize(text: str) -> str:
    """Tesseract emits Urdu/Arabic text in VISUAL (display) order, but the
    label dictionary is in LOGICAL order — so the displayed word 'کلام'
    (مالک as rendered) would never match the label 'مالک'.

    Restore logical order per line WITHOUT any external library:
      * the visual form of an RTL word is EXACTLY the character-reverse of
        its logical form — so reverse each Arabic token's characters,
      * then reverse the token order (RTL lines display right-to-left),
      * digit/Latin runs are kept as-is.
    Applied only when the text is predominantly Arabic script.
    """
    arabic = sum(1 for ch in text if 0x0600 <= ord(ch) <= 0x06FF)
    total = sum(1 for ch in text if not ch.isspace())
    if total <= 0 or arabic / total <= 0.3:
        return text
    out_lines = []
    for ln in text.splitlines():
        toks = []
        for t in ln.split():
            toks.append(t if re.match(r"[0-9A-Za-z.]", t) else t[::-1])
        out_lines.append(" ".join(reversed(toks)))
    return "\n".join(out_lines)


# --------------------------------------------------------------------------
# NEW-kind document corner coordinates (v3.13)
# --------------------------------------------------------------------------
# A coordinate pair as printed on new-format records, e.g.
#   "23.17642 N, 80.01231 E"  "23.17642° N 80.01231° E"  "23.17642, 80.01231"
# The gap between the two numbers tolerates OCR letter junk ("23.35 WN, 77.35")
# — up to 10 non-digit characters; hemisphere hints come from N/S letters in
# that gap and an E/W letter right after the longitude.
_COORD_RE = re.compile(
    r"(?<![\d.])(-?\d{1,3}(?:\.\d+)?)"                    # latitude number
    r"([^\d\-]{0,10}?)"                                      # gap (dir letters/junk)
    r"(-?\d{1,3}(?:\.\d+)?)\s*(?:°|º|deg)?\s*([EW])?",      # longitude + dir
    re.IGNORECASE)


def parse_coordinate(value: str):
    """Parse a printed corner coordinate into (lat, lon). Returns None when
    the text does not hold a plausible lat/lon pair (bad range = None, which
    pushes the record into the review queue instead of a wrong map point)."""
    if not value:
        return None
    m = _COORD_RE.search(str(value))
    if not m:
        return None
    try:
        lat, lon = float(m.group(1)), float(m.group(3))
    except (TypeError, ValueError):
        return None
    gap = (m.group(2) or "").upper()
    ns = re.findall(r"[NS]", gap)            # last hemisphere letter wins
    if ns and ns[-1] == "S" and lat > 0:
        lat = -lat
    ew = (m.group(4) or "").upper()          # only a letter AFTER lon counts —
    if ew == "W" and lon > 0:                # stray junk in the gap (e.g. the W
        lon = -lon                           # in "23.35 WN,") must not flip it
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (round(lat, 7), round(lon, 7))


def ring_area_m2(ring) -> float:
    """Area of a [[lat, lon], ...] ring in m² — equirectangular (planar)
    approximation, identical math to the map's mapRingAreaM2().  Used to
    cross-check printed coordinates against the recorded area."""
    import math
    if not ring or len(ring) < 3:
        return 0.0
    lat0 = math.radians(ring[0][0])
    mlat = 111320.0
    mlon = 111320.0 * math.cos(lat0)
    a2 = 0.0
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        a2 += (a[0] * mlat) * (b[1] * mlon) - (b[0] * mlat) * (a[1] * mlon)
    return abs(a2) / 2.0


_COORD_LABEL_WORDS = (r"coordinate|coordinates|coord|corner|point|gps|"
                      r"निर्देशांक|कोऑर्डिनेट|कोआर्डिनेट")
_COORD_IDX_RE = re.compile(
    r"(" + _COORD_LABEL_WORDS + r")\s*[-:]?\s*([1-5])\b\s*[:\-]?\s*(.*)$",
    re.I)
_COORD_NOIDX_RE = re.compile(
    r"(" + _COORD_LABEL_WORDS + r")\s*[:\-]?\s*(.*)$", re.I)
_COORD_LETTER_RE = re.compile(
    r"^(\s*[\|>\-\u00b7*]*\s*)(" + _COORD_LABEL_WORDS +
    r")(\s*[-:]?\s*)([A-Za-z])(?=[\s:])(.*)$", re.I)
# OCR reads the corner NUMBER as a letter: 5->S, 0->O, 1->I/l, 8->B, 6->G, 2->Z
_OCR_DIGIT_FIX = {"s": "5", "o": "0", "i": "1", "l": "1", "b": "8", "g": "6",
                  "z": "2"}
_COORD_DAMAGE_RE = re.compile(
    r"torn|illegib|anpath|अनपठ|smudg|unclear|unread|missing|damag|faded"
    r"|^na\b|----", re.I)


def _repair_corner_digits(text: str) -> str:
    """Fix corner-number OCR (e.g. 'Coordinate S: 23.17 N' -> 'Coordinate 5:')
    so the pair still binds to its own slot."""
    out = []
    for line in text.splitlines():
        m = _COORD_LETTER_RE.match(line)
        if m and m.group(4).lower() in _OCR_DIGIT_FIX                 and re.search(r"\d", m.group(5) or ""):
            line = (m.group(1) + m.group(2) + m.group(3)
                    + _OCR_DIGIT_FIX[m.group(4).lower()] + m.group(5))
        out.append(line)
    return "\n".join(out)


def _looks_like_coord_value(v: str) -> bool:
    """True when a label remainder plausibly IS a printed corner: a decimal-
    degree number, or a legibility/tear marker an officer should correct.
    Anything else (section headers like '... (GPS Survey)', page junk) is
    not a corner — so absent corner slots stay absent."""
    vv = (v or "").strip()
    if not vv:
        return False
    if re.search(r"\d{1,3}\.\d", vv):
        return True
    return bool(_COORD_DAMAGE_RE.search(vv))


def _coord_clean(value: str) -> str:
    value = _clean_value(_strip_junk(value or ""))
    # a coordinate pair is short — never let it swallow header junk
    value = re.split(r"\s{2,}", value)[0][:60]
    if len(value.split()) > 8:
        value = " ".join(value.split()[:8])
    return value


def extract_coordinates(ocr_result: dict) -> dict:
    """Read the printed corner coordinates (NEW document kind only) — a plot
    may print 3 corners (triangle), 4 or 5, so coordinate_1..coordinate_5 are
    all read and the SHAPE comes from however many the paper actually has.

    Binder strategy specific to corner lines (labels are well-known):
      1. repair OCR'd corner digits (5->S, 0->O, 1->I/l, 8->B, 6->G, 2->Z)
      2. 'Coordinate 3: ...' style indexed lines fill their own slot (the
         better/parseable value wins if a line repeats)
      3. a corner line whose NUMBER was dropped by OCR (e.g. 'निर्देशांक :
         23.20 N, 77.08 E') fills the first empty slot, in printed order
      4. a value that does not parse as a lat/lon pair is kept (officer can
         correct it) but its confidence is capped, so the record lands in
         the review queue
    """
    raw_text = _norm_text(ocr_result["full_text"])
    text = _repair_corner_digits(_maybe_bidi_normalize(raw_text))
    mean_ocr_conf = ocr_result["mean_conf"] / 100.0

    slots = {}      # corner number -> (value, quality)
    homeless = []   # coordinate-ish line values without a corner number
    for raw in text.splitlines():
        line = raw.strip().strip("|*\u00b7> ")
        if not line:
            continue
        m = _COORD_IDX_RE.search(line)
        if m:
            idx = int(m.group(2))
            value = _coord_clean(m.group(3))
            if not value:
                continue
            if idx not in slots or (parse_coordinate(slots[idx][0]) is None
                                    and parse_coordinate(value) is not None):
                slots[idx] = (value, 0.9)
            continue
        m2 = _COORD_NOIDX_RE.search(line)
        if m2:
            rest = _coord_clean(m2.group(2))
            if rest and _looks_like_coord_value(rest):
                homeless.append(rest)
    for idx in range(1, 6):
        if idx in slots or not homeless:
            continue
        slots[idx] = (homeless.pop(0), 0.8)

    fields = {}
    for idx, (value, quality) in sorted(slots.items()):
        fields["coordinate_%d" % idx] = {"value": value, "quality": quality}

    for fid, f in fields.items():
        wc = _value_word_conf(f["value"], ocr_result)
        base = f.get("quality", 0.5)
        conf = 0.6 * wc + 0.4 * base
        if wc == 0.0:
            conf = 0.5 * mean_ocr_conf + 0.5 * base
        # unparseable coordinates MUST be eyeballed by an officer
        if parse_coordinate(f["value"]) is None:
            conf = min(conf, 0.5)
        f["confidence"] = round(min(0.99, conf), 3)
    return fields


def extract_fields(ocr_result: dict) -> dict:
    raw_text = _norm_text(ocr_result["full_text"])
    text = _maybe_bidi_normalize(raw_text)
    rtl = text != raw_text  # bidi restoration actually changed the text
    mean_ocr_conf = ocr_result["mean_conf"] / 100.0
    fields = {}

    for fid, _display, _labels in common.FIELD_DEFS:
        remainder, quality = _find_value(text, fid, rtl=rtl)
        if not remainder:
            continue
        value = _strip_junk(remainder)
        if not value:
            continue

        if fid in NUMERIC_FIELDS:
            m = re.search(r"[^\s]*\d[^\s]*", value)
            if not m:
                fields[fid] = {"value": value, "quality": quality * 0.4, "digits": ""}
                continue
            token = common.normalize_numerals(m.group(0)).strip(" :.,;—-")
            fields[fid] = {"value": token, "quality": quality,
                           "digits": common.digits_only(token)}
        else:
            value = re.split(r"\s{2,}", value)[0]
            # hard cap: a land-record value is never a paragraph
            if len(value.split()) > 10:
                value = " ".join(value.split()[:10])
            fields[fid] = {"value": value, "quality": quality}

    # year fallback: the year LABEL is often mangled by OCR, but the fiscal
    # year itself (2024-25 / 202425) survives almost always
    if "khatauni_year" not in fields:
        m = re.search(r"\b(20\d{2})[-\u2013/ ]?(\d{2})\b", text)
        if m:
            fields["khatauni_year"] = {"value": m.group(1) + "-" + m.group(2),
                                       "quality": 0.7}
        else:
            m = re.search(r"\b(20\d{2})\b", text)
            if m:
                # bare year (OCR dropped the separator) — low confidence,
                # it will be flagged for the officer
                fields["khatauni_year"] = {"value": m.group(1), "quality": 0.6}

    area = extract_area(text)
    if area:
        fields["area"] = {"value": area["display"], "quality": 0.8,
                          "num_value": area["value"], "unit": area["unit"]}

    lc = extract_class(text, "land", "land_class")
    if lc:
        fields["land_class"] = {"value": lc, "quality": 0.8}
    ot = extract_class(text, "ownership", "ownership_type")
    if ot:
        fields["ownership_type"] = {"value": ot, "quality": 0.8}

    # Per-field confidence = word-OCR-conf (60%) + match quality (40%)
    for fid, f in fields.items():
        wc = _value_word_conf(f["value"], ocr_result)
        base = f.get("quality", 0.5)
        conf = 0.6 * wc + 0.4 * base
        # garbage characters => strong uncertainty
        if any(g in f["value"] for g in GARBAGE_CHARS):
            conf = min(conf, 0.55)
        # fall back to document mean when no word overlap found
        if wc == 0.0:
            conf = 0.5 * mean_ocr_conf + 0.5 * base
        f["confidence"] = round(min(0.99, conf), 3)

    return fields
