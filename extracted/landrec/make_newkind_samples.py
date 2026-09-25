"""Generate the NEW-format (document kind) demo scans (v3.13):

New-format land records carry the GPS coordinates of the plot's four
corners PRINTED on the document.  Uploading one with Document Kind = New
reads those four coordinates and immediately turns them into the record's
map boundary (source: document) + exact pin at the polygon centroid.

  * khatauni_newkind_rampur_2025.png — English, Rampur Khas survey 145,
    owner Mohanlal Verma: all four coordinates printed.  As a bonus the
    demo court database holds a STAY ORDER (WPL/2023/0234) on exactly this
    survey, so an admin uploading it sees SA CourtLink fire AND the purple
    boundary appear on the map in one shot.

  * khatauni_newkind_hindi_2025.png — Hindi (Devanagari) new-format record,
    Manpura survey 66, with निर्देशांक coordinate lines.

  * khatauni_newkind_partial_2025.png — English, Sundarpur survey 77: the
    bottom corner of the paper is torn — only THREE of the four coordinate
    lines are legible, so no boundary is created and the record is pushed
    into the review queue until an officer supplies the 4th corner.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

np.random.seed(20240926)   # reproducible scans: same noise every run

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "samples")
os.makedirs(OUT, exist_ok=True)

_FONT_CANDIDATES = [
    os.path.expanduser("~/.fonts/NotoSansDevanagari-Regular.ttf"),
    "/usr/share/fonts/truetype/lohit-devanagari/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/lohit-deva/Lohit-Devanagari.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

# Rampur Khas (Bhopal) corner coordinates — a tidy ~60x70 m parcel
RAMPUR_COORDS = [(23.35214, 77.35126), (23.35241, 77.35189),
                 (23.35203, 77.35207), (23.35187, 77.35143)]
MANPURA_COORDS = [(23.20145, 77.08312), (23.20171, 77.08364),
                  (23.20129, 77.08381), (23.20116, 77.08329)]
SUNDARPUR_COORDS = [(23.28871, 77.41230), (23.28895, 77.41288),
                    (23.28856, 77.41305)]


def _font():
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("no usable font found")


def _coord_block(coords, label="Coordinate"):
    out = ["", "Geo Coordinates of Parcel Corners (GPS Survey):"]
    for i, (lat, lon) in enumerate(coords, 1):
        out.append("%s %d: %.5f N, %.5f E" % (label, i, lat, lon))
    return out


RAMPUR = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
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
""" + "\n".join(_coord_block(RAMPUR_COORDS)) + "\n"

HINDI = """खातौनी प्रमाण पत्र (नया प्रारूप)
राज्य: मध्य प्रदेश
जिला: सीहोर
तहसील: अष्टा
ग्राम: मानपुरा

खाता नंबर: 51
खसरा नंबर: 7/2
सर्वे नंबर: 66
भूमि स्वामी: राधेश्याम मीणा
पिता का नाम: नथ्थूलाल मीणा

क्षेत्रफल: 2.4 एकड़
भूमि का प्रकार: सिंचित कृषि
स्वामित्व: निजी

खातौनी वर्ष: 2024-25

भू-खंड कोनों के भौगोलिक निर्देशांक (GPS):
""" + "\n".join("निर्देशांक %d: %.5f N, %.5f E" % (i, lat, lon)
                for i, (lat, lon) in enumerate(MANPURA_COORDS, 1)) + "\n"

_SUNDARPUR_HEAD = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sundarpur

Khata Number: 29
Khasra Number: 4/6
Survey Number: 77
Landowner Name: Mahesh Chandra Gupta
Father's Name: Dwarika Prasad Gupta

Area: 2.2 acre
Land Type: Irrigated Agricultural
Ownership: Private

Mutation Number: 6201
Registration Number: MP/2025/1120
Khatauni Year: 2024-25
"""
SUNDARPUR_PARTIAL = _SUNDARPUR_HEAD + "\n".join(
    _coord_block(SUNDARPUR_COORDS)) + "\nCoordinate 4: [torn / anpathiya]\n"


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


if __name__ == "__main__":
    render(RAMPUR, _font(), os.path.join(OUT, "khatauni_newkind_rampur_2025.png"))
    render(HINDI, _font(), os.path.join(OUT, "khatauni_newkind_hindi_2025.png"))
    render(SUNDARPUR_PARTIAL, _font(),
           os.path.join(OUT, "khatauni_newkind_partial_2025.png"))
