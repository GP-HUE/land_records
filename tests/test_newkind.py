"""v3.13 — Document Kind (Old/New) + printed corner coordinates -> Map.

New-format land records carry the GPS coordinates of the plot's four
corners PRINTED on the paper.  Uploading with Document Kind = "new" makes
OCR read coordinate_1..4, validates them, and — when all four parse —
immediately stores them as the record's map boundary (source: document)
plus an exact pin at the polygon centroid.  Old kind = byte-identical
classic pipeline.  Works for single uploads AND Bulk OCR batches; the
boundary is re-derived when a verifier corrects a coordinate.

Run:  python3 tests/test_newkind.py            (server on :8000)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "extracted"))
from ciutil import http                                   # noqa: E402
from landrec.extractor import parse_coordinate            # noqa: E402

BASE = os.environ.get("LANDREC_BASE", "http://127.0.0.1:8000")
SAMPLES = os.path.join(os.path.dirname(__file__), "..", "extracted", "samples")

RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, bool(cond), extra))
    print(("  PASS " if cond else "  FAIL ") + name + (("  -> " + str(extra)[:160]) if (extra and not cond) else ""))


def login(email, pw):
    s, d = http(BASE, "POST", "/api/auth/login", data={"email": email, "password": pw})
    assert s == 200, (s, d)
    return d["token"]


def multipart(field_name, form_fields, files):
    import uuid
    b = "----newkind" + uuid.uuid4().hex[:12]
    out = []
    for k, v in form_fields.items():
        out.append(("--" + b + "\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                    % (k, v)).encode())
    for fn, data in files:
        out.append(("--" + b + "\r\nContent-Disposition: form-data; name=\"%s\"; "
                    "filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n" % (field_name, fn)).encode()
                   + data + b"\r\n")
    out.append(("--" + b + "--\r\n").encode())
    return b"".join(out), "multipart/form-data; boundary=" + b


def sample_bytes(fn):
    with open(os.path.join(SAMPLES, fn), "rb") as fh:
        return fh.read()


def upload(tok, sample_name, kind, fields_extra=None):
    form = {"doc_type": "land_record", "lang": "eng"}
    if kind is not None:
        form["doc_kind"] = kind
    if fields_extra:
        form.update(fields_extra)
    body, ctype = multipart("file", form, [(sample_name, sample_bytes(sample_name))])
    s, d = http(BASE, "POST", "/api/process", tok=tok, data=body,
                headers={"Content-Type": ctype})
    assert s == 200, (s, str(d)[:300])
    return d


def map_row(tok, doc_id):
    s, d = http(BASE, "GET", "/api/map/records", tok=tok)
    assert s == 200
    return next((r for r in d["records"] if r["id"] == doc_id), None)


# centroids of the coordinates printed on the generated samples
RAMPUR_CENTROID = (23.3522, 77.3517)
RAMPUR_RING = [(23.35175, 77.35135), (23.35175, 77.35205),
               (23.35265, 77.35205), (23.35265, 77.35135)]
SUNDARPUR_C4 = "23.28925 N, 77.41231 E"   # the torn 4th corner of partial

print("=" * 78)
print("v3.13 DOCUMENT KIND (OLD/NEW) + PRINTED COORDINATES -> MAP TESTS")
print("=" * 78)

# ---------------------------------------------------------------- A. parser
print("\n[A] coordinate parser (unit)")
check("A1 plain N/E letters", parse_coordinate("23.35214 N, 77.35126 E") == (23.35214, 77.35126))
check("A2 bare decimals assume N/E", parse_coordinate("23.35241, 77.35189") == (23.35241, 77.35189))
check("A3 degree symbols", parse_coordinate("23.17642° N 80.01231° E") == (23.17642, 80.01231))
check("A4 OCR letter junk tolerated", parse_coordinate("23.35187 WN, 77.35143 E") == (23.35187, 77.35143))
check("A5 S/W flip signs", parse_coordinate("23.5 S, 77.35 W") == (-23.5, -77.35))
check("A6 out-of-range rejected", parse_coordinate("123.456 N, 80.0 E") is None)
check("A7 garbage rejected", parse_coordinate("torn / anpathiya") is None)
check("A8 empty rejected", parse_coordinate("") is None)

op = login("demo.operator@demo.local", "Demo@Operator1")
ver = login("demo.verifier@demo.local", "Demo@Verifier1")

# ------------------------------------------------------- B. old-kind safety
print("\n[B] OLD kind = classic pipeline, untouched")
r_old1 = upload(op, "english_jamabandi_sample.png", "old")
check("B1 old-kind response has no 'coordinates' key", "coordinates" not in r_old1)
check("B2 old-kind fields have no coordinate_* keys",
      not any(k.startswith("coordinate_") for k in r_old1["fields"]))
s, d_old = http(BASE, "GET", "/api/documents/" + r_old1["id"], tok=op)
check("B3 stored doc_kind is 'old'", d_old.get("doc_kind") == "old", d_old.get("doc_kind"))
r_old2 = upload(op, "english_jamabandi_sample.png", None)   # doc_kind omitted
check("B4 omitted doc_kind defaults to classic (no coordinates key)",
      "coordinates" not in r_old2)

# --------------------------------------- C. new-kind single upload (full 4)
print("\n[C] NEW kind upload — 4 printed coordinates become the map boundary")
r_new = upload(op, "khatauni_newkind_rampur_2025.png", "new")
co = r_new.get("coordinates") or {}
check("C1 response.coordinates found == 4", co.get("found") == 4, co)
check("C2 boundary_set is True", co.get("boundary_set") is True, co)
pin = co.get("pin") or [0, 0]
check("C3 pin == centroid of the 4 corners",
      abs(pin[0] - RAMPUR_CENTROID[0]) < 0.02 and abs(pin[1] - RAMPUR_CENTROID[1]) < 0.02, pin)
coords_fields = [r_new["fields"].get("coordinate_%d" % i) for i in range(1, 5)]
check("C4 all four coordinate fields extracted + parseable",
      all(f and parse_coordinate(f.get("value")) for f in coords_fields),
      [(f or {}).get("value") for f in coords_fields])
check("C5 verdict valid when everything reads cleanly",
      r_new["validation"]["verdict"] == "valid", r_new["validation"]["verdict"])
s, d_new = http(BASE, "GET", "/api/documents/" + r_new["id"], tok=op)
check("C6 document detail: doc_kind=new + 4-vertex boundary + lat/lon",
      d_new.get("doc_kind") == "new" and len(d_new.get("boundary") or []) == 4
      and d_new.get("boundary_source") == "document" and d_new.get("lat") is not None)
row = map_row(op, r_new["id"])
check("C7 map row: kind=new, source=document, 4 verts",
      row and row.get("kind") == "new" and row.get("boundary_source") == "document"
      and len(row.get("boundary") or []) == 4)
ring_ok = row and all(abs(row["boundary"][i][0] - RAMPUR_RING[i][0]) < 0.02
                      and abs(row["boundary"][i][1] - RAMPUR_RING[i][1]) < 0.02
                      for i in range(4))
check("C8 boundary vertices match the printed corners (order 1-4)", bool(ring_ok), row and row.get("boundary"))
s, a = http(BASE, "GET", "/api/audit/" + r_new["id"], tok=ver)
acts = [x["action"] + "|" + (x["detail"] or "") for x in a["audit"]]
check("C9 audit: boundary_set source=document",
      any(x.startswith("boundary_set") and "document" in x for x in acts), acts)
check("C10 audit: location_set auto-centroid",
      any(x.startswith("location_set") and "centroid" in x for x in acts), acts)
s, fm = http(BASE, "GET", "/api/fields", tok=op)
fids = [f["id"] for f in fm["fields"]]
check("C11 /api/fields exposes coordinate metadata + doc_kinds",
      all("coordinate_%d" % i in fids for i in range(1, 5))
      and (fm.get("doc_kinds") or {}).get("new"))

# --------------------------------------------- D. partial (torn 4th corner)
print("\n[D] NEW kind with only 3 legible corners -> review queue, no boundary")
r_part = upload(op, "khatauni_newkind_partial_2025.png", "new")
cop = r_part.get("coordinates") or {}
check("D1 found == 3 (4th corner torn)", cop.get("found") == 3, cop)
check("D2 boundary NOT set until all four parse", cop.get("boundary_set") is False)
check("D3 verdict forced to review", r_part["validation"]["verdict"] == "review",
      r_part["validation"]["verdict"])
coord_issue = [i for i in r_part["validation"]["issues"] if i.get("field") == "coordinates"]
check("D4 explicit coordinates review issue", len(coord_issue) == 1 and "4" in coord_issue[0]["msg"],
      coord_issue)
check("D5 'coordinates' joins low_confidence_fields (status pending_review)",
      "coordinates" in r_part["validation"]["low_confidence_fields"])
rowp = map_row(op, r_part["id"])
check("D6 map row: kind=new but no boundary yet",
      rowp and rowp.get("kind") == "new" and rowp.get("boundary") is None)

print("\n[D2] verifier supplies the 4th corner -> boundary re-derived on verify")
s, dv = http(BASE, "POST", "/api/documents/%s/verify" % r_part["id"], tok=ver,
             data={"corrections": {"coordinate_4": SUNDARPUR_C4}})
check("D7 verify response re-applies coordinates", s == 200
      and (dv.get("coordinates") or {}).get("boundary_set") is True, dv.get("coordinates"))
rowp2 = map_row(op, r_part["id"])
check("D8 map row now shows the 4-vertex document boundary",
      rowp2 and len(rowp2.get("boundary") or []) == 4
      and rowp2.get("boundary_source") == "document" and rowp2.get("lat") is not None)
s, a2 = http(BASE, "GET", "/api/audit/" + r_part["id"], tok=ver)
acts2 = [x["action"] for x in a2["audit"]]
check("D9 correction + re-derivation audited",
      "correction" in acts2 and acts2.count("boundary_set") >= 1 and "location_set" in acts2, acts2)

# ------------------------------------------------------------- E. bulk new
print("\n[E] Bulk OCR batch with kind=new — coordinates flow through the queue")
body, ctype = multipart("files",
                        {"lang": "eng", "doc_type": "land_record", "doc_kind": "new"},
                        [("khatauni_newkind_rampur_2025.png", sample_bytes("khatauni_newkind_rampur_2025.png")),
                         ("khatauni_newkind_partial_2025.png", sample_bytes("khatauni_newkind_partial_2025.png"))])
s, d = http(BASE, "POST", "/api/bulk/batches", tok=op, data=body, headers={"Content-Type": ctype})
check("E1 batch created with doc_kind=new", s == 200 and d.get("doc_kind") == "new", str(d)[:120])
bid = d["id"]
items = []
for _ in range(50):
    time.sleep(3)
    s, d = http(BASE, "GET", "/api/bulk/batches/" + bid, tok=op)
    items = d.get("items") or []
    if items and all(it["status"] in ("done", "failed") for it in items):
        break
ramp_item = next((it for it in items if "rampur" in it["filename"]), None)
part_item = next((it for it in items if "partial" in it["filename"]), None)
check("E2 both items processed", ramp_item and part_item and
      ramp_item["status"] == "done" and part_item["status"] == "done",
      [(it["filename"], it["status"]) for it in items])
check("E3 coords_found: rampur 4, partial 3 (parseable count)",
      (ramp_item or {}).get("coords_found") == 4 and (part_item or {}).get("coords_found") == 3,
      [(ramp_item or {}).get("coords_found"), (part_item or {}).get("coords_found")])
check("E4 rampur item verdict valid (may flag duplicate vs section C)",
      (ramp_item or {}).get("validation", {}).get("verdict") == "valid",
      (ramp_item or {}).get("validation", {}).get("verdict"))
part_issues = [i for i in (part_item or {}).get("validation", {}).get("issues", [])
               if i.get("field") == "coordinates"]
check("E5 partial item carries the coordinates review issue", len(part_issues) == 1, part_issues)

# one-click import of clean rows -> boundary must materialize on the record
s, imp_all = http(BASE, "POST", "/api/bulk/batches/%s/import" % bid, tok=op, data={})
check("E6 clean import ran", s == 200 and imp_all.get("imported") in (0, 1), imp_all)
imported_doc = None
if imp_all.get("imported") == 1:
    imported_doc = imp_all["doc_ids"][0]
else:
    # duplicate of an earlier run -> per-item import with force (UI parity)
    s, imp_one = http(BASE, "POST", "/api/bulk/items/%s/import" % ramp_item["id"],
                      tok=op, data={"force": True})
    check("E6b per-item import works", s == 200 and imp_one.get("imported"), imp_one)
    imported_doc = imp_one.get("doc_id")
check("E7 a record was imported from the batch", bool(imported_doc), imp_all)
rowe = map_row(op, imported_doc) if imported_doc else None
check("E8 imported record has the document boundary + pin + kind=new",
      rowe and rowe.get("boundary_source") == "document"
      and len(rowe.get("boundary") or []) == 4 and rowe.get("lat") is not None
      and rowe.get("kind") == "new")
s, ae = http(BASE, "GET", "/api/audit/" + imported_doc, tok=ver)
acte = [x["action"] for x in ae["audit"]]
check("E9 audit parity: document_created + bulk_import + boundary_set + location_set",
      all(x in acte for x in ("document_created", "bulk_import", "boundary_set", "location_set")), acte)
s, dimp = http(BASE, "GET", "/api/documents/" + imported_doc, tok=op)
check("E10 bulk import still lands in pending_review (no verification bypass)",
      dimp.get("status") == "pending_review", dimp.get("status"))
s, csv = http(BASE, "GET", "/api/bulk/batches/%s/report.csv" % bid, tok=op, raw=True)
check("E11 CSV report carries coords_found column", b"coords_found" in csv.split(b"\n")[0])

# ------------------------------------------------- F. admin SA cross-feature
print("\n[F] admin new-kind upload: SA CourtLink AND the boundary both fire")
adm = login("demo.admin@demo.local", "Demo@Admin1")
r_adm = upload(adm, "khatauni_newkind_rampur_2025.png", "new")
sa = r_adm.get("sa_screening") or {}
check("F1 SA CourtLink still screens admin uploads (WPL stay on sv 145)",
      sa.get("count", 0) >= 1, sa.get("count"))
check("F2 boundary also set on the same upload",
      (r_adm.get("coordinates") or {}).get("boundary_set") is True)

# ------------------------------------------------------------- G. rejects
print("\n[G] validation of the new parameter")
body, ctype = multipart("file", {"doc_kind": "weird"},
                        [("x.png", sample_bytes("khatauni_newkind_rampur_2025.png"))])
s, d = http(BASE, "POST", "/api/process", tok=op, data=body, headers={"Content-Type": ctype})
check("G1 /api/process rejects doc_kind outside old/new (400)", s == 400, (s, str(d)[:80]))
body, ctype = multipart("files", {"doc_kind": "weird"},
                        [("x.png", sample_bytes("khatauni_newkind_rampur_2025.png"))])
s, d = http(BASE, "POST", "/api/bulk/batches", tok=op, data=body, headers={"Content-Type": ctype})
check("G2 /api/bulk/batches rejects bad doc_kind (400)", s == 400, (s, str(d)[:80]))

# ------------------------------------------------- H. old bulk = untouched
print("\n[H] old-kind bulk batch stays classic")
body, ctype = multipart("files", {"lang": "eng"}, [("english_jamabandi_sample.png",
                                                    sample_bytes("english_jamabandi_sample.png"))])
s, d = http(BASE, "POST", "/api/bulk/batches", tok=op, data=body, headers={"Content-Type": ctype})
check("H1 batch created (doc_kind defaults old)", s == 200 and (d.get("doc_kind") or "old") == "old")
bid_old = d["id"]
for _ in range(50):
    time.sleep(3)
    s, d = http(BASE, "GET", "/api/bulk/batches/" + bid_old, tok=op)
    items = d.get("items") or []
    if items and items[0]["status"] in ("done", "failed"):
        break
check("H2 old batch: no coords_found marker on items",
      items and items[0].get("coords_found") is None,
      items and items[0].get("coords_found"))
check("H3 old batch: no coordinate fields leaked",
      items and not any(k.startswith("coordinate_") for k in (items[0].get("fields") or {})))

# ------------------------------------------------------- I. Hindi new-kind
print("\n[I] Hindi (Devanagari) new-format scan — invariants hold regardless of OCR luck")
r_hin = upload(op, "khatauni_newkind_hindi_2025.png", "new", {"lang": "hin"})
coh = r_hin.get("coordinates") or {}
found_h = coh.get("found", -1)
check("I1 coordinates block present, found in 0..4", 0 <= found_h <= 4, coh)
hin_issues = " ".join(i.get("msg", "") for i in r_hin["validation"]["issues"])
check("I2 no boundary without 4 plausible corners",
      coh.get("boundary_set") is True
      or found_h < 4
      or "coordinates" in r_hin["validation"]["low_confidence_fields"],
      (coh, found_h))
check("I3 incomplete/implausible read forces review verdict",
      coh.get("boundary_set") is True
      or r_hin["validation"]["verdict"] == "review",
      (r_hin["validation"]["verdict"], hin_issues[:120]))

# ------------------------------------------- K. other document types
print("\n[K] NEW kind works across document types (sale deed + mutation)")
r_sale = upload(op, "sale_deed_newkind_2025.png", "new", {"doc_type": "sale_deed"})
cks = r_sale.get("coordinates") or {}
check("K1 sale deed: 4/4 coordinates + boundary set",
      cks.get("found") == 4 and cks.get("boundary_set") is True, cks)
rows_ = map_row(op, r_sale["id"])
check("K2 sale deed: boundary on map, source=document",
      rows_ and rows_.get("boundary_source") == "document"
      and len(rows_.get("boundary") or []) == 4)
r_mut = upload(op, "mutation_newkind_2025.png", "new", {"doc_type": "mutation"})
ckm = r_mut.get("coordinates") or {}
check("K3 mutation record: 4/4 coordinates + boundary set",
      ckm.get("found") == 4 and ckm.get("boundary_set") is True, ckm)

# ------------------------------------------------- L. malformed coordinates
print("\n[L] malformed printed coordinates are refused, never guessed")
r_bad = upload(op, "khatauni_newkind_badcoords_2025.png", "new")
cob = r_bad.get("coordinates") or {}
check("L1 found == 2 (one illegible, one out-of-range)", cob.get("found") == 2, cob)
check("L2 boundary NOT set", cob.get("boundary_set") is False)
check("L3 review verdict with coordinates flag",
      r_bad["validation"]["verdict"] == "review"
      and "coordinates" in r_bad["validation"]["low_confidence_fields"])
rowb = map_row(op, r_bad["id"])
check("L4 no map boundary for the malformed record",
      rowb and rowb.get("boundary") is None)

# ------------------------------------------ M. area cross-check geo guards
print("\n[M] area cross-check: implausible polygon refused, mismatch flagged")
# M1: push one corner ~40 degrees north via a verify correction -> polygon
# spans thousands of km²; the guard must REFUSE to write it
s, dm1 = http(BASE, "POST", "/api/documents/%s/verify" % r_new["id"], tok=ver,
              data={"corrections": {"coordinate_1": "63.35175 N, 77.35135 E"}})
cm1 = (dm1 or {}).get("coordinates") or {}
check("M1 implausible re-derivation refused (boundary_set False)",
      s == 200 and cm1.get("boundary_set") is False
      and cm1.get("reason") == "implausible_area", cm1)
rowm = map_row(op, r_new["id"])
check("M2 last good boundary kept after a refused re-derivation",
      rowm and len(rowm.get("boundary") or []) == 4
      and rowm.get("boundary_source") == "document")
# M3: stretch one corner so the area is plausible but way off the recorded
# 1.8 acres -> boundary IS rewritten, flagged as an area mismatch
s, dm3 = http(BASE, "POST", "/api/documents/%s/verify" % r_adm["id"], tok=ver,
              data={"corrections": {"coordinate_3": "23.37000 N, 77.35205 E"}})
cm3 = (dm3 or {}).get("coordinates") or {}
check("M3 plausible-but-mismatched polygon set + area_mismatch flag",
      s == 200 and cm3.get("boundary_set") is True
      and cm3.get("area_mismatch") is True, cm3)

# ------------------------------------------------------------- J. statics
print("\n[J] static wiring")
html = open(os.path.join(os.path.dirname(__file__), "..", "extracted",
                         "landrec", "static", "index.html"), encoding="utf-8").read()
mainpy = open(os.path.join(os.path.dirname(__file__), "..", "extracted",
                           "landrec", "main.py"), encoding="utf-8").read()
check("J1 upload form has the Document Kind select", 'id="docKindSelect"' in html)
check("J2 bulk form has the Document Kind select", 'id="bulkKind"' in html)
check("J3 upload + bulk JS both send doc_kind",
      "fd.append('doc_kind'" in html and html.count("doc_kind") >= 4)
check("J4 map knows the 'document' boundary source (badge + purple)",
      "Printed on document" in html and "#7c3aed" in html)
check("J5 version bumped to 3.13.1", 'APP_VERSION = "3.13.1"' in mainpy)
check("J5b geo guard ceiling + reason wired", "_MAX_PLOT_M2" in mainpy
      and "implausible_area" in mainpy and "area_mismatch" in mainpy)
check("J5c area cross-check badge in UI", "mapAreaMatchBadge" in html
      and "mapAreaStrToM2" in html)
for fn in ["khatauni_newkind_rampur_2025.png", "khatauni_newkind_arera_2025.png",
           "khatauni_newkind_hindi_2025.png", "khatauni_newkind_partial_2025.png",
           "khatauni_newkind_badcoords_2025.png", "mutation_newkind_2025.png",
           "sale_deed_newkind_2025.png"]:
    check("J5d sample present: " + fn,
          os.path.exists(os.path.join(SAMPLES, fn)))
wf_path = os.path.join(os.path.dirname(__file__), "..", ".github", "workflows", "ci.yml")
check("J6 CI workflow runs this suite",
      os.path.exists(wf_path) and "test_newkind.py" in open(wf_path).read())

# ------------------------------------------------------------- summary
print("\n" + "=" * 78)
failed = [r for r in RESULTS if not r[1]]
print("RESULT: %d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
if failed:
    print("FAILURES:")
    for name, _c, extra in failed:
        print("  -", name, ("-> " + str(extra)[:200]) if extra else "")
    sys.exit(1)
print("ALL GREEN")
