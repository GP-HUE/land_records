"""OCR language-pack diagnostics + tesseract auto-location tests (unit).

Simulates a machine with only the English pack installed (the #1 'OCR is
broken' scenario: Windows default Tesseract install) and verifies:
  * ocr_image flags the situation with an actionable pack_hint
  * the AI-rescue diagnosis surfaces that hint FIRST
  * a normal all-packs machine gets no false-positive hint
  * locate_tesseract() resolves the binary on this machine
"""
import os
import sys

sys.path.insert(0, '/home/user/land_records/extracted')

from landrec import ocr  # noqa: E402
from landrec import ai_rescue  # noqa: E402
from PIL import Image  # noqa: E402

SAMPLES = '/home/user/land_records/extracted/samples'
HINDI = os.path.join(SAMPLES, 'hindi_khatauni_sample.png')

PASS = FAIL = 0
def check(name, cond, extra=''):
    global PASS, FAIL
    print(('PASS  ' if cond else 'FAIL  ') + name + ('' if cond else '  | ' + str(extra)[:160]))
    PASS += 1 if cond else 0
    FAIL += 0 if cond else 1

# ---- 1. tesseract location ----
loc = ocr.locate_tesseract()
check('locate_tesseract finds the binary on this machine', bool(loc), repr(loc))
check('located binary is executable', os.path.exists(loc) and os.access(loc, os.X_OK), repr(loc))

# ---- 2. pack hint: simulate an English-only machine ----
real_available = ocr.available_langs
ocr.available_langs = lambda: {"eng"}
try:
    img = Image.open(HINDI)
    res = ocr.ocr_image(img, langs=None)
finally:
    ocr.available_langs = real_available

check('eng-only machine: Hindi scan produces pack_hint',
      bool(res.get('pack_hint')), res.get('pack_hint', '<none>')[:160])
check('pack_hint names the installed packs (eng)',
      'eng' in (res.get('pack_hint') or ''), res.get('pack_hint', '<none>')[:160])
check('pack_hint is actionable (mentions language data install)',
      'language data' in (res.get('pack_hint') or ''), res.get('pack_hint', '<none>')[:160])

# ---- 3. diagnosis surfaces the hint first ----
diag = ai_rescue.assess({"pages": [res], "full_text": res.get("text", ""),
                         "mean_conf": res.get("mean_conf", 0),
                         "pack_hint": res.get("pack_hint", "")},
                        {})
check('assess() diagnosis starts with the pack hint',
      bool(diag['diagnosis']) and (diag['diagnosis'][0] or '').startswith('No readable text was found, and this machine'),
      diag['diagnosis'][:2])

# ---- 4. normal machine (all packs): no false-positive hint on Hindi sample ----
img = Image.open(HINDI)
res2 = ocr.ocr_image(img, langs=None)
check('full-packs machine: Hindi scan has NO pack_hint',
      not res2.get('pack_hint'), res2.get('pack_hint', '<none>')[:120])
words2 = len(res2.get('words') or [])
check('full-packs machine: Hindi OCR still extracts text (no regression)',
      words2 > 20, 'words=%d' % words2)

# ---- 5. explicit language hint on eng-only machine: clear error, not silence ----
ocr.available_langs = lambda: {"eng"}
try:
    img = Image.open(HINDI)
    try:
        ocr.ocr_image(img, langs=["hin"])
        err = None
    except ValueError as e:
        err = str(e)
    finally:
        ocr.available_langs = real_available
    check('explicit missing pack: clear ValueError naming the pack',
          err is not None and 'hin' in err and 'not installed' in err, err)
except Exception as e:
    ocr.available_langs = real_available
    check('explicit missing pack: clear ValueError naming the pack', False, repr(e))

print('\nOCR LANGPACK TESTS: %d passed, %d failed' % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
