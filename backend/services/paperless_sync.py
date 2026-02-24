"""
Sync receipt edits back to Paperless-ngx.
Called after any manual edit or after processing. All operations non-fatal.
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def _safe_title(date: Optional[str], vendor: Optional[str]) -> Optional[str]:
    if not date and not vendor:
        return None
    parts = []
    if date:
        parts.append(date)
    if vendor:
        safe = re.sub(r'[<>:"/\\|?*\n\r\t]', "", vendor).strip()[:60]
        if safe:
            parts.append(safe)
    parts.append("Receipt")
    return "-".join(parts)


def sync_receipt_to_paperless(receipt, db) -> dict:
    """
    Push vendor, amount, category, currency, date and filename to Paperless.
    Safe to call after any edit — never raises.
    """
    from services.paperless import PaperlessClient

    if not receipt.document or not receipt.document.paperless_id:
        return {"skipped": "no paperless_id"}

    pid      = receipt.document.paperless_id
    currency = getattr(receipt, "currency", "CAD") or "CAD"
    total    = receipt.total or 0.0
    results  = {}

    try:
        pl = PaperlessClient()
    except Exception as e:
        logger.warning(f"Paperless client init failed: {e}")
        return {"error": str(e)}

    # ── 1. Rename document ────────────────────────────────────────────────
    title = _safe_title(receipt.date, receipt.vendor)
    if title:
        try:
            pl.rename_document(pid, title)
            results["title"] = title
        except Exception as e:
            results["title_error"] = str(e)
            logger.warning(f"Doc {pid}: rename failed: {e}")

    # ── 2. Set created date ───────────────────────────────────────────────
    # Send noon UTC so no timezone (e.g. EST = UTC-5) shifts the date back
    # to the previous day when Paperless renders it in local time.
    if receipt.date:
        try:
            pl.set_created_date(pid, receipt.date)
            results["created_date"] = receipt.date
        except Exception as e:
            results["created_date_error"] = str(e)
            logger.warning(f"Doc {pid}: set_created_date failed: {e}")

    # ── 3. Custom fields ──────────────────────────────────────────────────
    # Paperless Monetary field value format: "<amount> <CURRENCY>"
    # e.g. "20.00 CAD" — Paperless parses and stores both together.
    # A separate Currency (Text) field is kept for easy filtering/search.
    cat_name = receipt.category.name if receipt.category else ""
    fields = {
        "Amount":   f"{currency}{total:.2f}",   # Monetary format: "CAD20.00" (code before, no space)
        "Vendor":   receipt.vendor or "",
        "Category": cat_name,
        "Currency": currency,                    # Text: "CAD"
    }
    try:
        pl.set_custom_fields(pid, fields)
        results["custom_fields"] = fields
        logger.info(
            f"Doc {pid}: synced → title='{title}' "
            f"amount={total:.2f} {currency} "
            f"vendor='{receipt.vendor}' category='{cat_name}'"
        )
    except Exception as e:
        results["custom_fields_error"] = str(e)
        logger.warning(f"Doc {pid}: custom fields failed: {e}")

    return results
