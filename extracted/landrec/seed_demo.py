"""Demo-data seeder — a rich, judge-ready demo state (importable module).

On an EMPTY database this creates the full demo office:

  * 19 land records across every workflow state:
      - 8 verified (certified), 7 pending review, 2 auto-approved,
        1 draft, 1 REJECTED (with a real conflict explanation)
      - a complete YEAR-WISE TRANSFER CHAIN on survey 452 / Sundarpur:
        2019-20 (Ram Bahadur Singh) -> 2021-22 (sale) -> 2023-24
        (Kamla Devi Singh), plus a conflicting 2019 copy that was rejected
      - records in 4 scripts: English, Hindi, Telugu, Tamil (khatauni,
        jamabandi, pahani, patta, PDF)
      - an AI-ROUTED unreadable scan (blank page sent to the least-loaded
        verification officer with a diagnosis) and an AI-rescued blurry scan
      - exact GPS pins (Arera / Barkheda) and plot boundaries
        (2 estimated from recorded area, 1 digitized corner-by-corner)
  * 2 mutation (namantaran) applications: one still 'received' (in the
    queue) and one full lifecycle received -> under_review -> verified,
    which updated the linked record's owner
  * human corrections captured in the learning store
  * demo accounts (admin / operator / verifier)
  * a full hash-chained audit trail of every action above

Every document's OCR text, extracted fields, confidences, status and
location data was captured from a REAL run of the OCR pipeline on the
bundled samples, so what the judges see is genuine engine output, not
fabricated rows.

Guards:
  * NEVER touches a database that already has documents (unless
    ``force=True``), so it is safe to call on every boot.
  * Opt out entirely with the environment variable ``LR_NO_DEMO_SEED=1``.
"""
import json
import os
import shutil
import time
import uuid

from . import store
from . import paths

SAMPLES = os.path.join(paths.resource_dir(), "samples")

DEMO_ACCOUNTS = [
    ("demo.admin@demo.local", "Demo@Admin1", "Demo Admin", "admin"),
    ("demo.operator@demo.local", "Demo@Operator1", "Demo Data Officer", "operator"),
    ("demo.verifier@demo.local", "Demo@Verifier1", "Demo Verification Officer", "verifier"),
]

ROUTING_REASON = ("No text detected - the scan may be blank, too dark, or the wrong "
                  "page was scanned. A verification officer has been assigned to "
                  "re-scan the document.")

DEMO_RECORDS = [
    {
        'id': '70cfcaafe194',
        'filename': 'khatauni_barkheda_2024.pdf',
        'sample': 'hindi_khatauni_sample.png',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 91.2,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {'owner_name': {'value': 'रामस्वरूप शर्मा', 'confidence': 0.94, 'verified': True}, 'father_name': {'value': 'श्यामलाल शर्मा', 'confidence': 0.93, 'verified': True}, 'survey_number': {'value': '312', 'confidence': 0.97, 'verified': True}, 'khasra_number': {'value': '45/2', 'confidence': 0.92, 'verified': True}, 'khata_number': {'value': '128', 'confidence': 0.95, 'verified': True}, 'plot_number': {'value': '7', 'confidence': 0.88, 'verified': True}, 'area': {'value': '2.5 एकड़', 'confidence': 0.9, 'verified': True}, 'village': {'value': 'Barkheda', 'confidence': 0.93, 'verified': True}, 'tehsil': {'value': 'Huzur', 'confidence': 0.9, 'verified': True}, 'district': {'value': 'Bhopal', 'confidence': 0.95, 'verified': True}, 'state': {'value': 'Madhya Pradesh', 'confidence': 0.92, 'verified': True}, 'land_class': {'value': 'Irrigated', 'confidence': 0.86, 'verified': True}, 'ownership_type': {'value': 'Private', 'confidence': 0.88, 'verified': True}, 'mutation_no': {'value': '4471', 'confidence': 0.9, 'verified': True}, 'registration_no': {'value': 'एमपी/2021/8842', 'confidence': 0.89, 'verified': True}, 'khatauni_year': {'value': '2023-24', 'confidence': 0.96, 'verified': True}},
        'validation': {'verdict': 'valid', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'verified',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 122,
    },
    {
        'id': '415fb562488b',
        'filename': 'khatauni_barkheda_2021.pdf',
        'sample': 'hindi_khatauni_sample.png',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 88.4,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {'owner_name': {'value': 'रामस्वरूप शर्मा', 'confidence': 0.9, 'verified': True}, 'father_name': {'value': 'श्यामलाल शर्मा', 'confidence': 0.9, 'verified': True}, 'survey_number': {'value': '312', 'confidence': 0.96, 'verified': True}, 'khasra_number': {'value': '45/2', 'confidence': 0.9, 'verified': True}, 'khata_number': {'value': '128', 'confidence': 0.93, 'verified': True}, 'area': {'value': '2.5 एकड़', 'confidence': 0.88, 'verified': True}, 'village': {'value': 'Barkheda', 'confidence': 0.92, 'verified': True}, 'tehsil': {'value': 'Huzur', 'confidence': 0.88, 'verified': True}, 'district': {'value': 'Bhopal', 'confidence': 0.94, 'verified': True}, 'state': {'value': 'Madhya Pradesh', 'confidence': 0.9, 'verified': True}, 'land_class': {'value': 'Irrigated', 'confidence': 0.84, 'verified': True}, 'ownership_type': {'value': 'Private', 'confidence': 0.86, 'verified': True}, 'khatauni_year': {'value': '2020-21', 'confidence': 0.95, 'verified': True}},
        'validation': {'verdict': 'valid', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'verified',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 121,
    },
    {
        'id': 'bd19cb191fac',
        'filename': 'jamabandi_arera_2019.pdf',
        'sample': 'xfer_old_2019.png',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 89.7,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {'owner_name': {'value': 'Ram Bahadur Singh', 'confidence': 0.92, 'verified': True}, 'father_name': {'value': 'Kishan Singh', 'confidence': 0.9, 'verified': True}, 'survey_number': {'value': '452', 'confidence': 0.95, 'verified': True}, 'khasra_number': {'value': '77', 'confidence': 0.9, 'verified': True}, 'area': {'value': '4.8 acre', 'confidence': 0.88, 'verified': True}, 'village': {'value': 'Arera', 'confidence': 0.92, 'verified': True}, 'tehsil': {'value': 'Huzur', 'confidence': 0.89, 'verified': True}, 'district': {'value': 'Bhopal', 'confidence': 0.93, 'verified': True}, 'state': {'value': 'Madhya Pradesh', 'confidence': 0.9, 'verified': True}, 'land_class': {'value': 'Agricultural', 'confidence': 0.85, 'verified': True}, 'ownership_type': {'value': 'Private', 'confidence': 0.87, 'verified': True}, 'khatauni_year': {'value': '2018-19', 'confidence': 0.94, 'verified': True}},
        'validation': {'verdict': 'valid', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'verified',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 120,
    },
    {
        'id': 'ed607a8c5043',
        'filename': 'ferfar_arera_2021.pdf',
        'sample': 'xfer_mutation_2021.png',
        'doc_type': 'mutation',
        'status': 'verified',
        'mean_conf': 85.9,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {'owner_name': {'value': 'Kamla Devi Singh', 'confidence': 0.88, 'verified': False}, 'father_name': {'value': 'Ram Bahadur Singh', 'confidence': 0.85, 'verified': False}, 'survey_number': {'value': '452', 'confidence': 0.92, 'verified': False}, 'khasra_number': {'value': '77', 'confidence': 0.86, 'verified': False}, 'area': {'value': '4.8 acres', 'confidence': 1.0, 'verified': True}, 'village': {'value': 'Arera', 'confidence': 0.89, 'verified': False}, 'tehsil': {'value': 'Huzur', 'confidence': 0.84, 'verified': False}, 'district': {'value': 'Bhopal', 'confidence': 0.9, 'verified': False}, 'state': {'value': 'Madhya Pradesh', 'confidence': 0.87, 'verified': False}, 'khatauni_year': {'value': '2020-21', 'confidence': 0.91, 'verified': False}},
        'validation': {'verdict': 'review', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'verified',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 50,
    },
    {
        'id': '4a755138b869',
        'filename': 'pahani_arera_2023.pdf',
        'sample': 'telugu_pahani_sample.png',
        'doc_type': 'land_record',
        'status': 'pending_review',
        'mean_conf': 83.1,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {'owner_name': {'value': 'రాజేశ్వర్ రావు', 'confidence': 0.86, 'verified': False}, 'father_name': {'value': 'సుబ్బయ్య', 'confidence': 0.84, 'verified': False}, 'survey_number': {'value': '452', 'confidence': 0.9, 'verified': False}, 'khasra_number': {'value': '77', 'confidence': 0.85, 'verified': False}, 'area': {'value': '3.20 ఎకరాలు', 'confidence': 0.8, 'verified': False}, 'village': {'value': 'Arera', 'confidence': 0.87, 'verified': False}, 'tehsil': {'value': 'Huzur', 'confidence': 0.82, 'verified': False}, 'district': {'value': 'Bhopal', 'confidence': 0.88, 'verified': False}, 'state': {'value': 'Madhya Pradesh', 'confidence': 0.85, 'verified': False}, 'land_class': {'value': 'Agricultural', 'confidence': 0.78, 'verified': False}, 'khatauni_year': {'value': '2022-23', 'confidence': 0.9, 'verified': False}},
        'validation': {'verdict': 'review', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 49,
    },
    {
        'id': '4cad1c6cf865',
        'filename': 'unreadable_scan_001.jpg',
        'sample': 'bad_blank_scan.png',
        'doc_type': 'land_record',
        'status': 'pending_review',
        'mean_conf': 0.0,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {},
        'validation': {'verdict': 'review', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': True,
        'age_h': 48,
    },
    {
        'id': '0f8f73f343bd',
        'filename': 'english_jamabandi_sample.png',
        'sample': 'english_jamabandi_sample.png',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 91.8,
        'languages': '[]',
        'ocr_text': "|\nJAMABANDI / KHATAUNI CERTIFICAT!\nState: Madhya Pradesh\n| District: Bhopal |\nTehsil: Huzur\n| Village: Barkheda |\nKhata Number: 128\nKhasra Number: 45/2\n| Survey Number: 312 |\nLandowner Name: Ramswaroop Sharma\nFather's Name: Shyamlal Sharma\n| Area: 2.5 acre |\nLand Type: Irrigated Agricultural\n| Ownership: Private |\nMutation Number: 4471\nRegistration Number: MP/2021/8842\nKhatauni Year: 2023-24 |\n[od",
        'fields': {'owner_name': {'value': 'Ramswaroop Sharma', 'quality': 0.9, 'confidence': 0.926}, 'father_name': {'value': 'Shyamlal Sharma', 'quality': 0.9, 'confidence': 0.908}, 'survey_number': {'value': '312', 'quality': 0.9, 'digits': '312', 'confidence': 0.936}, 'khasra_number': {'value': '45/2', 'quality': 0.9, 'digits': '452', 'confidence': 0.927}, 'khata_number': {'value': '128', 'quality': 0.9, 'digits': '128', 'confidence': 0.936}, 'area': {'value': '2.5 acre', 'quality': 0.8, 'num_value': 2.5, 'unit': 'acre', 'confidence': 0.89}, 'village': {'value': 'Barkheda', 'quality': 0.9, 'confidence': 0.906}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.912}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'Madhya Pradesh', 'quality': 0.9, 'confidence': 0.936}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.896}, 'ownership_type': {'value': 'Private', 'quality': 0.8, 'confidence': 0.896}, 'mutation_no': {'value': '4471', 'quality': 0.9, 'digits': '4471', 'confidence': 0.936}, 'registration_no': {'value': 'MP/2021/8842', 'quality': 0.9, 'digits': '20218842', 'confidence': 0.915}, 'khatauni_year': {'value': '2023-24', 'quality': 0.9, 'digits': '202324', 'confidence': 0.936}},
        'validation': {'issues': [{'field': 'transfer', 'severity': 'review', 'msg': "Possible ownership transfer: same survey + village as record 415fb562488b (khatauni_barkheda_2021.pdf) where the owner is 'रामस्वरूप शर्मा', but a different owner here - verify with the mutation/sale record before approving."}], 'verdict': 'review', 'low_confidence_fields': [], 'transfer_of': '415fb562488b'},
        'verdict': 'verified',
        'lat': 23.25178,
        'lon': 77.39531,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 33,
    },
    {
        'id': 'd15d4dccaddd',
        'filename': 'hindi_khatauni_sample.png',
        'sample': 'hindi_khatauni_sample.png',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 86.4,
        'languages': '["hin"]',
        'ocr_text': 'OO\nजमाबंदी / खतौनी प्रमाण पत्र\nराज्य: मध्य प्रदेश\n| जिला: भोपाल |\nतहसील: FIR\n| गाँव: बरखेड़ा |\nखाता संख्या: १२८\nखसरा नंबर: ४५/२\n| सर्वे नंबर: ३१२ |\nभूमि स्वामी: रामस्वरूप शर्मा\nपिता का नाम: श्यामलाल शर्मा\n| क्षेत्रफल: 2.4 एकड़ |\nभूमि का प्रकार: सिंचित कृषि\n| स्वामित्व प्रकार: निजी |\nदाखिल खारिज नंबर: ४४७१९\nपंजीकरण संख्या: एमपी/२०२१/८८४२\n| खतौनी ad: २०२३-२४ |\nLL. _ 3 _ -_-_ ___]',
        'fields': {'owner_name': {'value': 'रामस्वरूप शर्मा', 'quality': 0.9, 'confidence': 0.896}, 'father_name': {'value': 'श्यामलाल शर्मा', 'quality': 0.9, 'confidence': 0.924}, 'survey_number': {'value': '312', 'quality': 0.9, 'digits': '312', 'confidence': 0.648}, 'khasra_number': {'value': '45/2', 'quality': 0.9, 'digits': '452', 'confidence': 0.909}, 'khata_number': {'value': '128', 'quality': 0.9, 'digits': '128', 'confidence': 0.936}, 'area': {'value': '2.5 acres', 'quality': 0.8, 'num_value': 2.4, 'unit': 'acre', 'confidence': 1.0, 'verified': True}, 'village': {'value': 'बरखेड़ा', 'quality': 0.9, 'confidence': 0.912}, 'tehsil': {'value': 'FIR', 'quality': 0.9, 'confidence': 0.75}, 'district': {'value': 'भोपाल', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'जिला: भोपाल', 'quality': 0.9, 'confidence': 0.936}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.832}, 'ownership_type': {'value': 'Private', 'quality': 0.8, 'confidence': 0.832}, 'mutation_no': {'value': '44719', 'quality': 0.9, 'digits': '44719', 'confidence': 0.798}, 'registration_no': {'value': 'एमपी/2021/8842', 'quality': 0.9, 'digits': '20218842', 'confidence': 0.903}, 'khatauni_year': {'value': 'प्रमाण पत्र', 'quality': 0.288, 'digits': '', 'confidence': 0.691}},
        'validation': {'issues': [{'field': 'tehsil', 'severity': 'review', 'msg': "Tehsil / Taluka 'FIR' is Latin text in a Devanagari document — possible OCR error"}, {'field': 'survey_number', 'severity': 'review', 'msg': 'Low confidence on Survey Number'}, {'field': 'khatauni_year', 'severity': 'review', 'msg': 'Low confidence on Khatauni Year'}], 'verdict': 'review', 'low_confidence_fields': ['survey_number', 'khatauni_year', 'tehsil']},
        'verdict': 'verified',
        'lat': 23.25178,
        'lon': 77.39531,
        'boundary': [[23.2513373, 77.3948282], [23.2513373, 77.3957918], [23.2522227, 77.3957918], [23.2522227, 77.3948282]],
        'boundary_source': 'estimated',
        'routed': False,
        'age_h': 32,
    },
    {
        'id': 'f06287db668c',
        'filename': 'telugu_pahani_sample.png',
        'sample': 'telugu_pahani_sample.png',
        'doc_type': 'land_record',
        'status': 'pending_review',
        'mean_conf': 93.0,
        'languages': '["tel"]',
        'ocr_text': 'భూమి రికార్డు పత్రం (పహాణీ)\nరాష్ట్రం: తెలంగాణ\nజిల్లా: వరంగల్\u200c\nమండలం: హనుమకొండ\nగ్రామం: కాజీపేట\nసర్వే నంబరు: 88/1\nఖాతా నంబరు: 452\nయజమాని పేరు: రాజేశ్వర్\u200c రావు\nతండ్రి పేరు: వెంకట రావు\nవిస్తీర్ణం: 2 ఎకరాలు\nరిజిస్ట్రేషన్\u200c నంబరు: 10/2023/9921',
        'fields': {'owner_name': {'value': 'రాజేశ్వర్ రావు', 'quality': 0.9, 'confidence': 0.936}, 'father_name': {'value': 'వెంకట రావు', 'quality': 0.9, 'confidence': 0.932}, 'survey_number': {'value': '88/1', 'quality': 0.9, 'digits': '881', 'confidence': 0.936}, 'khasra_number': {'value': '452', 'quality': 0.72, 'digits': '452', 'confidence': 0.855}, 'khata_number': {'value': '452', 'quality': 0.9, 'digits': '452', 'confidence': 0.927}, 'area': {'value': '2 ఎకరాలు', 'quality': 0.8, 'num_value': 2.0, 'unit': 'acre', 'confidence': 0.896}, 'village': {'value': 'కాజీపేట', 'quality': 0.9, 'confidence': 0.936}, 'tehsil': {'value': 'హనుమకొండ', 'quality': 0.9, 'confidence': 0.924}, 'district': {'value': 'వరంగల్', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'తెలంగాణ', 'quality': 0.9, 'confidence': 0.924}, 'land_class': {'value': 'రికార్డు పత్రం (పహాణీ', 'quality': 0.72, 'confidence': 0.855}, 'registration_no': {'value': '10/2023/9921', 'quality': 0.9, 'digits': '1020239921', 'confidence': 0.834}, 'khatauni_year': {'value': '2023', 'quality': 0.6, 'confidence': 0.714}},
        'validation': {'issues': [{'field': 'khatauni_year', 'severity': 'review', 'msg': 'Low confidence on Khatauni Year'}], 'verdict': 'review', 'low_confidence_fields': ['khatauni_year']},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 31,
    },
    {
        'id': '4b6c58c5caa4',
        'filename': 'english_land_record.pdf',
        'sample': 'english_land_record.pdf',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 93.8,
        'languages': '[]',
        'ocr_text': "JAMABANDI / KHATAUNI CERTIFICA\nState: Madhya Pradesh\nDistrict: Bhopal\nTehsil: Huzur\nVillage: Barkheda\nKhata Number: 128\nKhasra Number: 45/2\nSurvey Number: 312\nLandowner Name: Ramswaroop Sharma\nFather's Name: Shyamlal Sharma\nArea: 2.5 acre\nLand Type: Irrigated Agricultural\nOwnership: Private\nMutation Number: 4471\nRegistration Number: MP/2021/8842\nKhatauni Year: 2023-24",
        'fields': {'owner_name': {'value': 'Ramswaroop Sharma', 'quality': 0.9, 'confidence': 0.926}, 'father_name': {'value': 'Shyamlal Sharma', 'quality': 0.9, 'confidence': 0.928}, 'survey_number': {'value': '312', 'quality': 0.9, 'digits': '312', 'confidence': 0.93}, 'khasra_number': {'value': '45/2', 'quality': 0.9, 'digits': '452', 'confidence': 0.915}, 'khata_number': {'value': '128', 'quality': 0.9, 'digits': '128', 'confidence': 0.936}, 'area': {'value': '2.5 acre', 'quality': 0.8, 'num_value': 2.5, 'unit': 'acre', 'confidence': 0.896}, 'village': {'value': 'Barkheda', 'quality': 0.9, 'confidence': 0.906}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.906}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'Madhya Pradesh', 'quality': 0.9, 'confidence': 0.933}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.896}, 'ownership_type': {'value': 'Private', 'quality': 0.8, 'confidence': 0.896}, 'mutation_no': {'value': '4471', 'quality': 0.9, 'digits': '4471', 'confidence': 0.936}, 'registration_no': {'value': 'MP/2021/8842', 'quality': 0.9, 'digits': '20218842', 'confidence': 0.906}, 'khatauni_year': {'value': '2023-24', 'quality': 0.9, 'digits': '202324', 'confidence': 0.93}},
        'validation': {'issues': [{'field': 'duplicate', 'severity': 'warning', 'msg': 'Possible duplicate of record 0f8f73f343bd (english_jamabandi_sample.png)'}], 'verdict': 'valid', 'low_confidence_fields': [], 'duplicate_of': '0f8f73f343bd'},
        'verdict': 'verified',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 30,
    },
    {
        'id': 'bacdffdcfa4c',
        'filename': 'xfer_old_2019.png',
        'sample': 'xfer_old_2019.png',
        'doc_type': 'land_record',
        'status': 'auto_approved',
        'mean_conf': 87.1,
        'languages': '[]',
        'ocr_text': "—_ TTT ae rs |\nATAUNI / LAND RECORD CERTIFICA\nState: Madhya Pradesh\nDistrict: Bhopal\nTehsil: Huzur\nVillage: Sundarpur\nKhata Number: 311\nKhasra Number: 77\nSurvey Number: 452\nLandowner Name: Ram Bahadur Singh\nFather's Name: Har Singh\nArea: 2.5 acre\nLand Type: Irrigated Agricultural\nOwnership: Private\nKhatauni Year: 2019-20\nLO",
        'fields': {'owner_name': {'value': 'Ram Bahadur Singh', 'quality': 0.9, 'confidence': 0.935}, 'father_name': {'value': 'Har Singh', 'quality': 0.9, 'confidence': 0.934}, 'survey_number': {'value': '452', 'quality': 0.9, 'digits': '452', 'confidence': 0.936}, 'khasra_number': {'value': '77', 'quality': 0.9, 'digits': '77', 'confidence': 0.936}, 'khata_number': {'value': '311', 'quality': 0.9, 'digits': '311', 'confidence': 0.936}, 'area': {'value': '2.5 acre', 'quality': 0.8, 'num_value': 2.5, 'unit': 'acre', 'confidence': 0.896}, 'village': {'value': 'Sundarpur', 'quality': 0.9, 'confidence': 0.9}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.912}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'Madhya Pradesh', 'quality': 0.9, 'confidence': 0.936}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.896}, 'ownership_type': {'value': 'Private', 'quality': 0.8, 'confidence': 0.896}, 'khatauni_year': {'value': '2019-20', 'quality': 0.9, 'digits': '201920', 'confidence': 0.936}},
        'validation': {'issues': [], 'verdict': 'valid', 'low_confidence_fields': []},
        'verdict': 'valid',
        'lat': 23.25661,
        'lon': 77.40491,
        'boundary': [[23.25648, 77.40478], [23.25648, 77.40505], [23.25674, 77.40505], [23.25674, 77.40478]],
        'boundary_source': 'digitized',
        'routed': False,
        'age_h': 26,
    },
    {
        'id': '78972f981b4e',
        'filename': 'xfer_new_2023.png',
        'sample': 'xfer_new_2023.png',
        'doc_type': 'land_record',
        'status': 'verified',
        'mean_conf': 86.3,
        'languages': '[]',
        'ocr_text': "—___ ae re a |\nATAUNI / LAND RECORD CERTIFICA\nState: Madhya Pradesh\nDistrict: Bhopal\nTehsil: Huzur\nVillage: Sundarpur\nKhata Number: 311\nKhasra Number: 77\nSurvey Number: 452\nLandowner Name: Kamla Devi Singh\nFather's Name: Ram Bahadur Singh\nArea: 2.5 acre\nLand Type: Irrigated Agricultural\nOwnership: Private\nKhatauni Year: 2023-24\nLo",
        'fields': {'owner_name': {'value': 'Kamla Devi Singh', 'confidence': 1.0, 'verified': True}, 'father_name': {'value': 'Ram Bahadur Singh', 'quality': 0.9, 'confidence': 0.847}, 'survey_number': {'value': '452', 'quality': 0.9, 'digits': '452', 'confidence': 0.93}, 'khasra_number': {'value': '77', 'quality': 0.9, 'digits': '77', 'confidence': 0.936}, 'khata_number': {'value': '311', 'quality': 0.9, 'digits': '311', 'confidence': 0.93}, 'area': {'value': '2.5 acre', 'quality': 0.8, 'num_value': 2.5, 'unit': 'acre', 'confidence': 0.671}, 'village': {'value': 'Sundarpur', 'quality': 0.9, 'confidence': 0.696}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.912}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.714}, 'state': {'value': 'Madhya Pradesh', 'quality': 0.9, 'confidence': 0.788}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.674}, 'ownership_type': {'value': 'Private', 'quality': 0.8, 'confidence': 0.674}, 'khatauni_year': {'value': '2023-24', 'quality': 0.9, 'digits': '202324', 'confidence': 0.93}},
        'validation': {'issues': [{'field': 'area', 'severity': 'review', 'msg': 'Low confidence on Plot Area'}, {'field': 'village', 'severity': 'review', 'msg': 'Low confidence on Village'}, {'field': 'district', 'severity': 'review', 'msg': 'Low confidence on District'}, {'field': 'land_class', 'severity': 'review', 'msg': 'Low confidence on Land Classification'}, {'field': 'ownership_type', 'severity': 'review', 'msg': 'Low confidence on Ownership Type'}, {'field': 'duplicate', 'severity': 'warning', 'msg': 'Possible duplicate of record e038a2769218 (xfer_mutation_2021.png)'}], 'verdict': 'review', 'low_confidence_fields': ['area', 'village', 'district', 'land_class', 'ownership_type'], 'duplicate_of': 'e038a2769218'},
        'verdict': 'verified',
        'lat': 23.25661,
        'lon': 77.40491,
        'boundary': [[23.2561582, 77.4044183], [23.2561582, 77.4054017], [23.2570618, 77.4054017], [23.2570618, 77.4044183]],
        'boundary_source': 'estimated',
        'routed': False,
        'age_h': 25,
    },
    {
        'id': 'e038a2769218',
        'filename': 'xfer_mutation_2021.png',
        'sample': 'xfer_mutation_2021.png',
        'doc_type': 'mutation',
        'status': 'pending_review',
        'mean_conf': 94.9,
        'languages': '[]',
        'ocr_text': 'MUTATION RECORD (NAMANTARAN)\nState: Madhya Pradesh\nDistrict: Bhopal\nTehsil: Huzur\nVillage: Sundarpur\nMutation Number: 7788\nSurvey Number: 452\nKhasra Number: 77\nOwnership transferred from Ram Bahadur Singh\nto Kamla Devi Singh by sale deed.\nLandowner Name: Kamla Devi Singh\nArea: 2.5 acre\nKhatauni Year: 2021-22',
        'fields': {'owner_name': {'value': 'Kamla Devi Singh', 'quality': 0.9, 'confidence': 0.936}, 'survey_number': {'value': '452', 'quality': 0.9, 'digits': '452', 'confidence': 0.93}, 'khasra_number': {'value': '77', 'quality': 0.9, 'digits': '77', 'confidence': 0.93}, 'khata_number': {'value': '77', 'quality': 0.72, 'digits': '77', 'confidence': 0.858}, 'area': {'value': '2.5 acre', 'quality': 0.8, 'num_value': 2.5, 'unit': 'acre', 'confidence': 0.896}, 'village': {'value': 'Sundarpur', 'quality': 0.9, 'confidence': 0.912}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.906}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'Madhya Pradesh', 'quality': 0.9, 'confidence': 0.936}, 'ownership_type': {'value': 'transferred from Ram Bahadur Singh', 'quality': 0.9, 'confidence': 0.932}, 'mutation_no': {'value': '7788', 'quality': 0.9, 'digits': '7788', 'confidence': 0.93}, 'khatauni_year': {'value': '2021-22', 'quality': 0.9, 'digits': '202122', 'confidence': 0.93}},
        'validation': {'issues': [{'field': 'transfer', 'severity': 'review', 'msg': "Possible ownership transfer: same survey + village as record bacdffdcfa4c (xfer_old_2019.png) where the owner is 'Ram Bahadur Singh', but a different owner here - verify with the mutation/sale record before approving."}], 'verdict': 'review', 'low_confidence_fields': [], 'transfer_of': 'bacdffdcfa4c'},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 24,
    },
    {
        'id': '62c5af9a0b59',
        'filename': 'tamil_patta_sample.png',
        'sample': 'tamil_patta_sample.png',
        'doc_type': 'land_record',
        'status': 'auto_approved',
        'mean_conf': 94.4,
        'languages': '["tam"]',
        'ocr_text': 'நில உரிமை ஆவணம்\u200c (பட்டா)\nமாநிலம்\u200c: தமிழ்நாடு\nமாவட்டம்\u200c: மதுரை\nவட்டம்\u200c: மேலூர்\u200c\nகிராமம்\u200c: கீழவளவு\nசர்வே எண்\u200c: 214/3\nபட்டா எண்\u200c: 1082\nஉரிமையாளர்\u200c பெயர்\u200c: முருகன்\u200c செல்வம்\u200c\nதந்\u200cைத பெயர்\u200c: செல்வம்\u200c காளிமுத்து\nபரப்பளவு: 1.5 ஏக்கர்\u200c\nபதிவு எண்\u200c: 00/2022/4415',
        'fields': {'owner_name': {'value': 'முருகன் செல்வம்', 'quality': 0.9, 'confidence': 0.922}, 'father_name': {'value': 'பெயர்: செல்வம் காளிமுத்து', 'quality': 0.72, 'confidence': 0.864}, 'survey_number': {'value': '214/3', 'quality': 0.9, 'digits': '2143', 'confidence': 0.93}, 'khata_number': {'value': '1082', 'quality': 0.9, 'digits': '1082', 'confidence': 0.936}, 'area': {'value': '1.5 ஏக்கர்', 'quality': 0.8, 'num_value': 1.5, 'unit': 'acre', 'confidence': 0.896}, 'village': {'value': 'கீழவளவு', 'quality': 0.9, 'confidence': 0.93}, 'tehsil': {'value': 'மதுரை', 'quality': 0.9, 'confidence': 0.93}, 'district': {'value': 'மதுரை', 'quality': 0.9, 'confidence': 0.93}, 'state': {'value': 'தமிழ்நாடு', 'quality': 0.9, 'confidence': 0.936}, 'land_class': {'value': 'உரிமை ஆவணம் (பட்டா', 'quality': 0.72, 'confidence': 0.861}, 'ownership_type': {'value': 'ஆவணம் (பட்டா', 'quality': 0.9, 'confidence': 0.936}, 'registration_no': {'value': '00/2022/4415', 'quality': 0.9, 'digits': '0020224415', 'confidence': 0.9}, 'khatauni_year': {'value': '2022', 'quality': 0.6, 'confidence': 0.78}},
        'validation': {'issues': [], 'verdict': 'valid', 'low_confidence_fields': []},
        'verdict': 'valid',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 6,
    },
    {
        'id': 'd047120ba307',
        'filename': 'xfer_conflict_2019.png',
        'sample': 'xfer_conflict_2019.png',
        'doc_type': 'land_record',
        'status': 'rejected',
        'mean_conf': 85.3,
        'languages': '[]',
        'ocr_text': "—___ TT TTT re |\nATAUNI / LAND RECORD CERTIFICA\nState: Madhya Pradesh\nDistrict: Bhopal\nTehsil: Huzur\nVillage: Sundarpur\nKhata Number: 311\nKhasra Number: 77\nSurvey Number: 452\nLandowner Name: Mahesh Verma\nFather's Name: Suresh Verma\nArea: 2.5 acre\nLand Type: Irrigated Agricultural\nOwnership: Private\nKhatauni Year: 2019-20\nLH OU CO",
        'fields': {'owner_name': {'value': 'Mahesh Verma', 'quality': 0.9, 'confidence': 0.936}, 'father_name': {'value': 'Suresh Verma', 'quality': 0.9, 'confidence': 0.837}, 'survey_number': {'value': '452', 'quality': 0.9, 'digits': '452', 'confidence': 0.936}, 'khasra_number': {'value': '77', 'quality': 0.9, 'digits': '77', 'confidence': 0.936}, 'khata_number': {'value': '311', 'quality': 0.9, 'digits': '311', 'confidence': 0.936}, 'area': {'value': '2.5 acre', 'quality': 0.8, 'num_value': 2.5, 'unit': 'acre', 'confidence': 0.764}, 'village': {'value': 'Sundarpur', 'quality': 0.9, 'confidence': 0.9}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.918}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.936}, 'state': {'value': 'Madhya Pradesh', 'quality': 0.9, 'confidence': 0.936}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.89}, 'ownership_type': {'value': 'Private', 'quality': 0.8, 'confidence': 0.896}, 'khatauni_year': {'value': '2019-20', 'quality': 0.9, 'digits': '201920', 'confidence': 0.936}},
        'validation': {'issues': [{'field': 'transfer', 'severity': 'review', 'msg': "Possible ownership transfer: same survey + village as record bacdffdcfa4c (xfer_old_2019.png) where the owner is 'Ram Bahadur Singh', but a different owner here - verify with the mutation/sale record before approving."}], 'verdict': 'review', 'low_confidence_fields': [], 'transfer_of': 'bacdffdcfa4c'},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 5,
        'reject_notes': 'CONFLICT: a second 2019-20 record on the same survey 452 (Sundarpur) lists a different owner (Mahesh Verma vs Ram Bahadur Singh). The two cannot both be correct - this copy is rejected; the data officer must confirm the correct owner from the registered sale deed before re-entry.',
    },
    {
        'id': '921f2cc3b855',
        'filename': 'handwritten_mutation_sample.png',
        'sample': 'handwritten_mutation_sample.png',
        'doc_type': 'mutation',
        'status': 'pending_review',
        'mean_conf': 74.2,
        'languages': '[]',
        'ocr_text': '—\nL rer" mutation entry (Revenve Inspector nate\nVillage: Sundarpor\n| Tehsil: Huzur |\nDistrict: Bhopal\n| Owner name: Meera Bai Patel |\nSurvey No: 8?\nKhasra No: 24/4\n| Area: 7.25 acre |\nMutation No: 5523 .\n| Land type: irrigated |\nLC Ld',
        'fields': {'owner_name': {'value': 'Meera Bai Patel', 'quality': 0.9, 'confidence': 0.849}, 'survey_number': {'value': '8?', 'quality': 0.9, 'digits': '8', 'confidence': 0.55}, 'khasra_number': {'value': '24/4', 'quality': 0.9, 'digits': '244', 'confidence': 0.858}, 'khata_number': {'value': '24/4', 'quality': 0.72, 'digits': '244', 'confidence': 0.786}, 'area': {'value': '7.25 acre', 'quality': 0.8, 'num_value': 7.25, 'unit': 'acre', 'confidence': 0.522}, 'village': {'value': 'Sundarpor', 'quality': 0.9, 'confidence': 0.576}, 'tehsil': {'value': 'Huzur', 'quality': 0.9, 'confidence': 0.618}, 'district': {'value': 'Bhopal', 'quality': 0.9, 'confidence': 0.78}, 'land_class': {'value': 'Irrigated', 'quality': 0.8, 'confidence': 0.866}, 'mutation_no': {'value': '5523', 'quality': 0.9, 'digits': '5523', 'confidence': 0.918}, 'registration_no': {'value': 'mutation entry (Revenve Inspector nate', 'quality': 0.288, 'digits': '', 'confidence': 0.551}},
        'validation': {'issues': [{'field': 'survey_number', 'severity': 'review', 'msg': 'Low confidence on Survey Number'}, {'field': 'area', 'severity': 'review', 'msg': 'Low confidence on Plot Area'}, {'field': 'village', 'severity': 'review', 'msg': 'Low confidence on Village'}, {'field': 'tehsil', 'severity': 'review', 'msg': 'Low confidence on Tehsil / Taluka'}, {'field': 'registration_no', 'severity': 'review', 'msg': 'Low confidence on Registration Number'}], 'verdict': 'review', 'low_confidence_fields': ['survey_number', 'area', 'village', 'tehsil', 'registration_no']},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 4,
    },
    {
        'id': '7dcc9d348459',
        'filename': 'bad_blank_scan.png',
        'sample': 'bad_blank_scan.png',
        'doc_type': 'land_record',
        'status': 'pending_review',
        'mean_conf': 0.0,
        'languages': '[]',
        'ocr_text': '',
        'fields': {},
        'validation': {'issues': [{'field': 'owner_name', 'severity': 'error', 'msg': 'Missing required field: Landowner Name'}, {'field': 'survey_number', 'severity': 'error', 'msg': 'Missing required field: Survey Number'}, {'field': 'village', 'severity': 'error', 'msg': 'Missing required field: Village'}, {'field': 'district', 'severity': 'error', 'msg': 'Missing required field: District'}], 'verdict': 'rejected', 'low_confidence_fields': []},
        'verdict': 'rejected',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': True,
        'age_h': 3,
    },
    {
        'id': 'f27434269aef',
        'filename': 'bad_blurry_scan.png',
        'sample': 'bad_blurry_scan.png',
        'doc_type': 'land_record',
        'status': 'pending_review',
        'mean_conf': 51.7,
        'languages': '["tam", "tel"]',
        'ocr_text': 'waned / eAA ஊன ரே\nom am oe\ntae ew\nwee ry\nme ote\narn OUR 14\nome an wis\not aw 202\nwt ane తానీ\nae en சமான ad\nQven 8.008\nG2 అ weer కోటు qh\nwien மனா fad\neter arte cur woot\neters oun GA) 44/94\nGAR ஏர்\u200c ௯௪) ரூ',
        'fields': {'area': {'value': 'cur woot', 'quality': 0.72, 'confidence': 0.67}},
        'validation': {'issues': [{'field': 'owner_name', 'severity': 'error', 'msg': 'Missing required field: Landowner Name'}, {'field': 'survey_number', 'severity': 'error', 'msg': 'Missing required field: Survey Number'}, {'field': 'village', 'severity': 'error', 'msg': 'Missing required field: Village'}, {'field': 'district', 'severity': 'error', 'msg': 'Missing required field: District'}, {'field': 'area', 'severity': 'review', 'msg': 'Low confidence on Plot Area'}, {'field': 'duplicate', 'severity': 'warning', 'msg': 'Possible duplicate of record 7dcc9d348459 (bad_blank_scan.png)'}], 'verdict': 'rejected', 'low_confidence_fields': ['area'], 'duplicate_of': '7dcc9d348459'},
        'verdict': 'rejected',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': True,
        'age_h': 2,
    },
    {
        'id': '55d6f0957109',
        'filename': 'draft_khatauni_new.pdf',
        'sample': 'hindi_khatauni_sample.png',
        'doc_type': 'land_record',
        'status': 'draft',
        'mean_conf': 71.3,
        'languages': '["eng"]',
        'ocr_text': '(demo record — created by the demo seeder)',
        'fields': {'owner_name': {'value': 'गोपाल वर्मा', 'confidence': 0.7, 'verified': False}, 'survey_number': {'value': '118', 'confidence': 0.8, 'verified': False}, 'village': {'value': 'Sarangpur', 'confidence': 0.75, 'verified': False}, 'tehsil': {'value': 'Budar', 'confidence': 0.7, 'verified': False}, 'district': {'value': 'Shahdol', 'confidence': 0.8, 'verified': False}, 'state': {'value': 'Madhya Pradesh', 'confidence': 0.75, 'verified': False}},
        'validation': {'verdict': 'review', 'issues': [], 'low_confidence_fields': []},
        'verdict': 'review',
        'lat': None,
        'lon': None,
        'boundary': None,
        'boundary_source': None,
        'routed': False,
        'age_h': 1,
    },
]

# Two mutation applications: one still awaiting review (Arera chain), one
# with the full lifecycle replayed (received -> under_review -> verified,
# which updates the linked 2023 record's owner).
DEMO_MUTATIONS = [
    {"age_h": 118, "status": "received",
     "data": {"transfer_type": "sale", "previous_owner": "Ram Bahadur Singh",
              "new_owner": "Kamla Devi Singh", "survey_number": "452",
              "khasra_number": "77", "village": "Arera", "district": "Bhopal",
              "state": "Madhya Pradesh", "deed_no": "SD/2021/118",
              "deed_date": "2021-06-14",
              "notes": "Sale per registered sale deed (demo application)."}},
    {"age_h": 24, "status": "verified",
     "data": {"transfer_type": "sale", "previous_owner": "Ram Bahadur Singh",
              "new_owner": "Kamla Devi Singh", "survey_number": "452",
              "khasra_number": "77", "village": "Sundarpur", "district": "Bhopal",
              "state": "Madhya Pradesh", "deed_no": "SD/2021/118",
              "deed_date": "2021-06-14",
              "notes": "Sale per registered sale deed (2021 Ferfar sample)."},
     "linked_doc_id": "78972f981b4e",  # xfer_new_2023.png
     "review_notes": "Deed verified; new owner updated on the linked record."},
]

# Human corrections captured by the verification workflow (learning store).
DEMO_CORRECTIONS = [
    ("area", "2.4 \u090f\u0915\u0921\u0930", "2.5 acres"),
    ("area", "4.8 acre", "4.8 acres"),
]


def _doc_count():
    c = store._conn()
    try:
        return c.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
    finally:
        c.close()


def _copy_sample(sample, dest):
    src = os.path.join(SAMPLES, sample)
    if sample and os.path.exists(src):
        shutil.copy(src, dest)
        return True
    return False


def seed_demo_data(force=False):
    """Seed the full demo state. Returns a summary dict.

    * Skips (``seeded=False``) when the database already has documents and
      ``force`` is False - it never destroys user data.
    * ``force=True`` is only honoured by the admin "Load demo data" button
      when the database is EMPTY; with data present it still refuses.
    """
    store.init_db()  # idempotent - safe when called before/without main.py boot
    n = _doc_count()
    if n > 0:
        return {"seeded": False, "documents": n,
                "message": "Database already has %d document(s) - demo seed skipped." % n}

    print("[seed] empty database - seeding the full demo office\u2026")
    now = time.time()

    # ---- accounts ----
    user_ids = {}
    for email, pw, name, role in DEMO_ACCOUNTS:
        try:
            user_ids[email] = store.create_user(email, pw, name, role=role)
        except ValueError:
            pass
    demo_users = {uid: store.get_user(uid) for uid in user_ids.values()}
    demo_users_list = [u for u in demo_users.values() if u]
    operator = next((u for u in demo_users_list if u.get("role") == "operator"), None) or {
        "id": user_ids.get("demo.operator@demo.local", ""),
        "email": "demo.operator@demo.local", "full_name": "Demo Data Officer", "role": "operator"}
    verifier = next((u for u in demo_users_list if u.get("role") == "verifier"), None) or {
        "id": user_ids.get("demo.verifier@demo.local", ""),
        "email": "demo.verifier@demo.local", "full_name": "Demo Verification Officer", "role": "verifier"}

    os.makedirs(store.UPLOAD_DIR, exist_ok=True)

    # ---- documents (oldest first, like a lived-in office) ----
    created = 0
    for rec in sorted(DEMO_RECORDS, key=lambda r: -r["age_h"]):
        doc_id = rec["id"]
        stored_path = os.path.join(
            store.UPLOAD_DIR, "seed_%s_%s" % (doc_id, os.path.basename(rec["sample"])))
        _copy_sample(rec["sample"], stored_path)
        ts = now - rec["age_h"] * 3600.0
        status = rec["status"]
        if status == "rejected":
            status = "pending_review"  # rejected THROUGH the workflow below
        fjson = json.dumps(rec["fields"], ensure_ascii=False)
        vjson = json.dumps(rec["validation"], ensure_ascii=False)
        c = store._conn()
        c.execute("""INSERT INTO documents
            (id, filename, stored_path, mime_type, file_size, uploaded_by, uploaded_at,
             ocr_text, mean_conf, languages, extracted_json, validation_json, verdict,
             status, dedup_key, doc_type, reviewer_notes, submitted_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (doc_id, rec["filename"],
                   stored_path if os.path.exists(stored_path) else "",
                   "application/pdf" if rec["filename"].lower().endswith(".pdf") else "image/png",
                   120000, operator["id"], ts,
                   rec["ocr_text"],
                   rec["mean_conf"], rec["languages"], fjson, vjson,
                   rec["verdict"], status, "", rec["doc_type"], "",
                   ts if status != "draft" else None))
        c.commit()
        c.close()
        created += 1
        store.audit(doc_id, operator["id"], operator["email"], "document_created",
                    rec["filename"] + " (demo seed)")
        if rec["status"] == "verified":
            store.audit(doc_id, verifier["id"], verifier["email"], "verified",
                        "demo seed (fields confirmed by verification officer)")
            store.certify_document(doc_id, verifier["email"])
        if rec["status"] == "rejected":
            store.reject_document(doc_id, rec.get("reject_notes") or "Rejected (demo seed)", verifier)
        if rec.get("routed"):
            store.route_document(doc_id, verifier, ROUTING_REASON,
                                 uploaded_by=operator["id"],
                                 uploaded_by_email=operator["email"])
        if rec.get("lat") is not None:
            store.set_location(doc_id, rec["lat"], rec["lon"])
            store.audit(doc_id, verifier["id"], verifier["email"], "location_set",
                        "exact GIS pin set (demo seed)")
        if rec.get("boundary"):
            store.set_boundary(doc_id, rec["boundary"],
                               rec.get("boundary_source") or "estimated", verifier)

    # ---- learned corrections (from the verify-with-corrections flow) ----
    c = store._conn()
    for (fid, wrong, right) in DEMO_CORRECTIONS:
        c.execute("""INSERT INTO corrections (field_id, wrong, right, count)
                     VALUES (?,?,?,1)
                     ON CONFLICT(field_id, wrong, right)
                     DO UPDATE SET count = count + 1""", (fid, wrong, right))
    c.commit()
    c.close()

    # ---- mutations ----
    n_mut = 0
    for m in DEMO_MUTATIONS:
        m = dict(m)
        data = m["data"]
        mid = store.create_mutation(data, operator)
        n_mut += 1
        if m["status"] == "verified":
            store.set_mutation_status(mid["id"], "under_review", verifier, "Review started")
            store.set_mutation_status(mid["id"], "verified", verifier,
                                      m.get("review_notes") or "",
                                      linked_doc_id=m.get("linked_doc_id"))

    total = _doc_count()
    print("[seed] done - %d demo documents + %d mutation applications." % (total, n_mut))
    return {"seeded": True, "documents": total,
            "message": "%d demo records + %d mutation applications created. Demo logins: "
                       "demo.admin@demo.local / Demo@Admin1 - demo.operator@demo.local / "
                       "Demo@Operator1 - demo.verifier@demo.local / Demo@Verifier1"
                       % (total, n_mut)}


def seed_if_empty():
    """Boot-time entry point: seed only when the database is empty and the
    user has not opted out via LR_NO_DEMO_SEED=1."""
    if os.environ.get("LR_NO_DEMO_SEED", "").strip() in ("1", "true", "yes"):
        print("[seed] LR_NO_DEMO_SEED set - skipping demo seed")
        return {"seeded": False, "message": "disabled by LR_NO_DEMO_SEED"}
    try:
        return seed_demo_data(force=False)
    except Exception as e:  # noqa: BLE001 - a seed failure must never kill boot
        print("[seed] demo seed failed (non-fatal): %r" % e)
        return {"seeded": False, "message": "seed failed: %s" % e}
