"""Generate test documents for the OWNERSHIP-TRANSFER scenario battery.

Covers the sale scenario: A sells land to B; B's papers match A's record in
every land detail (survey 452, khasra 77, village Sundarpur) and only the
owner name changed - plus the edge cases:

  xfer_old_2019.png        A's khatauni, year 2019          (the base record)
  xfer_mutation_2021.png   mutation record A -> B, year 2021 (the bridge)
  xfer_new_2023.png        B's khatauni, year 2023          (the transfer)
  xfer_conflict_2019.png   C's khatauni, year 2019          (same year as A:
                                                              GENUINE conflict)
  xfer_other_village_2023.png  same survey no, DIFFERENT village (two different
                                                              lands, no conflict)
  xfer_partition_2022.png      B's sub-khasra 77/1, area 1.25, year 2022
                                                              (partition sale)

Run:  python3 landrec/make_transfer_samples.py
(Requires DejaVu Sans - present on every Linux system; the documents are
English so no Indic font is needed.)
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "samples")
os.makedirs(OUT, exist_ok=True)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    os.path.expanduser("~/.fonts/DejaVuSans.ttf"),
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

OLD = """KHATAUNI / LAND RECORD CERTIFICATE
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sundarpur

Khata Number: 311
Khasra Number: 77
Survey Number: 452
Landowner Name: Ram Bahadur Singh
Father's Name: Har Singh

Area: 2.5 acre
Land Type: Irrigated Agricultural
Ownership: Private

Khatauni Year: 2019-20
"""

MUTATION = """MUTATION RECORD (NAMANTARAN)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sundarpur

Mutation Number: 7788
Survey Number: 452
Khasra Number: 77

Ownership transferred from Ram Bahadur Singh
to Kamla Devi Singh by sale deed.

Landowner Name: Kamla Devi Singh
Area: 2.5 acre
Khatauni Year: 2021-22
"""

NEW = """KHATAUNI / LAND RECORD CERTIFICATE
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sundarpur

Khata Number: 311
Khasra Number: 77
Survey Number: 452
Landowner Name: Kamla Devi Singh
Father's Name: Ram Bahadur Singh

Area: 2.5 acre
Land Type: Irrigated Agricultural
Ownership: Private

Khatauni Year: 2023-24
"""

CONFLICT = """KHATAUNI / LAND RECORD CERTIFICATE
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sundarpur

Khata Number: 311
Khasra Number: 77
Survey Number: 452
Landowner Name: Mahesh Verma
Father's Name: Suresh Verma

Area: 2.5 acre
Land Type: Irrigated Agricultural
Ownership: Private

Khatauni Year: 2019-20
"""

OTHER_VILLAGE = """PATTAM / LAND RECORD
State: Andhra Pradesh
District: Kurnool
Tehsil: Palamaner
Village: Pipariya

Khata Number: 208
Khasra Number: 77
Survey Number: 452
Landowner Name: Kamla Devi Singh
Father's Name: Ram Bahadur Singh

Area: 2.5 acre
Land Type: Dry Agricultural
Ownership: Private

Khatauni Year: 2023-24
"""

PARTITION = """KHATAUNI / LAND RECORD CERTIFICATE
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sundarpur

Khata Number: 311
Khasra Number: 77/1
Survey Number: 452
Landowner Name: Kamla Devi Singh
Father's Name: Ram Bahadur Singh

Area: 1.25 acre
Land Type: Irrigated Agricultural
Ownership: Private (partition of khasra 77)

Khatauni Year: 2022-23
"""

CASES = [
    ("xfer_old_2019.png", OLD),
    ("xfer_mutation_2021.png", MUTATION),
    ("xfer_new_2023.png", NEW),
    ("xfer_conflict_2019.png", CONFLICT),
    ("xfer_other_village_2023.png", OTHER_VILLAGE),
    ("xfer_partition_2022.png", PARTITION),
]


def render(text, font_path, out_path, size=26, noise=True, rotate=0.3):
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


def main():
    font_path = next((p for p in FONT_CANDIDATES if os.path.exists(p)), None)
    if not font_path:
        raise SystemExit("No DejaVu/Liberation font found - cannot render samples.")
    for name, text in CASES:
        render(text, font_path, os.path.join(OUT, name))
    print("All transfer-scenario test documents written to", OUT)


if __name__ == "__main__":
    main()
