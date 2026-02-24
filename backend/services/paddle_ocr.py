"""
PaddleOCR + CLIP for receipt processing.

This module provides:
  - OCR text extraction using PaddleOCR (primary, fast, accurate)
  - Logo/vendor detection using CLIP zero-shot classification
  - Receipt parsing using DeterministicParser + Ollama LLM fallback
  - Post-processing via ReceiptPostProcessor

Falls back to Ollama vision OCR when PaddleOCR fails or produces
insufficient text.
"""
import io
import logging
import os
import threading
from typing import Optional, Tuple

from PIL import Image

from services.image_prep import (
    ReceiptImagePipeline,
    extract_pdf_text,
    is_pdf,
    pdf_to_image,
    crop_top_region,
    crop_bottom_region,
)
from services.deterministic_parser import DeterministicParser
from services.receipt_parser import ReceiptPostProcessor

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MIN_OCR_LENGTH = 40

# Vendor labels for CLIP zero-shot classification
VENDOR_LOGOS = [
    "Home Depot",
    "Canadian Tire",
    "Costco",
    "Walmart",
    "IGA",
    "Metro",
    "Jean Coutu",
    "Pharmaprix",
    "Shoppers Drug Mart",
    "SAQ",
    "Tim Hortons",
    "McDonald's",
    "Subway",
    "Dollarama",
    "IKEA",
    "Rona",
    "Best Buy",
    "Staples",
    "Loblaws",
    "Provigo",
    "Super C",
    "Maxi",
    "Amazon",
    "eBay",
    "Starbucks",
    "A&W",
    "Virgin Plus",
    "Bell",
    "Rogers",
    "Telus",
    "Videotron",
    "Fido",
    "Koodo",
    "Hydro-Quebec",
    "Enbridge",
    "UPS",
    "FedEx",
    "Canada Post",
]


class PaddleOCRClient:
    """Primary OCR client using PaddleOCR + CLIP."""

    # Class-level lock prevents concurrent PaddleOCR initialization.
    # Multiple HTTP requests can trigger health_check() or ocr_document()
    # simultaneously, and PaddleOCR's model download is not thread-safe.
    _paddle_lock = threading.Lock()
    _clip_lock = threading.Lock()

    def __init__(self):
        self._paddle_engine = None
        self._clip_model = None
        self._clip_processor = None
        self._ollama_client = None
        self._paddle_ready = False
        self._clip_ready = False
        self._pipeline = ReceiptImagePipeline()
        self._parser = ReceiptPostProcessor()
        self._det = DeterministicParser()

    # ── Lazy initialization ────────────────────────────────────────────────────

    def _init_paddle(self, max_retries: int = 3) -> bool:
        """
        Lazy-load PaddleOCR engine on first use.

        Thread-safe: uses a class-level lock so only one thread downloads
        models at a time. Retries up to max_retries times, cleaning up
        corrupt model files between attempts.
        """
        if self._paddle_ready:
            return True

        with PaddleOCRClient._paddle_lock:
            # Double-check after acquiring lock (another thread may have finished)
            if self._paddle_ready:
                return True

            for attempt in range(1, max_retries + 1):
                try:
                    # Clean up any corrupt/partial model downloads before init.
                    self._cleanup_partial_downloads()

                    from paddleocr import PaddleOCR

                    self._paddle_engine = PaddleOCR(
                        use_angle_cls=True,
                        lang="en",
                        show_log=False,
                        use_gpu=False,
                    )
                    self._paddle_ready = True
                    logger.info("PaddleOCR engine initialized successfully")
                    return True
                except Exception as e:
                    err_str = str(e).lower()
                    logger.error(
                        f"PaddleOCR init attempt {attempt}/{max_retries} failed: {e}"
                    )
                    # If corrupt download, clean up and retry
                    if any(kw in err_str for kw in [
                        "unexpected end of data",
                        "not a gzip file",
                        "no such file or directory",
                        "truncated",
                        ".tar",
                    ]):
                        logger.info(
                            "Cleaning up corrupt PaddleOCR model files before retry"
                        )
                        self._cleanup_partial_downloads()
                        if attempt < max_retries:
                            continue
                    # Non-recoverable error or max retries reached
                    break

            self._paddle_ready = False
            return False

    @staticmethod
    def _cleanup_partial_downloads():
        """
        Remove corrupt/partial .tar model files from PaddleOCR cache.

        PaddleOCR downloads model .tar files and extracts them. If a download
        is interrupted, the .tar remains but is incomplete. PaddleOCR sees
        the file exists and skips re-download, then fails on extraction.
        We validate each .tar and remove any that are corrupt.
        """
        import glob
        import tarfile

        paddle_home = os.path.expanduser("~/.paddleocr")
        if not os.path.isdir(paddle_home):
            return

        for tar_path in glob.glob(
            os.path.join(paddle_home, "**", "*.tar"), recursive=True
        ):
            try:
                # Quick validation: try to open and read the tar file
                with tarfile.open(tar_path, "r") as tf:
                    tf.getnames()
            except Exception:
                # Corrupt or incomplete — remove it
                try:
                    os.remove(tar_path)
                    logger.info(f"Removed corrupt model file: {tar_path}")
                except OSError as rm_err:
                    logger.warning(f"Could not remove {tar_path}: {rm_err}")

    def _init_clip(self):
        """Lazy-load CLIP model and processor on first use. Thread-safe."""
        if self._clip_ready:
            return True

        with PaddleOCRClient._clip_lock:
            # Double-check after acquiring lock
            if self._clip_ready:
                return True

            try:
                from transformers import CLIPProcessor, CLIPModel

                model_name = "openai/clip-vit-base-patch32"
                self._clip_processor = CLIPProcessor.from_pretrained(model_name)
                self._clip_model = CLIPModel.from_pretrained(model_name)
                self._clip_ready = True
                logger.info("CLIP model loaded successfully")
                return True
            except Exception as e:
                logger.warning(f"CLIP initialization failed: {e}")
                self._clip_ready = False
                return False

    def _get_ollama(self):
        """Lazy-load Ollama client for LLM fallback."""
        if self._ollama_client is None:
            from services.ollama import OllamaClient

            self._ollama_client = OllamaClient()
        return self._ollama_client

    # ── Health check ───────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Check if PaddleOCR can be initialized."""
        try:
            return self._init_paddle()
        except Exception as e:
            logger.warning(f"PaddleOCR health check failed: {e}")
            return False

    # ── OCR document ───────────────────────────────────────────────────────────

    def ocr_document(
        self,
        raw_bytes: bytes,
        model: Optional[str] = None,
        paperless_text: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Extract text from a document using PaddleOCR.

        Returns (ocr_text, ocr_method, logo_hint).

        Pipeline:
        1. If PDF with selectable text → extract directly (fastest)
        2. If PDF scan → rasterize to image
        3. Preprocess image for OCR
        4. Run PaddleOCR
        5. If PaddleOCR fails or insufficient text → fall back to Ollama vision
        """
        logo_hint = "unknown"

        # ── PDF handling ───────────────────────────────────────────────────
        if is_pdf(raw_bytes):
            pdf_text = extract_pdf_text(raw_bytes)
            if pdf_text and len(pdf_text) >= MIN_OCR_LENGTH:
                return pdf_text, "pdf_direct", "not applicable"
            # Rasterize PDF to image for OCR
            img_bytes = pdf_to_image(raw_bytes)
            if img_bytes:
                raw_bytes = img_bytes
            else:
                # Can't rasterize — try Ollama vision as last resort
                logger.warning("PDF rasterization failed, falling back to Ollama vision")
                return self._ollama_vision_fallback(raw_bytes, model, paperless_text)

        # ── Image preprocessing ────────────────────────────────────────────
        preprocessed = self._pipeline.process(raw_bytes)

        # ── PaddleOCR extraction ───────────────────────────────────────────
        if self._init_paddle():
            try:
                ocr_text = self._run_paddle_ocr(preprocessed)

                if ocr_text and len(ocr_text.strip()) >= MIN_OCR_LENGTH:
                    # Check if we got totals — if not, retry on bottom region
                    det_quick = self._det.parse(ocr_text)
                    if det_quick.get("total") is None:
                        logger.info("PaddleOCR missed total — retrying on bottom region")
                        bottom_bytes = crop_bottom_region(preprocessed, fraction=0.45)
                        if bottom_bytes:
                            bottom_text = self._run_paddle_ocr(bottom_bytes)
                            if bottom_text and bottom_text.strip():
                                ocr_text = (
                                    ocr_text
                                    + "\n\n[BOTTOM REGION RESCAN]\n"
                                    + bottom_text
                                )
                                logger.info(
                                    f"Bottom rescan added {len(bottom_text)} chars"
                                )

                    logger.info(
                        f"PaddleOCR extracted {len(ocr_text)} chars"
                    )
                    return ocr_text, "paddleocr", logo_hint

                # Insufficient text from PaddleOCR
                logger.warning(
                    f"PaddleOCR produced insufficient text "
                    f"({len(ocr_text.strip()) if ocr_text else 0} chars), "
                    f"falling back to Ollama"
                )
            except Exception as e:
                logger.error(f"PaddleOCR extraction failed: {e}")

        # ── Fallback: Ollama vision ────────────────────────────────────────
        return self._ollama_vision_fallback(raw_bytes, model, paperless_text)

    def _run_paddle_ocr(self, img_bytes: bytes) -> str:
        """
        Run PaddleOCR on image bytes and return extracted text.
        Converts bytes to PIL Image, runs OCR, concatenates results.
        """
        try:
            # Convert bytes to PIL Image, then to numpy array
            import numpy as np

            img = Image.open(io.BytesIO(img_bytes))
            img = img.convert("RGB")
            img_array = np.array(img)

            result = self._paddle_engine.ocr(img_array, cls=True)

            if not result or not result[0]:
                return ""

            # Extract text lines, sorted by vertical position (top to bottom)
            lines = []
            for line_info in result[0]:
                if line_info and len(line_info) >= 2:
                    bbox = line_info[0]
                    text_info = line_info[1]
                    if isinstance(text_info, (list, tuple)) and len(text_info) >= 1:
                        text = str(text_info[0])
                        confidence = float(text_info[1]) if len(text_info) > 1 else 0.0
                        # Use top-left Y coordinate for sorting
                        y_pos = bbox[0][1] if bbox else 0
                        lines.append((y_pos, text, confidence))

            # Sort by vertical position
            lines.sort(key=lambda x: x[0])

            # Group lines that are at similar Y positions (same row)
            if not lines:
                return ""

            grouped_lines = []
            current_group = [lines[0]]
            y_threshold = 15  # pixels — lines within this are on the same row

            for i in range(1, len(lines)):
                if abs(lines[i][0] - current_group[-1][0]) < y_threshold:
                    current_group.append(lines[i])
                else:
                    # Sort current group by X position (left to right)
                    current_group.sort(
                        key=lambda x: 0
                    )  # Already in reading order from PaddleOCR
                    grouped_lines.append(
                        " ".join(item[1] for item in current_group)
                    )
                    current_group = [lines[i]]

            # Don't forget the last group
            if current_group:
                grouped_lines.append(
                    " ".join(item[1] for item in current_group)
                )

            return "\n".join(grouped_lines)

        except Exception as e:
            logger.error(f"PaddleOCR run failed: {e}")
            return ""

    def _ollama_vision_fallback(
        self,
        raw_bytes: bytes,
        model: Optional[str] = None,
        paperless_text: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """Fall back to Ollama vision OCR when PaddleOCR fails."""
        try:
            ollama = self._get_ollama()
            text, method, logo = ollama.ocr_document(raw_bytes, model=model, paperless_text=paperless_text)
            return text, f"ollama_{method}", logo
        except Exception as e:
            logger.error(f"Ollama vision fallback also failed: {e}")
            # Last resort: use paperless text if available
            if paperless_text and len(paperless_text) >= MIN_OCR_LENGTH:
                return paperless_text, "paperless_fallback", "unknown"
            return "", "failed", "unknown"

    # ── Logo / vendor identification ───────────────────────────────────────────

    def identify_logo(
        self, img_bytes: bytes, model: Optional[str] = None
    ) -> str:
        """
        Identify vendor/brand from receipt image using CLIP zero-shot classification.

        Crops the top 22% of the image (where logos appear) and classifies
        against known vendor labels. Falls back to Ollama vision if CLIP
        is unavailable or confidence is too low.

        Returns a vendor name string, or "unknown".
        """
        # Crop top region where logo/vendor name appears
        top_bytes = crop_top_region(img_bytes, fraction=0.22) or img_bytes

        # ── Try CLIP first ─────────────────────────────────────────────────
        if self._init_clip():
            try:
                clip_result = self._clip_classify(top_bytes)
                if clip_result:
                    logger.info(f"CLIP logo identified: '{clip_result}'")
                    return clip_result
            except Exception as e:
                logger.warning(f"CLIP classification failed: {e}")

        # ── Fallback: Ollama vision logo detection ─────────────────────────
        try:
            ollama = self._get_ollama()
            return ollama.identify_logo(img_bytes, model=model)
        except Exception as e:
            logger.warning(f"Ollama logo fallback failed: {e}")
            return "unknown"

    def _clip_classify(self, img_bytes: bytes) -> Optional[str]:
        """
        Run CLIP zero-shot classification on image bytes against VENDOR_LOGOS.
        Returns the vendor name if confidence > threshold, else None.
        """
        try:
            import torch

            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            # Prepare text labels for zero-shot classification
            text_labels = [f"a receipt from {v}" for v in VENDOR_LOGOS]
            text_labels.append("an unknown receipt or document")

            inputs = self._clip_processor(
                text=text_labels,
                images=img,
                return_tensors="pt",
                padding=True,
            )

            with torch.no_grad():
                outputs = self._clip_model(**inputs)
                logits = outputs.logits_per_image
                probs = logits.softmax(dim=1)

            # Get the best match
            best_idx = probs.argmax().item()
            best_prob = probs[0][best_idx].item()

            # The last label is "unknown" — if that wins, return None
            if best_idx >= len(VENDOR_LOGOS):
                logger.debug(
                    f"CLIP: best match is 'unknown' with prob {best_prob:.3f}"
                )
                return None

            vendor = VENDOR_LOGOS[best_idx]

            # Require reasonable confidence (> 0.15 for zero-shot is decent)
            if best_prob < 0.15:
                logger.debug(
                    f"CLIP: best match '{vendor}' but low confidence {best_prob:.3f}"
                )
                return None

            logger.debug(f"CLIP: '{vendor}' with confidence {best_prob:.3f}")
            return vendor

        except Exception as e:
            logger.warning(f"CLIP classify error: {e}")
            return None

    # ── Receipt parsing ────────────────────────────────────────────────────────

    def parse_receipt(
        self,
        ocr_text: str,
        model: Optional[str] = None,
        vendor_hints: Optional[list] = None,
        logo_hint: Optional[str] = None,
    ) -> dict:
        """
        Parse OCR text into structured receipt data.

        Pipeline:
        1. Deterministic pre-scan (regex-based, high confidence)
        2. If deterministic finds all key fields → skip LLM entirely
        3. Otherwise → Ollama LLM parse with deterministic anchors
        4. Merge deterministic wins over LLM
        5. Post-process (math validation, tax sanity, date normalization)
        6. Second pass if confidence is still low

        Returns a dict with: is_receipt, date, vendor, total, gst, qst, pst, hst,
        pre_tax, currency, confidence, _warnings, _math_valid
        """
        if len(ocr_text.strip()) < MIN_OCR_LENGTH:
            return {"is_receipt": False, "confidence": 0.0, "error": "text_too_short"}

        # ── Step 1: Deterministic pre-scan ─────────────────────────────────
        det = self._det.parse(ocr_text)
        det_context = self._det.format_as_prompt_context(det)

        # Apply logo hint to deterministic results
        if logo_hint and logo_hint not in (
            "unknown",
            "not applicable",
            "not identified",
        ):
            if not det.get("vendor"):
                det["vendor"] = logo_hint
                logger.info(f"Deterministic vendor from logo: '{logo_hint}'")

        # ── Step 2: Check if deterministic is sufficient ───────────────────
        has_vendor = bool(det.get("vendor"))
        has_date = bool(det.get("date"))
        has_total = bool(det.get("total"))
        has_tax = any(
            det.get(t) for t in ["gst", "qst", "pst", "hst"]
        )

        if has_vendor and has_date and has_total:
            # Deterministic found all key fields — build result without LLM
            logger.info(
                "Deterministic parser found all key fields — skipping LLM"
            )
            base = {
                "is_receipt": True,
                "date": det.get("date"),
                "vendor": det.get("vendor"),
                "total": det.get("total") or 0.0,
                "gst": det.get("gst") or 0.0,
                "qst": det.get("qst") or 0.0,
                "pst": det.get("pst") or 0.0,
                "hst": det.get("hst") or 0.0,
                "pre_tax": det.get("pre_tax") or 0.0,
                "currency": "CAD",
                "confidence": 0.85 if has_tax else 0.75,
            }
            return self._parser.process(base, ocr_text=ocr_text)

        # ── Step 3: LLM parse via Ollama ───────────────────────────────────
        try:
            ollama = self._get_ollama()
            parsed = ollama.parse_receipt(
                ocr_text,
                model=model,
                vendor_hints=vendor_hints,
                logo_hint=logo_hint,
            )
            return parsed
        except Exception as e:
            logger.error(f"Ollama parse failed: {e}")
            # Build best-effort result from deterministic only
            return self._build_from_deterministic(det, ocr_text)

    def _build_from_deterministic(self, det: dict, ocr_text: str) -> dict:
        """Build a result dict from deterministic findings only (no LLM)."""
        base = {
            "is_receipt": True,
            "date": det.get("date"),
            "vendor": det.get("vendor"),
            "total": det.get("total") or 0.0,
            "gst": det.get("gst") or 0.0,
            "qst": det.get("qst") or 0.0,
            "pst": det.get("pst") or 0.0,
            "hst": det.get("hst") or 0.0,
            "pre_tax": det.get("pre_tax") or 0.0,
            "currency": "CAD",
            "confidence": 0.4,
        }
        return self._parser.process(base, ocr_text=ocr_text)
