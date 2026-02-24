"""
Vendor normalisation and category auto-assignment.
No AI — pure deterministic lookup.
"""
import re
import unicodedata
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from models.database import Receipt, Category


def normalize_vendor(name: str) -> str:
    if not name:
        return ""
    # Strip unicode combining chars
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower()
    # Remove common legal suffixes
    for suffix in [r"\binc\.?\b", r"\bltd\.?\b", r"\bcorp\.?\b", r"\bco\.?\b", r"\bllc\.?\b"]:
        name = re.sub(suffix, "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^\w\s]", "", name)   # remove punctuation
    name = re.sub(r"\s+", " ", name).strip()
    return name


def lookup_category_for_vendor(db: Session, normalized_vendor: str) -> Optional[int]:
    """
    Find the most-recently-used category for a given normalized vendor.
    Returns category_id or None. Never calls AI.
    """
    if not normalized_vendor:
        return None
    receipt = (
        db.query(Receipt)
        .filter(
            Receipt.normalized_vendor == normalized_vendor,
            Receipt.category_id.isnot(None),
        )
        .order_by(Receipt.updated_at.desc())
        .first()
    )
    return receipt.category_id if receipt else None


def assign_category(db: Session, receipt: Receipt) -> bool:
    """
    Auto-assign category based on vendor history.
    Returns True if a category was assigned.
    """
    if receipt.category_id is not None:
        return False
    norm = normalize_vendor(receipt.vendor or "")
    if not norm:
        return False
    receipt.normalized_vendor = norm
    cat_id = lookup_category_for_vendor(db, norm)
    if cat_id:
        receipt.category_id = cat_id
        receipt.updated_at = datetime.utcnow()
        db.commit()
        return True
    return False
