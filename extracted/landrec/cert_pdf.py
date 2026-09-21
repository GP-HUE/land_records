"""Certified-copy PDF generation (pure-Python, offline).

Uses PyMuPDF (already a dependency) for layout and the `qrcode` package
for the QR code. An Indic TTF font (Nirmala on Windows, Lohit/Noto on
Linux, DevanagariMT on macOS) is auto-located so Hindi values render
properly; if no such font is found the PDF falls back to Latin text.
"""
import glob
import os
import time

import pymupdf
import qrcode

FONT_CANDIDATES = [
    # Windows (NirmalaUI/Mangal ship with Windows 10/11)
    r"C:\Windows\Fonts\NirmalaUI.ttc", r"C:\Windows\Fonts\nirmalaui.ttf",
    r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\Mangal.ttf",
    # macOS
    "/System/Library/Fonts/DevanagariMT.ttf",
    "/System/Library/Fonts/Supplemental/Kohinoor Devanagari.ttf",
    # Linux (common package names)
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/kohinoor/KohinoorDevanagari.ttf",
]


_fontfile = None
_font_checked = False


def _find_indic_fonts():
    """All candidate Indic font files that exist, in priority order."""
    hits = [p for p in FONT_CANDIDATES if os.path.exists(p)]
    for pat in ("/usr/share/fonts/**/*Deva*.ttf", "/usr/share/fonts/**/*deva*.ttf"):
        for h in glob.glob(pat, recursive=True):
            if h not in hits:
                hits.append(h)
    return hits


def _indic_font():
    """pymupdf.Font for Indic text, or None (Latin-only fallback).
    Tries each existing candidate (a .ttc collection may fail to load —
    in that case the next .ttf is used)."""
    global _fontfile, _font_checked
    if not _font_checked:
        _font_checked = True
        for p in _find_indic_fonts():
            try:
                f = pymupdf.Font(fontfile=p)
                _fontfile = p
                return f
            except Exception:
                continue
        _fontfile = None
    if _fontfile:
        try:
            return pymupdf.Font(fontfile=_fontfile)
        except Exception:
            return None
    return None


def _has_indic(s):
    return any(ord(ch) > 0x0900 and ord(ch) < 0x0E80 for ch in str(s or ""))


def _sanitize(s, have_indic):
    s = str(s if s not in (None, "") else "—")
    if not have_indic:
        # no Indic font: keep ASCII legible, replace Indic runs with a marker
        out = []
        for ch in s:
            out.append(ch if ord(ch) < 0x2000 or ord(ch) > 0x0E7F else "")
        s = "".join(out).strip() or "—"
    return s


def render_certified_pdf(doc: dict, fields: dict, verify_url: str) -> bytes:
    """A4 certified copy: official header, field table, verification
    block, certification hash and a QR code pointing at the verify page."""
    cert_font = _indic_font()
    helv = pymupdf.Font("helv")
    helv_b = pymupdf.Font("hebo")

    docf = pymupdf.open()
    page = docf.new_page(width=595, height=842)  # A4 portrait
    W = 595
    M = 52  # margin

    def line(y, text, size=10, bold=False, center=False, color=(0.06, 0.17, 0.29)):
        f = helv_b if bold else helv
        x = M if not center else (W - f.text_length(text, fontsize=size)) / 2
        tw = pymupdf.TextWriter(page.rect, color=color)
        tw.append((x, y), text, font=f, fontsize=size)
        tw.write_text(page)
        return y

    def indic_line(y, text, size=10, bold=False, center=False, color=(0.06, 0.17, 0.29)):
        f = cert_font
        if f is None:
            return line(y, _sanitize(text, False), size, bold, center, color)
        x = M if not center else (W - f.text_length(text, fontsize=size)) / 2
        tw = pymupdf.TextWriter(page.rect, color=color)
        tw.append((x, y), text, font=f, fontsize=size)
        tw.write_text(page)
        return y

    def rule(y, w=W - 2 * M, x0=M):
        page.draw_line((x0, y), (x0 + w, y), color=(0.2, 0.3, 0.45), width=1.1)

    # ---------------- header ----------------
    y = 74
    line(y, "INTELLIGENT LAND RECORD DIGITIZATION & VALIDATION SYSTEM", 13, True, True)
    y = indic_line(y + 20, "भूमि अभिलेख डिजिटाइज़ेशन एवं प्रमाणीकरण प्रणाली", 11, True, True)
    y = line(y + 18, "CERTIFIED COPY  -  प्रमाणित प्रतिलिपि  (Certified Land Record)", 11, True, True,
             color=(0.25, 0.1, 0.05))
    rule(y + 8)
    y += 30

    # ---------------- field table ----------------
    g = lambda k: str((fields.get(k) or {}).get("value", "") or "") if isinstance(fields.get(k), dict) else ""
    rowdefs = [
        ("Owner Name  (भूस्वामी)", g("owner_name")),
        ("Father / Guardian Name", g("father_name")),
        ("Survey Number", g("survey_number")),
        ("Khasra Number", g("khasra_number")),
        ("Khata Number", g("khata_number")),
        ("Plot Number", g("plot_number")),
        ("Extent / Area", g("area")),
        ("Village", g("village")),
        ("Tehsil / Taluka", g("tehsil")),
        ("District", g("district")),
        ("State", g("state")),
        ("Land Class", g("land_class")),
        ("Ownership", g("ownership_type")),
        ("Record Year (Khatauni)", g("khatauni_year")),
        ("Document Type", doc.get("doc_type") or "land_record"),
    ]
    for label, val in rowdefs:
        if not val:
            continue
        line(y, label, 9, True, color=(0.30, 0.38, 0.48))
        vtext = _sanitize(val, cert_font is not None)
        if _has_indic(val) and cert_font is not None:
            indic_line(y + 14, vtext[:90], 10.5, True, False, color=(0.05, 0.05, 0.05))
        else:
            line(y + 14, vtext[:90], 10.5, True, color=(0.05, 0.05, 0.05))
        y += 25
        if y > 480:
            break

    # ---------------- verification block ----------------
    y = max(y + 12, 500)
    rule(y - 12, w=W - 2 * M)
    y = line(y, "VERIFICATION DETAILS  (प्रमाणीकरण विवरण)", 10, True)
    y += 20

    v_by = doc.get("cert_by") or "—"
    v_at = time.strftime("%d %b %Y %H:%M", time.localtime(doc.get("cert_at") or 0)) if doc.get("cert_at") else "—"
    cert_lines = [
        ("Record ID", doc.get("id", "—")),
        ("Source File", (doc.get("filename") or "—")[:70]),
        ("Record Status", (doc.get("status") or "—").upper()),
        ("Certified By", v_by),
        ("Certified On", v_at),
        ("Certification Hash (SHA-256)", doc.get("cert_hash") or "—"),
    ]
    for label, val in cert_lines:
        line(y, label, 8.5, True, color=(0.30, 0.38, 0.48))
        line(y + 12, str(val)[:78], 9.5, True, color=(0.05, 0.05, 0.05))
        y += 21

    # ---------------- signature ----------------
    y = max(y + 10, 700)
    sig_x = W - M - 210
    line(y, "Verified & Certified By", 9, True, color=(0.25, 0.32, 0.42))
    page.draw_line((sig_x - 10, y + 18), (sig_x + 180, y + 18), color=(0.3, 0.35, 0.45), width=0.8)
    line(y + 24, "Verification Officer / System", 8.5, False, color=(0.45, 0.5, 0.58))

    # ---------------- QR code ----------------
    qr = qrcode.QRCode(box_size=1, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(verify_url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    qsize = 118
    cell = qsize / n
    q0x, q0y = W - M - qsize, y - 34
    for r_i in range(n):
        for c_i in range(n):
            if matrix[r_i][c_i]:
                page.draw_rect(pymupdf.Rect(q0x + c_i * cell, q0y + r_i * cell,
                                             q0x + (c_i + 1) * cell, q0y + (r_i + 1) * cell),
                               color=(0, 0, 0), fill=(0, 0, 0))
    cap = "Scan to verify"
    capw = helv_b.text_length(cap, fontsize=8)
    tw = pymupdf.TextWriter(page.rect, color=(0.25, 0.32, 0.42))
    tw.append((q0x + (qsize - capw) / 2, q0y + qsize + 9), cap, font=helv_b, fontsize=8)
    tw.write_text(page)

    # ---------------- footer ----------------
    fy = 812
    rule(fy - 12)
    line(fy, "This certified copy was generated by the system on " +
         time.strftime("%d %b %Y %H:%M"), 7.5, False, color=(0.45, 0.5, 0.58))
    line(fy + 11, "Authenticity: scan the QR code or open  /verify/" + doc.get("id", "") +
         "  -  any alteration after certification is detectable via the hash above.",
         7.5, False, color=(0.45, 0.5, 0.58))

    out = pymupdf.open()
    out.insert_pdf(docf)
    data = out.tobytes(garbage=3, deflate=True)
    docf.close()
    out.close()
    return data


def render_encumbrance_pdf(doc: dict, risk: dict, years: int,
                           issued_by: str = "") -> bytes:
    """Encumbrance-Check Report (EC-style) for the land behind a record:
    land identity, the checked period, the verdict (free of encumbrances /
    list of encumbrances), any risk flags, and a QR carrying a content
    hash so the report itself is tamper-evident."""
    import hashlib
    cert_font = _indic_font()
    helv = pymupdf.Font("helv")
    helv_b = pymupdf.Font("hebo")

    fields = {}
    try:
        import json as _json
        fields = _json.loads(doc.get("extracted_json") or "{}")
    except Exception:  # noqa: BLE001
        fields = {}
    g = lambda k: str((fields.get(k) or {}).get("value", "") or "") if isinstance(fields.get(k), dict) else ""

    now = time.time()
    encs = risk.get("encumbrances") or []
    period_from = time.strftime("%d %b %Y", time.localtime(now - years * 365 * 86400))
    period_to = time.strftime("%d %b %Y", time.localtime(now))
    in_window = []
    for e in encs:
        md = str(e.get("mortgage_date") or "")
        if not md or md >= period_from[:6] or True:  # show ALL registered; window noted in text
            in_window.append(e)
    active = [e for e in encs if e.get("status") == "active"]
    verdict = risk.get("verdict") or "clear"

    docf = pymupdf.open()
    page = docf.new_page(width=595, height=842)
    W, M = 595, 52

    def line(y, text, size=10, bold=False, center=False, color=(0.06, 0.17, 0.29)):
        f = helv_b if bold else helv
        x = M if not center else (W - f.text_length(text, fontsize=size)) / 2
        tw = pymupdf.TextWriter(page.rect, color=color)
        tw.append((x, y), text, font=f, fontsize=size)
        tw.write_text(page)
        return y

    def rule(y, w=W - 2 * M, x0=M):
        page.draw_line((x0, y), (x0 + w, y), color=(0.2, 0.3, 0.45), width=1.1)

    # ---------------- header ----------------
    y = 70
    line(y, "INTELLIGENT LAND RECORD DIGITIZATION & VALIDATION SYSTEM", 13, True, True)
    y += 22
    line(y, "ENCUMBRANCE CHECK REPORT  -  %d-YEAR PERIOD  (EC-style)" % years, 12, True, True,
         color=(0.25, 0.1, 0.05))
    rule(y + 8)
    y += 26

    # ---------------- land identity ----------------
    line(y, "LAND IDENTITY", 10, True, color=(0.30, 0.38, 0.48))
    y += 16
    land_rows = [
        ("Survey Number", risk.get("survey") or "—"),
        ("Khasra Number", risk.get("khasra") or "—"),
        ("Village", risk.get("village") or "—"),
        ("Tehsil / District", (" / ".join([x for x in (g("tehsil"), g("district")) if x]) or "—")),
        ("Record Used", (doc.get("filename") or "—")[:70]),
        ("Period Checked", "%s  to  %s  (%d years)" % (period_from, period_to, years)),
    ]
    for label, val in land_rows:
        line(y, label, 9, True, color=(0.30, 0.38, 0.48))
        line(y + 13, str(val)[:95], 10.5, True, color=(0.05, 0.05, 0.05))
        y += 24

    # ---------------- verdict ----------------
    y += 6
    if not in_window:
        page.draw_rect(pymupdf.Rect(M, y, W - M, y + 40), color=(0.1, 0.5, 0.25), fill=(0.9, 0.97, 0.9))
        line(y + 16, "✔  FREE OF ENCUMBRANCES", 13, True, color=(0.05, 0.35, 0.15))
        line(y + 31, "No loan / mortgage / encumbrance is registered on this land in the system.",
             9.5, False, color=(0.1, 0.3, 0.15))
        y += 52
    else:
        line(y, "ENCUMBRANCES REGISTERED ON THIS LAND", 10, True)
        y += 16
        for e in in_window:
            st = (e.get("status") or "active").upper()
            col = (0.55, 0.1, 0.05) if st == "ACTIVE" else (0.3, 0.3, 0.35)
            line(y, "%-28s ₹%-12s %s" % (str(e.get("creditor") or "")[:28],
                                         "{:,.0f}".format(e["amount"]) if e.get("amount") else "—",
                                         ("[" + st + "]")), 10, True, color=col)
            y += 13
            line(y, "     mortgaged %s   |   ref. %s   |   settled %s"
                 % (e.get("mortgage_date") or "—", e.get("reference_no") or "—",
                    e.get("settlement_date") or ("NOT SETTLED" if st == "ACTIVE" else "—")),
                 9, False, color=col)
            y += 18
            if y > 470:
                break
        y += 4
        if active:
            line(y, "⚠  ACTIVE encumbrance(s) present — the land is NOT encumbrance-free "
                    "until the loan is released (settlement recorded).", 9.5, True,
                    color=(0.55, 0.1, 0.05))
            y += 16
        else:
            line(y, "All registered encumbrances are settled. Encumbrance-free as far as "
                    "this system's register shows.", 9.5, True, color=(0.05, 0.35, 0.15))
        y += 18

    # ---------------- risk flags ----------------
    flags = [f for f in (risk.get("flags") or []) if f.get("code") not in
             ("ACTIVE_ENCUMBRANCE", "SALE_DURING_ENCUMBRANCE")]
    if flags:
        y += 4
        rule(y - 10)
        line(y, "RISK FLAGS FROM LAND-LEVEL CHECKS", 10, True)
        y += 16
        for f in flags[:8]:
            col = {"critical": (0.55, 0.1, 0.05), "warning": (0.5, 0.35, 0.0),
                   "info": (0.2, 0.35, 0.5)}.get(f.get("severity"), (0.3, 0.3, 0.35))
            line(y, "[%s]  %s" % (f.get("severity", "").upper(), f.get("title") or f.get("code")),
                 9.5, True, color=col)
            y += 13
            det = (f.get("detail") or "")[:118]
            line(y, "     " + det, 8.5, False, color=col)
            y += 16
            if y > 700:
                break

    # ---------------- disclaimer + signature ----------------
    y = max(y + 10, 726)
    line(y, "Internal risk-check report generated from encumbrances registered in this system. "
            "The legally conclusive Encumbrance Certificate is issued by the Sub-Registrar.",
         7.5, False, color=(0.45, 0.5, 0.58))
    y += 11
    line(y, "Issued by %s on %s" % (issued_by or "system", time.strftime("%d %b %Y %H:%M")),
         7.5, False, color=(0.45, 0.5, 0.58))
    sig_x = W - M - 200
    line(y - 34, "Verification Officer", 8.5, True, color=(0.25, 0.32, 0.42))
    page.draw_line((sig_x - 10, y - 18), (sig_x + 190, y - 18), color=(0.3, 0.35, 0.45), width=0.8)

    # ---------------- QR (tamper-evident content hash) ----------------
    payload = "|".join([
        "EC", str(risk.get("survey") or ""), str(risk.get("khasra") or ""),
        str(risk.get("village") or ""), str(years), verdict,
        ";".join("%s:%s" % (e.get("creditor"), e.get("status")) for e in in_window),
    ])
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    qr = qrcode.QRCode(box_size=1, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data("LANDREC-EC " + digest)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    qsize = 92
    cell = qsize / n
    q0x, q0y = W - M - qsize, y - 66
    for r_i in range(n):
        for c_i in range(n):
            if matrix[r_i][c_i]:
                page.draw_rect(pymupdf.Rect(q0x + c_i * cell, q0y + r_i * cell,
                                            q0x + (c_i + 1) * cell, q0y + (r_i + 1) * cell),
                               color=(0, 0, 0), fill=(0, 0, 0))
    cap = "report hash: " + digest[:16]
    capw = helv.text_length(cap, fontsize=6.5)
    tw = pymupdf.TextWriter(page.rect, color=(0.25, 0.32, 0.42))
    tw.append((q0x + (qsize - capw) / 2, q0y + qsize + 8), cap, font=helv, fontsize=6.5)
    tw.write_text(page)

    out = pymupdf.open()
    out.insert_pdf(docf)
    data = out.tobytes(garbage=3, deflate=True)
    docf.close()
    out.close()
    return data
