"""Offline AI Assistant - chat + website knowledge + document search.

The floating 🤖 assistant in the UI talks to this module. 100% rule-based
and local: no internet, no API keys, no document data ever leaves the
machine (the right posture for government land records).

What it handles:
  1. Chat   - greetings and small talk (hi, hello, namaste, thanks, bye...)
  2. Help   - every aspect of the website: features, how they work, where
              things are, roles, statuses, security, the AI features, ...
  3. Stats  - "how many verified records?"
  4. Search - natural-language document search ("find khatauni of Ramesh",
              "show pending documents", "which record has survey 45/2?")
  5. Record - "details of document #abc" (12-char ID), "who owns survey 312?",
              "show the latest documents"
"""
import json
import re
import time

from . import ai_support, common, extractor, risk, store

# "show more" state: last search results per user (in-memory, resets on restart)
_last_results = {}

# --------------------------------------------------------------------------
# 0) Chat / small talk (only for short queries, so 'hi, find khatauni of
#    Ramesh' still gets searched instead of greeting)
# --------------------------------------------------------------------------
SMALLTALK = [
    (r"^(hi+|hii+|hello+|hey+|hola|namaste|namaskar|namaskar|salaam|salam|assalamualaikum|good (morning|afternoon|evening))\b",
     "Hello! 👋 I'm the offline AI assistant for this Land Records Portal. "
     "Ask me about:\n"
     "• How any part of the website works — upload, verification, queue, "
     "comparison, consistency check, dashboard, audit log\n"
     "• The land modules — 🏦 loans/encumbrance, ⚖️ court cases, 🛡️ fraud risk, "
     "📄 certified/EC PDFs + QR, 💾 backup, 🗺️ real map & boundaries, 📝 mutations\n"
     "• Finding documents — 'find khatauni of Ramesh', 'show pending records', "
     "'which records have an active loan?', 'which records are risky?'\n"
     "• Record details — 'who owns survey 312?', 'risk of <record id>', "
     "'details of document #abc'\n"
     "Admins: type 'SA' for Superior Administrator mode. Try a question now, or "
     "tap a quick chip below."),

    (r"^(how are you|kaise ho|kaise hain|kaisi ho)\b",
     "I'm running fine — fully local and offline. How can I help you with the "
     "records today?"),

    (r"^(thank[su]?|shukriya|dhanyavad|dhanyawad)\b",
     "You're welcome! 🙏 Anything else — another record, a how-to, or the "
     "pending queue?"),

    (r"^(bye|goodbye|see you|alvida|phir milenge)\b",
     "Goodbye! 👋 The records and the assistant will be right here when you "
     "come back."),

    (r"^(ok(ay)?|superb|great|excellent|nice|good|badhiya|wah)\b",
     "Glad it helped! If you need anything else — another search or a how-to "
     "— just ask."),

    (r"^(who are you|what are you|tum kaun|apna parichay|your name)\b",
     "I'm the built-in offline AI assistant of this Land Records Portal — a "
     "rule-based helper that runs entirely on this computer. I know how every "
     "part of the website works and I can search the document database. No "
     "internet, no data sharing — which matters for government records."),
]

# --------------------------------------------------------------------------
# 1) Website help knowledge base (pattern, answer)
# --------------------------------------------------------------------------
HELP = [
    # --- the big picture ---
    (r"\b(what is this (website|site|app|portal|system)|about (this |the )?(website|portal|site)|website (kya hai|kaun sa)|tell me (about|the) (website|portal|system)|kya hai yeh|dilrmp)\b",
     "This is the Intelligent Land Record Digitization & Validation System — "
     "a DILRMP-style portal for digitizing, verifying and searching land "
     "records. Everything runs locally on this computer (no internet needed).\n\n"
     "The 8 sections:\n"
     "1. Upload — scan in a document (PDF/JPG/PNG/TIFF), OCR + AI field "
     "extraction, then draft or submit for verification\n"
     "2. Dashboard — totals, OCR confidence, AI-flagged count, corrections, "
     "state/district charts\n"
     "3. Records — every document with filters, search, view & delete\n"
     "4. Verification Queue — pending records for Verification Officers to "
     "approve or return\n"
     "5. Comparison — diff two versions of a record (ownership/area/survey "
     "changes) with an AI explanation\n"
     "6. Consistency Check — cross-check 2-6 related records for conflicts, "
     "duplicates and chain-of-title hints\n"
     "7. AI Feedback — the learning loop: officer corrections become "
     "auto-correction rules\n"
     "8. Audit Log — hash-chained, tamper-evident history of every action\n"
     "Plus Users and Account tabs. Ask me about any of these in detail — "
     "e.g. 'how does verification work?' or 'what is the audit log for?'"),

    # --- upload ---
    (r"how (do i|to) (upload|add|scan)|upload (a |the |documents?)|new (document|abhilekh)|कसे (अपलोड|upload)|कैसे (अपलोड|upload)|scan (a |the |document)|ocr (how|kaise)",
     "To upload a document:\n"
     "1. Open the Upload tab and drag & drop the scan (PDF, JPG, PNG or TIFF, "
     "up to 20 MB)\n"
     "2. Choose the document type (Land Record / Mutation / Sale Deed / Tax "
     "Receipt)\n"
     "3. Choose the language — picking the known language is faster and more "
     "accurate than Auto-detect\n"
     "4. Press Upload — OCR runs and the 16 fields are extracted\n"
     "5. Review the fields (low-confidence ones are marked), correct anything "
     "wrong, then Save as Draft or Submit for Verification\n"
     "Tip: you can also test with a built-in sample from the sample strip at "
     "the top of the Upload tab."),

    (r"sample|demo|test (with|a|the) sample|try it|example document",
     "On the Upload tab there is a sample strip with ready-made documents in "
     "Hindi, English, Tamil, Telugu, Bengali and a handwritten mutation "
     "record. Click one (e.g. 'hindi_khatauni_sample.png') and it is processed "
     "exactly like a real upload — a good way to see OCR + extraction + the AI "
     "decision card without your own scan."),

    # --- verification workflow ---
    (r"how (do i|to) (verify|approve)|verify(ing|ication)? (a |the |document|record)|verification (how|work|kaise|process|workflow|matlab)|how (does|do) (the )?verification (work|happen)|verification (matlab|kya)|सत्यापित कसे|कैसे सत्यापित",
     "Verification workflow:\n"
     "1. A Data Operator uploads and submits a record → it becomes PENDING "
     "VERIFICATION\n"
     "2. A Verification Officer (or Admin) opens it from the Verification "
     "Queue tab\n"
     "3. Read the AI Decision Support card: ROUTINE CLEAR = spot-check is "
     "enough; REVIEW REQUIRED = confirm the flagged fields; CAUTION = do not "
     "approve without manual review\n"
     "4. Correct any wrong fields in the detail view, then press 'Save & "
     "Verify' → the record becomes VERIFIED\n"
     "5. If it's wrong beyond a quick fix, press 'Return with Notes' and write "
     "what must be corrected → the record goes back to the data officer as "
     "RETURNED"),

    (r"why (is|are) (my |the |this )?(document|record|abhilekh) (pending|in queue)|pending (verification)? (matlab|means|kya|why)|what does pending mean",
     "A record becomes PENDING VERIFICATION when a data operator submits it "
     "after upload. It stays there until a Verification Officer (or Admin) "
     "opens it, checks the fields against the scan, and either verifies it or "
     "returns it with notes. You can see everything waiting in the "
     "Verification Queue tab — or just ask me 'show pending documents'."),

    (r"return(ing)? (a |the )?(record|document)|send (it |the |record )?back|returned (record|matlab|kya)|लौटा(व|या)|वापस",
     "To return a record for correction (Verification Officer/Admin): open the "
     "record, press 'Return with Notes' in the detail view and write what must "
     "be corrected. The record becomes RETURNED with your notes; the data "
     "operator sees it in their returned list, fixes the fields and submits it "
     "again."),

    (r"difference (between|of) (verified|auto)|auto.?approved (vs|and|matlab|kya)|verified vs auto",
     "VERIFIED = a human Verification Officer checked the fields and approved "
     "the record. AUTO-APPROVED = the record passed every automated check "
     "(no missing required fields, no low-confidence fields, no warnings) so "
     "the system approved it without a human. Both count as 'verified' in the "
     "public totals, but the AI Feedback / audit trail shows which path each "
     "record took."),

    # --- tabs & where things are ---
    (r"\bwhere (is|do i|can i|to|from) (the )?(audit|dashboard|queue|comparison|consistency|learn|correction|users?|account|system status|statistics|pending)\b",
     None),  # handled by the WHERE map below

    (r"dashboard|statistics|stats (card|page)|chart",
     "The Dashboard tab (📊) shows: total submissions and processed records, "
     "average OCR confidence, drafts, returned and pending counts, verified "
     "total, accuracy estimate, AI Flagged (records the automated analysis "
     "flagged for review) and Human Corrections (corrections officers applied "
     "- the learning loop's input). Below that are bar charts of records by "
     "state and by district."),

    (r"records (tab|list|page)|abhilekh (suchi|list)|all records|document list",
     "The Records tab (🗂️) lists every document with filters by status "
     "(All / Draft / Pending / Verified / Returned), a text search (ID, owner, "
     "village, survey), and pagination. Each row opens the full detail view "
     "with the AI Decision Support card, all extracted fields, raw OCR text "
     "and the record's audit trail. Admins can delete records from here."),

    (r"what fields|which fields|kya kya (nikalta|field)|field (list|list of)|what does it extract|extracted (fields|data)",
     "The system extracts 16 fields from each document: Landowner Name, "
     "Father's Name, Survey Number, Khasra Number, Khata Number, Plot Number, "
     "Plot Area, Village, Tehsil, District, State, Land Classification, "
     "Ownership Type, Mutation Number, Registration Number and Khatauni Year. "
     "Every field carries an OCR confidence score; four are required for "
     "validation (owner, survey, village, district)."),

    (r"document (types|categories)|kaha (prakar|type)|khatauni (kya hai|matlab)|what is (a )?(khatauni|patta|pattai|mutation (record|namantaran)|ferfar|sale deed|r\.?o\.?r|jamabandi|pahani)",
     "The four document types: Land Record (Khatauni / Record of Rights / 7-12 "
     "— the core ownership record; also called patta, pattai, jamabandi, "
     "pahani in different states), Mutation Record (Namantaran / Ferfar — "
     "ownership transfer entries), Sale Deed (Registry / Conveyance — registered "
     "sale documents) and Tax Receipt (Lagan / Revenue Slip). You choose the "
     "type when uploading; it is stored, shown and filterable everywhere."),

    (r"comparison|compare (two|a )?(record|version|document)|version difference|तुलना|do (version|sanaskaran)",
     "The Comparison tab (⚖️) has two modes:\n"
     "• Compare Existing Records — pick Document A (previous) and B (current) "
     "from the database\n"
     "• Upload Two Documents — OCR two fresh scans (they are NOT saved)\n"
     "You get a field-by-field diff with changed rows highlighted, plus an AI "
     "Differences Explanation that calls out ownership transfer, area variance, "
     "renumbering and location/year changes."),

    (r"consisten(cy|t)|cross.?docum|chain of title|multiple doc(ument)?s|सुसंगति",
     "The Consistency Check tab (🔍) cross-checks 2-6 related records: same "
     "survey number with different owners (title conflict), identical "
     "survey+khasra+owner (possible duplicate), district/village mismatches, "
     "and family/chain-of-title hints (an owner appearing as a father's name). "
     "It finishes with an AI Consistency Explanation and a recommendation."),

    (r"learn(ing)? (loop|ai|feedback|system)|ai (feedback|corrections)|how (does|do) (the )?(ai )?(learn|improve|correction)|corrections (how|work)|auto.?correct",
     "The AI Feedback tab (🧠) shows the learning loop: when an officer "
     "corrects a field while verifying (e.g. OCR read 'Romes' but the officer "
     "typed 'Ramesh'), that correction is stored. Once the same wrong→right "
     "correction is confirmed by 2+ officers on different documents, the system "
     "auto-applies it to future extractions and marks the field as "
     "auto-corrected. Numbers are never auto-corrected (a survey number must "
     "always be human-checked). Officers can delete learned rules."),

    (r"audit|hash|tamper|integrity|ऑडिट",
     "The Audit Log tab (🔐) records every action with a timestamp and user: "
     "uploads, verifications, corrections, returns, user changes, deletions. "
     "Each entry is SHA-256 hash-chained to the previous one — press 'Verify "
     "Integrity' and the app recomputes the chain; if any entry was ever "
     "tampered with, the chain breaks and the entry is flagged. This makes the "
     "log tamper-evident."),

    (r"make (someone |a |the |him |her )?(an? )?(admin|verifier|operator)|promote (a |the )?user|change (a |the |user )?(role|admin)|user (role|roles) change",
     "An Admin does it in the Users tab (👥): open the row, change the role "
     "dropdown (Viewer / Data Operator / Verification Officer / Admin) and "
     "save. The user needs to log in again for the new role to apply. Ask me "
     "'how do I reset a password?' or 'how do I deactivate a user?' for the "
     "other user-management steps."),

    (r"deactivate|disable (a |the |user )?account|block (a |the )?user|suspend",
     "An Admin disables a user in the Users tab (👥) — the row shows a "
     "Deactivate action. A deactivated user can no longer log in; their "
     "documents and audit history stay in the system."),

    (r"roles?|viewer|data (operator|officer)|verif(ication|ier) officer|admin(istrator)?|permissions|kaun kya kar sakta|कोण काय करू शकतो|कौन क्या कर सकता",
     "Four roles:\n"
     "• Viewer — can inspect records only (no upload)\n"
     "• Data Operator — upload, draft, submit, resubmit returned records\n"
     "• Verification Officer — everything above plus the Verification Queue, "
     "Comparison and Consistency tabs; can verify or return records\n"
     "• Admin — everything plus user management (create, role change, "
     "deactivate, password reset, import/export) and deleting records\n"
     "New signups start as Data Operator; an Admin promotes them in the Users "
     "tab. Ask me 'how do I make someone an admin?' for the steps."),

    (r"reset (a |the |user )?password|forgot (my |the |password)|password (forgot|reset|bhool)|naya password",
     "Two ways:\n"
     "• Self-service: any logged-in user changes their own password in the "
     "Account tab (⚙️)\n"
     "• Forgotten password: an Admin resets it from the Users tab (👥) — "
     "open the row and use the password-reset action, then share the new "
     "password with the user over a safe channel."),

    (r"locked|lockout|too many (attempts|logins)|login (failing|not working)|password (wrong|galat) (5|five|times|baar)|account locked",
     "After 5 failed login attempts the account is locked for 5 minutes "
     "(brute-force protection). Wait 5 minutes and try again with the correct "
     "password — or ask an Admin to confirm the credentials."),

    (r"status(es)?|draft|pending|verified|auto.?approv|returned|स्थिती|स्थिति",
     "Record statuses:\n"
     "• DRAFT — saved by the data operator, not submitted yet\n"
     "• PENDING VERIFICATION — submitted, waiting for a Verification Officer\n"
     "• VERIFIED — approved by an officer\n"
     "• AUTO-APPROVED — passed all automated checks with no low-confidence "
     "fields, approved by the system\n"
     "• RETURNED — sent back to the data operator with correction notes"),

    (r"confidence|ocr accuracy|सटीकता|how accurate",
     "OCR confidence is Tesseract's 0-100% estimate per field. Fields below "
     "75% are flagged 'Low confidence' (shown in the field list and the AI "
     "Decision Support card) and make the recommendation at least REVIEW "
     "REQUIRED. The dashboard shows the average confidence across all "
     "documents. Tips for better confidence: pick the right language (not "
     "Auto), and use a sharp, straight, well-lit scan."),

    (r"extraction (wrong|incorrect|galat)|wrong (values|fields|names)|ocr (wrong|gave|read) (wrong|incorrect)|galat (value|field|naam)|data (galat|wrong)|reading (wrong|galat)",
     "If OCR got values wrong:\n"
     "1. The field will usually show low confidence — check it against the "
     "scan in the detail view and correct it\n"
     "2. Repeating the same correction on 2+ documents teaches the system "
     "(learning loop), so it auto-corrects that field in future\n"
     "3. For a whole document: a sharper, straighter, well-lit scan, and "
     "selecting the document's actual language instead of Auto-detect, "
     "usually fixes most errors\n"
     "Numbers (survey/khasra) are NEVER auto-corrected — they always need a "
     "human check."),

    (r"language(s)?|which (scripts|languages)|hindi|tamil|telugu|bengali|urdu|gujarati|punjabi|oriya|kannada|malayalam|भाषा",
     "The app OCRs 11 languages: English, Hindi, Bengali, Tamil, Telugu, "
     "Gujarati, Punjabi, Oriya, Kannada, Malayalam and Urdu. Pick the "
     "document's language in the Upload tab for best speed and accuracy "
     "(Auto-detect is slower and less precise). Languages whose pack is not "
     "installed on this machine are greyed out in the dropdown."),

    (r"delete (a |the )?(record|document)|remove (a |the )?(record|document)|मिटव|मिटाएँ|mitao",
     "Only an Admin can delete records: Records tab (🗂️) → trash icon on the "
     "row. Deletion removes the document, its audit trail and the stored scan "
     "file. (Disabling a USER is different — that's in the Users tab.)"),

    (r"account (settings|tab|page)|my profile|apna (account|profile)|personal (details|information)",
     "The Account tab (⚙️) has your profile (name, email, role), the change-"
     "password form, and the System Status panel — version, OCR worker, "
     "Tesseract, database and language packs all in one place, plus a "
     "self-test that checks every component."),

    (r"system (status|selftest|self-test|health)|worker (status|crash)|server (status|down)|tesseract (status|version)",
     "Open the Account tab (⚙️) → System Status: it shows the app version, "
     "whether the OCR worker process is alive, the Tesseract version, the "
     "database location and the installed language packs. 'Run Self-Test' "
     "checks every component end-to-end. If the OCR worker ever dies on a bad "
     "file, it is respawned automatically — the website itself never crashes "
     "because of a bad document."),

    (r"privacy|is (my |the )?(data|safe)|data (safe|secure)|data (kahan|where|store|stored|saved|jaat|jaata|raha)|where (is|is the) (the )?data (stored|store|saved|ke)|database (where|kahan)|storage|cloud|need (an? )?internet|internet (needed|connection|chahiye)|without internet|no internet|offline",
     "Everything runs 100% offline on this computer:\n"
     "• Data is stored locally in dist\\LandRecordSystem\\data\\ (landrec.db "
     "SQLite database + the uploaded scan files) — never in any cloud\n"
     "• No internet connection is needed at all, and no data ever leaves the "
     "machine (OCR, the AI assistant and all analysis are local)\n"
     "• Security: salted+hashed passwords, 5-attempt login lockout, "
     "role-based access, rate-limited OCR, and a hash-chained tamper-evident "
     "audit log\n"
     "Backup = copy the data\\ folder. Never delete it — it holds all "
     "accounts and records."),

    (r"password|pass ?word|पासवर्ड|login|log ?in|sign ?(up|in)",
     "Change your password in the Account tab (⚙️). Forgotten password? An "
     "Admin resets it from the Users tab (👥). To create an account use Sign "
     "Up on the login page — new accounts start as Data Operator and an Admin "
     "can promote them. After 5 wrong attempts the account locks for 5 "
     "minutes."),

    (r"search|find (a |the |document|record)|dhoondo|khojo|खोज|ढूंढो|शोधा|how do i (find|search)|document (search|kaise)",
     "You can find documents two ways:\n"
     "1. Here, in plain words — 'find the khatauni of Ramesh Sharma', 'show "
     "records from village Barkheda', 'which document has survey no 45/2?', "
     "'show pending mutation records'. I search owner, survey, khasra, khata, "
     "village, district, year, type and status.\n"
     "2. The Records tab (🗂️) — text search + status filters over every "
     "document."),

    (r"latest|newest|recent|naye|naya|taaza|new (document|records?)|just uploaded",
     "Just say 'show the latest documents' and I'll list the most recently "
     "added records — or the Records tab always lists newest first."),

    (r"version|update|kaun sa version|which version|naya version",
     "Check the footer of any page or the Account tab (⚙️) → System Status "
     "panel — it always shows the running build version. The latest build "
     "notes are in the QUICKSTART.txt that comes with each release zip."),

    (r"duplicate|दुहेरी|पुनरावृत्ती|दोबारा|already (exists|stored|verified)",
     "If the AI flags 'Possible duplicate of record ...', the owner + survey + "
     "village combination matches an already stored document. If it is a "
     "genuine re-upload you can still verify it (the flag is a reminder, not a "
     "blocker); otherwise discard the new copy."),

    (r"ai (decision|badge|support|assistant|bot)|routine clear|review required|caution|discrepanc|ai kya hai",
     "The AI Decision Support card grades each record: ROUTINE CLEAR = all "
     "automated checks passed, a spot-check is enough. REVIEW REQUIRED = some "
     "fields have low OCR confidence or warnings - confirm them against the "
     "scan. CAUTION - DISCREPANCY = missing required fields, validation "
     "errors, a possible duplicate, or very low confidence - do not approve "
     "without manual review.\n\n"
     "And I'm the 🤖 assistant in the corner — everything I do (including "
     "document search) is local rule-based analysis: no internet, no API keys, "
     "no data leaves this PC."),
]

# "go to X" navigation actions
NAV = [
    (r"\b(go to|open|take me to|jump to|chalo) (the )?verif\w* (queue)?\b",  "queue",       "⏳ Verification Queue"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?queue\b",             "queue",       "⏳ Verification Queue"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?dashboard\b",          "dashboard",   "📊 Dashboard"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?records?\b",           "documents",   "🗂️ Records"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?compar\w*\b",          "compare",     "⚖️ Comparison"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?consisten\w*\b",       "consistency", "🔍 Consistency Check"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?upload\b",             "upload",      "📤 Upload"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?audit\b",              "audit",       "🔐 Audit Log"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?users?\b",             "users",       "👥 Users"),
    (r"\b(go to|open|take me to|jump to|chalo) (the )?(account|profile)\b",  "account",     "⚙️ Account"),
]

# "where is X" quick map
WHERE = [
    (r"audit",                 "the Audit Log tab (🔐) in the top menu"),
    (r"data|database|storage|backup",
     "the data\\ folder next to the app (dist\\LandRecordSystem\\data\\) — it holds "
     "landrec.db (the database) and your uploaded scans. Back it up by copying the "
     "folder; never delete it"),
    (r"dashboard|statistic|stat\b|chart", "the Dashboard tab (📊)"),
    (r"queue|pending",         "the Verification Queue tab (⏳)"),
    (r"compar",                "the Comparison tab (⚖️)"),
    (r"consisten",             "the Consistency Check tab (🔍)"),
    (r"learn|correction",      "the AI Feedback tab (🧠)"),
    (r"user",                  "the Users tab (👥)"),
    (r"system status|self-?test|tesseract|worker", "the Account tab (⚙️) → System Status panel"),
    (r"account|password|profile", "the Account tab (⚙️)"),
    (r"records?|document|abhilekh", "the Records tab (🗂️)"),
    (r"upload|scan|naya",      "the Upload tab (📤)"),
]

DOC_TYPE_KEYWORDS = [
    ("mutation",     r"mutation|namantaran|ferfar|दाखिल ?खारिज"),
    ("sale_deed",    r"sale deed|conveyance|registry|बिक्री"),
    ("tax_receipt",  r"tax receipt|revenue slip|lagan|टॅक्स|रसीद|टैक्स"),
    ("land_record",  r"khatauni|khasra|record of rights|land record|7.?12|jamabandi|patta|pahani|खतौनी|खसरा|भू-?अभिलेख"),
]

STATUS_KEYWORDS = [
    ("pending_review", r"pending|verification queue|queue|समीक्षाधीन"),
    ("returned",       r"returned|लौटा(व|या)"),
    ("draft",          r"draft|ड्राफ्ट"),
    ("verified",       r"verified|approved|सत्यापित|स्वीकृत"),
    ("auto_approved",  r"auto.?approv"),
]

STATS_TRIGGERS = r"how many|how much|total (number|count|records|documents)|count of|kitne|कितने|संख्या किती"
SEARCH_TRIGGERS = (r"\b(find|search|locate|show|list|display|which|who (owns|is)|owner of|status of|"
                   r"details of|dikha|dikhao|dhoondo|khojo|kaun|kahan|कौन|कोण|खोज|ढूंढो|शोधा|दिसवा|दिकाओ)\b|"
                   r"(record|document|abhilekh) (with|having|number|no\.?)")
LATEST_TRIGGERS = r"\b(latest|newest|recent(ly)?|naye|naya|taaza)\b"
DOC_ID_RE = r"\b[0-9a-f]{12}\b"
STOPWORDS = set("""
find search locate show list display which who owns own is are the of in from with
and or record records documents document abhilekh khatauni khatauni patta pahani
jamabandi mutation deed sale conveyance registry tax receipt lagan revenue slip
pending verification queue verified approved draft returned auto draft
khata khasra survey village district state owner malik swami name naam year
sabhi all number no doc how what where latest newest recent
""".split())


# --------------------------------------------------------------------------
# 1b) Feature knowledge — the newer modules (asked as their own section so
#     specific topics answer before the older generic help list)
# --------------------------------------------------------------------------
HELP_FEATURES = [
    # --- loans / encumbrance ---
    (r"\b(encumbrance|encumbered|loan|loans|mortgage|bandhak|bakaya|ऋण|बंधक|रिन|ec report|ec certificate|encumbrance certificate)\b",
     "🏦 LOAN / ENCUMBRANCE module (added on every record's detail view):\n"
     "• What it is — loans (mortgages/bandhak) registered against a survey number: "
     "creditor (bank), amount, mortgage date, settlement date, status active/settled\n"
     "• Where — open any record → the '🏦 Encumbrance / Loan Check' panel loads "
     "automatically; verifier+ can Add a loan or Settle one (settling = recording the "
     "bank's NOC/release)\n"
     "• Why it matters — an ACTIVE loan flags the land 🛡️ 'encumbered', stamps a ⚠ "
     "warning into any mutation approval of that land, and blocks a clean "
     "Encumbrance Certificate\n"
     "• Matching is land-based: same survey number + village (a loan saved without a "
     "village applies to the whole survey number)\n"
     "• Paper proof — '⬇ EC Report (13 yrs, PDF+QR)' downloads an Encumbrance-"
     "Certificate-style PDF\n"
     "Try: 'which records have an active loan?' or 'how many loans are active?'"),

    # --- court cases / litigation ---
    (r"\b(court|litigation|mukadma|मुकदमा|मुकदमा|vakalat|stay order|पटका|case (on|against) (the |this |a )?(land|record|survey|plot))\b",
     "⚖️ COURT CASE / LITIGATION tracking:\n"
     "• What it is — court cases tied to a survey number: case number, court name, "
     "parties, filed date, status (active / decided / withdrawn / settled)\n"
     "• Where — open any record → the '⚖️ Court Case Check' panel; verifier+ can "
     "register a new case or Close an active one with its outcome\n"
     "• Cascade when a case closes — the risk flag flips from ACTIVE_LITIGATION "
     "(critical) to CLOSED_LITIGATION_ON_RECORD (info), any 🟠 litigation banner "
     "clears, and the event is written to the audit trail; a transfer that happened "
     "DURING the case keeps its TRANSFER_DURING_LITIGATION flag by design\n"
     "• Active litigation has the same weight as an active loan: mutation approvals "
     "get stamped '⚠ APPROVED WITH ACTIVE LITIGATION…' and the verdict chip shows "
     "litigation until it is resolved\n"
     "Try: 'how many court cases are active?' or 'which records have a court case?'"),

    # --- fraud risk engine ---
    (r"\b(fraud|risk|dhokha|धोखा|forgery|forged|fake|suspicious|land fraud|欺诈)\b",
     "🛡️ FRAUD RISK engine (rule-based, local):\n"
     "• Open any record → the '🛡️ Risk' panel computes flags for the WHOLE LAND "
     "(all records + loans + mutations + court cases on that survey number)\n"
     "• Flags detected: ACTIVE_ENCUMBRANCE, SALE_DURING_ENCUMBRANCE, OWNER_CONFLICT_YEAR "
     "(same year, different owners), OWNER_CHANGE_NO_MUTATION (owner renamed without a "
     "mutation application — even across Hindi/English spellings), AREA_JUMP, "
     "REJECTED_CONFLICT_COPY, CHAIN_GAP, ACTIVE_LITIGATION, TRANSFER_DURING_LITIGATION\n"
     "• Verdict order: 🔴 encumbered → 🟠 litigation → 🔴 risk → 🟡 review → 🟢 clear\n"
     "• Every flag shows its evidence (which loan, case, deed or record caused it) — "
     "it is explainable AI, not a black box\n"
     "• Demo tip: upload a second document for the same survey number with a "
     "different owner and watch the conflict flags appear\n"
     "Try: 'which records are risky?' or 'risk of <record id>'"),

    # --- PDFs / certificates ---
    (r"\b(certified (copy|pdf)|certificate|pramanit|प्रमाणित|qr code|qr\b|download .*pdf|pdf .*download|ec pdf|village sheet pdf|report pdf)\b",
     "📄 The portal generates 3 authenticated PDFs, all with a QR code:\n"
     "1. Certified Copy — open a VERIFIED record → '⬇ Certified PDF': the full "
     "extracted record with a certification hash; the QR opens a public check page "
     "that proves the PDF was not altered\n"
     "2. EC Report — '⬇ EC Report (13 yrs, PDF+QR)': Encumbrance-Certificate style — "
     "'free of encumbrances in the last 13 years' or the loans found (internal risk "
     "report; the conclusive EC is issued by the Sub-Registrar, as stated on it)\n"
     "3. Village Sheet PDF — 🗺️ Real Map tab → pick a village → the village-sheet view "
     "lists every plot with owner/area and exports as PDF\n"
     "Land-history summary PDFs are on the History panel of each record. Ask: "
     "'how do I verify a certificate QR?' to learn the public-check flow."),

    # --- backup & restore ---
    (r"\b(backup|restore|export (all |the )?data|import (data|backup)|database backup|data (safety|safe))\b",
     "💾 BACKUP & RESTORE (Admin → ⚙️ Account tab → Backup card):\n"
     "• '⬇ Create backup' downloads ONE zip: landrec.db (every record, user, loan, "
     "case, audit entry) + all uploaded scans + a manifest.json (version, counts, "
     "created-by) — audit-logged\n"
     "• '⬆ Restore from backup' uploads that zip on any machine: it validates the "
     "zip and the SQLite database, KEEPS a safety copy of the current state in "
     "data/backup_before_restore_<timestamp>/, then swaps the data in\n"
     "Use it for judge demos (prepare data → restore it live), machine migration, or "
     "disaster recovery."),

    # --- recorded vs computed area cross-check (map) ---
    (r"\b(computed area|recorded area|measured area|calculated area|rakba|रकबा|"
     r"area (cross.?check|match|mismatch)|area.{0,25}(map|boundary))\b",
     "📐 RECORDED vs COMPUTED AREA — the map area cross-check:\n"
     "• 📄 Recorded — the क्षेत्रफल / area printed on the document, read by OCR "
     "(e.g. '1.8 acre'): what the paper claims\n"
     "• 📏 Computed — the area the portal measures from the record's map boundary "
     "(GPS corners, shoelace geometry): what the land actually spans\n"
     "• The two sit side by side on the Map boundary card and the record detail, "
     "with a badge: ✓ green when they agree within 5% (OCR/rounding noise is "
     "normal), ⚠ red 'N% OFF the recorded area' when they don't — the Map tab's "
     "'⚠ Area mismatch records' button lists them all with a coordinate editor\n"
     "• Guards for NEW-kind records: a polygon over ~494 acres (>20 lakh m²) is "
     "REFUSED as implausible (a classic OCR digit slip like 77.08 -> 7.08) and the "
     "last good boundary is kept; plausible-but->5%-off boundaries are written "
     "but auto-flagged to the review queue\n"
     "• A record needs a boundary for the computed area: draw it on 🗺️ Real Map, "
     "or upload a document of kind NEW (its 4 printed corner coordinates set the "
     "boundary automatically)\n"
     "Try: 'computed area of <record id>', 'area of survey 312', or "
     "'which records have an area mismatch?'"),

    # --- real map / gis / digitize ---
    (r"\b(real map|map tab|naksha|नक्शा|gis|boundary|boundaries|plot map|digitize|coordinates|satellite|topo|khasra map|bhu.?naksha)\b",
     "🗺️ REAL MAP tab:\n"
     "• Every record with a 📍 location appears as a marker; toggle 'Show: Selected / "
     "All records'; pick a marker row to focus it\n"
     "• Layers: OpenStreetMap (with automatic mirror fallbacks) → Esri satellite → "
     "Carto → Topo — if one tile server is unreachable it fails over\n"
     "• Village sheet: pick a village → every plot listed (survey, owner, area, "
     "status) with a PDF export\n"
     "• Boundary tools: drop a pin on a record, or draw the plot boundary polygon; "
     "the point-in-polygon check verifies a record's pin lies inside its own boundary\n"
     "• Digitize coordinates: a khatauni shows corner coordinates? Enter them in the "
     "Digitize panel and the plot polygon is generated from them\n"
     "Try: 'where is the map?' or open 🗺️ Real Map and press the village selector."),

    # --- mutation workflow ---
    (r"\b(mutation (process|workflow|status|apply|kya)|namantaran|ferfar|दाखिल ?खारिज|owner(ship)? (change|transfer) (process|kaise))\b",
     "📝 MUTATION (ownership transfer / नामांतरण) workflow:\n"
     "• Apply from the Mutations tab with the deed number/date; statuses go "
     "received → under_review → verified (or rejected)\n"
     "• SAFETY GATE: approving a mutation on a land with an active loan or active "
     "court case still lets the officer proceed, but the approval note is "
     "auto-stamped '⚠ APPROVED WITH ACTIVE ENCUMBRANCE/LITIGATION…' and a special "
     "audit event is written — officer responsibility stays explicit\n"
     "• The risk engine cross-reads mutations: a sale dated during a live loan → "
     "SALE_DURING_ENCUMBRANCE; an owner renamed in records without a mutation → "
     "OWNER_CHANGE_NO_MUTATION\n"
     "Try: 'how many mutations are pending?'"),

    # --- per-record audit trail (new) ---
    (r"\b(per.?record audit|audit trail|record( 's| s)? audit|history of (this |a )?record|kaun kaun (se )?action|who (verified|changed|touched)|किसने (सत्यापित|बदला))\b",
     "📜 AUDIT TRAIL — two levels:\n"
     "1. Per record: open any record → '📜 Audit Trail' panel (next to History) — "
     "every action ON that record, oldest first: who uploaded it, draft saves, "
     "verification, returns/rejections with notes, mutation gate stamps, PDF "
     "downloads — each row with the tamper-evident hash prefix\n"
     "2. Whole system: the 🔐 Audit Log tab (admin) — the global hash-chained log "
     "with an integrity self-check ('✓ audit chain intact')\n"
     "Deleting a record never deletes its audit rows — accountability survives."),

    # --- SA mode itself ---
    (r"\b(superior admin|sa mode|sa feature|hidden mode|activate sa|what is sa)\b",
     "🛡️ SA = Superior Administrator mode — the hidden secure layer of this assistant "
     "(admins only). Type 'SA' here → pick an administrator identity → re-enter that "
     "admin's password (checked server-side only). In SA mode I can plan and "
     "coordinate across features: 'verify record <id>', 'reject record <id> because "
     "…', 'delete record <id>', 'assign record <id> to verification officer'. "
     "SA also answers ANY question about the ⚖️ court database (pending / stay / "
     "decided cases, courts covered, parties, next hearings, a case number) — and, "
     "like normal mode, runs the 📐 area cross-check ('computed area of <record id>', "
     "'which records have an area mismatch?'). "
     "Consequential actions still stop at the 📋 AI Approval Center, and everything "
     "I do is logged in the 📊 SA Activity Report. The session lasts until you "
     "type 'exit SA' or log out (an app restart also ends it)."),
]


def _fields_of(d):
    return d.get("fields") or {}


def _g(fields, key):
    f = fields.get(key)
    return str(f.get("value", "") or "") if isinstance(f, dict) else ""


def _result_row(d):
    f = _fields_of(d)
    return {"id": d["id"], "filename": d.get("filename", ""),
            "doc_type": d.get("doc_type", ""), "status": d.get("status", ""),
            "owner": _g(f, "owner_name"),
            "survey": _g(f, "survey_number"),
            "village": _g(f, "village"),
            "matched": []}


def _stats(qn):
    d = store.dashboard_stats()
    if re.search(r"pending|queue", qn):
        return {"type": "stats", "results": [],
                "answer": "There are %d document(s) pending verification right now. "
                          "Open the Verification Queue tab to review them — or ask me "
                          "'show pending documents' to list them here." % d["pending_review"]}
    if re.search(r"verified|approved", qn):
        return {"type": "stats", "results": [],
                "answer": "There are %d verified record(s) (including %d auto-approved)."
                          % (d["verified"], d["auto_approved"])}
    if re.search(r"draft", qn):
        return {"type": "stats", "results": [],
                "answer": "There are %d draft(s) saved but not yet submitted." % d["drafts"]}
    if re.search(r"returned", qn):
        return {"type": "stats", "results": [],
                "answer": "There are %d record(s) returned for correction." % d["returned"]}
    return {"type": "stats", "results": [],
            "answer": ("Totals: %d document(s) — %d verified (incl. %d auto-approved), "
                       "%d pending verification, %d drafts, %d returned, %d rejected by "
                       "automated checks. Average OCR confidence %s%%."
                       % (d["total"], d["verified"], d["auto_approved"], d["pending_review"],
                          d["drafts"], d["returned"], d["rejected"], d["avg_ocr_confidence"]))}


def _latest():
    docs = store.list_documents(limit=5)
    if not docs:
        return {"type": "search", "results": [],
                "answer": "No documents in the database yet — upload one from the Upload tab."}
    lines = []
    for i, d in enumerate(docs, 1):
        f = _fields_of(d)
        lines.append("%d. #%s — %s | %s | owner: %s | survey: %s | %s | status: %s"
                     % (i, d["id"], d.get("filename", ""), d.get("doc_type", ""),
                        _g(f, "owner_name") or "—", _g(f, "survey_number") or "—",
                        _g(f, "village") or "—", (d.get("status") or "").replace("_", " ")))
    return {"type": "search",
            "answer": "The 5 most recently added documents:\n%s\nUse 'Open record' below to view any of them."
                      % "\n".join(lines),
            "results": [_result_row(d) for d in docs]}


def _doc_by_id(doc_id):
    doc = store.get_document(doc_id)
    if not doc:
        return {"type": "search", "results": [],
                "answer": "I couldn't find a document with ID '%s'. Check the 12-character "
                          "ID (visible in the Records tab) and try again." % doc_id}
    f = json.loads(doc.get("extracted_json") or "{}")
    val = lambda k: (f.get(k) or {}).get("value", "") if isinstance(f.get(k), dict) else ""
    lines = ["Document #%s — %s" % (doc["id"], doc.get("filename", "")),
             "Type: %s | Status: %s | OCR confidence: %s%%"
             % (doc.get("doc_type", ""), (doc.get("status") or "").replace("_", " "),
                round(doc.get("mean_conf") or 0))]
    for fid, label, _ in common.FIELD_DEFS:
        v = val(fid)
        if v:
            lines.append("%s: %s" % (label, v))
    _rec, _comp, _src = _area_facts(doc)
    if _comp is not None:
        _emoji, _verdict, _pct = _area_verdict(_rec, _comp)
        lines.append("📐 Map area: 📄 recorded %s | 📏 computed ≈ %.2f acres — %s %s"
                     % (_rec or "?", _comp / 4046.86, _emoji or "⚪",
                        (_verdict or "").split(" (")[0]))
    row = _result_row({"id": doc["id"], "filename": doc.get("filename", ""),
                       "doc_type": doc.get("doc_type", ""), "status": doc.get("status", ""),
                       "fields": {k: {"value": val(k)} for k in ("owner_name", "survey_number", "village")}})
    row["matched"] = ["document id"]
    return {"type": "search", "answer": "\n".join(lines), "results": [row]}


def _search(qn):
    docs = store.list_documents(limit=2000)

    statuses = None
    for st, pat in STATUS_KEYWORDS:
        if re.search(pat, qn):
            statuses = ("verified", "auto_approved") if st == "verified" else (st,)
            break
    dtype = None
    for dt, pat in DOC_TYPE_KEYWORDS:
        if re.search(pat, qn):
            dtype = dt
            break

    nums = re.findall(r"\b\d{1,4}(?:[/.]\d{1,4})?\b", qn)
    words = [w for w in re.findall(r"[\u0900-\u097F\u0980-\u09FF\u0B80-\u0BFF\u0C00-\u0C7F\u0C80-\u0CFF\u0A00-\u0A7F\u0B60-\u0B7F]{3,}|[A-Za-z]{3,}", qn)
             if w.lower() not in STOPWORDS]

    scored = []
    for d in docs:
        if statuses and d.get("status") not in statuses:
            continue
        if dtype and d.get("doc_type") != dtype:
            continue
        f = _fields_of(d)
        owner, survey = _g(f, "owner_name"), _g(f, "survey_number")
        khasra, khata, plot = _g(f, "khasra_number"), _g(f, "khata_number"), _g(f, "plot_number")
        village, district, year = _g(f, "village"), _g(f, "district"), _g(f, "khatauni_year")

        score, matched = 0, []
        if owner and words:
            ow = owner.lower()
            for w in words:
                if w.lower() in ow:
                    score += 3
                    matched.append("owner ~ '%s'" % w)
        if village and words:
            for w in words:
                if w.lower() in village.lower():
                    score += 2
                    matched.append("village ~ '%s'" % w)
        if district and words:
            for w in words:
                if w.lower() in district.lower():
                    score += 2
                    matched.append("district ~ '%s'" % w)
        for n in nums:
            nn = n.replace("/", "").replace(".", "")
            if survey and (n in survey or (nn and nn == survey.replace("/", "").replace(".", ""))):
                score += 4; matched.append("survey = %s" % n)
            elif khasra and n in khasra:
                score += 3; matched.append("khasra = %s" % n)
            elif khata and n in khata:
                score += 3; matched.append("khata = %s" % n)
            elif plot and n in plot:
                score += 2; matched.append("plot = %s" % n)
            elif year and n in year:
                score += 2; matched.append("year ~ %s" % n)
        if score > 0:
            scored.append((score, d, matched))

    scored.sort(key=lambda x: -x[0])
    top = scored[:5]
    if not top and (statuses or dtype) and not words and not nums:
        # Pure list request ("show pending documents") - show the first
        # filter matches, newest first, without requiring a text/number hit.
        pool = [d for d in docs
                if (not statuses or d.get("status") in statuses)
                and (not dtype or d.get("doc_type") == dtype)]
        if pool:
            top = [(0, d, ["status/type list"]) for d in pool[:5]]
            lines = []
            for i, (score, d, matched) in enumerate(top, 1):
                f = _fields_of(d)
                lines.append("%d. #%s — %s | %s | owner: %s | survey: %s | %s, %s"
                             % (i, d["id"], d.get("filename", ""), d.get("doc_type", ""),
                                _g(f, "owner_name") or "—", _g(f, "survey_number") or "—",
                                _g(f, "village") or "—", _g(f, "district") or "—"))
            return {"type": "search",
                    "answer": "Showing %d of %d matching record(s) (newest first):\n%s\n"
                              "Use 'Open record' below to view any of them - or ask 'show more'."
                              % (len(top), len(pool), "\n".join(lines)),
                    "results": [dict(_result_row(d), matched=matched) for (score, d, matched) in top],
                    "_scored": [(0, d, ["status/type list"]) for d in pool]}
    if not top:
        hint = "No matching record(s) found."
        if dtype:
            hint += " (document type: %s)" % dtype
        if statuses:
            hint += " (status: %s)" % ", ".join(statuses)
        return {"type": "search", "results": [],
                "answer": hint + " Tip: OCR'd owner names may be slightly misspelled - "
                                 "try fewer letters, a number, or just the village name."}
    lines = []
    for i, (score, d, matched) in enumerate(top, 1):
        f = _fields_of(d)
        lines.append("%d. #%s — %s | %s | owner: %s | survey: %s | %s, %s | matched: %s"
                     % (i, d["id"], d.get("filename", ""), d.get("doc_type", ""),
                        _g(f, "owner_name") or "—", _g(f, "survey_number") or "—",
                        _g(f, "village") or "—", _g(f, "district") or "—",
                        ", ".join(matched[:4])))
    return {"type": "search",
            "answer": "Found %d match(es):\n%s\nUse 'Open record' below to view any of them - or ask 'show more'."
                      % (len(top), "\n".join(lines)),
            "results": [dict(_result_row(d), matched=matched) for (score, d, matched) in top],
            "_scored": scored}


def _nav(qn):
    for pat, tab, label in NAV:
        if re.search(pat, qn):
            return {"type": "action", "results": [],
                    "answer": "Sure — press the button to jump to %s." % label,
                    "action": {"type": "switch_tab", "tab": tab, "label": label}}
    return None


def _top_villages(n=5):
    docs = store.list_documents(limit=5000)
    cnt = {}
    for d in docs:
        v = _g(_fields_of(d), "village")
        if v:
            cnt[v] = cnt.get(v, 0) + 1
    return sorted(cnt.items(), key=lambda x: -x[1])[:n]


def _analytics(qn):
    d = store.dashboard_stats()
    # top districts / states / villages
    if re.search(r"\b(top|most)\b.*\b(district|village|state)s?\b|\bwhich (district|village|state)s?\b|\b(district|village|state)s? (top|list|wise)\b", qn):
        if re.search(r"\bvillages?\b", qn):
            rows = _top_villages()
            head = "Top villages by number of records:"
        elif re.search(r"\bstates?\b", qn):
            rows = list(d["by_state"].items())[:5]
            head = "Top states by number of records:"
        else:
            rows = list(d["by_district"].items())[:5]
            head = "Top districts by number of records:"
        if not rows:
            return {"type": "help", "results": [], "answer": "No village/district data has been extracted yet."}
        body = "\n".join("%d. %s — %d record(s)" % (i, k, v) for i, (k, v) in enumerate(rows, 1))
        return {"type": "help", "results": [], "answer": head + "\n" + body}
    # most common document type
    if re.search(r"\b(most common|top|popular)\b.*\b(document )?types?\b|\bwhat (are|is) the (document )?types\b", qn) and re.search(r"\b(most common|top|popular|count)\b", qn):
        if not d["by_type"]:
            return {"type": "help", "results": [], "answer": "No documents yet."}
        body = "\n".join("%d. %s — %d record(s)" % (i, k, v)
                         for i, (k, v) in enumerate(sorted(d["by_type"].items(), key=lambda x: -x[1]), 1))
        return {"type": "help", "results": [], "answer": "Document types by count:\n" + body}
    # low-confidence / needs-review records
    if re.search(r"\b(low confidence|needs? review|which (records?|documents?) need|flagged (records?|documents?)|review (list|queue))\b", qn):
        docs = [x for x in store.list_documents(limit=2000) if x.get("status") == "pending_review"]
        docs.sort(key=lambda x: x.get("mean_conf") or 0)
        top = docs[:5]
        if not top:
            return {"type": "search", "results": [],
                    "answer": "No records are currently pending verification, so nothing needs a low-confidence review right now."}
        lines = []
        for i, doc in enumerate(top, 1):
            f = _fields_of(doc)
            lines.append("%d. #%s — %s | owner: %s | survey: %s | OCR %s%%"
                         % (i, doc["id"], doc.get("filename", ""), _g(f, "owner_name") or "—",
                            _g(f, "survey_number") or "—", round(doc.get("mean_conf") or 0)))
        return {"type": "search",
                "answer": "%d record(s) pending verification, shown from LOWEST OCR confidence first (these need the most human checking):\n%s\nUse 'Open record' to review one."
                          % (len(docs), "\n".join(lines)),
                "results": [_result_row(doc) for doc in top]}
    return None


def _compare_docs(a_id, b_id):
    a, b = store.get_document(a_id), store.get_document(b_id)
    if not a or not b:
        missing = a_id if not a else b_id
        return {"type": "unknown", "results": [],
                "answer": "I couldn't find document #%s, so I can't compare. Check the 12-character ID and try again." % missing}
    fa = json.loads(a["extracted_json"] or "{}")
    fb = json.loads(b["extracted_json"] or "{}")
    rows, highlights = [], []
    for fid, disp, _ in common.FIELD_DEFS:
        va = str(fa.get(fid, {}).get("value", "") or "") if isinstance(fa.get(fid), dict) else ""
        vb = str(fb.get(fid, {}).get("value", "") or "") if isinstance(fb.get(fid), dict) else ""
        rows.append({"field": fid, "label": disp, "a": va, "b": vb, "changed": va != vb})
        if va and vb and va != vb:
            if fid == "owner_name":
                highlights.append("Ownership change")
            if fid == "area":
                highlights.append("Area variance")
            if fid in ("survey_number", "khasra_number", "khata_number", "plot_number"):
                highlights.append("%s modified" % disp)
    exp = ai_support.compare_explanation({"filename": a["filename"]}, {"filename": b["filename"]}, rows, highlights)
    return {"type": "search",
            "answer": exp + "\n\nUse the buttons below to open either record.",
            "results": [_result_row(a), _result_row(b)]}


def _explain_doc(did):
    doc = store.get_document(did)
    if not doc:
        return {"type": "search", "results": [], "answer": "I couldn't find document #%s to explain." % did}
    f = json.loads(doc["extracted_json"] or "{}")
    v = json.loads(doc["validation_json"] or "{}")
    ai = ai_support.decision_support(f, v, doc.get("mean_conf") or 0, doc.get("status") or "")
    lines = ["Document #%s — %s" % (doc["id"], doc.get("filename", "")),
             "Recommendation: %s" % ai["recommendation"],
             "Summary: %s" % ai["summary"],
             "Why: %s" % ai["explanation"]]
    if ai["flags"]:
        lines.append("Flags: " + "; ".join(ai["flags"]))
    return {"type": "search", "answer": "\n".join(lines), "results": [_result_row(doc)]}


def _action_open(did):
    doc = store.get_document(did)
    if not doc:
        return {"type": "unknown", "results": [], "answer": "I couldn't find document #%s to open." % did}
    row = _result_row(doc)
    row["matched"] = ["direct open"]
    return {"type": "action",
            "answer": "Here is document #%s — %s (status: %s). Press the button to open it in the Records view."
                      % (did, doc.get("filename", ""), (doc.get("status") or "").replace("_", " ")),
            "results": [row],
            "action": {"type": "open_doc", "doc_id": did, "label": "📄 Open record #%s" % did}}


def _action_verify(did, user):
    doc = store.get_document(did)
    if not doc:
        return {"type": "unknown", "results": [], "answer": "I couldn't find document #%s to verify." % did}
    fields = json.loads(doc.get("extracted_json") or "{}")
    try:
        p = store.create_proposal(
            "verify_document", did,
            "AI proposal: verify record #%s as-is (no field changes)" % did,
            "Record #%s — %s (status: %s)" % (did, doc.get("filename", ""), doc.get("status") or ""),
            {"status": doc.get("status"), "fields": fields},
            {"status": "verified"},
            "Requested via AI assistant", user)
    except Exception as e:  # noqa: BLE001
        return {"type": "unknown", "results": [], "answer": "Could not draft the proposal: %s" % e}
    return {"type": "proposal", "results": [],
            "answer": ("I drafted proposal #%s: VERIFY record #%s (%s) exactly as it is. "
                       "Nothing has changed yet — it goes to the AI Approval Center, where a "
                       "Verification Officer / Admin re-authorizes it (the system re-checks the "
                       "record's state and executes it only once, writing it to the audit trail)."
                       % (p["id"], did, doc.get("filename", ""))),
            "action_card": {"proposal_id": p["id"], "action_type": p["action_type"],
                            "action_description": "Verify record #%s as-is" % did,
                            "target_display": p["target_display"],
                            "label": "📋 Open Approval Center"}}


def _action_delete(did, user):
    doc = store.get_document(did)
    if not doc:
        return {"type": "unknown", "results": [], "answer": "I couldn't find document #%s to delete." % did}
    try:
        p = store.create_proposal(
            "delete_document", did,
            "AI proposal: permanently delete record #%s" % did,
            "Record #%s — %s (status: %s)" % (did, doc.get("filename", ""), doc.get("status") or ""),
            {"status": doc.get("status")},
            {"status": "deleted"},
            "Requested via AI assistant", user)
    except Exception as e:  # noqa: BLE001
        return {"type": "unknown", "results": [], "answer": "Could not draft the proposal: %s" % e}
    return {"type": "proposal", "results": [],
            "answer": ("⚠ I drafted proposal #%s: PERMANENTLY DELETE record #%s (%s). "
                       "Nothing has changed yet — open the AI Approval Center to approve it. "
                       "Approving deletes the scan file (the audit trail is kept)."
                       % (p["id"], did, doc.get("filename", ""))),
            "action_card": {"proposal_id": p["id"], "action_type": p["action_type"],
                            "action_description": "Permanently delete record #%s" % did,
                            "target_display": p["target_display"],
                            "label": "📋 Open Approval Center"}}


def _show_more(user_id):
    saved = _last_results.get(user_id)
    if not saved or saved["offset"] >= len(saved["all"]):
        return None
    allres = saved["all"]
    start = saved["offset"]
    top = allres[start:start + 5]
    saved["offset"] = start + 5
    lines = []
    for i, (score, d, matched) in enumerate(top, start + 1):
        f = _fields_of(d)
        lines.append("%d. #%s — %s | %s | owner: %s | survey: %s | matched: %s"
                     % (i, d["id"], d.get("filename", ""), d.get("doc_type", ""),
                        _g(f, "owner_name") or "—", _g(f, "survey_number") or "—",
                        ", ".join(matched[:3])))
    left = len(allres) - saved["offset"]
    return {"type": "search",
            "answer": ("Showing the next %d of your previous search (%d more after these):\n%s"
                       % (len(top), max(left, 0), "\n".join(lines))) if top else "No more results from the previous search.",
            "results": [dict(_result_row(d), matched=matched) for (score, d, matched) in top]}


RISK_TERMS = (r"\b(loan|loans|encumbran[a-z]*|mortgage|bandhak|ऋण|court|courts|litigation|mukadma|मुकदमा|"
              r"risk|risky|fraud|fraudulent|dhokha|धोखा|suspicious)\b")
LOAN_TERMS = r"(loan|loans|encumbran[a-z]*|mortgage|bandhak|ऋण)"
CASE_TERMS = r"(court|courts|litigation|mukadma|मुकदमा|case|cases)"


def _norm_s(v):
    return " ".join(str(v or "").strip().lower().split())


def _land_flags(survey, village):
    """Full risk computation for one land (records + loans + mutations + cases)."""
    encs = store.list_encumbrances(survey, village)
    cases = store.list_court_cases(survey, village)
    muts = [m for m in store.list_mutations(limit=300)
            if _norm_s(m.get("survey_number")) == _norm_s(survey)
            and (not village or _norm_s(m.get("village")) == _norm_s(village)
                 or not m.get("village"))]
    hist = store.record_history(survey, village)
    flags = risk.land_risk(hist, encs, muts, cases)
    active = sum(1 for e in encs if e.get("status") == "active")
    active_cases = sum(1 for c in cases if c.get("status") == "active")
    if active:
        verdict = "encumbered"
    elif active_cases:
        verdict = "litigation"
    elif any(f["severity"] == "critical" for f in flags):
        verdict = "risk"
    elif flags:
        verdict = "review"
    else:
        verdict = "clear"
    return {"encs": encs, "cases": cases, "muts": muts, "flags": flags,
            "active": active, "active_cases": active_cases, "verdict": verdict}


_VERDICT_EMOJI = {"encumbered": "🔴 ENCUMBERED", "litigation": "🟠 LITIGATION",
                  "risk": "🔴 RISK", "review": "🟡 REVIEW", "clear": "🟢 CLEAR",
                  "no_survey": "⚪ NO SURVEY NUMBER"}


def _doc_risk(did):
    doc = store.get_document(did)
    if not doc:
        return {"type": "search", "results": [],
                "answer": "I couldn't find document #%s. Check the 12-character ID in the Records tab." % did}
    f = json.loads(doc.get("extracted_json") or "{}")
    v = lambda k: (f.get(k) or {}).get("value", "") if isinstance(f.get(k), dict) else ""
    survey, village = v("survey_number"), v("village")
    if not survey:
        return {"type": "search", "results": [_result_row(doc)],
                "answer": ("Record #%s has no survey number extracted, so I cannot tie it to a "
                           "land for loan/court/risk checks. Correct the fields in the record "
                           "detail and verify it first.") % did}
    lz = _land_flags(survey, village)
    lines = ["🛡️ Land risk — record #%s · survey %s · %s" % (did, survey, village or "—"),
             "Verdict: %s" % _VERDICT_EMOJI[lz["verdict"]]]
    if lz["encs"]:
        act = [e for e in lz["encs"] if e.get("status") == "active"]
        lines.append("🏦 Loans: %d registered (%d active)" % (len(lz["encs"]), len(act)))
        for e in (act or lz["encs"])[:3]:
            lines.append("   · %s — ₹%s — %s — status %s" % (
                e.get("creditor") or "creditor",
                "{:,.0f}".format(e["amount"]) if e.get("amount") else "—",
                e.get("mortgage_date") or "date ?", e.get("status")))
    else:
        lines.append("🏦 Loans: none registered on this land")
    if lz["cases"]:
        act = [c for c in lz["cases"] if c.get("status") == "active"]
        lines.append("⚖️ Court cases: %d on record (%d active)" % (len(lz["cases"]), len(act)))
        for cs in (act or lz["cases"])[:3]:
            lines.append("   · %s — %s — filed %s — %s" % (
                cs.get("case_number") or "case", cs.get("court_name") or "court",
                cs.get("filed_date") or "?", cs.get("status")))
    else:
        lines.append("⚖️ Court cases: none on this land")
    if lz["flags"]:
        lines.append("🚩 Flags (%d):" % len(lz["flags"]))
        for fl in lz["flags"][:5]:
            lines.append("   · [%s] %s" % (fl["severity"].upper(), fl["title"]))
    else:
        lines.append("🚩 Flags: none — this land is clean")
    lines.append("Open the record → 🏦 / ⚖️ / 🛡️ panels for full evidence, or download the "
                 "'⬇ EC Report (PDF+QR)'.")
    row = _result_row(doc)
    row["matched"] = ["land risk query"]
    return {"type": "search", "answer": "\n".join(lines), "results": [row]}


def _risk_lands(qn, want_rows=True):
    """Screen every land in the database and list the ones caught by the
    loan / court-case / risk focus of the question."""
    focus_loan = re.search(LOAN_TERMS, qn)
    focus_case = re.search(CASE_TERMS, qn) and not re.search(r"\b(case|cases) (study|s)\b", qn)
    docs = store.list_documents(limit=400)
    lands = {}
    for d in docs:
        f = _fields_of(d)
        survey = _g(f, "survey_number").strip()
        if not survey:
            continue
        key = (_norm_s(survey), _norm_s(_g(f, "village")))
        lands.setdefault(key, []).append(d)
    if not lands:
        return {"type": "search", "results": [],
                "answer": "No records with a survey number yet — risk screening needs "
                          "records that carry a survey/khasra number."}
    hits, clear = [], 0
    for (sk, vk), ds in lands.items():
        survey = _g(_fields_of(ds[0]), "survey_number").strip()
        village = _g(_fields_of(ds[0]), "village").strip()
        lz = _land_flags(survey, village)
        ok = False
        if focus_loan and lz["active"]:
            ok = True
        if focus_case and lz["active_cases"]:
            ok = True
        if not focus_loan and not focus_case and lz["verdict"] in ("encumbered", "litigation", "risk"):
            ok = True
        if lz["verdict"] == "clear":
            clear += 1
        if not ok:
            continue
        rep = next((d for d in ds if d.get("status") in ("verified", "auto_approved")), ds[0])
        why = []
        if lz["active"]:
            e = next(e for e in lz["encs"] if e.get("status") == "active")
            why.append("active loan: %s" % (e.get("creditor") or "creditor"))
        if lz["active_cases"]:
            cs = next(c for c in lz["cases"] if c.get("status") == "active")
            why.append("active case: %s" % (cs.get("case_number") or "case"))
        why.extend(f["title"] for f in lz["flags"][:2] if f["code"] not in ("ACTIVE_ENCUMBRANCE", "ACTIVE_LITIGATION"))
        hits.append((lz, rep, "%s · survey %s · %s — %s"
                     % (_VERDICT_EMOJI[lz["verdict"]], survey, village or "—",
                        "; ".join(why[:3]) or "flagged")))
    if not hits:
        return {"type": "search", "results": [],
                "answer": "Good news 🟢 — no land in the database matches that concern "
                          "(%d land(s) screened, all clear)." % len(lands)}
    hits.sort(key=lambda h: 0 if h[0]["verdict"] in ("encumbered", "risk") else 1)
    lines = ["I screened %d land(s) in the database — %d need attention, %d clear:"
             % (len(lands), len(hits), clear)]
    for lz, rep, line in hits[:6]:
        lines.append("· #%s — %s" % (rep["id"], line))
    if len(hits) > 6:
        lines.append("…and %d more. Ask 'risk of <record id>' for any of them." % (len(hits) - 6))
    lines.append("Open a record below to see the 🏦/⚖️/🛡️ evidence panels.")
    rows = []
    for lz, rep, _ in hits[:5]:
        r = _result_row(rep)
        r["matched"] = ["land risk: " + lz["verdict"]]
        rows.append(r)
    return {"type": "search", "answer": "\n".join(lines), "results": rows}


# --------------------------------------------------------------------------
# Area cross-check: 📄 recorded (printed) vs 📏 computed (map boundary)
# --------------------------------------------------------------------------
AREA_WORD = r"\b(area|rakba|रकबा|क्षेत्रफल)\b"
AREA_FOCUS = (r"\b(computed|recorded|measured|calculated|printed)\b[^.]{0,40}\barea\b|"
              r"\barea\b[^.]{0,40}\b(computed|measured|calculated|match|mismatch|dismatch|"
              r"differ|map|boundary|coordinates|off|cross.?check)\b|"
              r"\barea (mis)?match\b|"
              r"\b(check|cross.?check|verify|milao|मिलाओ|मिलान)\b[^.]{0,25}\b(area|rakba|रकबा|क्षेत्रफल)\b|"
              r"\b(area|rakba|रकबा|क्षेत्रफल)\b[^.]{0,25}\b(check|verify|survey|khasra|sahi|galat)\b")
AREA_MAX_PLOT_M2 = 2_000_000.0  # same ~494-acre per-parcel guard as the pipeline


def _area_str_to_m2(area_str):
    """Mirror of the pipeline parser: '1.8 acre' / '45 सेंट' / '2 bigha' -> m².
    Unknown or absent unit -> assume acres (the Indian-records default)."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*([A-Za-z\u0900-\u097F\u0B80-\u0BFF]*)",
                  str(area_str or ""))
    if not m:
        return None
    val = float(m.group(1))
    unit = (m.group(2) or "").lower()
    if not unit or "acre" in unit or unit in ("ac", "acres", "acre.") \
       or "एकड़" in unit or "एकड" in unit or "ekad" in unit:
        return val * 4046.86
    if "cent" in unit or "सेंट" in unit:
        return val * 40.4686
    if "bigha" in unit or "बिगा" in unit or "बिगहा" in unit:
        return val * 2529.29
    return val * 4046.86


def _area_facts(doc):
    """(recorded_area_str, computed_m2, boundary_source) for one document row —
    works for both list_documents rows and get_document rows."""
    f = doc.get("fields")
    if not f:
        try:
            f = json.loads(doc.get("extracted_json") or "{}")
        except Exception:  # noqa: BLE001
            f = {}
    recorded = ""
    if isinstance(f.get("area"), dict):
        recorded = str(f.get("area", {}).get("value") or "").strip()
    comp = None
    if doc.get("boundary_geojson"):
        try:
            ring = json.loads(doc["boundary_geojson"])
            comp = extractor.ring_area_m2(ring)
        except Exception:  # noqa: BLE001
            comp = None
    return recorded, comp, doc.get("boundary_source")


def _area_verdict(recorded, comp):
    """(emoji, verdict_text, pct_off) — the same thresholds as the UI badge
    (✓ within 5%, ⚠ beyond; 🚩 above the implausible-area ceiling)."""
    if comp is None:
        return None, None, None
    if comp > AREA_MAX_PLOT_M2:
        return "🚩", ("IMPLAUSIBLE — the boundary spans %.0f acres, beyond the "
                      "~494-acre per-parcel ceiling; the coordinates are wrong"
                      % (comp / 4046.86)), None
    rec_m2 = _area_str_to_m2(recorded)
    if not rec_m2:
        return "⚪", "computed only (no recorded area text to compare)", None
    pct = abs(comp - rec_m2) / rec_m2 * 100.0
    if pct <= 5.0:
        return "✓", ("MATCHES the recorded area (%.0f%% apart — inside the 5%% "
                     "tolerance band)" % pct), pct
    return "⚠", ("MISMATCH — %.0f%% OFF the recorded area%s"
                 % (pct, " (the upload pipeline auto-flags >5% for review)"
                    if pct > 5 else "")), pct


def _doc_area(did):
    doc = store.get_document(did)
    if not doc:
        return {"type": "search", "results": [],
                "answer": "I couldn't find document #%s. Check the 12-character ID "
                          "in the Records tab." % did}
    recorded, comp, src = _area_facts(doc)
    srcname = {"document": "4 corner coordinates printed on this NEW-kind record",
               "digitized": "officer-digitized boundary",
               "imported": "imported boundary",
               "estimated": "estimated area-square boundary"}.get(src, "map boundary")
    lines = ["📐 AREA CROSS-CHECK — record #%s (%s)" % (did, doc.get("filename", "")),
             "📄 Recorded (OCR'd from the document): %s" % (recorded or "— none printed/extracted —")]
    if comp is not None:
        lines.append("📏 Computed (from the %s): %s m² ≈ %.2f acres"
                     % (srcname, "{:,.0f}".format(comp), comp / 4046.86))
        emoji, verdict, _pct = _area_verdict(recorded, comp)
        if verdict:
            lines.append("Verdict: %s %s" % (emoji, verdict))
    else:
        lines.append("📏 Computed: not available — this record has no map boundary yet.")
        lines.append("How to get one: open 🗺️ Real Map and draw the boundary (or use "
                     "the Digitize panel), OR upload the record as document kind NEW "
                     "with its 4 printed corner coordinates — the boundary and this "
                     "check then happen automatically.")
    lines.append("📄 Recorded = what the paper claims · 📏 Computed = what the GPS "
                 "corners measure. A big gap means a data-entry slip — or fraud.")
    row = _result_row(doc)
    row["matched"] = ["area cross-check"]
    return {"type": "search", "answer": "\n".join(lines), "results": [row]}


def _area_screen(qn):
    """Cross-check every boundary-backed record; or answer for one land when a
    survey number is named ('check the area of survey 145')."""
    docs = store.list_documents(limit=400)
    nums = re.findall(r"\b\d{1,4}(?:/\d{1,4})?\b", qn)
    if nums and not re.search(r"\b(all|every|each|sab|saare|kitne|total)\b", qn):
        for d in docs:
            surv = _g(_fields_of(d), "survey_number")
            for n in nums:
                nn = n.replace("/", "").replace(".", "")
                if surv and (n in surv
                             or (nn and nn == surv.replace("/", "").replace(".", ""))):
                    return _doc_area(d["id"])
    mism, matches, computed_only, no_boundary = [], [], 0, 0
    for d in docs:
        recorded, comp, _src = _area_facts(d)
        if comp is None:
            no_boundary += 1
            continue
        emoji, verdict, pct = _area_verdict(recorded, comp)
        if emoji == "✓":
            matches.append((pct or 0, d, verdict))
        elif emoji in ("⚠", "🚩"):
            mism.append((pct if pct is not None else 9999.0, d, verdict))
        else:
            computed_only += 1
    if not matches and not mism and not computed_only:
        return {"type": "search", "results": [],
                "answer": "No record has a map boundary yet, so there is nothing to "
                          "cross-check. Draw one on 🗺️ Real Map, or upload a NEW-kind "
                          "document with 4 printed corner coordinates — its boundary "
                          "is set automatically."}
    want_matches = (re.search(r"\b(match(ed|es|ing)?|correct|right|consistent|sahi|theek)\b", qn)
                    and not re.search(r"\b(mismatch|dismatch|not match|differ|galat|wrong|off|discrepanc)\b", qn))
    head = ("📐 Area cross-check — %d record(s) examined: %d match the recorded area ✓, "
            "%d mismatch ⚠, %d computed-only (no printed area), %d have no boundary."
            % (len(matches) + len(mism) + computed_only, len(matches), len(mism),
               computed_only, no_boundary))
    show = matches if want_matches else mism
    lines = [head]
    if not show:
        lines.append("🟢 No area mismatches found — every boundary-backed record "
                     "agrees with its paper area within tolerance." if not want_matches else
                     "No record's computed area matches a recorded area yet.")
    else:
        show.sort(key=lambda t: -t[0])
        for _pct, d, verdict in show[:6]:
            f = _fields_of(d)
            lines.append("· #%s — survey %s, %s — owner %s — %s"
                         % (d["id"], _g(f, "survey_number") or "?", _g(f, "village") or "?",
                            _g(f, "owner_name") or "?", verdict))
        if len(show) > 6:
            lines.append("…and %d more — ask 'computed area of <record id>' for any of them."
                         % (len(show) - 6))
    lines.append("Per-record check: 'area of survey 312' or 'computed area of <record id>'.")
    rows = []
    for _pct, d, _v in show[:5]:
        r = _result_row(d)
        r["matched"] = ["area cross-check"]
        rows.append(r)
    return {"type": "search", "answer": "\n".join(lines), "results": rows}


def _stats_features(qn):
    """Statistics for the newer modules (loans, cases, mutations, tasks,
    proposals, risk) — None when the question is not about them."""
    if re.search(LOAN_TERMS, qn):
        encs = store.all_encumbrances()
        act = [e for e in encs if e.get("status") == "active"]
        lines = ["🏦 %d loan(s)/encumbrance(s) registered — %d ACTIVE, %d settled."
                 % (len(encs), len(act), len(encs) - len(act))]
        for e in act[:4]:
            lines.append("· ACTIVE: %s — ₹%s on survey %s %s (since %s)" % (
                e.get("creditor") or "creditor",
                "{:,.0f}".format(e["amount"]) if e.get("amount") else "—",
                e.get("survey_number") or "?", ("(" + e["village"] + ")") if e.get("village") else "",
                e.get("mortgage_date") or "?"))
        lines.append("An active loan makes its land 🔴 encumbered — ask 'which records have an active loan?'.")
        return {"type": "stats", "results": [], "answer": "\n".join(lines)}
    if re.search(CASE_TERMS, qn):
        cases = store.all_court_cases()
        act = [c for c in cases if c.get("status") == "active"]
        by = {}
        for c in cases:
            by[c.get("status")] = by.get(c.get("status"), 0) + 1
        lines = ["⚖️ %d court case(s) on record — %s." % (
            len(cases), " · ".join("%d %s" % (n, s) for s, n in sorted(by.items())) or "none")]
        for c in act[:4]:
            lines.append("· ACTIVE: %s — %s, survey %s, filed %s" % (
                c.get("case_number") or "case", c.get("court_name") or "court",
                c.get("survey_number") or "?", c.get("filed_date") or "?"))
        lines.append("An active case makes its land 🟠 litigation — ask 'which records have a court case?'.")
        return {"type": "stats", "results": [], "answer": "\n".join(lines)}
    if re.search(r"mutation|namantaran|ferfar|ownership transfer", qn):
        m = store.mutation_counts()
        return {"type": "stats", "results": [],
                "answer": "📝 Mutations: %d received, %d under review, %d approved/verified, %d rejected."
                          % (m.get("received", 0), m.get("under_review", 0),
                             m.get("verified", 0), m.get("rejected", 0))}
    if re.search(r"task|assignment|delegate", qn):
        seen, open_t = set(), 0
        for r in ("operator", "verifier", "admin"):
            for t in store.list_tasks(r):
                if t["id"] in seen:
                    continue
                seen.add(t["id"])
                if str(t.get("status", "")).upper() not in ("COMPLETED", "CANCELLED"):
                    open_t += 1
        return {"type": "stats", "results": [],
                "answer": "🤖 AI Tasks: %d assigned so far, %d still open (check the '🤖 AI Tasks' "
                          "inbox, bottom-right)." % (len(seen), open_t)}
    if re.search(r"proposal|approval center|pending approval", qn):
        pend = store.list_proposals(status="PENDING")
        return {"type": "stats", "results": [],
                "answer": "📋 AI Approval Center: %d proposal(s) waiting for a human decision."
                          % len(pend)}
    if re.search(r"risk|fraud|flag|suspicious|encumbered|litigation", qn):
        r = _risk_lands(qn.replace("how many", "show").replace("kitne", "show"),
                        want_rows=False)
        text = r["answer"]
        first = text.split("\n")[0]
        return {"type": "stats", "results": r.get("results", []),
                "answer": "🛡️ Risk screening — " + first + "\n(full list: ask 'which records are risky?')"}
    return None


def answer(q: str, user_id: str = None, role: str = None, user: dict = None) -> dict:
    qn = " ".join((q or "").lower().split())
    if not qn:
        return {"type": "unknown", "results": [], "answer": "Type a question first."}

    # 0.5) "show more" - paginate the previous search for THIS user
    if re.search(r"^\s*(show (me )?more|more (results|records|documents)?|aage|aur (dikhao|diko|results)|next (5|page|batch))\b", qn):
        r = _show_more(user_id)
        if r:
            return r

    # 0) small talk - only for short queries
    if len(qn.split()) <= 3:
        for pat, ans in SMALLTALK:
            if re.search(pat, qn):
                return {"type": "chat", "results": [], "answer": ans}

    # 1) stats questions — newer modules first (loans/court cases/mutations/
    #    tasks/proposals/risk), then the classic record counters
    if re.search(STATS_TRIGGERS, qn):
        r = _stats_features(qn)
        return r if r else _stats(qn)

    # 1.5) analytics (top districts/villages, common types, low-confidence list)
    r = _analytics(qn)
    if r:
        return r

    # 2) document(s) addressed by their 12-char ID
    ids = re.findall(DOC_ID_RE, qn)
    if re.search(r"\b(compare|tumana|vs|versus|diff|difference|फरक|तुलना)\b", qn) and ids:
        if len(ids) >= 2:
            return _compare_docs(ids[0], ids[1])
        return {"type": "unknown", "results": [],
                "answer": "To compare, I need TWO valid 12-character document IDs (letters a-f and digits) "
                          "separated by 'and' or 'vs' - example: 'compare 6f380d677145 and 3a43a4edd852'. "
                          "You can find the IDs in the Records tab."}
    if len(ids) == 1:
        did = ids[0]
        if re.search(r"\b(verify|approve|santyaptit|सत्यापित|सत्यापित करा)\b", qn):
            if role in ("verifier", "admin"):
                return _action_verify(did, user or {"id": user_id or "", "email": "assistant", "full_name": "AI Assistant"})
            return {"type": "unknown", "results": [],
                    "answer": "Verifying records needs Verification Officer or Admin rights - "
                              "your role is '%s'. A Verification Officer can open the record "
                              "and press 'Save & Verify'." % (role or "unknown")}
        if re.search(r"\b(delete|remove|mitao|mitavo|mita|hatavo|हटाओ|हटवा|मिटा|मिटाव)\b", qn):
            if role == "admin":
                return _action_delete(did, user or {"id": user_id or "", "email": "assistant", "full_name": "AI Assistant"})
            return {"type": "unknown", "results": [],
                    "answer": "Deleting records is an Admin-only action - your role is '%s'. "
                              "Ask an Admin, or use the trash icon in the Records tab if you are one." % (role or "unknown")}
        if re.search(r"\b(explain|matlab|samjhao|vivechana|व्याख्या|समजा|समझाओ)\b", qn):
            return _explain_doc(did)
        if re.search(r"\b(open|khole|kholo|खोल|विर)\b", qn):
            return _action_open(did)
        if re.search(RISK_TERMS, qn) or re.search(CASE_TERMS, qn):
            return _doc_risk(did)
        if re.search(AREA_WORD, qn):
            return _doc_area(did)
        r = _doc_by_id(did)
        if r["results"]:
            return r

    # 3) latest / recent documents (checked before generic search so that
    #    'show the latest documents' lists recents instead of searching text)
    if re.search(LATEST_TRIGGERS, qn):
        return _latest()

    # 3.5) "go to X" navigation actions
    r = _nav(qn)
    if r:
        return r

    # 3.8) targeted precedence fixes vs the terse "where is X" map:
    #      'audit trail of a record' deserves the per-record answer (not the
    #      global Audit Log pointer), and backup/restore the full answer.
    if re.search(r"audit trail|per.?record audit|record('s|s)? audit|backup|restore", qn):
        for pat, ans in HELP_FEATURES:
            if re.search(pat, qn):
                return {"type": "help", "results": [], "answer": ans}

    # 4) "where is X" quick map
    if re.search(r"\bwhere (is|do i|can i|to|from|shall i)\b", qn):
        for pat, place in WHERE:
            if re.search(pat, qn):
                return {"type": "help", "results": [],
                        "answer": "You'll find it in %s." % place}

    # 4.4) recorded-vs-computed map area: per-land or whole-database screening.
    #      'what is the computed area?' style definitional questions fall
    #      through to the explainer in HELP_FEATURES below.
    if re.search(AREA_WORD, qn) and re.search(AREA_FOCUS, qn) \
            and not (re.search(r"\b(what is|what's|kya (hai|hota)|matlab|meaning|"
                               r"difference between|sunjhao|samjhao|explain)\b", qn)
                     and not re.search(SEARCH_TRIGGERS, qn)):
        return _area_screen(qn)

    # 4.5) land-level screening: "show records with a loan / court case / risk / fraud"
    if re.search(RISK_TERMS, qn) and re.search(SEARCH_TRIGGERS, qn):
        return _risk_lands(qn)

    # 5) explicit document search / document questions
    if re.search(SEARCH_TRIGGERS, qn) or re.search(r"\b\d{1,4}(?:[/.]\d{1,4})?\b", qn):
        r = _search(qn)
        if r["results"]:
            if user_id:
                scored = r.pop("_scored", None)
                if scored:
                    _last_results[user_id] = {"all": scored, "offset": len(r["results"])}
            r.pop("_scored", None)
            return r
        if re.search(SEARCH_TRIGGERS, qn):
            return r  # they explicitly asked to find something - report the miss

    # 6) website help — newest features first
    for pat, ans in HELP_FEATURES:
        if re.search(pat, qn):
            return {"type": "help", "results": [], "answer": ans}
    for pat, ans in HELP:
        if ans is None:
            continue
        if re.search(pat, qn):
            return {"type": "help", "results": [], "answer": ans}

    # 7) last resort: looks like names? try search once more
    if re.search(r"[\u0900-\u097F]{4,}|[A-Za-z]{4,}", qn):
        r = _search(qn)
        if r["results"]:
            r.pop("_scored", None)
            return r

    return {"type": "unknown", "results": [],
            "answer": "I couldn't match that to a website topic or a document search. "
                      "Try: 'How does verification work?', 'What is the encumbrance module?', "
                      "'What do court-case statuses mean?', 'How does fraud risk work?', "
                      "'Which records are risky?', 'How many loans are active?', "
                      "'Which records have an area mismatch?', "
                      "'Computed area of <record id>', 'Find khatauni of <name>', "
                      "'Compare <id> and <id>', 'What is SA mode?' — "
                      "or 'Show more' after a search."}


def briefing(user_id=None, role=None):
    """System briefing for the AI Admin Assistant: one synthesized
    summary of what the administrator needs to know right now."""
    stats = store.dashboard_stats()
    sla = store.sla_stats()
    mut = store.mutation_counts()
    docs = store.list_documents(limit=500)
    low = [d for d in docs if (d.get("mean_conf") or 100) < 70 and d.get("status") in ("pending_review", "draft")]
    low.sort(key=lambda d: d.get("mean_conf") or 0)
    recent = store.get_audit(limit=8) if hasattr(store, "get_audit") else []
    recent_lines = []
    for a in recent[:8]:
        recent_lines.append("%s — %s (%s)" % (
            time.strftime("%d %b %H:%M", time.localtime(a.get("ts") or 0)),
            a.get("action", ""), a.get("username") or ""))
    lines = []
    lines.append("📊 SYSTEM BRIEFING — " + time.strftime("%d %b %Y %H:%M"))
    lines.append("")
    lines.append("📁 RECORDS: %d total · %d verified · %d pending verification · %d drafts · %d returned · %d rejected"
                 % (stats.get("total", 0), stats.get("verified", 0), stats.get("pending_review", 0),
                    stats.get("drafts", 0), stats.get("returned", 0), stats.get("rejected", 0)))
    lines.append("⏱ SLA: %d pending — %d waiting over 30 days (action needed)"
                 % (sla.get("pending", 0), sla.get("overdue_30d", 0)))
    lines.append("📝 MUTATIONS: %d received · %d under review · %d approved"
                 % (mut.get("received", 0), mut.get("under_review", 0), mut.get("verified", 0)))
    try:
        encs = store.all_encumbrances()
        act_enc = sum(1 for e in encs if e.get("status") == "active")
        if encs:
            lines.append("🏦 LOANS: %d registered · %d ACTIVE (lands encumbered)" % (len(encs), act_enc))
        cases = store.all_court_cases()
        act_cs = sum(1 for c in cases if c.get("status") == "active")
        if cases:
            lines.append("⚖️ COURT CASES: %d on record · %d ACTIVE (lands in litigation)" % (len(cases), act_cs))
    except Exception:  # noqa: BLE001
        pass
    avg = stats.get("avg_ocr_confidence")
    if avg:
        lines.append("🔤 OCR quality: average confidence %.1f%% across all records" % avg)
    if low:
        lines.append("⚠ LOW-CONFIDENCE records to review first:")
        for d in low[:5]:
            f = d.get("fields") or {}
            own = (f.get("owner_name") or {}).get("value") or "—"
            lines.append("   · #%s — %s | conf %.0f%% | owner: %s"
                         % (d["id"], d.get("filename", ""), d.get("mean_conf") or 0, own))
    else:
        lines.append("✅ No low-confidence (<70%) records waiting.")
    if recent_lines:
        lines.append("")
        lines.append("🕓 RECENT ACTIVITY:")
        lines.extend("   · " + l for l in recent_lines)
    pend_prop = store.list_proposals(status="PENDING")
    if pend_prop:
        lines.append("")
        lines.append("📋 %d AI proposal(s) awaiting approval in the Approval Center." % len(pend_prop))
    lines.append("")
    lines.append("Ask me for details on any of these — e.g. 'show low-confidence records' or 'find pending records'.")
    return {"briefing": "\n".join(lines)}
