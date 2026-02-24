"""
Vendor alias merging service.

Allows users to declare that multiple vendor name variants all resolve
to the same canonical vendor — enabling unified categorization and reporting.

Examples:
  "TIM HORTON'S #042" ─┐
  "Tim Hortons"        ─┤─→ canonical: "Tim Hortons"
  "Timmies"            ─┘

No AI. Pure deterministic lookup with normalization.
"""
from __future__ import annotations
import logging
from sqlalchemy.orm import Session
from models.database import VendorAlias, Receipt
from services.vendor import normalize_vendor

logger = logging.getLogger(__name__)


def resolve_vendor(db: Session, raw_name: str) -> str:
    """
    Given a raw vendor string, return the canonical name if an alias exists,
    otherwise return the original.
    """
    if not raw_name:
        return raw_name
    norm = normalize_vendor(raw_name)
    alias = db.query(VendorAlias).filter_by(normalized_raw=norm).first()
    return alias.canonical_name if alias else raw_name


def create_alias(db: Session, raw_name: str, canonical_name: str) -> VendorAlias:
    """
    Declare that raw_name is an alias for canonical_name.
    Updates existing alias if raw_name is already mapped.
    Also re-normalizes all receipts with the raw vendor to the canonical.
    """
    norm_raw = normalize_vendor(raw_name)
    norm_canonical = normalize_vendor(canonical_name)

    existing = db.query(VendorAlias).filter_by(normalized_raw=norm_raw).first()
    if existing:
        existing.canonical_name = canonical_name
        existing.normalized_canonical = norm_canonical
        alias = existing
    else:
        alias = VendorAlias(
            raw_name=raw_name,
            normalized_raw=norm_raw,
            canonical_name=canonical_name,
            normalized_canonical=norm_canonical,
        )
        db.add(alias)

    db.flush()

    # Backfill: update any receipts where normalized_vendor matches raw alias
    updated = (
        db.query(Receipt)
        .filter(Receipt.normalized_vendor == norm_raw)
        .all()
    )
    for r in updated:
        r.vendor = canonical_name
        r.normalized_vendor = norm_canonical

    db.commit()
    logger.info(f"Alias created: '{raw_name}' → '{canonical_name}' ({len(updated)} receipts updated)")
    return alias


def delete_alias(db: Session, alias_id: int) -> bool:
    alias = db.query(VendorAlias).filter_by(id=alias_id).first()
    if not alias:
        return False
    db.delete(alias)
    db.commit()
    return True


def list_aliases(db: Session) -> list[VendorAlias]:
    return db.query(VendorAlias).order_by(VendorAlias.canonical_name).all()


def suggest_merge_candidates(db: Session, threshold: int = 2) -> list[dict]:
    """
    Find vendor groups that likely refer to the same business.
    Groups normalized vendors sharing the first N words.
    Returns candidates for user review — never auto-merges.
    """
    from sqlalchemy import func
    rows = (
        db.query(Receipt.normalized_vendor, func.count(Receipt.id).label("cnt"))
        .filter(Receipt.normalized_vendor.isnot(None))
        .group_by(Receipt.normalized_vendor)
        .having(func.count(Receipt.id) >= 1)
        .all()
    )

    # Group by first 2 tokens of normalized vendor name
    groups: dict[str, list] = {}
    for row in rows:
        name = row.normalized_vendor or ""
        tokens = name.split()
        key = " ".join(tokens[:2]) if len(tokens) >= 2 else name
        if key not in groups:
            groups[key] = []
        groups[key].append({"vendor": row.normalized_vendor, "count": row.cnt})

    candidates = [
        {"key": k, "variants": v}
        for k, v in groups.items()
        if len(v) >= threshold
    ]
    return sorted(candidates, key=lambda x: -sum(i["count"] for i in x["variants"]))
