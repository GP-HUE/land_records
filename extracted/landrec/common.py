"""Shared utilities: numeral conversion, script detection, label dictionaries."""
import re

# ---------- Numeral conversion ----------
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def normalize_numerals(text: str) -> str:
    """Convert Devanagari and Arabic-Indic digits to ASCII digits."""
    if not text:
        return ""
    return text.translate(DEVANAGARI_DIGITS).translate(ARABIC_DIGITS)

def digits_only(text: str) -> str:
    """Return only ASCII digits from a string."""
    return re.sub(r"[^0-9]", "", normalize_numerals(text or ""))

# ---------- Script detection (for OCR language selection) ----------
SCRIPT_RANGES = {
    "Devanagari": ("hin", (0x0900, 0x097F)),
    "Bengali":    ("ben", (0x0980, 0x09FF)),
    "Gurmukhi":   ("pan", (0x0A00, 0x0A7F)),
    "Gujarati":   ("guj", (0x0A80, 0x0AFF)),
    "Odia":       ("ori", (0x0B00, 0x0B7F)),
    "Tamil":      ("tam", (0x0B80, 0x0BFF)),
    "Telugu":     ("tel", (0x0C00, 0x0C7F)),
    "Kannada":    ("kan", (0x0C80, 0x0CFF)),
    "Malayalam":  ("mal", (0x0D00, 0x0D7F)),
    "Urdu/Arabic":("urd", (0x0600, 0x06FF)),
}

def detect_scripts(text: str) -> list:
    """Return ordered list of ISO-639-3 language codes present in text.
    A script must appear at least MIN_CHARS times to count (avoids stray glyphs)."""
    MIN_CHARS = 3
    found = []
    for name, (code, (lo, hi)) in SCRIPT_RANGES.items():
        cnt = sum(1 for ch in text if lo <= ord(ch) <= hi)
        if cnt >= MIN_CHARS:
            found.append(code)
    return found

# ---------- Field definitions & label dictionaries ----------
# Each field: id, display name, list of label synonyms (lowercased) used for matching.
FIELD_DEFS = [
    ("owner_name",      "Landowner Name", [
        "भूमि स्वामी", "मालिक का नाम", "काश्तकार", "स्वामी का नाम", "धारक का नाम",
        "land owner", "landowner", "owner name", "owner", "name of owner", "khatedar",
        "holder name", "ryot", "cultivator", "पट्टाधारक", "मालिक", "स्वामी",
        "landowner name", "name of landowner"]),
    ("father_name",     "Father's Name", [
        "पिता का नाम", "पिता", "father name", "father's name", "s/o", "son of",
        "वालिद का नाम", "father"]),
    ("survey_number",   "Survey Number", [
        "सर्वे नंबर", "सर्वে नं.", "सर्वे नं.", "सर्वे संख्या", "सर्वेक्षण संख्या", "सर्वेक्षण क्र.",
        "survey no", "survey number", "survey #", "s.no", "survey no."]),
    ("khasra_number",   "Khasra Number", [
        "खसरा नंबर", "खसरा संख्या", "खसरा क्र.", "खसरा",
        "khasra no", "khasra number", "khasra #",
        "خسرا نمبر", "خسرا"],),
    ("khata_number",    "Khata Number", [
        "खाता नंबर", "खाता संख्या", "खाता क्र.",
        "khata no", "khata number", "account no", "account number", "खाता", "کتا نمبر", "کتا"]),
    ("plot_number",     "Plot Number", [
        "प्लॉट नंबर", "प्लाट नं.", "भूखंड संख्या",
        "plot no", "plot number", "plot #", "प्लॉट", "प्लाट",
        "پلاٹ نمبر", "پلاٹ", "પ્લોટ નંબર", "પ્લોટ", "ಪ್ಲಾಟ್‌ ಸಂಖ್ಯೆ", "ಪ್ಲಾಟ್",
        "പ്ലോട്ട്", "ਪਲਾਟ ਨੰ", "ਪਲਾਟ", "ପ୍ଲାଟ୍‌ ନମ୍ବର", "ପ୍ଲାଟ୍"]),
    ("area",            "Plot Area", [
        "क्षेत्रफल", "रकबा", "क्षेत्र", "एरिया",
        "area", "extent", "total area", "plot area", "land area", "رقبہ", "رقبہ"]),
    ("village",         "Village", [
        "गाँव", "गांव", "ग्राम", "ग्राम का नाम", "मौजा", "ग्राम पंचायत",
        "village", "mauza", "gram", "گاؤں"]),
    ("tehsil",          "Tehsil / Taluka", [
        "तहसील", "तालुका", "तालुक", "मंडल",
        "tehsil", "tahsil", "taluka", "taluk", "mandal", "تحصیل", "تہصیل"]),
    ("district",        "District", [
        "जिला", "ज़िला", "जनपद",
        "district", "zilla", "ضلع"]),
    ("state",           "State", [
        "राज्य", "प्रदेश", "state", "صوبہ"]),
    ("land_class",      "Land Classification", [
        "भूमि का प्रकार", "भूमि वर्ग", "भू-वर्ग", "वर्ग", "भूमि का वर्ग",
        "land type", "land classification", "class of land", "land class", "soil type", "زمین کی قسم"]),
    ("ownership_type",  "Ownership Type", [
        "स्वामित्व प्रकार", "स्वामित्व", "मालिकाना",
        "ownership type", "ownership", "tenure", "ملکیت"]),
    ("mutation_no",     "Mutation Number", [
        "दाखिल खारिज", "म्यूटेशन नंबर", "म्यूटेशन संख्या", "इंतकाल",
        "mutation no", "mutation number", "mutation #", "dakhil kharij",
        "रसीद", "रोर", "r.s.d", "متیشن نمبر", "متیشن"]),
    ("registration_no","Registration Number", [
        "पंजीकरण संख्या", "पंजीयन क्र.", "रजिस्ट्री नंबर", "रजिस्ट्रेशन नंबर",
        "registration no", "registration number", "reg. no", "reg no"]),
    ("khatauni_year",   "Khatauni Year", [
        "खतौनी वर्ष", "फसल वर्ष", "साल",
        "khatauni year", "crop year", "year", "fiscal year", "f.s.", "वर्ष", "سال",
        "பருவாண்டு", "ஆம் ஆண்டு",
        "ఆంశ సంవత్సరం", "సంవత్సరం",
        "বর্ষ", "বরষ",
        "વર્ષ", "ವರ್ಷ", "വർഷം", "ਸਾਲ", "ବର୍ଷ"]),
]

# ---------- Regional-language label synonyms (merged into FIELD_DEFS below) ----------
# Tamil, Telugu, Bengali, Gujarati, Kannada, Malayalam, Punjabi (Gurmukhi), Odia
REGIONAL_LABELS = {
    "owner_name": [
        "உரிமையாளர் பெயர்", "நில உரிமையாளர்", "பட்டாதாரர்",
        "యజమాని పేరు", "భూ యజమాని", "భూమి యజమాని", "పట్టాదారు",
        "মালিকের নাম", "জমির মালিক", "রায়ত", "মালিক",
        "માલિકનું નામ", "જમીન માલિક", "ખાતેદાર", "માલિક",
        "ಮಾಲೀಕರ ಹೆಸರು", "ಭೂ ಮಾಲೀಕ", "ಖಾತೆದಾರ", "ಮಾಲೀಕ",
        "ഉടമയുടെ പേര്", "ഭൂവുടമ", "ഉടമ",
        "ਮਾਲਕ ਦਾ ਨਾਮ", "ਜ਼ਮੀਨ ਮਾਲਕ", "ਖਾਤੇਦਾਰ", "ਮਾਲਕ",
        "ମାଲିକଙ୍କ ନାମ", "ଜମି ମାଲିକ", "ମାଲିକ",
        "مالک", "مالک کا نام"],
    "father_name": [
        "தந்தையின் பெயர்", "தந்தை பெயர்", "தந்தை",
        "తండ్రి పేరు", "తండ్రి", "పితర", "దండ్రి", "پিতەر", "পিতার নাম", "পিতা", "પિતાનું નામ", "પિતા",
        "ತಂದೆಯ ಹೆಸರು", "ತಂದೆ", "പിതാവിന്റെ പേര്", "പിതാവ്",
        "पिता", "والد",
        "ਪਿਤਾ ਦਾ ਨਾਮ", "ପିତାଙ୍କ ନାମ"],
    "survey_number": [
        "سروی نمبر", "سروی",
        "சர்வே எண்", "புல எண்",
        "సర్వే నంబరు", "సర్వే నంబర్", "సర్వే నెంబరు", "సర్వే సంఖ్య",
        "সার্ভে নম্বর", "সার্ভে নং", "দাগ নম্বর", "দাগ",
        "સર્વે નંબર",
        "ಸರ್ವೆ ನಂಬರ್", "ಸರ್ವೇ ಸಂಖ್ಯೆ",
        "സർവേ നമ്പർ", "ਸਰਵੇ ਨੰਬਰ", "ସର୍ଭେ ନମ୍ବର"],
    "khasra_number": ["ਖਸਰਾ ਨੰਬਰ", "ખસરા નંબર", "கசரா", "ఖసరా", "খসড়া", "খসরা"],
    "khata_number": [
        "பட்டா எண்", "கணக்கு எண்",
        "పట్టా నంబరు", "ఖాతా నంబరు", "ఖాతా నంబర్",
        "খতিয়ান নম্বর", "খাতা নম্বর", "খাতা নং", "খাতা",
        "ખાતા નંબર", "ಖಾತೆ ಸಂಖ್ಯೆ", "ಪಹಣಿ ಸಂಖ್ಯೆ",
        "ਖਾਤਾ ਨੰਬਰ", "ଖାତା ନମ୍ବର"],
    "area": [
        "பரப்பளவு", "நில அளவு", "பரப்பு", "அளவு",
        "విస్తీర్ణం", "వైశాల్యం",
        "জমির পরিমাণ", "আয়তন", "এলাকা",
        "ક્ષેત્રફળ", "વિસ્તાર",
        "ವಿಸ್ತೀರ್ಣ", "വിസ്തീർണ്ണം",
        "ਖੇਤਰਫਲ", "ਰਕਬਾ", "କ୍ଷେତ୍ରଫଳ"],
    "village": [
        "கிராமம்", "గ్రామం", "গ্রাম", "মৌজা", "ગામ",
        "ಗ್ರಾಮ", "ഗ്രാമം", "ਪਿੰਡ", "ଗ୍ରାମ", "ମୌଜା"],
    "tehsil": [
        "வட்டம்", "தாலுகா", "మండలం", "తహసీల్", "తహసిల్", "তহসিল", "તાલુકો",
        "ತಾಲ್ಲೂಕು", "താലൂക്ക്", "ਤਹਿਸੀਲ", "ତହସିଲ"],
    "district": [
        "மாவட்டம்", "జిల్లా", "জেলা", "જિલ્લો",
        "ಜಿಲ್ಲೆ", "ജില്ല", "ਜ਼ਿਲ੍ਹਾ", "ଜିଲ୍ଲା"],
    "state": [
        "மாநிலம்", "రాష్ట్రం", "రాష్ట్రము", "রাজ্য", "রাষ্ট্র", "રાજ્ય",
        "ರಾಜ್ಯ", "സംസ്ഥാനം", "ਰਾਜ", "ରାଜ୍ୟ"],
    "land_class": [
        "நில வகை", "భూమి రకం", "জমির ধরন", "જમીનનો પ્રકાર",
        "ಭೂಮಿ ವಿಧ", "ഭൂമി തരം", "ਜ਼ਮੀਨ ਦੀ ਕਿਸਮ", "ଜମି ପ୍ରକାର"],
    "ownership_type": [
        "உரிமை வகை", "உரிமை", "యాజమాన్య రకం", "స్వంతం", "స్వాధీనం", "মালিকানার ধরন", "মালিকানা", "માલિકીનો પ્રકાર",
        "ಮಾಲೀಕತ್ವ ವಿಧ", "ഉടമസ്ഥത", "ਮਾਲਕੀ ਕਿਸਮ", "ମାଲିକାନା ପ୍ରକାର"],
    "mutation_no": [
        "மியூட்டேஷன் எண்", "மாற்ற எண்", "மாற்றம் எண்", "வர்த்தை எண்", "మ్యుటేషన్ నంబరు", "మ్యుటేషన్", "মিউটেশন নম্বর", "মিউটেশন", "નામફેર નંબર",
        "ಮ್ಯುಟೇಶನ್ ಸಂಖ್ಯೆ", "ਇੰਤਕਾਲ ਨੰਬਰ", "ମ୍ୟୁଟେସନ ନମ୍ବର"],
    "registration_no": [
        "பதிவு எண்", "రిజిస్ట్రేషన్ నంబరు", "নিবন্ধন নম্বর", "નોંધણી નંબર",
        "ನೋಂದಣಿ ಸಂಖ್ಯೆ", "രജിസ്ട്രേഷൻ നമ്പർ", "ਰਜਿਸਟਰੇਸ਼ਨ ਨੰਬਰ", "ପଞ୍ଜୀକରଣ ନମ୍ବର"],
}
FIELD_DEFS = [(fid, disp, labels + REGIONAL_LABELS.get(fid, []))
              for fid, disp, labels in FIELD_DEFS]

FIELD_IDS = [f[0] for f in FIELD_DEFS]
FIELD_LABELS = {f[0]: f[1] for f in FIELD_DEFS}

# Ordered labels per field, longest first so "survey number" beats "survey"
LABEL_PATTERNS = {}
for fid, _display, labels in FIELD_DEFS:
    LABEL_PATTERNS[fid] = sorted(labels, key=len, reverse=True)

# ---------- NEW-kind document corner coordinates (v3.13) ----------
# New-format land records carry the GPS coordinates of the plot's four
# corners PRINTED on the document.  These fields live OUTSIDE FIELD_DEFS on
# purpose: they are only read when the user declares the upload as the NEW
# document kind — old-kind processing stays byte-for-byte identical.  They
# are still registered in FIELD_LABELS / LABEL_PATTERNS so the extraction
# machinery and the validator can handle them like any other field.
COORD_FIELD_DEFS = [
    ("coordinate_1", "Coordinate 1 (निर्देशांक 1)", [
        "coordinate 1", "coordinates 1", "coordinate-1", "coord 1", "coord-1",
        "corner 1", "corner-1", "point 1", "gps 1", "gps coordinate 1",
        "निर्देशांक 1", "निर्देशांक-1", "कोऑर्डिनेट 1", "कोआर्डिनेट 1"]),
    ("coordinate_2", "Coordinate 2 (निर्देशांक 2)", [
        "coordinate 2", "coordinates 2", "coordinate-2", "coord 2", "coord-2",
        "corner 2", "corner-2", "point 2", "gps 2", "gps coordinate 2",
        "निर्देशांक 2", "निर्देशांक-2", "कोऑर्डिनेट 2", "कोआर्डिनेट 2"]),
    ("coordinate_3", "Coordinate 3 (निर्देशांक 3)", [
        "coordinate 3", "coordinates 3", "coordinate-3", "coord 3", "coord-3",
        "corner 3", "corner-3", "point 3", "gps 3", "gps coordinate 3",
        "निर्देशांक 3", "निर्देशांक-3", "कोऑर्डिनेट 3", "कोआर्डिनेट 3"]),
    ("coordinate_4", "Coordinate 4 (निर्देशांक 4)", [
        "coordinate 4", "coordinates 4", "coordinate-4", "coord 4", "coord-4",
        "corner 4", "corner-4", "point 4", "gps 4", "gps coordinate 4",
        "निर्देशांक 4", "निर्देशांक-4", "कोऑर्डिनेट 4", "कोआर्डिनेट 4"]),
]
COORD_FIELD_IDS = [f[0] for f in COORD_FIELD_DEFS]
for _fid, _disp, _labels in COORD_FIELD_DEFS:
    FIELD_LABELS[_fid] = _disp
    LABEL_PATTERNS[_fid] = sorted(_labels, key=len, reverse=True)

# ---------- Land classification vocabulary (Hindi + English) ----------
LAND_CLASSES = [
    ("Irrigated",      ["सिंचित", "sichit", "irrigated", "irrigated land",
                        "ಸುರಕ್ಷಿತ ನೀರಿನ", "ಬಿಲ್‌", "ବିଲ୍ ଧରି", "wet land", "வயல்"]),
    ("Non-Irrigated",  ["असिंचित", "बारानी", "asinchit", "barani", "non-irrigated", "unirrigated", "rainfed",
                        "வெளிப்புழுது", "dry land"]),
    ("Agricultural",   ["कृषि", "कृषि योग्य", "agricultural", "cultivable", "arable",
                        "వ్యవసాయ", "खेती", "ಖೇತಿಯ", "কৃষিজ",
                        "വിളവേതുമണ്ണ്", "farmland", "زرعی", "kheti"]),
    ("Barren",         ["बंजर", "बन्जर", "banjar", "barren", "wasteland", "uncultivable"]),
    ("Residential",    ["आवासीय", "गैर-मुकिन", "residential", "abadi", "आबादी", "nagari"]),
    ("Pasture",        ["चरागाह", "गोचर", "charagah", "gochar", "pasture", "grazing"]),
    ("Forest",         ["वन", "जंगल", "forest", "jungle"]),
]

OWNERSHIP_TYPES = [
    ("Private",       ["निजी", "private", "personal", "sole owner", "full owner",
                        "individual owner", "complete owner",
                        "వ్యక్తిగత", "వ్యక్తిగత యజమాని", "సంపూర్ణ యజమాని",
                        "সম্পূর্ণ মালিক", "পূর্ণ মালিক", "ಪೂರ್ಣ ಮಾಲೀಕ", "ಪೂರ್ಣ ಮಾಲಿಕ",
                        "ಪೂರ್ಣ ಮಾಲೀಕ", "പൂർണ ഉടമ", "വ്യക്തിഗത", "ਮਲਿਕ",
                        "സമ്പൂର୍ଣ୍ଣ", "مکمل مالک", "مکمل"]),
    ("Government",    ["सरकारी", "राजकीय", "government", "govt", "state"]),
    ("Joint/Co-owned",["संयुक्त", "joint", "co-owned", "shared", "संयुक्त स्वामित्व",
                        "సంయుక్త", "সংযুক্ত", "સંયુકત"]),
    ("Tenant/Cultivator", ["काश्तकार", "tenant", "cultivator", "गैर-खातेदार"]),
]

# ---------- Area units ----------
AREA_UNITS = {
    "hectare": ["हेक्टेयर", "हेक्टर", "hectare", "ha", "hect", "ஹெக்டேர்", "హెక్టారు", "হেক্টর", "હેક્ટર", "ಹೆಕ್ಟೇರ್", "ഹെക്ടർ", "ਹੈਕਟੇਅਰ", "ହେକ୍ଟର"],
    "acre":    ["एकड़", "एकड", "acre", "ac", "acres", "ஏக்கர்", "ఎకరం", "ఎకరాలు", "একর", "એકર", "ಎಕರೆ", "ഏക്കർ", "ਏਕੜ", "ଏକର"],
    "bigha":   ["बीघा", "bigha", "বিঘা", "વીઘા", "ਵਿੱਘਾ"],
    "sq_ft":   ["वर्ग फुट", "वर्ग फीट", "square feet", "sq ft", "sqft", "sq. ft", "sft"],
    "sq_m":    ["वर्ग मीटर", "square meter", "sq m", "sqm", "sq. m", "sqmt"],
    "gunta":   ["गुंठा", "गुंठे", "gunta", "gunta"],
}
