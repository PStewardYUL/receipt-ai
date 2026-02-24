"""
Receipt post-processing: math validation, tax sanity, date normalisation,
OCR character fixes, QST/USD support, confidence adjustment.
Fully bilingual: English + French Quebec receipts.
"""
import logging
import unicodedata
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

OCR_NUM_FIXES = {
    "O": "0", "o": "0", "l": "1", "I": "1",
    "S": "5", "B": "8", "G": "6", "Z": "2", "§": "5",
}

# English + French month names (including accented variants and abbreviations)
MONTH_NAMES = {
    # English
    "january":1,  "february":2,  "march":3,     "april":4,
    "may":5,      "june":6,      "july":7,       "august":8,
    "september":9,"october":10,  "november":11,  "december":12,
    "jan":1, "feb":2, "mar":3, "apr":4,
    "jun":6, "jul":7, "aug":8, "sep":9, "oct":10, "nov":11, "dec":12,
    # French (with and without accents — OCR often drops accents)
    "janvier":1,  "fevrier":2,  "février":2,   "mars":3,
    "avril":4,    "mai":5,      "juin":6,       "juillet":7,
    "aout":8,     "août":8,     "septembre":9,  "octobre":10,
    "novembre":11,"decembre":12,"décembre":12,
    # French abbreviations
    "janv":1, "févr":2, "fevr":2, "avr":4,
    "juil":7, "sept":9,
}

MATH_TOLERANCE = 0.15
MAX_TAX_RATIO  = 0.50



# Domain name → canonical vendor name
# Used as a reliable fallback when OCR text contains a website/email
DOMAIN_VENDOR_MAP = {
    "virginplus.ca":       "Virgin Plus",
    "virginmobile.ca":     "Virgin Plus",
    "bell.ca":             "Bell",
    "rogers.com":          "Rogers",
    "fido.ca":             "Fido",
    "telus.com":           "TELUS",
    "koodo":               "Koodo",
    "videotron.com":       "Vidéotron",
    "hydroquebec.com":     "Hydro-Québec",
    "hydro.qc.ca":         "Hydro-Québec",
    "hydro.on.ca":         "Hydro One",
    "enbridge.com":        "Enbridge Gas",
    "amazon.ca":           "Amazon",
    "amazon.com":          "Amazon",
    "paypal.com":          "PayPal",
    "homedepot.ca":        "Home Depot",
    "canadiantire.ca":     "Canadian Tire",
    "ikea.com":            "IKEA",
    "costco.ca":           "Costco",
    "walmart.ca":          "Walmart",
    "bestbuy.ca":          "Best Buy",
    "staples.ca":          "Staples",
    "dollarama.com":       "Dollarama",
    "rona.ca":             "Rona",
    "lowes.ca":            "Lowe's",
    "saq.com":             "SAQ",
    "metro.ca":            "Metro",
    "iga.net":             "IGA",
    "maxi.ca":             "Maxi",
    "provigo.ca":          "Provigo",
    "jeancoutu.com":       "Jean Coutu",
    "pharmaprix.ca":       "Pharmaprix",
    "shoppersdrugmart.ca": "Shoppers Drug Mart",
    "timhortons.com":      "Tim Hortons",
    "mcdonalds.com":       "McDonald's",
    "subway.com":          "Subway",
}
class ReceiptPostProcessor:

    def process(self, raw: dict, ocr_text: str = "") -> dict:
        if not raw.get("is_receipt"):
            return raw

        result = dict(raw)
        warnings = []

        result = self._fix_numeric_ocr_errors(result, warnings)
        result = self._sanitize_tax_values(result, warnings)
        result["date"]   = self._normalise_date(result.get("date"), ocr_text, warnings)
        result["vendor"] = self._clean_vendor(result.get("vendor"))
        # Domain-based vendor inference — reliable regex fallback, runs after LLM
        if not result.get("vendor") and ocr_text:
            result["vendor"] = self._infer_vendor_from_domain(ocr_text) or result.get("vendor")
        result, warnings = self._validate_math(result, warnings)
        result["confidence"] = self._adjust_confidence(result, warnings)
        result["_warnings"]  = warnings
        result["_math_valid"] = not any(w.startswith("MATH_MISMATCH") for w in warnings)

        if warnings:
            logger.info(f"Post-processing warnings for {result.get('vendor')}: {warnings}")
        return result

    # ── Tax sanity ─────────────────────────────────────────────────────────────

    def _sanitize_tax_values(self, data: dict, warnings: list) -> dict:
        total = self._to_float(data.get("total"))
        for field in ["gst", "pst", "hst", "qst"]:
            val = self._to_float(data.get(field))
            if val <= 0:
                continue
            if total > 0 and val >= total:
                warnings.append(f"TAX_SANITY_ZEROED:{field}={val:.2f}≥total={total:.2f}")
                data[field] = 0.0
            elif total > 0 and val > MAX_TAX_RATIO * total:
                warnings.append(f"TAX_SANITY_CAPPED:{field}={val:.2f}>50%_of_{total:.2f}")
                data[field] = 0.0
        return data

    # ── OCR character fixes ────────────────────────────────────────────────────

    def _fix_numeric_ocr_errors(self, data: dict, warnings: list) -> dict:
        for field in ["total", "gst", "pst", "hst", "qst", "pre_tax"]:
            raw_val = data.get(field)
            if raw_val is None:
                continue
            try:
                float(raw_val)
                continue
            except (TypeError, ValueError):
                pass
            if isinstance(raw_val, str):
                fixed = self._fix_ocr_number_string(raw_val)
                if fixed is not None:
                    warnings.append(f"OCR_FIX:{field}:{raw_val}→{fixed}")
                    data[field] = fixed
                else:
                    warnings.append(f"OCR_UNPARSEABLE:{field}:{raw_val}")
                    data[field] = 0.0
        return data

    def _fix_ocr_number_string(self, s: str) -> Optional[float]:
        cleaned = re.sub(r"[$€£¥\s,]", "", str(s))
        result  = "".join(OCR_NUM_FIXES.get(c, c) for c in cleaned)
        result  = re.sub(r"[^\d.\-]", "", result)
        parts   = result.split(".")
        if len(parts) > 2:
            result = parts[0] + "." + parts[-1]
        try:
            return round(float(result), 2)
        except ValueError:
            return None

    # ── Date normalisation ─────────────────────────────────────────────────────

    def _normalise_date(self, raw_date, ocr_text: str, warnings: list) -> Optional[str]:
        if raw_date:
            iso = self._parse_date_string(str(raw_date))
            if iso:
                return iso
            warnings.append(f"DATE_PARSE_FAILED:{raw_date}")
        if ocr_text:
            iso = self._scan_text_for_date(ocr_text)
            if iso:
                warnings.append(f"DATE_FROM_OCR:{iso}")
                return iso
        warnings.append("DATE_MISSING")
        return None

    def _parse_date_string(self, s: str) -> Optional[str]:
        s = s.strip()

        # ISO already
        if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            return s if self._valid_date(s) else None

        # DD/MM/YYYY or MM/DD/YYYY
        m = re.match(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$", s)
        if m:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            iso = f"{y:04d}-{b:02d}-{a:02d}" if a > 12 else f"{y:04d}-{a:02d}-{b:02d}"
            return iso if self._valid_date(iso) else None

        # YYYY/MM/DD or YYYY-MM-DD
        m = re.match(r"^(\d{4})[/\-](\d{2})[/\-](\d{2})$", s)
        if m:
            iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            return iso if self._valid_date(iso) else None

        # YYYYMMDD
        m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
        if m:
            iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            return iso if self._valid_date(iso) else None

        # "10 mars 2024", "March 10, 2024", "10 mars, 2024", "le 10 mars 2024"
        s_clean = re.sub(r"^le\s+", "", s.lower().strip())  # strip French "le"
        for month_name, month_num in MONTH_NAMES.items():
            if month_name in s_clean:
                numbers = re.findall(r"\d+", s_clean)
                year  = next((n for n in numbers if len(n) == 4), None)
                days  = [n for n in numbers if n != year and 1 <= int(n) <= 31]
                if year and days:
                    iso = f"{year}-{month_num:02d}-{int(days[0]):02d}"
                    if self._valid_date(iso):
                        return iso

        return None

    # Labels that indicate a date is NOT the transaction date
    _REJECT_DATE_LABELS = re.compile(
        r"(expir|valid until|return by|retour avant|best before|meilleur avant|relev[eé]|"
        r"print date|imprim|[eé]ch[eé]ance(?!.*fact)|policy|politique|void after|annul|"
        r"next bill|next billing|prochaine|next due|next payment|prochain paiement|"
        r"renewal|renouvellement)",
        re.IGNORECASE,
    )
    # Labels that CONFIRM a date IS the transaction date
    # Note: "^date" won't work on context string — use word-boundary "\bdate\b" instead
    _PREFER_DATE_LABELS = re.compile(
        r"(\bbill date\b|billing date|date de facturation|invoice date|statement date|"
        r"\btransaction\b|purchase date|achat|\bdate\b|sale date|"
        r"\breceipt\b|re[cç]u le|processed on|paid on|pay[eé] le)",
        re.IGNORECASE,
    )

    def _scan_text_for_date(self, text: str) -> Optional[str]:
        """
        Extract ALL dates from text, then pick the most likely transaction date.
        Avoids expiry dates, print dates, return-by dates, etc.
        """
        patterns = [
            r"\b(\d{4}-\d{2}-\d{2})\b",
            r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
            r"\b(\d{1,2}-\d{1,2}-\d{4})\b",
            r"\b(\d{1,2}\.\d{1,2}\.\d{4})\b",
            r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+\d{1,2},?\s*\d{4})\b",
            r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s*,?\s*\d{4})\b",
            r"\b(\d{1,2}\s+(?:janv?|f[eé]vr?|mars|avr|avril|mai|juin|juil|ao[uû]t|sept?|oct|nov|d[eé]c)\w*\.?\s*,?\s*\d{4})\b",
        ]

        candidates = []  # (iso_date, score, position)
        lines = text.split("\n")

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                iso = self._parse_date_string(match.group(1))
                if not iso:
                    continue

                pos = match.start()
                score = 0

                # Find the line containing this date and the line before it (label)
                line_idx = text[:pos].count("\n")
                current_line = lines[line_idx] if line_idx < len(lines) else ""
                prev_line = lines[line_idx - 1] if line_idx > 0 else ""
                context = (prev_line + " " + current_line).lower()

                # Heavily penalise non-transaction date labels
                if self._REJECT_DATE_LABELS.search(context):
                    score -= 10
                    continue  # skip entirely — not a transaction date

                # Boost confirmed transaction date labels
                if self._PREFER_DATE_LABELS.search(context):
                    score += 5

                # Prefer dates that appear with a time (strong signal of transaction)
                if re.search(r"\b\d{1,2}:\d{2}(:\d{2})?\b", current_line):
                    score += 3

                # Prefer dates in the top half of the receipt
                total_lines = max(len(lines), 1)
                if line_idx < total_lines * 0.5:
                    score += 1

                candidates.append((iso, score, pos))

        if not candidates:
            return None

        # Pick highest-scored candidate; break ties by earliest position
        candidates.sort(key=lambda x: (-x[1], x[2]))
        chosen = candidates[0][0]

        if len(candidates) > 1:
            logger.debug(
                f"Date disambiguation: {len(candidates)} candidates, chose {chosen} "
                f"(score={candidates[0][1]}) from: {[c[0] for c in candidates]}"
            )

        return chosen

    def _valid_date(self, iso: str) -> bool:
        try:
            dt = datetime.strptime(iso, "%Y-%m-%d")
            return datetime(2000, 1, 1) <= dt <= datetime(datetime.now().year + 1, 12, 31)
        except ValueError:
            return False

    # ── Vendor cleanup ─────────────────────────────────────────────────────────

    def _clean_vendor(self, vendor: Optional[str]) -> Optional[str]:
        if not vendor:
            return None
        v = vendor.strip()
        # Reject placeholder-only names
        for p in [
            r"^\s*receipt\s*$", r"^\s*reçu\s*$", r"^\s*facture\s*$",
            r"^\s*tax invoice\s*$", r"^\s*\d+\s*$", r"^\s*unknown\s*$",
            r"^\s*n/?a\s*$", r"^\s*none\s*$",
        ]:
            if re.match(p, v, re.IGNORECASE):
                return None
        v = re.sub(r"\s+", " ", v)
        if v.isupper() and len(v) > 3:
            v = v.title()
        # Strip accents: é→e, à→a, ç→c, etc. (better for Paperless search/display)
        v = unicodedata.normalize("NFD", v)
        v = "".join(c for c in v if unicodedata.category(c) != "Mn")
        return v[:200]

    # ── Math validation ────────────────────────────────────────────────────────

    def _infer_vendor_from_domain(self, text: str) -> Optional[str]:
        """Extract vendor from domain names in OCR text — more reliable than LLM inference."""
        text_lower = text.lower()
        # Sort by length descending so longer/more-specific domains match first
        for domain, vendor in sorted(DOMAIN_VENDOR_MAP.items(), key=lambda x: -len(x[0])):
            if domain in text_lower:
                logger.info(f"Vendor inferred from domain '{domain}': '{vendor}'")
                return vendor
        return None

    def _validate_math(self, data: dict, warnings: list) -> tuple:
        total   = self._to_float(data.get("total"))
        pre_tax = self._to_float(data.get("pre_tax"))
        gst     = self._to_float(data.get("gst"))
        pst     = self._to_float(data.get("pst"))
        hst     = self._to_float(data.get("hst"))
        qst     = self._to_float(data.get("qst"))
        tax_sum = round(gst + pst + hst + qst, 2)

        if total > 0 and pre_tax > 0:
            computed = round(pre_tax + tax_sum, 2)
            diff = abs(computed - total)
            if diff <= MATH_TOLERANCE:
                pass
            elif diff <= 2.0:
                data["pre_tax"] = round(total - tax_sum, 2)
                warnings.append(f"MATH_ADJUSTED_PRETAX:{pre_tax}→{data['pre_tax']}")
            else:
                warnings.append(
                    f"MATH_MISMATCH:pre_tax={pre_tax}+tax={tax_sum:.2f}≠total={total} (diff={diff:.2f})"
                )
        elif total > 0 and pre_tax == 0 and tax_sum > 0:
            inferred = round(total - tax_sum, 2)
            if 0 < inferred < total:
                data["pre_tax"] = inferred
                warnings.append(f"MATH_INFERRED_PRETAX:{inferred}")
        elif pre_tax > 0 and total == 0:
            data["total"] = round(pre_tax + tax_sum, 2)
            warnings.append(f"MATH_INFERRED_TOTAL:{data['total']}")

        if self._to_float(data.get("total")) <= 0:
            warnings.append("MATH_MISSING_TOTAL")

        return data, warnings

    # ── Confidence ─────────────────────────────────────────────────────────────

    def _adjust_confidence(self, data: dict, warnings: list) -> float:
        base = float(data.get("confidence", 0.5))
        penalties = {
            "DATE_MISSING":      0.15,
            "DATE_PARSE_FAILED": 0.10,
            "MATH_MISMATCH":     0.20,
            "MATH_MISSING_TOTAL":0.25,
            "OCR_UNPARSEABLE":   0.15,
            "TAX_SANITY":        0.10,
        }
        for warning in warnings:
            for key, penalty in penalties.items():
                if warning.startswith(key):
                    base -= penalty
                    break
        if data.get("vendor"):                                          base += 0.05
        if data.get("date"):                                            base += 0.05
        if self._to_float(data.get("total")) > 0:                      base += 0.05
        if any(self._to_float(data.get(t)) > 0
               for t in ["gst", "hst", "qst"]):                        base += 0.05
        return round(max(0.0, min(1.0, base)), 3)

    def _to_float(self, v) -> float:
        try:
            return max(0.0, float(v or 0))
        except (TypeError, ValueError):
            return 0.0
