"""
Document processing pipeline.
Uses PaddleOCR as primary OCR with CLIP for logo detection.
Falls back to Ollama LLM only when needed.
"""
import hashlib
import logging
import re
import threading
import time
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models.database import Document, Receipt, ProcessingConfig, SessionLocal
from services.paperless import PaperlessClient
from services.paddle_ocr import PaddleOCRClient
from services.vendor import normalize_vendor, assign_category
from services.review import auto_flag_receipt

logger = logging.getLogger(__name__)
RECEIPT_TAG = "receipt-processed"
DOC_TIMEOUT = 300
# PDFs larger than this are almost certainly bank/credit card statements,
# not individual receipts. Real receipts rarely exceed 5,000 chars even
# for long itemized ones. Statements are typically 500k+ chars.
MAX_RECEIPT_CHARS = 15_000


def _run_with_timeout(fn, args=(), kwargs=None, timeout=DOC_TIMEOUT):
    if kwargs is None:
        kwargs = {}
    result = [None]
    exc    = [None]
    def target():
        try:
            result[0] = fn(*args, **kwargs)
        except Exception as e:
            exc[0] = e
    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"Timed out after {timeout}s")
    if exc[0]:
        raise exc[0]
    return result[0]


def _get_config(db: Session) -> dict:
    return {c.key: c.value for c in db.query(ProcessingConfig).all()}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _get_vendor_hints(db: Session) -> list[str]:
    """Return distinct known vendor names to feed into the LLM prompt."""
    rows = db.query(Receipt.vendor).filter(Receipt.vendor.isnot(None)).distinct().limit(50).all()
    return [r[0] for r in rows if r[0]]


def _looks_like_bank_statement(text: str) -> bool:
    """
    Check if the extracted text looks like a bank/credit card statement.
    Only skip if we're very confident it's NOT a receipt.
    """
    if not text:
        return False
    
    text_lower = text.lower()
    
    # Strong indicators of bank statements (multiple of these = skip)
    bank_keywords = [
        "account summary", "account balance", "previous balance",
        "new balance", "credit limit", "available credit",
        "payment due date", "minimum payment", "annual fee",
        "transaction date", "posting date", "merchant name",
        "opening balance", "closing balance", "interest charged",
        "finance charge", "periodic rate", "annual percentage",
    ]
    
    # Count matching keywords
    matches = sum(1 for kw in bank_keywords if kw in text_lower)
    
    # If we have multiple bank keywords AND the text is very long, it's likely a statement
    if matches >= 3 and len(text) > 200_000:
        return True
    
    return False


class DocumentProcessor:
    def __init__(self):
        self.paperless = PaperlessClient()
        self.paddle_ocr = PaddleOCRClient()

    def process_document(self, paperless_doc, force_reocr=False,
                         text_model=None, db=None) -> dict:
        own_db = db is None
        if own_db:
            db = SessionLocal()
        try:
            return self._pipeline(paperless_doc, force_reocr, text_model, db)
        except TimeoutError as e:
            logger.error(f"Doc {paperless_doc.get('id')} timed out: {e}")
            return {"status": "error", "error": "timeout"}
        except Exception as e:
            logger.exception(f"Pipeline error doc {paperless_doc.get('id')}: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            if own_db:
                db.close()

    def _pipeline(self, paperless_doc, force_reocr, text_model, db):
        pid = paperless_doc["id"]
        cfg = _get_config(db)

        use_paperless_first = cfg.get("use_paperless_ocr_first", "true") == "true"
        auto_skip_vision    = cfg.get("auto_skip_vision_if_text_exists", "true") == "true"
        force_reocr         = force_reocr or cfg.get("force_reocr", "false") == "true"
        text_model          = text_model   or cfg.get("text_model")   or None

        doc = db.query(Document).filter_by(paperless_id=pid).first()
        if not doc:
            doc = Document(paperless_id=pid)
            db.add(doc)
            db.flush()

        # ── Check if tag was removed — force rescan of existing receipt ────
        already_tagged = self._is_tagged(paperless_doc)
        existing_receipt = db.query(Receipt).filter_by(document_id=doc.id).first()
        if not already_tagged and existing_receipt and not force_reocr:
            logger.info(f"Doc {pid}: tag removed — forcing rescan")
            force_reocr = True

        doc.last_status = "processing"
        doc.updated_at  = datetime.utcnow()
        db.commit()

        paperless_text  = (paperless_doc.get("content") or "").strip()
        content_hash    = _sha256(paperless_text) if paperless_text else None
        content_changed = bool(content_hash and content_hash != doc.content_hash)
        text_changed    = bool(text_model   and text_model   != doc.text_model_used)
        doc.content_hash = content_hash

        # ── OCR phase ──────────────────────────────────────────────────────
        # Primary: PaddleOCR (fast, accurate)
        # Fallback: Ollama vision (only when PaddleOCR fails)
        has_text  = len(paperless_text) >= 40
        skip_vis  = (has_text and use_paperless_first and not force_reocr
                     and (auto_skip_vision or not text_changed))

        if skip_vis:
            ocr_text, ocr_method, logo_hint = paperless_text, "paperless", ""
            logger.info(f"Doc {pid}: using Paperless text ({len(ocr_text)} chars)")
        else:
            needs_ocr = (force_reocr or content_changed
                         or not doc.ocr_text or not has_text)
            if needs_ocr:
                try:
                    raw_bytes = _run_with_timeout(self.paperless.download_document, args=(pid,))
                    
                    # Use PaddleOCR as primary
                    ocr_text, ocr_method, logo_hint = _run_with_timeout(
                        self.paddle_ocr.ocr_document, 
                        args=(raw_bytes,),
                        kwargs={"paperless_text": paperless_text or None})
                    
                    # Try CLIP logo detection ONLY for images (not PDF text)
                    # CLIP only works on actual images, not PDF bytes
                    if ocr_method != "pdf_direct" and (logo_hint == "unknown" or not logo_hint):
                        try:
                            logo_hint = _run_with_timeout(
                                self.paddle_ocr.identify_logo,
                                args=(raw_bytes,))
                        except Exception as e:
                            logger.warning(f"Doc {pid}: CLIP logo detection failed: {e}")
                    
                    doc.ocr_text = ocr_text
                    doc.ocr_text_hash = _sha256(ocr_text) if ocr_text else None
                    char_count = len(ocr_text or '')
                    logger.info(f"Doc {pid}: OCR [{ocr_method}] — {char_count} chars")
                    
                    # Large PDF handling - be less aggressive about skipping
                    if ocr_method == "pdf_direct" and char_count > MAX_RECEIPT_CHARS:
                        # Only skip if definitely a bank statement
                        if _looks_like_bank_statement(ocr_text):
                            logger.warning(
                                f"Doc {pid}: BANK STATEMENT — {char_count:,} chars. Skipping."
                            )
                            doc.last_status = "done"
                            doc.error_message = f"statement_detected:{char_count}_chars"
                            doc.processed_timestamp = datetime.utcnow()
                            doc.updated_at = datetime.utcnow()
                            db.commit()
                            self._tag(pid)
                            return {"status": "done", "is_receipt": False, "reason": "statement_detected"}
                        else:
                            # Not a bank statement - process as receipt (phone records, etc.)
                            # For statements: keep first 8000 chars (summary page) + last 2000 chars
                            # This ensures we capture the total/summary on page 1
                            logger.info(f"Doc {pid}: Large document ({char_count:,} chars) - truncating for LLM")
                            first_section = ocr_text[:8000]
                            last_section  = ocr_text[-2000:] if len(ocr_text) > 8000 else ""
                            sep = "\n\n[... middle section omitted ...]\n\n"
                            ocr_text = first_section + (sep + last_section if last_section else "")
                            doc.ocr_text = ocr_text
                            doc.ocr_text_hash = _sha256(ocr_text)
                            logger.info(f"Doc {pid}: Truncated to {len(ocr_text)} chars for LLM")
                            
                except Exception as e:
                    logger.error(f"Doc {pid}: OCR error: {e}")
                    if doc.ocr_text:           ocr_text, ocr_method, logo_hint = doc.ocr_text, "cached", ""
                    elif has_text:             ocr_text, ocr_method, logo_hint = paperless_text, "paperless_fallback", ""
                    else:
                        doc.last_status = "error"; doc.error_message = str(e)
                        doc.updated_at = datetime.utcnow(); db.commit()
                        return {"status": "error", "error": str(e)}
            else:
                ocr_text, ocr_method, logo_hint = (doc.ocr_text or paperless_text), "cached", ""

        doc.ocr_text = ocr_text
        doc.ocr_text_hash = _sha256(ocr_text) if ocr_text else None
        doc.updated_at = datetime.utcnow()
        db.commit()

        if not (ocr_text or "").strip():
            doc.last_status = "skipped"; doc.error_message = "No usable text"
            doc.updated_at = datetime.utcnow(); db.commit()
            return {"status": "skipped", "reason": "no_text"}

        # ── Parse phase ────────────────────────────────────────────────────
        # Primary: Deterministic parser (fast, reliable)
        # Fallback: LLM (only when deterministic insufficient)
        ocr_hash    = _sha256(ocr_text)
        needs_parse = (not doc.structured_parse_hash
                       or doc.structured_parse_hash != ocr_hash
                       or text_changed or force_reocr)

        if not needs_parse:
            doc.last_status = "done"; doc.updated_at = datetime.utcnow(); db.commit()
            receipt = db.query(Receipt).filter_by(document_id=doc.id).first()
            self._tag(pid)  # ensure tag is present
            return {"status": "done", "is_receipt": receipt is not None, "cached": True}

        vendor_hints = _get_vendor_hints(db)
        
        try:
            # Use PaddleOCR's parse method (deterministic + optional LLM fallback)
            parsed = _run_with_timeout(
                self.paddle_ocr.parse_receipt, 
                args=(ocr_text,),
                kwargs={"vendor_hints": vendor_hints, "logo_hint": logo_hint})
        except TimeoutError:
            doc.last_status = "error"; doc.error_message = "parse timeout"
            doc.updated_at = datetime.utcnow(); db.commit()
            return {"status": "error", "error": "parse timeout"}

        doc.text_model_used = text_model
        doc.structured_parse_hash = ocr_hash

        if parsed.get("error"):
            doc.last_status = "error"; doc.error_message = parsed["error"]
            doc.updated_at = datetime.utcnow(); db.commit()
            return {"status": "error", "error": parsed["error"]}

        if not parsed.get("is_receipt"):
            doc.last_status = "done"; doc.processed_timestamp = datetime.utcnow()
            doc.updated_at = datetime.utcnow(); db.commit()
            return {"status": "done", "is_receipt": False}

        # ── Upsert receipt ─────────────────────────────────────────────────
        receipt = db.query(Receipt).filter_by(document_id=doc.id).first()
        if not receipt:
            receipt = Receipt(document_id=doc.id)
            db.add(receipt)

        receipt.vendor            = parsed.get("vendor")
        receipt.normalized_vendor = normalize_vendor(parsed.get("vendor") or "")
        receipt.date              = parsed.get("date")
        receipt.pre_tax           = parsed.get("pre_tax",    0.0)
        receipt.gst               = parsed.get("gst",        0.0)
        receipt.qst               = parsed.get("qst",        0.0)
        receipt.pst               = parsed.get("pst",        0.0)
        receipt.hst               = parsed.get("hst",        0.0)
        receipt.total             = parsed.get("total",      0.0)
        receipt.currency          = parsed.get("currency",   "CAD")
        receipt.confidence        = parsed.get("confidence", 0.0)
        receipt.updated_at        = datetime.utcnow()
        db.flush()

        assign_category(db, receipt)
        auto_flag_receipt(db, receipt)

        # ── Paperless: rename file + tag + custom fields ───────────────────
        self._update_paperless(pid, receipt)

        doc.last_status = "done"; doc.processed_timestamp = datetime.utcnow()
        doc.updated_at = datetime.utcnow(); db.commit()

        return {
            "status": "done", "is_receipt": True,
            "vendor": receipt.vendor, "total": receipt.total,
            "confidence": receipt.confidence, "ocr_method": ocr_method,
            "warnings": parsed.get("_warnings", []),
        }

    def _is_tagged(self, paperless_doc: dict) -> bool:
        """Check if the document already has the receipt-processed tag."""
        tags = paperless_doc.get("tags", [])
        if not tags:
            return False
        # tags can be list of ints or list of dicts
        if isinstance(tags[0], dict):
            return any(t.get("name") == RECEIPT_TAG for t in tags)
        # If ints, we'd need to look up — assume not tagged to be safe
        return False

    def _tag(self, pid: int):
        """Tag document in Paperless, with retry on failure."""
        for attempt in range(3):
            try:
                tag_id = self.paperless.get_or_create_tag(RECEIPT_TAG)
                self.paperless.add_tags(pid, [tag_id])
                return
            except Exception as e:
                logger.warning(f"Doc {pid}: tag attempt {attempt+1} failed: {e}")
                time.sleep(1)
        logger.error(f"Doc {pid}: tagging failed after 3 attempts")

    def _update_paperless(self, pid: int, receipt: Receipt):
        """Tag, rename, and set custom fields in Paperless."""
        self._tag(pid)
        # Rename file to DATE-VENDOR-Receipt
        try:
            if receipt.date and receipt.vendor:
                safe_vendor = re.sub(r'[<>:"/\\|?*]', '', receipt.vendor)[:50]
                new_title = f"{receipt.date}-{safe_vendor}-Receipt"
                self.paperless.rename_document(pid, new_title)
        except Exception as e:
            logger.warning(f"Doc {pid}: rename failed (non-fatal): {e}")
        # Set custom fields
        try:
            self.paperless.set_custom_fields(pid, {
                "Vendor":    receipt.vendor or "",
                "Amount":    str(receipt.total or 0),
                "Category":  receipt.category.name if receipt.category else "",
                "Currency":  receipt.currency or "CAD",
            })
        except Exception as e:
            logger.debug(f"Doc {pid}: custom fields not set (non-fatal): {e}")


def run_batch(limit=None, force_reocr=False) -> dict:
    processor = DocumentProcessor()
    db = SessionLocal()
    stats = {"processed": 0, "receipts": 0, "errors": 0, "skipped": 0, "flagged": 0}
    try:
        count = 0
        for doc in processor.paperless.get_all_documents():
            if limit and count >= limit:
                break
            result = processor.process_document(doc, force_reocr=force_reocr, db=db)
            count += 1
            stats["processed"] += 1
            if   result.get("status") == "error":   stats["errors"]  += 1
            elif result.get("status") == "skipped":  stats["skipped"] += 1
            elif result.get("is_receipt"):
                stats["receipts"] += 1
                if result.get("warnings"):           stats["flagged"] += 1
            time.sleep(0.15)
    finally:
        db.close()
    logger.info(f"Batch complete: {stats}")
    return stats
