"""
Deterministic receipt parser — runs BEFORE the LLM.

Core insight from receipt-parser research (tinvois, StatsCan study):
  - Dollar amounts on receipts are almost always parseable by regex
  - The LABEL to the LEFT of a number tells you what it is
  - Position matters: top = vendor, bottom = totals
  - LLM should CONFIRM deterministic findings, not replace them

This module extracts anchors with near-100% precision:
  total, gst, qst, pst, hst, pre_tax, date, vendor_candidates

These anchors are passed to the LLM as ground truth to confirm/correct,
and used by the post-processor to override hallucinated LLM values.
"""
import logging
import re
import unicodedata
from typing import Optional

logger = logging.getLogger(__name__)

# ── Amount extraction ──────────────────────────────────────────────────────────

AMOUNT_RE = re.compile(
    r"""
    (?:^|[\s:=\t])          # start of line or whitespace/delimiter
    -?                       # optional negative sign
    \$?                      # optional dollar sign
    (\d{1,6}[.,]\d{2})       # the amount: digits + decimal (comma or period)
    (?:\s*(?:CAD|USD|$))?    # optional currency suffix
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _parse_amount(s: str) -> Optional[float]:
    """Extract the first dollar amount from a string, handling French commas."""
    m = AMOUNT_RE.search(s)
    if not m:
        # try bare number like "41.53" at end of line
        m2 = re.search(r"(\d{1,6}[.,]\d{2})\s*$", s)
        if not m2:
            return None
        raw = m2.group(1)
    else:
        raw = m.group(1)
    return round(float(raw.replace(",", ".")), 2)


def _strip_accents(s: str) -> str:
    n = unicodedata.normalize("NFD", s)
    return "".join(c for c in n if unicodedata.category(c) != "Mn")


# ── Keyword patterns ───────────────────────────────────────────────────────────

# Each tuple: (field_name, pattern, priority)
# Higher priority wins when multiple candidates found
LABEL_PATTERNS = [
    # ── Total ─────────────────────────────────────────────────────────────────
    ("total",   r"total\s*(amount|a\s*payer|du|paid|due)?",                 10),
    ("total",   r"montant\s*(total|du|a\s*payer|dû)",                       10),
    ("total",   r"amount\s*(due|paid|charged|total)",                       10),
    ("total",   r"balance\s*(due|forward|totale?)?",                        8),
    ("total",   r"solde\s*(du|total|a\s*payer)?",                           8),
    ("total",   r"grand\s*total",                                           12),
    ("total",   r"total\s*(current\s*charges?|charges?\s*including\s*tax)", 9),
    ("total",   r"total\s*taxes?\s*(on|including)",                         2),  # lower — this is tax sum not grand total

    # ── Pre-tax / Subtotal ─────────────────────────────────────────────────────
    ("pre_tax", r"sub\s*total",                                             8),
    ("pre_tax", r"sous[\s-]total",                                          8),
    ("pre_tax", r"before\s*tax",                                            8),
    ("pre_tax", r"net\s*(amount|total)?",                                   6),
    ("pre_tax", r"netto",                                                   6),
    ("pre_tax", r"monthly\s*charges?",                                      4),  # telecom

    # ── GST / TPS ──────────────────────────────────────────────────────────────
    ("gst",     r"(gst|tps)\s*(included|in\s*this\s*bill|on\s*charges?)?", 10),
    ("gst",     r"(gst|tps)\s*@?\s*5\s*%?",                                10),
    ("gst",     r"federal\s*tax",                                           6),

    # ── QST / TVQ ─────────────────────────────────────────────────────────────
    ("qst",     r"(qst|tvq)\s*(included|in\s*this\s*bill|telecom)?",       10),
    ("qst",     r"(qst|tvq)\s*@?\s*9[.,]?975?\s*%?",                       10),
    ("qst",     r"provincial\s*tax",                                        6),

    # ── PST ───────────────────────────────────────────────────────────────────
    ("pst",     r"pst\s*(@|tax)?",                                          8),
    ("pst",     r"rst\s*(@|tax)?",                                          6),

    # ── HST ───────────────────────────────────────────────────────────────────
    ("hst",     r"hst\s*(tax|@)?",                                          8),
]

# Compile all patterns
_COMPILED = [
    (field, re.compile(pat, re.IGNORECASE), priority)
    for field, pat, priority in LABEL_PATTERNS
]


# ── Date patterns ──────────────────────────────────────────────────────────────

# Keyword labels that confirm a date IS a transaction date (higher is better)
_DATE_CONFIRM = re.compile(
    r"(^bill\s*date|^invoice\s*date|^date\s*de\s*facturation|"
    r"^transaction|^purchased|^date\s*d'achat|"
    r"^date\b(?!\s*d'expir|\s*limite|\s*de\s*retour))",
    re.IGNORECASE,
)
# Labels that indicate NOT a transaction date
_DATE_REJECT = re.compile(
    r"(next|expir|valid\s*until|return|best\s*before|due\s*date|"
    r"prochaine|renouvellement|void\s*after|print)",
    re.IGNORECASE,
)

FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3,
    "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "aout": 8, "août": 8, "septembre": 9, "octobre": 10,
    "novembre": 11, "decembre": 12, "décembre": 12,
    "janv": 1, "févr": 2, "fevr": 2, "juil": 7, "sept": 9,
}
ENG_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
ALL_MONTHS = {**FRENCH_MONTHS, **ENG_MONTHS}

DATE_FORMATS = [
    re.compile(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b"),
    re.compile(r"\b(\d{1,2}[-/\.]\d{1,2}[-/\.]\d{4})\b"),
    re.compile(r"\b(\d{1,2}\s+(?:" + "|".join(ALL_MONTHS) + r")\w*\.?\s*,?\s*\d{4})\b", re.I),
    re.compile(r"\b((?:" + "|".join(ENG_MONTHS) + r")\w*\.?\s+\d{1,2}\s*,?\s*\d{4})\b", re.I),
    re.compile(r"\b((?:le\s+)?\d{1,2}\s+(?:" + "|".join(FRENCH_MONTHS) + r")\w*\.?\s*,?\s*\d{4})\b", re.I),
]


def _parse_iso_date(s: str) -> Optional[str]:
    """Try to parse a date string into YYYY-MM-DD. Returns None if invalid."""
    s = s.strip().lower()
    s = re.sub(r"^le\s+", "", s)

    # Already ISO
    m = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # DD/MM/YYYY or MM/DD/YYYY
    m = re.match(r"^(\d{1,2})[-/\.](\d{1,2})[-/\.](\d{4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12:
            return f"{y:04d}-{b:02d}-{a:02d}"
        return f"{y:04d}-{a:02d}-{b:02d}"

    # Written month
    for name, num in ALL_MONTHS.items():
        if name in s:
            numbers = re.findall(r"\d+", s)
            year = next((n for n in numbers if len(n) == 4), None)
            days = [n for n in numbers if n != year and 1 <= int(n) <= 31]
            if year and days:
                return f"{year}-{num:02d}-{int(days[0]):02d}"

    return None


def _valid_iso(iso: str) -> bool:
    try:
        from datetime import datetime
        dt = datetime.strptime(iso, "%Y-%m-%d")
        return 2000 <= dt.year <= 2035
    except ValueError:
        return False


# ── Known vendor list ──────────────────────────────────────────────────────────
# Used to match against first N lines of receipt

KNOWN_VENDORS = {
    # Hardware
    "home depot": "Home Depot", "rona": "Rona", "canadian tire": "Canadian Tire",
    "home hardware": "Home Hardware", "lowes": "Lowes", "lowe's": "Lowes",
    # Grocery
    "iga": "IGA", "metro": "Metro", "maxi": "Maxi", "super c": "Super C",
    "loblaws": "Loblaws", "provigo": "Provigo", "walmart": "Walmart",
    "costco": "Costco", "dollarama": "Dollarama", "giant tiger": "Giant Tiger",
    # Pharmacy
    "pharmaprix": "Pharmaprix", "shoppers": "Shoppers Drug Mart",
    "jean coutu": "Jean Coutu", "uniprix": "Uniprix", "brunet": "Brunet",
    # Restaurant / fast food
    "tim hortons": "Tim Hortons", "starbucks": "Starbucks",
    "mcdonald": "McDonald's", "subway": "Subway", "a&w": "A&W",
    "burger king": "Burger King", "pizza hut": "Pizza Hut",
    "domino": "Domino's", "bk": "Burger King",
    # Telecom
    "virgin plus": "Virgin Plus", "virgin mobile": "Virgin Plus",
    "bell": "Bell", "rogers": "Rogers", "telus": "Telus",
    "videotron": "Videotron", "vidéotron": "Videotron",
    "fido": "Fido", "koodo": "Koodo", "fizz": "Fizz",
    # Utility
    "hydro-québec": "Hydro-Quebec", "hydro-quebec": "Hydro-Quebec",
    "hydro québec": "Hydro-Quebec", "enbridge": "Enbridge",
    "gaz metro": "Energir", "energir": "Energir",
    # Specialty
    "saq": "SAQ", "ikea": "IKEA", "best buy": "Best Buy",
    "staples": "Staples", "winners": "Winners", "homesense": "HomeSense",
    "simons": "Simons", "reitmans": "Reitmans",
    # Government / Municipal
    "ville de montreal": "Ville de Montreal",
    "ville de québec": "Ville de Quebec",
    "gouvernement du québec": "Gouvernement du Quebec",
    "revenu québec": "Revenu Quebec", "cra": "Canada Revenue Agency",
    "service canada": "Service Canada",
    # E-commerce / Marketplaces
    "amazon": "Amazon", "apple": "Apple", "google": "Google",
    "microsoft": "Microsoft", "adobe": "Adobe",
    "ebay": "eBay", "paypal": "PayPal",
    "etsy": "Etsy", "shopify": "Shopify",
}

# Domain → vendor map (extracted from URLs in document)
DOMAIN_VENDORS = {
    "virginplus.ca": "Virgin Plus", "virginmobile.ca": "Virgin Plus",
    "bell.ca": "Bell", "bell.net": "Bell",
    "rogers.com": "Rogers", "fido.ca": "Fido",
    "telus.com": "Telus", "koodo.com": "Koodo",
    "videotron.com": "Videotron", "videotron.ca": "Videotron",
    "hydroquebec.com": "Hydro-Quebec",
    "amazon.ca": "Amazon", "amazon.com": "Amazon",
    "homedepot.ca": "Home Depot", "homedepot.com": "Home Depot",
    "canadiantire.ca": "Canadian Tire",
    "walmart.ca": "Walmart", "walmart.com": "Walmart",
    "costco.ca": "Costco", "costco.com": "Costco",
    "iga.net": "IGA", "metro.ca": "Metro",
    "pharmaprix.ca": "Pharmaprix", "shoppersdrugmart.ca": "Shoppers Drug Mart",
    "jeancoutu.com": "Jean Coutu",
    "saq.com": "SAQ", "ikea.com": "IKEA",
    "staples.ca": "Staples", "bestbuy.ca": "Best Buy",
    "rona.ca": "Rona", "lowes.ca": "Lowes",
    "ville.montreal.qc.ca": "Ville de Montreal",
    "montreal.ca": "Ville de Montreal",
    "revenuquebec.ca": "Revenu Quebec",
    "canada.ca": "Government of Canada",
    # E-commerce / Marketplaces
    "ebay.ca": "eBay", "ebay.com": "eBay",
    "paypal.com": "PayPal", "paypal.ca": "PayPal",
    "etsy.com": "Etsy",
}


# ── Main parser ────────────────────────────────────────────────────────────────

class DeterministicParser:
    """
    Fast regex-based pre-parser. Extracts high-confidence anchors from OCR text.
    Results passed to LLM as ground truth and to post-processor as override values.
    """

    def parse(self, text: str) -> dict:
        """
        Returns dict with keys: total, gst, qst, pst, hst, pre_tax, date, vendor.
        Values are None if not found with sufficient confidence.
        """
        lines = self._clean_lines(text)
        result = {
            "total":   None, "gst":     None, "qst":  None,
            "pst":     None, "hst":     None, "pre_tax": None,
            "date":    None, "vendor":  None,
        }

        result["vendor"] = self._extract_vendor(lines, text)
        result["date"]   = self._extract_date(lines)

        amounts = self._extract_amounts(lines)
        for field, value in amounts.items():
            result[field] = value

        # Sanity: zero out any tax ≥ total
        total = result.get("total")
        if total:
            for tax in ["gst", "qst", "pst", "hst"]:
                v = result.get(tax)
                if v and v >= total:
                    result[tax] = None

        logger.info(
            f"Deterministic: vendor={result['vendor']} date={result['date']} "
            f"total={result['total']} gst={result['gst']} qst={result['qst']} "
            f"pst={result['pst']} hst={result['hst']} pre_tax={result['pre_tax']}"
        )
        return result

    def _clean_lines(self, text: str) -> list[str]:
        lines = []
        for line in text.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
        return lines

    def _extract_vendor(self, lines: list[str], full_text: str) -> Optional[str]:
        """
        Priority:
        1. Domain match in full text (highest confidence)
        2. Known vendor list match in first 10 lines
        3. First substantive non-numeric line at top of receipt
        """
        # 1 — domain match anywhere in text
        for domain, vendor in DOMAIN_VENDORS.items():
            if domain in full_text.lower():
                logger.debug(f"Vendor from domain '{domain}': {vendor}")
                return vendor

        # 2 — known vendor match in top 10 lines
        top_text = " ".join(lines[:10]).lower()
        top_text = _strip_accents(top_text)
        for key, canonical in KNOWN_VENDORS.items():
            key_stripped = _strip_accents(key)
            if key_stripped in top_text:
                logger.debug(f"Vendor from known list '{key}': {canonical}")
                return canonical

        # 3 — detect marketplace receipts (eBay, Amazon, PayPal) by structure
        #     These have "Sold by:", "Order from:", "Ship to:" etc. in the text
        full_lower = _strip_accents(full_text.lower())
        marketplace_signals = [
            (r"ebay\.c|order\s+from\s+ebay|ebay\s+order|sold\s+on\s+ebay", "eBay"),
            (r"amazon\.c|fulfilled\s+by\s+amazon|sold\s+by.*amazon", "Amazon"),
            (r"paypal\.c|payment\s+via\s+paypal|paypal\s+receipt", "PayPal"),
            (r"etsy\.c|etsy\s+order|etsy\s+receipt", "Etsy"),
        ]
        for pattern, canonical in marketplace_signals:
            if re.search(pattern, full_lower):
                logger.debug(f"Marketplace detected via text signal: {canonical}")
                return canonical

        # Lines that indicate the following text is a BUYER or SHIPPING name, not vendor
        _BUYER_LABELS = re.compile(
            r"^(ship\s*to|bill\s*to|sold\s*to|deliver\s*to|buyer|customer|"
            r"livrer\s*(a|à)|factur[eé]\s*(a|à)|acheteur|nom\s*du\s*client)\s*[:\-]?\s*$",
            re.IGNORECASE,
        )
        # Lines that start with a buyer/shipping label inline (e.g. "Ship to: Phil Steward")
        _BUYER_INLINE = re.compile(
            r"^(ship\s*to|bill\s*to|sold\s*to|deliver\s*to|buyer|customer"
            r"|livrer|factur[eé]|acheteur)\s*[:\-]",
            re.IGNORECASE,
        )

        skip_next = False  # set True after seeing a "Ship to:" label line
        for line in lines[:8]:
            stripped = _strip_accents(line.strip())

            # If previous line was a buyer/ship label, this line is a personal name — skip it
            if skip_next:
                skip_next = False
                logger.debug(f"Skipping buyer name line: '{stripped}'")
                continue

            # If this line IS a buyer label, mark next line for skipping
            if _BUYER_LABELS.match(stripped):
                skip_next = True
                continue

            # If buyer label is inline, skip this whole line
            if _BUYER_INLINE.match(stripped):
                logger.debug(f"Skipping inline buyer line: '{stripped}'")
                continue

            # Skip: pure numbers, phone numbers, addresses, very short strings
            if re.match(r"^[\d\s\-\(\)\+\.]+$", stripped):
                continue
            if re.match(r"^\d+\s+\w", stripped):  # starts with street number
                continue
            if len(stripped) < 3:
                continue
            if re.search(r"(receipt|reçu|facture|invoice|bill|date|tel:|www\.|http)", stripped, re.I):
                continue

            # Looks like a business name
            logger.debug(f"Vendor from first-line heuristic: '{stripped}'")
            return stripped.title() if stripped.isupper() else stripped

        return None

    def _extract_date(self, lines: list[str]) -> Optional[str]:
        """
        Score each date found:
        - Confirmed label (bill date, invoice date) → score 10
        - Rejected label (next bill, expiry) → skip entirely
        - Has a time component → score 5
        - Near top of receipt → score 2
        Pick highest-scored valid date.
        """
        total_lines = max(len(lines), 1)
        candidates = []

        for idx, line in enumerate(lines):
            line_lower = line.lower()
            stripped = _strip_accents(line_lower)

            # Check if this line's label rejects it
            if _DATE_REJECT.search(stripped):
                continue

            # Find all dates in this line
            for pattern in DATE_FORMATS:
                for m in pattern.finditer(line):
                    iso = _parse_iso_date(m.group(1))
                    if iso and _valid_iso(iso):
                        score = 0
                        if _DATE_CONFIRM.search(stripped):
                            score += 10
                        if re.search(r"\d{1,2}:\d{2}", line):
                            score += 5
                        if idx < total_lines * 0.4:
                            score += 2
                        candidates.append((iso, score, idx))

        if not candidates:
            return None

        candidates.sort(key=lambda x: (-x[1], x[2]))
        if len(candidates) > 1:
            logger.debug(f"Date candidates: {candidates} → chose {candidates[0][0]}")
        return candidates[0][0]

    def _extract_amounts(self, lines: list[str]) -> dict:
        """
        For each line, check if a LABEL pattern matches the left side.
        Extract the dollar amount from the right side.
        
        Picks the LAST/BOTTOM occurrence of total (most likely the final total)
        and the FIRST occurrence of taxes (usually only listed once).
        """
        # Collect all matches per field
        found: dict[str, list[tuple[float, int, int]]] = {}  # field → [(amount, priority, line_idx)]

        for idx, line in enumerate(lines):
            clean = _strip_accents(line.lower().strip())

            for field, pattern, priority in _COMPILED:
                if not pattern.search(clean):
                    continue
                amount = _parse_amount(line)
                if amount and amount > 0:
                    found.setdefault(field, []).append((amount, priority, idx))

        result = {}
        for field, matches in found.items():
            if not matches:
                continue

            if field == "total":
                # For total: prefer the highest-priority match, break ties by bottom position
                # (the grand total is usually the last/bottom total on the receipt)
                best_priority = max(p for _, p, _ in matches)
                best_matches = [(a, p, i) for a, p, i in matches if p == best_priority]
                # Among equal priority, take the bottom-most (largest line index)
                best = max(best_matches, key=lambda x: x[2])
                result[field] = best[0]
            else:
                # For taxes: highest priority match
                best = max(matches, key=lambda x: (x[1], -x[2]))
                result[field] = best[0]

        return result

    def format_as_prompt_context(self, d: dict) -> str:
        """Format deterministic findings as a prompt section for the LLM."""
        lines = ["=== DETERMINISTIC PRE-SCAN (high confidence — prefer these values) ==="]
        any_found = False
        for field, label in [
            ("vendor",  "Vendor"),
            ("date",    "Date"),
            ("total",   "Total"),
            ("pre_tax", "Pre-tax"),
            ("gst",     "GST/TPS"),
            ("qst",     "QST/TVQ"),
            ("pst",     "PST"),
            ("hst",     "HST"),
        ]:
            val = d.get(field)
            if val is not None:
                lines.append(f"  {label}: {val}")
                any_found = True

        if not any_found:
            lines.append("  (no high-confidence values found — rely on full text analysis)")
        lines.append(
            "Trust these values unless the full text clearly contradicts them. "
            "If a field above conflicts with your reading of the text, use the text value "
            "and lower your confidence score."
        )
        return "\n".join(lines)
