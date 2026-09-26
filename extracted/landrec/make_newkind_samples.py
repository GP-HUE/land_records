"""Generate the NEW-format (document kind) demo scans (v3.13):

New-format land records print the GPS coordinates of the plot's four
corners on the document.  Uploading one with Document Kind = New reads
those coordinates and turns them into the map boundary (source: document)
+ an exact pin at the polygon centroid.

IMPORTANT (v3.13.1): every sample's corner rectangle is now computed FROM
its recorded area, so the map's "📏 computed area" matches the "📄 recorded
area" like a real, internally-consistent record.

Samples:
  * khatauni_newkind_rampur_2025.png — English khatauni, Rampur Khas 145,
    Mohanlal Verma, 1.8 acre.  Full 4 coordinates; the demo court DB holds
    a STAY ORDER on this survey (WPL/2023/0234) so an admin upload shows
    SA CourtLink + the purple boundary in one shot.
  * khatauni_newkind_arera_2025.png — English khatauni, Arera 518, Prakash
    Jatav, 2.6 acre.  Second healthy scan, so a NEW-kind bulk batch has
    two importable rows without duplicates.
  * khatauni_newkind_hindi_2025.png — Hindi (Devanagari), Manpura 66,
    2.4 acre, निर्देशांक coordinate lines.
  * khatauni_newkind_partial_2025.png — English, Sundarpur 77, 2.2 acre:
    bottom corner of the paper torn — only THREE of four coordinates are
    legible, so no boundary until a verifier supplies the 4th corner
    (review-queue demo).
  * khatauni_newkind_badcoords_2025.png — English, Khandwa 909: one
    coordinate illegible ("NA") and one out of range (126.61 N), so only
    2/4 parse — parser-safety demo (record goes to review, never a wrong
    map point).
  * mutation_newkind_2025.png — English mutation (namantaran) record,
    Barkheda 88, 1.5 acre + 4 coordinates (proves doc-type coverage).
  * sale_deed_newkind_2025.png — English sale deed, Arera 512, 3.0 acre
    + 4 coordinates.
"""
import math
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

M_PER_DEG_LAT = 111320.0
ACRE_M2 = 4046.86


def _font():
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    raise SystemExit("no usable font found")


def rect_corners(center_lat, center_lon, area_m2, aspect=1.4):
    """Four corners (SW, SE, NE, NW — ring order) of a rectangle of EXACTLY
    area_m2 around (center_lat, center_lon), so the map's computed area
    matches the area printed in the record."""
    side_ns = math.sqrt(area_m2 * aspect)
    side_ew = area_m2 / side_ns
    dlat = (side_ns / 2.0) / M_PER_DEG_LAT
    dlon = (side_ew / 2.0) / (M_PER_DEG_LAT * math.cos(math.radians(center_lat)))
    return [(center_lat - dlat, center_lon - dlon),
            (center_lat - dlat, center_lon + dlon),
            (center_lat + dlat, center_lon + dlon),
            (center_lat + dlat, center_lon - dlon)]


def poly_corners(center_lat, center_lon, area_m2, n, rotation_deg=0.0):
    """N corners (counter-clockwise ring order) of a REGULAR n-gon of EXACTLY
    area_m2 around (center_lat, center_lon) — a triangle (3), pentagon (5)
    or any other plot shape, so computed area ≈ recorded area."""
    # area of a regular n-gon with circumradius R: A = n R² sin(2π/n) / 2
    r = math.sqrt(2.0 * area_m2 / (n * math.sin(2.0 * math.pi / n)))
    mlat = M_PER_DEG_LAT
    mlon = M_PER_DEG_LAT * math.cos(math.radians(center_lat))
    pts = []
    for k in range(n):
        th = math.radians(rotation_deg) + 2.0 * math.pi * k / n
        pts.append((center_lat + (r * math.sin(th)) / mlat,
                    center_lon + (r * math.cos(th)) / mlon))
    return pts


def _coord_block(coords, label="Coordinate"):
    out = ["", "Geo Coordinates of Parcel Corners (GPS Survey):"]
    for i, (lat, lon) in enumerate(coords, 1):
        out.append("%s %d: %.5f N, %.5f E" % (label, i, lat, lon))
    return out


RAMPUR_COORDS = rect_corners(23.35220, 77.35170, 1.8 * ACRE_M2)          # 1.8 acre
ARERA_COORDS = rect_corners(23.21460, 77.40110, 2.6 * ACRE_M2, 1.3)      # 2.6 acre
MANPURA_COORDS = rect_corners(23.20145, 77.08345, 2.4 * ACRE_M2, 1.5)    # 2.4 acre
SUNDARPUR_COORDS = rect_corners(23.28875, 77.41270, 2.2 * ACRE_M2)       # 2.2 acre
KHANDWA_COORDS = rect_corners(21.82450, 76.35200, 2.0 * ACRE_M2, 1.6)    # 2.0 acre
BARKHEDA_COORDS = rect_corners(23.26900, 77.30400, 1.5 * ACRE_M2)        # 1.5 acre
ARERA_DEED_COORDS = rect_corners(23.21520, 77.40180, 3.0 * ACRE_M2, 1.25)  # 3.0 acre

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

ARERA = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Arera

Khata Number: 118
Khasra Number: 22/4
Survey Number: 418
Landowner Name: Prakash Jatav
Father's Name: Ramcharan Jatav

Area: 2.6 acre
Land Type: Irrigated Agricultural
Ownership: Private

Mutation Number: 7714
Registration Number: MP/2025/2208
Khatauni Year: 2024-25
""" + "\n".join(_coord_block(ARERA_COORDS)) + "\n"

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
SUNDARPUR_PARTIAL = (_SUNDARPUR_HEAD + "\n".join(
    _coord_block(SUNDARPUR_COORDS[:3])) + "\nCoordinate 4: [torn / anpathiya]\n")

KHANDWA_BAD = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
State: Madhya Pradesh
District: Khandwa
Tehsil: Khandwa
Village: Khandwa

Khata Number: 203
Khasra Number: 31/7
Survey Number: 909
Landowner Name: Bhimrao Sathe
Father's Name: Tukaram Sathe

Area: 2.0 acre
Land Type: Non-Irrigated Agricultural
Ownership: Private

Mutation Number: 8830
Registration Number: MP/2025/4491
Khatauni Year: 2024-25

Geo Coordinates of Parcel Corners (GPS Survey):
Coordinate 1: %.5f N, %.5f E
Coordinate 2: NA / anpathiya (illegible)
Coordinate 3: 126.61234 N, %.5f E
Coordinate 4: %.5f N, %.5f E
""" % (KHANDWA_COORDS[0][0], KHANDWA_COORDS[0][1],
       KHANDWA_COORDS[2][1], KHANDWA_COORDS[3][0], KHANDWA_COORDS[3][1])

MUTATION = """MUTATION / NAMANTARAN RECORD (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Barkheda

Mutation Number: 4471
Registration Number: MP/2021/8842
Survey Number: 88
Khasra Number: 45/2
Khata Number: 128
Landowner Name: Gopal Krishna Verma
Father's Name: Shyamlal Verma

Area: 1.5 acre
Land Type: Irrigated Agricultural
Ownership: Private
Khatauni Year: 2024-25
""" + "\n".join(_coord_block(BARKHEDA_COORDS)) + "\n"

SALE_DEED = """SALE DEED / REGISTRY EXTRACT (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Arera

Registration Number: MP/2024/6610
Survey Number: 314
Khasra Number: 18/1
Khata Number: 97
Landowner Name: Narottam Das Gupta
Father's Name: Kanhaiya Lal Gupta
Area: 3.0 acre
Land Type: Irrigated Agricultural
Ownership: Private
Khatauni Year: 2024-25
""" + "\n".join(_coord_block(ARERA_DEED_COORDS)) + "\n"


# ----- v3.14: variable-shape plots — triangle (3 corners), pentagon (5) -----
TRI_COORDS = poly_corners(23.43150, 77.43250, 1.2 * ACRE_M2, 3, rotation_deg=18)
PENT_COORDS = poly_corners(23.17625, 77.31875, 2.1 * ACRE_M2, 5, rotation_deg=60)
TWOC_COORDS = poly_corners(23.35075, 77.21975, 1.0 * ACRE_M2, 4, rotation_deg=45)

TRIANGLE = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Berasia
Village: Berasia

Khata Number: 71
Khasra Number: 9/1
Survey Number: 264
Landowner Name: Santosh Kumar Ahirwar
Father's Name: Ramdayal Ahirwar

Area: 1.2 acre
Land Type: Irrigated Agricultural
Ownership: Private

Mutation Number: 3307
Registration Number: MP/2025/0914
Khatauni Year: 2024-25
""" + "\n".join(_coord_block(TRI_COORDS)) + "\n"

PENTAGON = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Sukhi Sewaniya

Khata Number: 83
Khasra Number: 16/2
Survey Number: 655
Landowner Name: Ramnath Kushwaha
Father's Name: Bholaram Kushwaha

Area: 2.1 acre
Land Type: Irrigated Agricultural
Ownership: Private

Mutation Number: 5566
Registration Number: MP/2025/1871
Khatauni Year: 2024-25
""" + "\n".join(_coord_block(PENT_COORDS)) + "\n"

# surveyor printed only TWO corners (the rest smudged in a rain-soaked
# register) — a polygon cannot be built from 2 points, so the record must
# land in the review queue with no boundary, never a wrong shape
TWOCORNERS = """JAMABANDI / KHATAUNI CERTIFICATE (NEW FORMAT)
State: Madhya Pradesh
District: Bhopal
Tehsil: Huzur
Village: Ratua Ryt

Khata Number: 12
Khasra Number: 3/5
Survey Number: 41
Landowner Name: Jagdish Prasad Meena
Father's Name: Kalyan Singh Meena

Area: 1.0 acre
Land Type: Non-Irrigated Agricultural
Ownership: Private

Khatauni Year: 2024-25

Geo Coordinates of Parcel Corners (GPS Survey):
Coordinate 1: %.5f N, %.5f E
Coordinate 2: %.5f N, %.5f E
Note: remaining corner readings smudged in register; field visit ordered.
""" % (TWOC_COORDS[0][0], TWOC_COORDS[0][1], TWOC_COORDS[1][0], TWOC_COORDS[1][1])


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


ALL = [
    ("khatauni_newkind_rampur_2025.png", RAMPUR),
    ("khatauni_newkind_arera_2025.png", ARERA),
    ("khatauni_newkind_hindi_2025.png", HINDI),
    ("khatauni_newkind_partial_2025.png", SUNDARPUR_PARTIAL),
    ("khatauni_newkind_badcoords_2025.png", KHANDWA_BAD),
    ("mutation_newkind_2025.png", MUTATION),
    ("sale_deed_newkind_2025.png", SALE_DEED),
    # v3.14 variable-shape plots
    ("khatauni_newkind_triangle_2025.png", TRIANGLE),
    ("khatauni_newkind_pentagon_2025.png", PENTAGON),
    ("khatauni_newkind_2corners_2025.png", TWOCORNERS),
]

# the three v3.14 additions alone (regenerate them without touching the
# proven v3.13.x files)
SHAPES_V314 = ALL[7:]

# tuned render overrides: keeps every field above the low-confidence line
# (triangle's default render put land_class/registration_no on the border)
RENDER_OVERRIDES = {
    "khatauni_newkind_triangle_2025.png": {"size": 25, "rotate": 0.45},
}

if __name__ == "__main__":
    for fn, text in ALL:
        render(text, _font(), os.path.join(OUT, fn),
               **RENDER_OVERRIDES.get(fn, {}))
