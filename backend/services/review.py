"""
Confidence review queue service.
Auto-flags receipts that need human verification.
"""
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models.database import Receipt, ReviewFlag

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.65  # Below this → flagged


def auto_flag_receipt(db: Session, receipt: Receipt) -> ReviewFlag | None:
    """
    Evaluate a receipt and create a ReviewFlag if it needs attention.
    Called automatically during processing pipeline.
    """
    reasons = []

    if (receipt.confidence or 0) < CONFIDENCE_THRESHOLD:
        reasons.append("low_confidence")
    if not receipt.total or receipt.total <= 0:
        reasons.append("missing_total")
    if not receipt.date:
        reasons.append("missing_date")
    if not receipt.vendor:
        reasons.append("missing_vendor")

    if not reasons:
        return None

    reason_str = "|".join(reasons)
    existing = db.query(ReviewFlag).filter_by(receipt_id=receipt.id).first()
    if existing:
        if existing.status == "pending":
            existing.reason = reason_str
            db.commit()
        return existing

    flag = ReviewFlag(receipt_id=receipt.id, reason=reason_str, status="pending")
    db.add(flag)
    db.commit()
    logger.info(f"Receipt {receipt.id} flagged for review: {reason_str}")
    return flag


def get_review_queue(db: Session, status: str = "pending") -> list[dict]:
    """Return receipts pending review with full details."""
    flags = (
        db.query(ReviewFlag)
        .filter_by(status=status)
        .order_by(ReviewFlag.created_at.desc())
        .all()
    )
    result = []
    for f in flags:
        r = f.receipt
        if not r:
            continue
        result.append({
            "flag_id": f.id,
            "reason": f.reason,
            "status": f.status,
            "receipt_id": r.id,
            "paperless_id": r.document.paperless_id if r.document else None,
            "vendor": r.vendor,
            "date": r.date,
            "total": r.total,
            "confidence": r.confidence,
            "category_id": r.category_id,
            "category_name": r.category.name if r.category else None,
        })
    return result


def resolve_flag(db: Session, flag_id: int, action: str) -> bool:
    """action: 'approved' | 'rejected'"""
    flag = db.query(ReviewFlag).filter_by(id=flag_id).first()
    if not flag:
        return False
    flag.status = action
    flag.reviewed_at = datetime.utcnow()
    db.commit()
    return True
