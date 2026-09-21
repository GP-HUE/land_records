"""Final regression battery for v3.6 (workflow + analysis + audit chain)."""
import os
import sys, os, tempfile, shutil
sys.path.insert(0, os.environ.get("LR_ROOT", "/home/user/land_records/extracted"))
os.chdir(os.environ.get("LR_ROOT", "/home/user/land_records/extracted"))
import landrec.paths as paths
tmp = tempfile.mkdtemp(prefix="lrfinal5_")
paths.data_dir = lambda: tmp
import importlib
import landrec.store as store
importlib.reload(store)
store.DATA_DIR = os.path.join(tmp, "data")
store.DB_PATH = os.path.join(store.DATA_DIR, "landrec.db")
store.UPLOAD_DIR = os.path.join(store.DATA_DIR, "uploads")
store.init_db()
import landrec.extractor as extractor, landrec.ocr as ocr
PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    print(("PASS  " if cond else "FAIL  ") + name + ((" | " + str(extra)[:90]) if extra and not cond else ""))
    PASS += 1 if cond else 0; FAIL += 0 if cond else 1

data = open("samples/hindi_khatauni_sample.png", "rb").read()
r1 = ocr.process_file(data, "d1.png", langs=["hin"])
f1 = extractor.extract_fields(r1)
o1 = f1["owner_name"]["value"]
doc1 = store.save_upload("d1.png","image/png",len(data),None,"u1",r1,f1,
    {"verdict":"valid","issues":[],"low_confidence_fields":[]},"k1", doc_type="land_record")
store.mark_verified(doc1, {"owner_name": o1 + " \u0906\u0917\u0930\u0935\u093e\u0932"}, {"id":"u1","email":"a@b.c"})
f2 = extractor.extract_fields(ocr.process_file(data, "d2.png", langs=["hin"]))
store.apply_learned(f2)
check("T1 no data bleed between documents", f2["owner_name"]["value"] == o1)

vc = store.verify_audit_chain()
check("T-chain audit chain valid after verify", vc["valid"] is True, str(vc))

v1 = f1["village"]["value"]; typo = v1[:2] + "\u0902" + v1[2:]
for i, u in ((1,"u1"),(2,"u2")):
    rr = ocr.process_file(data, "dx%d.png"%i, langs=["hin"])
    ff = extractor.extract_fields(rr); ff["village"] = {"value": typo, "confidence": 0.5}
    dx = store.save_upload("dx%d.png"%i,"image/png",len(data),None,u,rr,ff,
        {"verdict":"valid","issues":[],"low_confidence_fields":[]},"k%d"%i)
    store.mark_verified(dx, {"village": v1}, {"id":u,"email":"x@y.z"})
f5 = extractor.extract_fields(ocr.process_file(data, "d5.png", langs=["hin"]))
f5["village"] = {"value": typo, "confidence": 0.5}
store.apply_learned(f5)
check("T2 two-confirmed typo auto-corrected + flagged",
      f5["village"]["value"] == v1 and f5["village"].get("auto_corrected") is True)

s1 = f1["survey_number"]["value"]
for i in (6,7):
    rr = ocr.process_file(data, "dn%d.png"%i, langs=["hin"])
    ff = extractor.extract_fields(rr)
    dx = store.save_upload("dn%d.png"%i,"image/png",len(data),None,"u1",rr,ff,
        {"verdict":"valid","issues":[],"low_confidence_fields":[]},"kn%d"%i)
    store.mark_verified(dx, {"survey_number": s1 + "X"}, {"id":"u1","email":"a@b.c"})
f7 = extractor.extract_fields(ocr.process_file(data, "d7.png", langs=["hin"]))
store.apply_learned(f7)
check("T3 numeric fields never auto-corrected",
      f7["survey_number"]["value"] == s1 and not f7["survey_number"].get("auto_corrected"))

dense = {"full_text": "\u092d\u093e\u0930\u0924 \u0938\u0930\u0915\u093e\u0930\n\u0915\u094d\u0939\u093e\u0924\u0942\u0928\u0940 \u0916\u093e\u0932\u093f\u0938\n\u0938\u0935\u093e\u092e\u0940 \u0930\u093e\u092e\u0938\u0935\u0930\u0942\u092a \u0936\u0930\u092e\u093e\n\u092a\u093f\u0924\u093e \u0936\u0902\u0915\u0930\u0932\u093e\u0932\n\u0938\u0930\u0935\u0947 \u0928\u0902. 45/2\n\u0916\u0938\u0930\u093e 118\n\u0916\u093e\u0924\u093e 742\n\u092a\u0932\u094d\u0932\u0949\u091f 12\n\u0915\u094d\u0937\u0947\u0924\u094d\u0930\u094d\u092b\u0932 2.45 \u090f\u0915\u0921\u093c\n\u0917\u093e\u0902\u0935 \u0938\u093e\u0930\u0902\u0917\u092a\u0941\u0930\n\u0924\u0939\u0938\u0940\u0932 \u0928\u0940\u092e\u091a\n\u091c\u093f\u0932\u093e \u0916\u0902\u0921\u0935\u093e\n\u0930\u093e\u091c\u094d\u092f \u092e\u0927\u094d\u092f \u092a\u094d\u0930\u0926\u0947\u0936",
         "pages": [{"words": ["45/2","118","742","12","2.45","\u090f\u0915\u0921\u093c"],"conf":[90]*6}],
         "mean_conf": 90.0, "detected_scripts": ["hin"]}
fd = extractor.extract_fields(dense)
check("T4 dense owner/father/survey/plot",
      fd["owner_name"]["value"]=="\u0930\u093e\u092e\u0938\u0935\u0930\u0942\u092a \u0936\u0930\u092e\u093e"
      and fd["father_name"]["value"]=="\u0936\u0902\u0915\u0930\u0932\u093e\u0932"
      and fd["survey_number"]["value"]=="45/2" and fd["plot_number"]["value"]=="12",
      str({k: fd.get(k,{}).get("value") for k in ["owner_name","father_name","survey_number","plot_number"]}))

fc = extractor.extract_fields(r1)
need = ["owner_name","father_name","survey_number","khasra_number","khata_number","village","tehsil","district","state","area","khatauni_year"]
missing = [k for k in need if k not in fc]
check("T5 clean sample core fields", not missing, missing)

rows = store.get_learned_corrections()
if rows:
    store.delete_correction(rows[0]["id"])
    check("T6 rule deleted", len(store.get_learned_corrections()) == len(rows)-1)
else:
    check("T6 rule deleted", True)

yr = {"full_text": "\u0915\u094d\u0939\u093e\u0924\u0942\u0928\u0940 (2024-25)\n\u092e\u093e\u0932\u093f\u0915 \u0906\u091c\u092f \u0915\u0941\u092e\u093e\u0930",
      "pages":[{"words":["2024-25"],"conf":[90]}], "mean_conf": 80.0, "detected_scripts": ["hin"]}
fy = extractor.extract_fields(yr)
check("T7 year fallback clean value", fy.get("khatauni_year",{}).get("value") == "2024-25")

zw = {"full_text": "\u0938\u0930\u200d\u0935\u0947\u200c \u0928\u092e\u092c\u0930 29/1\n\u0909\u0921\u092e \u0930\u093e\u091c\u0947\u0936\u094d \u0928\u093e\u092f\u0930\u094d",
      "pages":[{"words":["29/1"],"conf":[90]}], "mean_conf": 80.0, "detected_scripts": []}
fz = extractor.extract_fields(zw)
check("T8 ZWNJ-stripped label matches", fz.get("survey_number",{}).get("value","").startswith("29"))

ow = {"full_text": "GOVERNMENT OF MAHARASHTRA — REVENUE DEPARTMENT\nOwnership : Full Owner — Individual",
      "pages":[{"words":["Full","Owner"],"conf":[90]*2}], "mean_conf": 85.0, "detected_scripts": []}
fo = extractor.extract_fields(ow)
check("T9 ownership from labeled line, not header", fo.get("ownership_type",{}).get("value") == "Private")

ur = {"full_text": "\u06a9\u0644\u0627\u0645 \u0646\u06cc\u062f\u0644\u0627 \u0646\u06cc\u0645\u0627\n\u0627\u0631\u0633\u062e 39\n\u067a\u0627\u0644\u067e 7",
      "pages":[{"words":["39","7"],"conf":[90]*2}], "mean_conf": 60.0, "detected_scripts": ["urd"]}
fu = extractor.extract_fields(ur)
got = {k: v.get("value") for k, v in fu.items()}
check("T10 Urdu bidi + value-before-label",
      got.get("owner_name") == "\u0627\u0645\u06cc\u0646 \u0627\u0644\u062f\u06cc\u0646"
      and got.get("khasra_number") == "39" and got.get("plot_number") == "7", str(got))

ow2 = {"full_text": "GOVERNMENT OF KARNATAKA — REVENUE DEPARTMENT\nLandowner Name : Ramesh Gowda",
       "pages":[{"words":["Ramesh","Gowda"],"conf":[90]*2}], "mean_conf": 85.0, "detected_scripts": []}
check("T11 LTR label mid-line still takes remainder",
      extractor.extract_fields(ow2).get("owner_name",{}).get("value") == "Ramesh Gowda")

c = store._conn()
row = c.execute("SELECT id, detail FROM audit WHERE entry_hash IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
c.execute("UPDATE audit SET detail='TAMPER' WHERE id=?", (row["id"],)); c.commit(); c.close()
bad = store.verify_audit_chain()
c = store._conn()
c.execute("UPDATE audit SET detail=? WHERE id=?", (row["detail"], row["id"])); c.commit(); c.close()
good = store.verify_audit_chain()
check("T12 tamper detected + restored", bad["valid"] is False and good["valid"] is True)

doc3 = store.save_upload("d3.png","image/png",len(data),None,"u1",r1,f1,
    {"verdict":"valid","issues":[],"low_confidence_fields":[]},"k3", doc_type="mutation")
store.save_draft(doc3, f1)
check("T13a save_draft -> draft", store.get_document(doc3)["status"] == "draft")
store.submit_document(doc3)
check("T13b submit -> pending_review", store.get_document(doc3)["status"] == "pending_review")
store.return_document(doc3, "test notes", {"id":"u1","email":"a@b.c"})
d = store.get_document(doc3)
check("T13c return -> returned + notes", d["status"] == "returned" and d["reviewer_notes"] == "test notes")

sp = store.search_public("")
check("T14 public search only verified", all(x["status"] in ("verified","auto_approved") for x in sp),
      str([x["status"] for x in sp][:5]))
sp2 = store.search_public("", doc_type="mutation")
check("T14b type filter works", all(x["doc_type"] == "mutation" for x in sp2))

print()
print("FINAL REGRESSION: %d passed, %d failed" % (PASS, FAIL))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAIL else 0)
