"""OCR engine: image preprocessing + multilingual Tesseract OCR.

Runs ONLY inside the disposable worker process (see ocrpool.py /
ocr_worker.py) — never in the web-server process, so a pathological
file can at worst kill the worker, never the website.
"""
import io
import os
import re
import pytesseract
from PIL import Image, ImageChops, ImageFilter
import fitz  # PyMuPDF for PDFs

from . import common, paths

# Size limits — the main defence against out-of-memory kills.
# A 300 dpi A4 scan is ~2480 x 3508 px; OCR quality stays excellent far
# beyond that, so these caps cost nothing in accuracy.
MAX_LONG_EDGE = 10000         # silently downscale anything bigger
MAX_HARD_LONG_EDGE = 15000    # reject BEFORE decoding anything bigger
NO_DENOISE_ABOVE = 6000       # fastNlMeansDenoising is memory-hungry; skip on large pages
TESS_TIMEOUT = 240            # seconds per Tesseract pass

# Locate Tesseract:
#   1. explicit PYTESSERACT_PATH env var (e.g. set by the Windows launcher)
#   2. bundled copy inside a frozen .exe (tesseract/tesseract.exe + tessdata)
#   3. system PATH (Linux/macOS default)
if os.environ.get("PYTESSERACT_PATH"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["PYTESSERACT_PATH"]
elif paths.is_frozen():
    bundled = os.path.join(paths.resource_dir(), "tesseract", "tesseract.exe")
    if os.path.exists(bundled):
        pytesseract.pytesseract.tesseract_cmd = bundled
        os.environ.setdefault("TESSDATA_PREFIX",
                              os.path.join(paths.resource_dir(), "tesseract", "tessdata"))

TESSDATA_DIR = "/usr/share/tesseract-ocr/5/tessdata"

# All Indian languages this app supports (used for the UI language hint).
ALL_INDIC_LANGS = ["hin", "ben", "guj", "pan", "ori", "tam", "tel", "kan", "mal", "urd"]

# Languages for the AUTO-DETECT pass when the user picks "Auto".
# Deliberately SMALL: Tesseract's multi-language recognition cost grows with
# every added language, and an 11-language detection pass takes several
# minutes on a typical office-laptop CPU. These four cover the large majority
# of Indian land records; for rarer scripts use the document-language
# dropdown, which skips the detection pass entirely.
DETECT_LANGS = ["hin", "ben", "tam", "tel"]

# Max scripts the main (full-resolution) pass OCRs at once — stops the main
# pass ballooning when the quick pass hallucinates stray glyphs.
MAX_MAIN_SCRIPTS = 2


def _top_scripts(text: str, limit: int = MAX_MAIN_SCRIPTS) -> list:
    """Weighted script detection for OCR language selection.

    Unlike common.detect_scripts (any 3+ glyphs counts), this ranks scripts
    by how many glyphs actually appear and keeps only the dominant one or
    two. Tesseract's quick pass routinely hallucinates a handful of glyphs
    from another script (e.g. a few Tamil characters inside a Hindi page);
    without this filter the expensive full-resolution main pass would run
    with 3+ languages — the single biggest OCR slowdown.
    """
    counts = {}
    for _name, (code, (lo, hi)) in common.SCRIPT_RANGES.items():
        cnt = sum(1 for ch in text if lo <= ord(ch) <= hi)
        if cnt >= 3:
            counts[code] = cnt
    if not counts:
        return []
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    top_code, top_cnt = ranked[0]
    keep = [top_code]
    for code, cnt in ranked[1:]:
        if len(keep) >= limit:
            break
        if cnt >= max(8, int(0.25 * top_cnt)):
            keep.append(code)
    return keep

_available_langs = None


def available_langs() -> set:
    """Set of language codes actually installed for this Tesseract."""
    global _available_langs
    if _available_langs is None:
        try:
            _available_langs = set(pytesseract.get_languages(config=""))
        except Exception:
            _available_langs = {"eng"}
    return _available_langs


def _preprocess(gray):
    """Denoise + adaptive threshold to make faded/scanned text readable.

    PIL implementation of the proven cv2 pipeline (median 3x3 + Gaussian
    adaptive threshold, blockSize 31 / C 15). Output is bit-identical in
    effect: black text on white. PIL-only so the worker stays light —
    OpenCV's import alone costs ~200MB of RAM, which OOM-killed the worker
    on 512MB hosts (e.g. Render free tier).
    """
    if gray.size[0] == 0 or gray.size[1] == 0:
        raise ValueError("Empty image")
    # Fast, light denoise (milliseconds even on large scans)
    gray = gray.filter(ImageFilter.MedianFilter(3))
    # adaptive threshold: white where the pixel is within C of its local
    # (Gaussian) background, black where it diverges (the ink)
    bg = gray.filter(ImageFilter.GaussianBlur(radius=15))
    diff = ImageChops.subtract(bg, gray)   # bg - pixel, clipped at 0
    return diff.point(lambda p: 255 if p < 15 else 0)


def _text_from_data(data: dict) -> str:
    """Reconstruct line-structured text from image_to_data output.

    This avoids a SECOND full-size Tesseract pass (image_to_string) just to
    get the text — the words + line structure are already in the data dict.
    """
    lines = {}
    order = []
    n = len(data["text"])
    for i in range(n):
        w = (data["text"][i] or "").strip()
        if not w:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(w)
    return "\n".join(" ".join(lines[k]) for k in order)


def pdf_to_images(data: bytes, dpi: int = 300) -> list:
    """Convert PDF bytes to list of PIL Images (first 5 pages for demo).

    Each page is capped at MAX_LONG_EDGE px: huge PDF pages are rendered
    at a reduced DPI instead of blowing up memory.
    """
    doc = fitz.open(stream=data, filetype="pdf")
    images = []
    for page in doc[:5]:
        rect = page.rect
        longest_pt = max(rect.width, rect.height) or 1.0
        eff_dpi = dpi
        px_at_dpi = longest_pt * dpi / 72.0
        if px_at_dpi > MAX_LONG_EDGE:
            eff_dpi = max(72, int(dpi * MAX_LONG_EDGE / px_at_dpi))
        if longest_pt * eff_dpi / 72.0 > MAX_HARD_LONG_EDGE:
            continue  # skip absurdly huge pages instead of risking OOM
        pix = page.get_pixmap(dpi=eff_dpi)
        images.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    return images


def _to_gray(img: Image.Image):
    """PIL grayscale (L) — replaces the old cv2 RGB→BGR→gray round-trip."""
    return img.convert("L")


def ocr_image(img: Image.Image, langs: list = None,
              progress_cb=None) -> dict:
    """OCR a single PIL image. Returns dict with full text and per-word conf.

    If `langs` is provided (a language hint), the expensive script-detection
    pass is SKIPPED entirely — one OCR pass total, which is much faster and
    more accurate. When `langs` is None, a quick detection pass runs first.

    `progress_cb` (optional) receives short human-readable stage strings so
    the web UI can show live progress instead of a silent "processing...".
    """
    def _cb(stage):
        if progress_cb:
            try:
                progress_cb(stage)
            except Exception:
                pass

    gray = _to_gray(img)
    processed = _preprocess(gray)
    _cb("preparing image")

    if langs is None:
        # Script detection: ONE quick pass over the small high-coverage pool
        # (eng + top-4 Indic) on a downscaled image. (The old 10-language
        # detection pass was the main cause of "fast on server, slow on PC".)
        detect_pool = [l for l in DETECT_LANGS if l in available_langs()]
        # downscale large pages for the quick pass to keep it fast
        quick_img = processed
        w, h = processed.size
        if max(h, w) > 1000:
            scale = 1000 / max(h, w)
            quick_img = processed.resize((max(1, int(w * scale)), max(1, int(h * scale))),
                                         Image.BOX)
        _cb("detecting document language (OCR pass 1 of 2)")
        try:
            quick = pytesseract.image_to_string(
                quick_img, lang="+".join(["eng"] + detect_pool),
                config="--psm 6", timeout=TESS_TIMEOUT)
        except Exception:
            try:
                quick = pytesseract.image_to_string(quick_img, lang="eng",
                                                    config="--psm 6",
                                                    timeout=TESS_TIMEOUT)
            except Exception:
                quick = ""
        langs = _top_scripts(quick) or ["eng"]

    # keep only languages this Tesseract actually has data for
    have = set(available_langs())
    missing = [l for l in langs if l not in have]
    if langs and not (set(langs) & have):
        # The requested pack is not installed on THIS machine. Failing loudly
        # beats silently OCRing a Tamil document with the English model and
        # returning garbage the user cannot explain.
        raise ValueError(
            "Tesseract language pack '%s' is not installed on this machine. "
            "Add it in Tesseract setup (Setup additional language data) and "
            "rebuild the exe, or pick a different language." % langs[0])
    langs = [l for l in langs if l in have] or ["eng"]
    lang_str = "+".join(dict.fromkeys(langs + ["eng"]))
    cfg = "--psm 6"
    _cb("running OCR: extracting text" +
        ("" if langs is None else " in " + " + ".join(lang_str.split("+"))))
    data = pytesseract.image_to_data(processed, lang=lang_str, config=cfg,
                                     output_type=pytesseract.Output.DICT,
                                     timeout=TESS_TIMEOUT)

    words, confs = [], []
    for w, c in zip(data["text"], data["conf"]):
        w = (w or "").strip()
        if w:
            words.append(w)
            confs.append(int(float(c)))

    # Reconstruct line-structured text from the data pass — NO second
    # full-size Tesseract pass (this was the slow 3rd pass before).
    text = _text_from_data(data)
    mean_conf = (sum(confs) / len(confs)) if confs else 0.0
    return {"text": text, "words": words, "conf": confs, "mean_conf": round(mean_conf, 1),
            "langs": langs}


def _open_image_safe(data: bytes) -> Image.Image:
    """Open an uploaded image with hard size limits.

    The size is checked from the file HEADER (before any pixel decode), so
    a decompression-bomb image cannot allocate memory in the first place.
    """
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if max(w, h) > MAX_HARD_LONG_EDGE:
        raise ValueError(
            "Image too large (%dx%d px) - maximum supported size is %d px on the "
            "longest side. Please rescan at a lower resolution."
            % (w, h, MAX_HARD_LONG_EDGE))
    if max(w, h) > MAX_LONG_EDGE:
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
    return img.convert("RGB")


def process_file(data: bytes, filename: str, langs: list = None,
                 progress_cb=None) -> dict:
    """Entry point: file bytes -> OCR result (handles image or PDF).

    `langs` is an optional language hint (e.g. ["hin"]). When given, the
    script-detection pass is skipped — much faster and more accurate.
    `progress_cb` (optional) receives live stage strings for the UI.
    """
    name = filename.lower()
    if name.endswith(".pdf"):
        progress_cb and progress_cb("converting PDF pages to images")
        images = pdf_to_images(data)
    else:
        images = [_open_image_safe(data)]

    total = len(images)
    pages = []
    for i, img in enumerate(images, 1):
        base = ("Page %d of %d — " % (i, total)) if total > 1 else ""
        def _page_cb(stage, _b=base):
            if progress_cb:
                progress_cb(_b + stage)
        pages.append(ocr_image(img, langs=langs, progress_cb=_page_cb))

    full_text = "\n".join(p["text"] for p in pages)
    mean_conf = sum(p["mean_conf"] for p in pages) / len(pages) if pages else 0.0
    return {
        "pages": pages,
        "full_text": full_text,
        "mean_conf": round(mean_conf, 1),
        "num_pages": len(pages),
        "detected_scripts": common.detect_scripts(full_text),
    }
