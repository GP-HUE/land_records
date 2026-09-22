"""Land-level risk engine: encumbrance (loan) + fraud checks for a piece of
land, computed entirely from LOCAL data (the land's passbook, mutations and
encumbrance register). No external calls — consistent with the app's
offline, rule-based "AI" philosophy.

The same survey number + village identifies one piece of land (the same
key the year-wise history / passbook uses), so every check is about the
LAND, not about a single document — exactly what the mentor's question
asked for: "how do we know this land has no loan on it and is not fraud?"

Checks (each returns a flag with severity + evidence):
  CRITICAL
    * ACTIVE_ENCUMBRANCE        a bank loan is still live on the land
    * SALE_DURING_ENCUMBRANCE   a transfer happened while a loan was live
    * ACTIVE_LITIGATION         a court case on the land is still pending
    * TRANSFER_DURING_LITIGATION a deed was signed while a case was pending
  WARNING
    * OWNER_CONFLICT_YEAR       different owners recorded in the SAME year
    * OWNER_CHANGE_NO_MUTATION  owner rolled over between years with no
                                registered mutation linking them
    * AREA_JUMP                 area changed a lot between years with no
                                partition/merger mutation on file
  INFO
    * CLOSED_LITIGATION_ON_RECORD prior (now-closed) court cases, for transparency
    * REJECTED_CONFLICT_COPY    a conflicting record exists but was rejected
                                (the dispute is documented, not active)
    * CHAIN_GAP                 big gap in the passbook (informational)
"""
import re

from . import common  # reuse normalize_numerals for area parsing


def _year_num(year_str):
    m = re.search(r"(\d{4})", str(year_str or ""))
    return int(m.group(1)) if m else None


def _area_num(area_str):
    try:
        s = common.normalize_numerals(str(area_str or ""))
    except Exception:  # noqa: BLE001
        s = str(area_str or "")
    s = s.replace(",", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _norm_owner(name):
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


# Minimal Devanagari -> Latin table (names are the use case). The matra
# ा maps to a short "a" deliberately: राम -> "ram", which is how the same
# person is usually spelled in the English records (रामस्वरूप ->
# "ramsvroop" vs the English "ramswaroop" — close enough for the fuzzy
# match below, and far from a genuinely different name).
_DEVANAGARI_MAP = {
    "अ": "a", "आ": "a", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e",
    "ऐ": "ai", "ओ": "o", "औ": "au", "क": "k", "ख": "kh", "ग": "g", "घ": "gh",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny", "ट": "t", "ठ": "thh",
    "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh",
    "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y",
    "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e", "ै": "ai",
    "ो": "o", "ौ": "au", "ँ": "n", "ं": "m", "ः": "h", "्": "",
}


def _translit_devanagari(s):
    out = []
    for ch in str(s or ""):
        out.append(_DEVANAGARI_MAP.get(ch, ch))
    return "".join(out)


def _owner_key(name):
    """Script/spelling-normalized owner key: Devanagari is transliterated,
    then everything is lowercased and stripped to alphanumerics."""
    t = str(name or "").strip()
    if re.search(r"[\u0900-\u097F]", t):
        t = _translit_devanagari(t)
    t = re.sub(r"[^a-z0-9]+", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


def _same_owner(a, b):
    """True when two recorded owner names refer to the same person —
    exact after normalization, OR near-identical (same person spelled
    slightly differently / in a different script). Genuinely different
    names (Ram Bahadur Singh vs Mahesh Verma) score far below the
    threshold and stay distinct."""
    import difflib
    ka, kb = _owner_key(a), _owner_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.82


def land_risk(records, encumbrances=(), mutations=(), court_cases=()):
    """Compute the risk flags for a piece of land.

    records:      passbook rows (year, owner, area, khasra, doc_id,
                  filename, status, doc_type) — as returned by
                  store.record_history, newest/oldest order doesn't matter
    encumbrances: rows from store.list_encumbrances for this land
    mutations:    mutation applications for this land (any status)
    court_cases:  rows from store.list_court_cases for this land
    Returns a list of flags, most severe first:
      {code, severity, title, detail, evidence:[...]}
    """
    flags = []

    def add(code, severity, title, detail, evidence=None):
        flags.append({"code": code, "severity": severity, "title": title,
                      "detail": detail, "evidence": evidence or []})

    recs = [r for r in (records or []) if r.get("owner") or r.get("year")]
    by_year = {}
    for r in recs:
        y = _year_num(r.get("year"))
        if y:
            by_year.setdefault(y, []).append(r)

    # ---------- encumbrance checks ----------
    active = [e for e in (encumbrances or []) if e.get("status") == "active"]
    for e in active:
        add("ACTIVE_ENCUMBRANCE", "critical",
            "Active loan / encumbrance on this land",
            "%s — ₹%s — mortgaged %s (ref. %s). A live loan must be released "
            "(bank NOC / settlement recorded) before this land can be "
            "certified encumbrance-free or safely transferred."
            % (e.get("creditor") or "creditor",
               "{:,.0f}".format(e["amount"]) if e.get("amount") else "—",
               e.get("mortgage_date") or "date unknown",
               e.get("reference_no") or "no ref"),
            ["enc:%s" % e["id"]])

    # sales (mutations) that happened while an encumbrance was live
    for m in (mutations or []):
        mdate = str(m.get("deed_date") or "")
        if not mdate:
            continue
        for e in (encumbrances or []):
            lo = str(e.get("mortgage_date") or "")
            hi = str(e.get("settlement_date") or "")
            if lo and mdate >= lo and (not hi or mdate <= hi):
                add("SALE_DURING_ENCUMBRANCE", "critical",
                    "Transfer while loan was live",
                    "Deed %s dated %s transferred this land while %s's mortgage "
                    "(%s) was in force. The bank's consent / release must be "
                    "verified — a sale of mortgaged land without it is voidable."
                    % (m.get("deed_no") or "deed", mdate,
                       e.get("creditor") or "the creditor", lo),
                    ["mut:%s" % m.get("id"), "enc:%s" % e["id"]])
                break

    # ---------- court litigation ----------
    for cs in (court_cases or []):
        if cs.get("status") != "active":
            continue
        add("ACTIVE_LITIGATION", "critical",
            "Active court case on this land",
            "%s — %s%s%s (filed %s). Pending litigation — especially any "
            "stay order — must be cleared or explicitly disclosed before a "
            "transfer is approved."
            % (cs.get("case_number") or "case",
               cs.get("court_name") or "court",
               (", " + cs["case_type"]) if cs.get("case_type") else "",
               (" vs. " + cs["parties"]) if cs.get("parties") else "",
               cs.get("filed_date") or "date unknown"),
            ["case:%s" % cs["id"]])

    # transfers that happened while a case was pending
    for m in (mutations or []):
        mdate = str(m.get("deed_date") or "")
        if not mdate:
            continue
        for cs in (court_cases or []):
            lo = str(cs.get("filed_date") or "")
            hi = str(cs.get("closed_date") or "")
            if lo and mdate >= lo and (not hi or mdate <= hi):
                add("TRANSFER_DURING_LITIGATION", "critical",
                    "Transfer while a court case was pending",
                    "Deed %s dated %s transferred this land while case %s (%s) "
                    "was pending. A transfer made during pending title/possession "
                    "litigation is voidable and can be challenged in court."
                    % (m.get("deed_no") or "deed", mdate,
                       cs.get("case_number") or "case",
                       cs.get("court_name") or "court"),
                    ["mut:%s" % m.get("id"), "case:%s" % cs["id"]])
                break

    # closed litigation (transparency — the dispute history stays visible)
    closed_cases = [cs for cs in (court_cases or [])
                    if cs.get("status") in ("decided", "withdrawn", "settled")]
    if closed_cases:
        detail = "; ".join(
            "%s (%s) — %s%s" % (cs.get("case_number") or "case",
                                cs.get("filed_date") or "", cs.get("status"),
                                (": " + cs["decision_summary"]) if cs.get("decision_summary") else "")
            for cs in closed_cases[:3])
        add("CLOSED_LITIGATION_ON_RECORD", "info",
            "%d closed court case(s) on record for this land" % len(closed_cases),
            "Prior litigation (now closed) is shown for transparency. " + detail,
            ["case:%s" % cs["id"] for cs in closed_cases[:3]])

    def _group_distinct(rows):
        """Group rows by owner, merging names that refer to the same person
        (same person in Devanagari + Latin, spelling variants, ...)."""
        groups = []  # list of [representative_name, [rows]]
        for r in rows:
            placed = False
            for g in groups:
                if _same_owner(g[0], r.get("owner")):
                    g[1].append(r)
                    placed = True
                    break
            if not placed:
                groups.append([r.get("owner"), [r]])
        return groups

    # ---------- owner conflict in the same year ----------
    for y, rows in sorted(by_year.items()):
        live_rows = [r for r in rows if r.get("status") != "rejected"]
        live_groups = [g for g in _group_distinct(live_rows)]
        if len(live_groups) > 1:
            names = ", ".join(sorted(set(str(g[0]) for g in live_groups)))
            ev = []
            for g in live_groups:
                for r in g[1]:
                    ev.append("%s (%s — %s)" % (r.get("filename") or r.get("doc_id"),
                                                r.get("owner"), r.get("year")))
            add("OWNER_CONFLICT_YEAR", "warning",
                "Conflicting owners in the same year (%s)" % y,
                "Two or more LIVE records for this land in the same record year "
                "name different owners (%s). Both cannot be correct — one must "
                "be rejected after verifying against the original." % names, ev)

    # rejected conflicting copies (documented dispute -> info, not warning)
    for y, rows in sorted(by_year.items()):
        live = [r for r in rows if r.get("status") != "rejected"]
        dead = [r for r in rows if r.get("status") == "rejected"]
        if live and dead and len(_group_distinct(live + dead)) > 1:
            ev = ["%s (REJECTED — %s, %s)" % (r.get("filename") or r.get("doc_id"),
                                               r.get("owner"), r.get("year")) for r in dead]
            add("REJECTED_CONFLICT_COPY", "info",
                "Ownership dispute documented (rejected copy, %s)" % y,
                "A conflicting record for this year was REJECTED with reviewer "
                "notes. The dispute is on record — shown here for transparency; "
                "no action needed while the live record stands.", ev)

    # ---------- owner change without a mutation ----------
    years = sorted(by_year.keys())
    for prev_y, next_y in zip(years, years[1:]):
        prev_owner = by_year[prev_y][0].get("owner")
        next_owner = by_year[next_y][0].get("owner")
        if not prev_owner or not next_owner or _same_owner(prev_owner, next_owner):
            continue
        # a registered mutation covering this window? (owner names compared
        # with the same script/spelling-tolerant matcher)
        bridged = False
        for m in (mutations or []):
            if _same_owner(m.get("new_owner"), next_owner) and \
                    _same_owner(m.get("previous_owner"), prev_owner):
                bridged = True
                break
        if not bridged:
            add("OWNER_CHANGE_NO_MUTATION", "warning",
                "Owner changed with no registered mutation",
                "The recorded owner changed from '%s' (%s) to '%s' (%s) but no "
                "mutation (namantaran) on file explains the change. A sale/gift "
                "record should exist — verify or request the deed."
                % (by_year[prev_y][0].get("owner"), prev_y,
                   by_year[next_y][0].get("owner"), next_y),
                ["%s (%s)" % (by_year[next_y][0].get("filename") or by_year[next_y][0].get("doc_id"), next_y)])

    # ---------- area jump ----------
    for prev_y, next_y in zip(years, years[1:]):
        a1 = _area_num(by_year[prev_y][0].get("area"))
        a2 = _area_num(by_year[next_y][0].get("area"))
        if a1 and a2 and abs(a2 - a1) / a1 > 0.15:
            bridged = any(str(m.get("transfer_type") or "").lower() in ("partition", "merger")
                          for m in (mutations or []))
            if not bridged:
                add("AREA_JUMP", "warning",
                    "Area changed without partition/merger on file",
                    "Recorded area moved from %s (%s) to %s (%s) — a change of "
                    "%.0f%% with no partition/merger mutation on file. Verify "
                    "against the survey map." % (by_year[prev_y][0].get("area"), prev_y,
                                                 by_year[next_y][0].get("area"), next_y,
                                                 100 * abs(a2 - a1) / a1),
                    ["%s (%s)" % (by_year[next_y][0].get("filename") or by_year[next_y][0].get("doc_id"), next_y)])

    # ---------- chain gap (informational) ----------
    for prev_y, next_y in zip(years, years[1:]):
        if next_y - prev_y > 10:
            add("CHAIN_GAP", "info",
                "Gap of %d years in the record chain" % (next_y - prev_y),
                "No records between %d and %d in this passbook. Normal for "
                "annual khatauni if intermediate years were never scanned — "
                "worth knowing before a long-period title check." % (prev_y, next_y),
                [])

    order = {"critical": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: (order.get(f["severity"], 3), f["code"]))
    return flags
