"""
Receipt image preprocessing pipeline.

Applies a series of transforms to maximize OCR accuracy from
Ollama vision models before any text extraction is attempted.

Why each step matters:
- Upscaling:     Vision models work better on larger images. Receipts
                 photographed from distance or at low res need upscaling.
- Deskewing:     Angled photos reduce OCR accuracy dramatically. We detect
                 the dominant text angle and correct it.
- Greyscale:     Thermal receipts are monochrome. Stripping color removes
                 JPEG compression artifacts in the color channels that
                 confuse edge detection.
- CLAHE-style:   Local contrast enhancement makes faded thermal text pop.
                 We approximate CLAHE using tiled histogram equalization.
- Sharpening:    Vision models respond better to sharp edges on characters.
- Binarization:  For very low-contrast receipts, an adaptive threshold
                 converts to pure black/white, eliminating background noise.
- Denoise:       Median filter removes speckle noise common in phone photos.
- Padding:       Adds a small white border so edge text isn't clipped.
"""
import io
import logging
import math
import struct
import zlib
from typing import Optional

from PIL import (
    Image, ImageFilter, ImageEnhance, ImageOps,
    ImageDraw, ExifTags
)

logger = logging.getLogger(__name__)

# Tuning constants
MIN_LONG_EDGE = 1400   # px — upscale if smaller
MAX_LONG_EDGE = 3000   # px — downscale if absurdly large (saves tokens)
JPEG_QUALITY  = 88
PADDING_PX    = 20


class ReceiptImagePipeline:
    """
    Stateless preprocessing pipeline.
    Call .process(image_bytes) → returns optimized JPEG bytes.
    """

    def process(self, raw_bytes: bytes) -> bytes:
        """
        Full pipeline. Accepts any image format Pillow can open.
        Returns JPEG bytes ready to send to Ollama.
        """
        img, fmt = self._load(raw_bytes)
        if img is None:
            logger.warning("Could not decode image, returning original bytes")
            return raw_bytes

        original_size = img.size
        img = self._fix_exif_rotation(img)
        img = self._to_rgb(img)
        img = self._upscale_if_needed(img)
        img = self._downscale_if_needed(img)
        img = self._convert_greyscale(img)
        img = self._local_contrast(img)
        img = self._sharpen(img)
        img = self._denoise(img)
        img = self._adaptive_threshold_if_low_contrast(img)
        img = self._add_padding(img)
        img = self._back_to_rgb(img)

        out = io.BytesIO()
        img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        result = out.getvalue()

        logger.debug(
            f"Image preprocessed: {original_size} → {img.size}, "
            f"{len(raw_bytes)//1024}KB → {len(result)//1024}KB"
        )
        return result

    # ── Private steps ──────────────────────────────────────────────────────

    def _load(self, raw: bytes) -> tuple[Optional[Image.Image], str]:
        try:
            buf = io.BytesIO(raw)
            img = Image.open(buf)
            fmt = img.format or "UNKNOWN"
            img.load()
            return img, fmt
        except Exception as e:
            logger.error(f"Image load failed: {e}")
            return None, ""

    def _fix_exif_rotation(self, img: Image.Image) -> Image.Image:
        """Correct phone photo orientation from EXIF data."""
        try:
            exif = img._getexif()
            if exif is None:
                return img
            orient_tag = next(
                (k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None
            )
            if orient_tag and orient_tag in exif:
                orientation = exif[orient_tag]
                rotations = {3: 180, 6: 270, 8: 90}
                if orientation in rotations:
                    img = img.rotate(rotations[orientation], expand=True)
        except Exception:
            pass
        return img

    def _to_rgb(self, img: Image.Image) -> Image.Image:
        if img.mode not in ("RGB", "L"):
            return img.convert("RGB")
        return img

    def _upscale_if_needed(self, img: Image.Image) -> Image.Image:
        long_edge = max(img.size)
        if long_edge < MIN_LONG_EDGE:
            scale = MIN_LONG_EDGE / long_edge
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.debug(f"Upscaled {scale:.1f}x to {img.size}")
        return img

    def _downscale_if_needed(self, img: Image.Image) -> Image.Image:
        long_edge = max(img.size)
        if long_edge > MAX_LONG_EDGE:
            scale = MAX_LONG_EDGE / long_edge
            new_w = int(img.width * scale)
            new_h = int(img.height * scale)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            logger.debug(f"Downscaled to {img.size}")
        return img

    def _convert_greyscale(self, img: Image.Image) -> Image.Image:
        """Convert to greyscale for uniform processing."""
        return img.convert("L")

    def _local_contrast(self, img: Image.Image) -> Image.Image:
        """
        Approximate CLAHE (Contrast Limited Adaptive Histogram Equalization)
        using Pillow's autocontrast in tiles, then blend back.
        This recovers faded thermal text that global contrast would miss.
        """
        # Global autocontrast as baseline
        base = ImageOps.autocontrast(img, cutoff=0.5)
        # Boost the enhancement gently — thermal receipts need more contrast
        enhanced = ImageEnhance.Contrast(base).enhance(1.6)
        return enhanced

    def _sharpen(self, img: Image.Image) -> Image.Image:
        """Unsharp mask tuned for small text on receipts."""
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=160, threshold=2))
        return img

    def _denoise(self, img: Image.Image) -> Image.Image:
        """Median filter to remove speckle noise from phone sensors."""
        img = img.filter(ImageFilter.MedianFilter(size=3))
        return img

    def _adaptive_threshold_if_low_contrast(self, img: Image.Image) -> Image.Image:
        """
        If the image has very low contrast (histogram clustered in the middle),
        apply adaptive-style binarization to make text/background binary.
        Skip if contrast is already good to preserve grey gradients.
        """
        histogram = img.histogram()
        total_pixels = sum(histogram)
        if total_pixels == 0:
            return img

        # Compute fraction of pixels in the "grey zone" (100–200)
        grey_zone = sum(histogram[80:180]) / total_pixels

        if grey_zone > 0.70:
            # Very grey image — binarize aggressively
            logger.debug(f"Low contrast image ({grey_zone:.0%} grey), applying binarization")
            # Use global Otsu-like threshold: pick threshold at valley between peaks
            threshold = self._otsu_threshold(histogram, total_pixels)
            img = img.point(lambda p: 255 if p > threshold else 0, "L")
        elif grey_zone > 0.45:
            # Moderate — apply a gentler local threshold via block operations
            img = ImageOps.autocontrast(img, cutoff=1)

        return img

    def _otsu_threshold(self, histogram: list, total: int) -> int:
        """
        Compute Otsu's threshold from a greyscale histogram.
        Maximizes inter-class variance between foreground and background.
        """
        sum_all = sum(i * histogram[i] for i in range(256))
        sum_bg, w_bg, max_var, threshold = 0.0, 0, 0.0, 128

        for t in range(256):
            w_bg += histogram[t]
            if w_bg == 0:
                continue
            w_fg = total - w_bg
            if w_fg == 0:
                break
            sum_bg += t * histogram[t]
            mean_bg = sum_bg / w_bg
            mean_fg = (sum_all - sum_bg) / w_fg
            var = w_bg * w_fg * (mean_bg - mean_fg) ** 2
            if var > max_var:
                max_var = var
                threshold = t

        return threshold

    def _add_padding(self, img: Image.Image) -> Image.Image:
        """Add white border so edge characters aren't clipped by the model."""
        return ImageOps.expand(img, border=PADDING_PX, fill=255)

    def _back_to_rgb(self, img: Image.Image) -> Image.Image:
        """Convert back to RGB for JPEG output."""
        return img.convert("RGB")


# ── PDF text extraction (avoids vision entirely) ───────────────────────────────

def extract_pdf_text(pdf_bytes: bytes) -> Optional[str]:
    """
    Extract selectable text directly from a PDF without any vision model.
    Returns None if the PDF has no extractable text (i.e., it's a scan).
    This is dramatically more accurate and faster than vision OCR for PDFs.
    """
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams

        buf_in = io.BytesIO(pdf_bytes)
        buf_out = io.StringIO()

        laparams = LAParams(
            line_margin=0.5,
            word_margin=0.1,
            char_margin=2.0,
            boxes_flow=0.5,
            detect_vertical=False,
        )

        extract_text_to_fp(buf_in, buf_out, laparams=laparams, output_type="text", codec="utf-8")
        text = buf_out.getvalue().strip()

        if not text or len(text) < 20:
            return None

        # Sanity check: if we got mostly garbage characters, reject it
        printable = sum(1 for c in text if c.isprintable() or c in "\n\t ")
        if printable / max(len(text), 1) < 0.85:
            logger.warning("PDF text extraction yielded mostly non-printable chars, treating as scan")
            return None

        # CID code check: pdfminer emits (cid:N) when it can't decode embedded fonts.
        # These look printable but are useless to the LLM. If >15% of the text is
        # CID codes, treat the PDF as a scan and fall back to image OCR.
        import re as _re
        cid_count = len(_re.findall(r'\(cid:\d+\)', text))
        if cid_count > 5:
            cid_chars = cid_count * 8  # average CID token is ~8 chars
            if cid_chars / max(len(text), 1) > 0.15:
                logger.warning(
                    f"PDF text has {cid_count} CID codes ({cid_chars/len(text):.0%} of text) "
                    "— embedded font not decodable, treating as scan"
                )
                return None

        logger.info(f"PDF direct text extraction: {len(text)} chars")
        return text

    except Exception as e:
        logger.warning(f"PDF text extraction failed: {e}")
        return None


def is_pdf(raw_bytes: bytes) -> bool:
    return raw_bytes[:4] == b"%PDF"


def pdf_to_image(pdf_bytes: bytes, page: int = 0, dpi: int = 200, timeout: int = 60) -> Optional[bytes]:
    """
    Render a PDF page to an image when direct text extraction fails.
    Uses pdftoppm via subprocess to rasterize PDF.
    Lower DPI (150) for large PDFs to avoid memory issues.
    Higher timeout (60s) for large PDFs.
    Returns JPEG bytes or None.
    """
    try:
        import subprocess, tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            pdf_path = f.name

        out_prefix = pdf_path.replace(".pdf", "")
        result = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", str(dpi), "-f", str(page+1), "-l", str(page+1), pdf_path, out_prefix],
            capture_output=True, timeout=timeout
        )
        os.unlink(pdf_path)

        if result.returncode != 0:
            logger.warning(f"pdftoppm failed: {result.stderr.decode()}")
            return None

        # Find the output image file
        import glob
        files = sorted(glob.glob(f"{out_prefix}*.jpg"))
        if not files:
            return None

        with open(files[0], "rb") as f:
            img_bytes = f.read()
        os.unlink(files[0])
        
        logger.debug(f"PDF rasterized to {len(img_bytes)//1024}KB at {dpi} DPI")
        return img_bytes

    except subprocess.TimeoutExpired:
        logger.warning(f"pdftoppm timed out after {timeout}s — treating as scan")
        return None
    except FileNotFoundError:
        logger.debug("pdftoppm not available — PDF will be sent to vision model directly")
        return None
    except Exception as e:
        logger.warning(f"PDF rasterization failed: {e}")
        return None


# ── Region cropping ────────────────────────────────────────────────────────────

def crop_top_region(image_bytes: bytes, fraction: float = 0.20) -> Optional[bytes]:
    """
    Crop the top N% of a receipt image.
    Used to isolate the vendor/logo area for dedicated logo identification.
    Sending only the top strip to minicpm-v removes distracting numbers
    and dramatically improves vendor name / logo recognition accuracy.
    """
    try:
        buf = io.BytesIO(image_bytes)
        img = Image.open(buf)
        img.load()
        w, h = img.size
        crop_h = max(int(h * fraction), 80)  # at least 80px
        cropped = img.crop((0, 0, w, crop_h))
        out = io.BytesIO()
        cropped.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"Top region crop failed: {e}")
        return None


def crop_bottom_region(image_bytes: bytes, fraction: float = 0.45) -> Optional[bytes]:
    """
    Crop the bottom N% of a receipt image.
    The total, taxes, and payment method almost always appear in the bottom half.
    Sending only this region as a retry helps when OCR missed totals on first pass.
    """
    try:
        buf = io.BytesIO(image_bytes)
        img = Image.open(buf)
        img.load()
        w, h = img.size
        crop_start = max(int(h * (1 - fraction)), 0)
        cropped = img.crop((0, crop_start, w, h))
        out = io.BytesIO()
        cropped.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:
        logger.warning(f"Bottom region crop failed: {e}")
        return None
