"""
Ollama client — vision OCR + logo detection + text LLM structured parsing.
Uses /api/generate throughout (works on all Ollama versions).
"""
import base64
import json
import logging
import os
import re
from typing import Optional

import httpx

from services.image_prep import ReceiptImagePipeline, extract_pdf_text, is_pdf, pdf_to_image, crop_top_region, crop_bottom_region
from services.deterministic_parser import DeterministicParser
from services.receipt_parser import ReceiptPostProcessor

logger = logging.getLogger(__name__)

OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
VISION_MODEL = os.getenv("VISION_MODEL", "llava")
TEXT_MODEL   = os.getenv("TEXT_MODEL", "mistral")
MIN_OCR_LENGTH = 40

# ── Prompts ────────────────────────────────────────────────────────────────────

LOGO_PROMPT = """Look at this image carefully. Identify the brand, store, or company shown by any logo, wordmark, sign, or visual branding.

Recognise these by their distinctive visual identity:
- Home Depot: bold orange wordmark "The Home Depot", orange apron logo, bright orange background strip
- Canadian Tire: red triangle logo, red and white colours, maple leaf sometimes present
- eBay: multicolored wordmark with red e, blue b, yellow a, green y — always "eBay" not the seller name
- Costco: red and blue "Costco Wholesale" text
- Walmart: blue text with yellow starburst spark icon
- Amazon: orange smile arrow under "amazon" text
- IGA: red and white with "IGA" text
- Metro: stylized M logo in multiple colours
- Jean Coutu: red and white pharmacy cross
- Pharmaprix / Shoppers Drug Mart: Rx pharmacy symbol
- SAQ: blue and white with "SAQ" text
- Tim Hortons: brown and red, "Tim Hortons" script
- McDonald's: golden arches M
- IKEA: blue rectangle with yellow "IKEA" text
- Rona: green and white with "RONA" text
- Best Buy: blue and yellow price tag logo
- Dollarama: red with "Dollarama" text
- Loblaws / Provigo: red with stylized L
- Staples: red with "Staples" text

Rules:
- Return ONLY the brand/store name — nothing else
- For eBay: always return "eBay" regardless of seller name shown
- For Home Depot: return "Home Depot" even if only the orange strip/apron is visible
- If you see a personal name, shipping address, or buyer name — ignore it, those are NOT the vendor
- If no brand is identifiable, return "unknown"

Examples: "Home Depot", "eBay", "Canadian Tire", "unknown"."""

VISION_PROMPT = """You are a receipt OCR specialist. Extract every piece of text from this image with maximum accuracy.

This receipt may be in English, French, or both (Quebec, Canada is common).

Focus on:
- Store/business name — top of receipt, often large or bold. If only a logo is visible with no text name, note: "[LOGO ONLY]"
- ALL dates visible — transaction date, print date, time — note each one with its label
- Individual line items with prices
- Subtotal / Sous-total
- ALL tax lines: GST/TPS (5%), QST/TVQ (9.975%), PST, HST
- Total / Montant dû / Solde / Balance / Total à payer / Montant total
- Currency (CAD, USD)
- GST/HST business registration number (format: 123456789 RT0001)
- Payment method

French terms: TPS=GST, TVQ=QST, SOUS-TOTAL=subtotal, MONTANT DÛ=amount due,
              REÇU=receipt, FACTURE=invoice, DATE=date

Rules:
- Output ONLY the raw text as it appears — no interpretation, no summarising
- Preserve ALL numbers exactly — every digit matters
- French decimal separator is comma: 20,50 means $20.50
- Preserve accented characters: é è ê à â ô û ç î
- List ALL dates you see, each on its own line with any label before it
- If a line is unclear, output your best reading followed by [?]
- Output nothing except the extracted text"""

VISION_RETRY_PROMPT = """Look at this receipt image again very carefully.

This may be a French or bilingual Quebec receipt. Focus specifically on:
1. The bottom third — ALL numbers for totals, taxes, amounts due
2. Lines with: GST, TPS, QST, TVQ, HST, PST, TOTAL, MONTANT, SOLDE, SOUS-TOTAL, SUBTOTAL, BALANCE
3. ALL dates — note each with its label (transaction date, print date, etc.)
4. Business name or logo at the very top
5. French decimal: 20,50 = $20.50 (comma is decimal point in French)

Output ONLY the raw extracted text."""

TEXT_PROMPT = """You are a Canadian receipt data extractor. Receipts may be in English, French, or both.

RECEIPT TEXT:
---
{ocr_text}
---

EXISTING VENDOR NAMES IN DATABASE (use for spelling correction):
{vendor_hints}

=== BILINGUAL TAX MAPPING ===
  GST / TPS  → gst field → ~5% of pre-tax (federal)
  QST / TVQ  → qst field → ~9.975% of pre-tax (Quebec)
  PST        → pst field → 6–10% (other provinces)
  HST        → hst field → 13–15% (ON/NS/NB/NL/PEI)

=== FRENCH KEYWORDS ===
  SOUS-TOTAL / SUBTOTAL          → pre_tax
  MONTANT DÛ / MONTANT TOTAL     → total
  TOTAL À PAYER / SOLDE          → total
  BALANCE DUE / TOTAL DUE        → total
  REÇU / FACTURE                 → is_receipt = true
  French decimal: 20,50 = 20.50

=== VENDOR IDENTIFICATION RULES ===
Use ALL available clues in priority order:

1. DOMAIN NAMES / URLs — highest reliability:
   virginplus.ca / virginmobile.ca → "Virgin Plus"
   bell.ca → "Bell"
   rogers.com → "Rogers"
   hydroquebec.com → "Hydro-Québec"
   videotron.com → "Vidéotron"
   amazon.ca / amazon.com → "Amazon"
   Example: if you see "virginplus.ca/benefits" → vendor is "Virgin Plus"

2. EXPLICIT BUSINESS NAME — in text, letterhead, address block, or email domain

3. GST/HST registration number (format: 123456789 RT0001) identifies registered businesses

4. BILL/INVOICE TYPE — if text says "Monthly charges", "Account Summary", "Current charges":
   - Check for telecom indicators (data, minutes, long distance, SMS) → mobile/telecom provider
   - Check for utility indicators (kWh, cubic metres, delivery charge) → utility company

5. PRODUCT INFERENCE (last resort only):
   - Lumber, screws, paint, tools, plumbing supplies → hardware store
   - Prescriptions, pharmacy items → pharmacy
   - Groceries, produce, dairy, meat → grocery store
   - Restaurant menu items, meals → restaurant

6. Strip store numbers from vendor: "HOME DEPOT #7038" → "Home Depot"
7. Use existing vendor names from database for spelling correction

MARKETPLACE RECEIPTS (eBay, Amazon, Etsy, PayPal):
- The VENDOR is the PLATFORM, not the individual seller
- eBay receipt → vendor = "eBay"  (not the seller's username or store name)
- Amazon receipt → vendor = "Amazon"  (not the third-party seller)
- If you see "Sold by: [seller name]" — the seller is NOT the vendor
- If you see "Ship to: [name]" or "Bill to: [name]" — that is the BUYER, not the vendor
- Buyer names, usernames, shipping addresses must NEVER be used as the vendor

=== DATE DISAMBIGUATION (CRITICAL) ===
Receipts and bills contain MULTIPLE dates. Pick the PRIMARY billing/transaction date:

PRIORITY ORDER — pick the FIRST match:
1. "Bill Date", "Invoice Date", "Date de facturation" → this IS the transaction date
2. "Transaction date", "Date", "Date d'achat", "Purchased" + a time → transaction date  
3. "Statement date", "Issue date" → use this if no Bill Date exists
4. Unlabelled date near top of document associated with a time → likely transaction date

IGNORE these dates (do NOT use):
- "Next Bill Date", "Prochaine date", "Next payment" → future date, wrong
- "Expiry", "Valid until", "Valide jusqu'au" → card/offer expiry
- "Return by", "Retour avant", "Best before" → return/expiry policy
- "Due date", "Date d'échéance" → payment due date (different from bill date)
- "Print date", "Printed on" → document generation date

Convert any format to YYYY-MM-DD

=== TAX SANITY (CRITICAL) ===
Each tax must be a small % of total — if not, set to 0:
  gst > 8% of total  → set gst to 0
  qst > 13% of total → set qst to 0
  pst > 15% of total → set pst to 0
  hst > 20% of total → set hst to 0
  Any tax ≥ total    → set to 0 (always wrong)

=== EXTRACTION RULES ===
- is_receipt: true for ANY of: purchase receipts, invoices, phone/utility bills, parking fines,
  payment confirmations, tax notices, government fees — anything showing money paid or owed
- vendor: the ORGANIZATION that issued the document. Use ALL clues:
  • Header / letterhead / "From:" field
  • Website URLs or email domains anywhere in text (ville.montreal.qc.ca → "Ville de Montreal")
  • Physical address city + department ("275 Rue Notre-Dame Est, Montréal... Ville de Montréal" → "Ville de Montreal")
  • "Issued by", "Billed by", "From", "De la part de"
  • Municipal/government clues: "City of X", "Ville de X", "Ministère", "Gouvernement"
  • Strip accents from vendor name: é→e, à→a, ç→c, ô→o (use plain ASCII)
  • Strip store numbers: "HOME DEPOT #7038" → "Home Depot"
- total: FINAL amount paid including ALL taxes
- pre_tax: subtotal BEFORE taxes (if not shown, calculate: total - all taxes)
- currency: "CAD" default; "USD" only if explicitly shown
- confidence: 0.9+ only if vendor+date+total are all clear; 0.5 if any are missing

{det_context}

Respond with ONLY valid JSON, no markdown, no explanation:

{{
  "is_receipt": true,
  "date": "YYYY-MM-DD",
  "vendor": "string",
  "total": 0.00,
  "gst": 0.00,
  "qst": 0.00,
  "pst": 0.00,
  "hst": 0.00,
  "pre_tax": 0.00,
  "currency": "CAD",
  "confidence": 0.0
}}"""

TEXT_CORRECTION = """Your previous response was not valid JSON. Respond with ONLY a JSON object.
No markdown, no explanation. Start with {{ and end with }}.
Required: is_receipt (bool), date (string or null), vendor (string or null),
total, gst, qst, pst, hst, pre_tax (all numbers), currency (string), confidence (number).
All tax values must be LESS than total. JSON only:"""


# ── Known logo → vendor mapping (fallback if model can't identify) ─────────────
LOGO_VENDOR_MAP = {
    "home depot": "Home Depot",
    "canadian tire": "Canadian Tire",
    "costco": "Costco",
    "walmart": "Walmart",
    "iga": "IGA",
    "metro": "Metro",
    "jean coutu": "Jean Coutu",
    "pharmaprix": "Pharmaprix",
    "shoppers drug mart": "Shoppers Drug Mart",
    "shoppers": "Shoppers Drug Mart",
    "saq": "SAQ",
    "tim hortons": "Tim Hortons",
    "mcdonalds": "McDonald's",
    "mcdonald's": "McDonald's",
    "subway": "Subway",
    "dollarama": "Dollarama",
    "ikea": "IKEA",
    "rona": "Rona",
    "best buy": "Best Buy",
    "staples": "Staples",
    "winners": "Winners",
    "homesense": "HomeSense",
    "loblaws": "Loblaws",
    "provigo": "Provigo",
    "super c": "Super C",
    "maxi": "Maxi",
    "amazon": "Amazon",
    "apple": "Apple",
    # Telecom / utility
    "virgin plus": "Virgin Plus", "virgin mobile": "Virgin Plus", "virgin": "Virgin Plus",
    "bell": "Bell", "rogers": "Rogers", "telus": "Telus",
    "videotron": "Videotron", "vidéotron": "Videotron",
    "fido": "Fido", "koodo": "Koodo", "fizz": "Fizz",
    "hydro-québec": "Hydro-Québec", "hydro-quebec": "Hydro-Québec", "hydro": "Hydro-Québec",
    "enbridge": "Enbridge",
}


class OllamaClient:
    def __init__(self):
        self.base_url  = OLLAMA_URL
        self.timeout   = httpx.Timeout(300.0)
        self._pipeline = ReceiptImagePipeline()
        self._parser   = ReceiptPostProcessor()
        self._det      = DeterministicParser()

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout)

    def health_check(self) -> bool:
        try:
            with self._client() as c:
                return c.get(f"{self.base_url}/api/tags", timeout=5.0).status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            with self._client() as c:
                r = c.get(f"{self.base_url}/api/tags")
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

    def identify_logo(self, img_bytes: bytes, model: Optional[str] = None) -> str:
        """
        Quick dedicated pass to identify brand logos visually.
        Crops the TOP 20% of the receipt — vendor/logo always appears there.
        Sending just the header strip avoids distraction from numbers/totals.
        Returns a clean vendor name string, or "unknown".
        """
        model = model or VISION_MODEL
        try:
            # Use only the top strip for logo ID — much more accurate
            top_bytes = crop_top_region(img_bytes, fraction=0.22) or img_bytes
            raw = self._vision_ocr(top_bytes, model, LOGO_PROMPT, num_predict=60)
            result = raw.strip().strip('"').strip("'").lower()
            # Map to canonical name
            for key, canonical in LOGO_VENDOR_MAP.items():
                if key in result:
                    logger.info(f"Logo identified: '{raw.strip()}' → '{canonical}'")
                    return canonical
            # Return raw if it's short and plausible (not "unknown" or long explanation)
            if result and result != "unknown" and len(result) < 60 and "\n" not in result:
                return raw.strip().title()
            return "unknown"
        except Exception as e:
            logger.warning(f"Logo identification failed: {e}")
            return "unknown"

    def ocr_document(
        self,
        raw_bytes: bytes,
        model: Optional[str] = None,
        paperless_text: Optional[str] = None,
    ) -> tuple[str, str, str]:
        """
        Returns (ocr_text, ocr_method, logo_hint).
        logo_hint is passed to parse_receipt to help vendor identification.
        """
        model = model or VISION_MODEL

        if is_pdf(raw_bytes):
            pdf_text = extract_pdf_text(raw_bytes)
            if pdf_text and len(pdf_text) >= MIN_OCR_LENGTH:
                return pdf_text, "pdf_direct", "not applicable"
            img_bytes = pdf_to_image(raw_bytes)
            if img_bytes:
                raw_bytes = img_bytes

        preprocessed = self._pipeline.process(raw_bytes)

        # ── Logo identification pass (quick, dedicated call) ───────────────
        logo_hint = self.identify_logo(preprocessed, model)
        logger.info(f"Logo scan result: '{logo_hint}'")

        # ── Main OCR pass ──────────────────────────────────────────────────
        text = self._vision_ocr(preprocessed, model, VISION_PROMPT)

        if len(text.strip()) < MIN_OCR_LENGTH:
            text2 = self._vision_ocr(preprocessed, model, VISION_RETRY_PROMPT)
            if len(text2) > len(text):
                text = text2

        if text.strip():
            text = self._normalise_french_decimals(text)
            # ── Bottom-region retry for totals/taxes ──────────────────────
            # If a quick deterministic scan shows missing total, re-run OCR
            # on just the bottom 45% where totals always live.
            det_quick = self._det.parse(text)
            if det_quick.get("total") is None:
                logger.info("Main OCR missed total — retrying on bottom region")
                bottom_bytes = crop_bottom_region(preprocessed, fraction=0.45)
                if bottom_bytes:
                    bottom_text = self._vision_ocr(bottom_bytes, model, VISION_RETRY_PROMPT)
                    if bottom_text.strip():
                        bottom_text = self._normalise_french_decimals(bottom_text)
                        text = text + "\n\n[BOTTOM REGION RESCAN]\n" + bottom_text
                        logger.info(f"Bottom rescan added {len(bottom_text)} chars")
            return text, "vision_preprocessed", logo_hint

        # Fallback: raw bytes
        text = self._vision_ocr(raw_bytes, model, VISION_PROMPT)
        text = self._normalise_french_decimals(text)
        return (text, "vision", logo_hint) if text.strip() else ("", "failed", logo_hint)

    def parse_receipt(
        self,
        ocr_text: str,
        model: Optional[str] = None,
        vendor_hints: Optional[list[str]] = None,
        logo_hint: Optional[str] = None,
    ) -> dict:
        model = model or TEXT_MODEL

        if len(ocr_text.strip()) < MIN_OCR_LENGTH:
            return {"is_receipt": False, "confidence": 0.0, "error": "text_too_short"}

        # ── Step 1: Deterministic pre-scan ────────────────────────────────────
        det = self._det.parse(ocr_text)
        det_context = self._det.format_as_prompt_context(det)
        if logo_hint and logo_hint not in ("unknown", "not applicable", "not identified"):
            if not det.get("vendor"):
                det["vendor"] = logo_hint
                logger.info(f"Deterministic vendor from logo: '{logo_hint}'")

        # ── Step 2: LLM parse with deterministic anchors ─────────────────────
        hints_str = "\n".join(f"- {v}" for v in (vendor_hints or [])[:20]) or "(none yet)"
        logo_str  = logo_hint or "not identified"
        prompt    = TEXT_PROMPT.format(
            ocr_text=ocr_text[:8000],
            vendor_hints=hints_str,
            logo_hint=logo_str,
            det_context=det_context,
        )
        raw_response = self._generate(model, prompt)
        parsed = self._extract_json(raw_response)

        if parsed is None:
            logger.warning("Pass 1 JSON parse failed — retrying")
            raw2 = self._generate(model, TEXT_CORRECTION, prior_bad=raw_response)
            parsed = self._extract_json(raw2)

        if parsed is None:
            logger.error(f"JSON parse failed after retry. Raw: {raw_response[:300]}")
            return self._build_from_deterministic(det, ocr_text)

        sanitized = self._sanitize(parsed)

        # ── Step 3: Merge deterministic wins over LLM when values conflict ────
        sanitized = self._merge_with_deterministic(sanitized, det)

        # ── Step 4: Post-process, then two-pass if still low confidence ───────
        post = self._parser.process(sanitized, ocr_text=ocr_text)
        if post.get("confidence", 1.0) < 0.65:
            missing = [f for f in ["vendor", "date", "total"] if not post.get(f)]
            if missing:
                logger.info(f"Low conf ({post.get('confidence'):.2f}) — second pass for: {missing}")
                post = self._second_pass(ocr_text, post, det, missing, model)

        return post

    def _merge_with_deterministic(self, llm: dict, det: dict) -> dict:
        merged = dict(llm)
        for field in ["gst", "qst", "pst", "hst", "pre_tax"]:
            if det.get(field) is not None and not merged.get(field):
                merged[field] = det[field]
                logger.info(f"Det override: {field}={det[field]}")
        det_total = det.get("total")
        llm_total = merged.get("total") or 0.0
        if det_total and llm_total and abs(det_total - llm_total) > 1.00:
            logger.info(f"Total: LLM={llm_total} vs det={det_total} — using det")
            merged["total"] = det_total
        elif det_total and not llm_total:
            merged["total"] = det_total
        if det.get("vendor") and not merged.get("vendor"):
            merged["vendor"] = det["vendor"]
        if det.get("date") and not merged.get("date"):
            merged["date"] = det["date"]
        return merged

    def _build_from_deterministic(self, det: dict, ocr_text: str) -> dict:
        base = {
            "is_receipt": True, "date": det.get("date"), "vendor": det.get("vendor"),
            "total": det.get("total") or 0.0, "gst": det.get("gst") or 0.0,
            "qst": det.get("qst") or 0.0, "pst": det.get("pst") or 0.0,
            "hst": det.get("hst") or 0.0, "pre_tax": det.get("pre_tax") or 0.0,
            "currency": "CAD", "confidence": 0.4,
        }
        return self._parser.process(base, ocr_text=ocr_text)

    def _second_pass(self, ocr_text: str, first: dict, det: dict, missing: list, model: str) -> dict:
        hints = {
            "vendor": "Identify the BUSINESS NAME or ORGANIZATION. Check domain names, letterhead, first lines, URLs.",
            "date":   "Find the TRANSACTION DATE or BILL DATE only (not expiry, not next billing).",
            "total":  "Find the FINAL TOTAL AMOUNT PAID — largest labelled amount near the bottom.",
        }
        focused = "\n".join(hints.get(f, "") for f in missing)
        prompt = (
            f"Receipt text:\n---\n{ocr_text[:5000]}\n---\n\n"
            f"Missing fields: {', '.join(missing)}\n{focused}\n\n"
            f"Return ONLY JSON with these keys: {', '.join(missing)}. "
            f"Use null if not found. No other fields:\n"
            f"{{{', '.join(chr(34)+f+chr(34)+': null' for f in missing)}}}"
        )
        raw = self._generate(model, prompt)
        patch = self._extract_json(raw)
        if not patch:
            return first
        result = dict(first)
        for f in missing:
            if patch.get(f) is not None:
                result[f] = patch[f]
                logger.info(f"2nd pass: {f}={patch[f]}")
        return self._parser.process(self._sanitize(result), ocr_text=ocr_text)


    def _normalise_french_decimals(self, text: str) -> str:
        """
        Convert French comma-decimal numbers to period-decimal.
        Only converts clear currency amounts: "20,50" → "20.50"
        Leaves large numbers like "1,234" (thousands separators) alone.
        """
        def replace_decimal(m):
            integer_part = m.group(1)
            decimal_part = m.group(2)
            # French decimal: exactly 2 digits after comma = decimal separator
            # "1,234" could be thousands separator if decimal_part is 3 digits
            if len(decimal_part) == 2:
                return f"{integer_part}.{decimal_part}"
            return m.group(0)  # leave unchanged

        return re.sub(r"\b(\d+),(\d+)\b", replace_decimal, text)

    # ── Vision call ────────────────────────────────────────────────────────────

    def _vision_ocr(self, img_bytes: bytes, model: str, prompt: str,
                    num_predict: int = 2048) -> str:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        payload = {
            "model": model,
            "prompt": prompt,
            "images": [b64],
            "stream": False,
            "options": {"temperature": 0.0, "seed": 42, "num_predict": num_predict},
        }
        try:
            with self._client() as c:
                r = c.post(f"{self.base_url}/api/generate", json=payload)
                r.raise_for_status()
            return r.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"Vision OCR failed: {e}")
            return ""

    # ── Text generate ──────────────────────────────────────────────────────────

    def _generate(self, model: str, prompt: str, prior_bad: Optional[str] = None) -> str:
        full_prompt = prompt
        if prior_bad:
            full_prompt = (
                f"{prompt}\n\nYour previous attempt produced invalid output:\n{prior_bad}\n\n"
                "Respond with ONLY valid JSON:"
            )
        payload = {
            "model": model,
            "prompt": full_prompt,
            "stream": False,
            "options": {"temperature": 0.0, "seed": 42, "num_predict": 768},
        }
        try:
            with self._client() as c:
                r = c.post(f"{self.base_url}/api/generate", json=payload)
                r.raise_for_status()
            return r.json().get("response", "").strip()
        except Exception as e:
            logger.error(f"Generate call failed: {e}")
            return ""

    # ── JSON extraction ────────────────────────────────────────────────────────

    def _extract_json(self, text: str) -> Optional[dict]:
        text = re.sub(r"```(?:json)?", "", text.strip()).strip().rstrip("`").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start = text.find("{")
        if start != -1:
            depth, end = 0, -1
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            if end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
        return None

    # ── Sanitise parsed output ─────────────────────────────────────────────────

    def _sanitize(self, data: dict) -> dict:
        def sf(v, default=0.0):
            try:
                return max(0.0, round(float(v or 0), 2))
            except (TypeError, ValueError):
                return default
        return {
            "is_receipt": bool(data.get("is_receipt", False)),
            "date":       str(data["date"]).strip() if data.get("date") else None,
            "vendor":     str(data["vendor"]).strip() if data.get("vendor") else None,
            "total":      sf(data.get("total")),
            "gst":        sf(data.get("gst")),
            "qst":        sf(data.get("qst")),
            "pst":        sf(data.get("pst")),
            "hst":        sf(data.get("hst")),
            "pre_tax":    sf(data.get("pre_tax")),
            "currency":   str(data.get("currency", "CAD")).upper()[:3],
            "confidence": max(0.0, min(1.0, sf(data.get("confidence"), 0.5))),
        }
