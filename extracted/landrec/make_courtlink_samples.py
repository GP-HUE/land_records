"""Generate the two SA-CourtLink demo scans (English-format jamabandi):

  * khatauni_rampur_2025.png — Rampur Khas survey 145, owner Mohanlal Verma.
    The demo court database holds a STAY ORDER (WPL/2023/0234) on exactly
    this survey, so an admin uploading this scan sees SA CourtLink fire.

  * khatauni_guroli_2025.png — Guroli survey 88, owner Sita Ram Yadav.
    The court database only has a case on Guroli survey 87, so this scan
    correctly comes back CLEAN (precision demo: same village != a match).
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "samples")
os.makedirs(OUT, exist_ok=True)

_FONT_CANDIDATES = [
    os.path.expanduser("~/.fonts/NotoSansDevanagari-Regular.ttf"),
    "/usr/share/fonts/truetype/lohit-deva/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font():
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("no usable font found")


RAMPUR = """JAMABANDI / KHATAUNI CERTIFICATE
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Rampur Khas

Khata Number: 64
Khasra Number: 12/3
Survey Number: 145
Landowner Name: Mohanlal Verma
Father's Name: Bansilal Verma

Area: 1.8 acre
Land Type: Irrigated Agricultural
Ownership: Private

Mutation Number: 5102
Registration Number: MP/2024/3391
Khatauni Year: 2024-25
"""

GUROLI = """JAMABANDI / KHATAUNI CERTIFICATE
State: Madhya Pradesh
District: Sehore
Tehsil: Ashta
Village: Guroli

Khata Number: 37
Khasra Number: 9/1
Survey Number: 88
Landowner Name: Sita Ram Yadav
Father's Name: Ramdayal Yadav

Area: 3.1 acre
Land Type: Dry Agricultural
Ownership: Private

Mutation Number: 6099
Registration Number: MP/2024/7710
Khatauni Year: 2024-25
"""


def render(text, font_path, out_path, size=26, noise=True, rotate=0.4):
    font = ImageFont.truetype(font_path, size)
    title = ImageFont.truetype(font_path, size + 8)
    lines = text.splitlines()

    tmp = ImageDraw.Draw(Image.new("L", (10, 10)))
    line_h = int(size * 1.7)
    max_w = max(tmp.textlength(l, font=font) for l in lines)
    w = int(max_w) + 120
    h = line_h * len(lines) + 160

    img = Image.new("RGB", (w, h), (250, 247, 238))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, w - 20, h - 20], outline=(120, 60, 20), width=3)
    d.rectangle([30, 30, w - 30, h - 30], outline=(120, 60, 20), width=1)

    y = 60
    for i, line in enumerate(lines):
        f = title if i == 0 else font
        if i == 0:
            d.text((w / 2, y), line, font=f, fill=(90, 30, 10), anchor="ma")
        else:
            d.text((60, y), line, font=f, fill=(30, 30, 30))
        y += line_h

    arr = np.array(img)
    if noise:
        n = np.random.normal(0, 4, arr.shape).astype(np.int16)
        arr = np.clip(arr.astype(np.int16) + n, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr).convert("L")
    if rotate:
        img = img.rotate(rotate, resample=Image.BILINEAR, fillcolor=250)
    img = img.filter(ImageFilter.GaussianBlur(0.4))
    img.save(out_path)
    print("wrote", out_path)


render(RAMPUR, _font(), os.path.join(OUT, "khatauni_rampur_2025.png"))
render(GUROLI, _font(), os.path.join(OUT, "khatauni_guroli_2025.png"))
