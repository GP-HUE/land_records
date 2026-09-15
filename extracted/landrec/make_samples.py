"""Generate sample scanned land-record images (Hindi + English) for testing."""
import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "samples")
os.makedirs(OUT, exist_ok=True)

DEV_FONT = os.path.expanduser("~/.fonts/NotoSansDevanagari-Regular.ttf")
SERIF_FONT = os.path.expanduser("~/.fonts/NotoSerifDevanagari-Regular.ttf")

HINDI = """जमाबंदी / खतौनी प्रमाण पत्र
राज्य: मध्य प्रदेश
जिला: भोपाल
तहसील: हुजूर
गाँव: बरखेड़ा

खाता संख्या: १२८
खसरा नंबर: ४५/२
सर्वे नंबर: ३१२
भूमि स्वामी: रामस्वरूप शर्मा
पिता का नाम: श्यामलाल शर्मा

क्षेत्रफल: २.५ एकड़
भूमि का प्रकार: सिंचित कृषि
स्वामित्व प्रकार: निजी

दाखिल खारिज नंबर: ४४७१
पंजीकरण संख्या: एमपी/२०२१/८८४२
खतौनी वर्ष: २०२३-२४
"""

HANDWRITTEN = """Handwritten mutation entry (Revenue Inspector note)

Village: Sundarpur
Tehsil: Huzur
District: Bhopal

Owner name: Meera Bai Patel
Survey No: 87
Khasra No: 21/4
Area: 1.25 acre

Mutation No: 5523
Land type: irrigated
"""

ENGLISH = """JAMABANDI / KHATAUNI CERTIFICATE
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Barkheda

Khata Number: 128
Khasra Number: 45/2
Survey Number: 312
Landowner Name: Ramswaroop Sharma
Father's Name: Shyamlal Sharma

Area: 2.5 acre
Land Type: Irrigated Agricultural
Ownership: Private

Mutation Number: 4471
Registration Number: MP/2021/8842
Khatauni Year: 2023-24
"""

def render(text, font_path, out_path, size=26, noise=True, rotate=0.4):
    font = ImageFont.truetype(font_path, size)
    title = ImageFont.truetype(font_path, size + 8)
    lines = text.splitlines()

    # measure
    tmp = ImageDraw.Draw(Image.new("L", (10, 10)))
    line_h = int(size * 1.7)
    max_w = max(tmp.textlength(l, font=font) for l in lines)
    w = int(max_w) + 120
    h = line_h * len(lines) + 160

    img = Image.new("RGB", (w, h), (250, 247, 238))
    d = ImageDraw.Draw(img)
    # header border
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

render(HINDI, DEV_FONT, os.path.join(OUT, "hindi_khatauni_sample.png"))
render(ENGLISH, DEV_FONT, os.path.join(OUT, "english_jamabandi_sample.png"))
render(HANDWRITTEN, os.path.expanduser("~/.fonts/Caveat.ttf"),
       os.path.join(OUT, "handwritten_mutation_sample.png"), size=30, rotate=0.5)
