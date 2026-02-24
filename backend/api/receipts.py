"""Receipt CRUD — every edit syncs back to Paperless immediately."""
import csv
import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models.database import get_db, Receipt, Category, Document

router = APIRouter(prefix="/api/receipts", tags=["receipts"])
logger = logging.getLogger(__name__)


class ReceiptOut(BaseModel):
    id: int
    document_id: int
    paperless_id: Optional[int] = None
    paperless_url: Optional[str] = None
    vendor: Optional[str] = None
    normalized_vendor: Optional[str] = None
    date: Optional[str] = None
    pre_tax: Optional[float] = None
    gst: Optional[float] = None
    qst: Optional[float] = None
    pst: Optional[float] = None
    hst: Optional[float] = None
    total: Optional[float] = None
    currency: Optional[str] = "CAD"
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    confidence: Optional[float] = None
    class Config:
        from_attributes = True


class ReceiptUpdate(BaseModel):
    category_id: Optional[int] = None
    vendor: Optional[str] = None
    date: Optional[str] = None
    total: Optional[float] = None
    pre_tax: Optional[float] = None
    gst: Optional[float] = None
    qst: Optional[float] = None
    pst: Optional[float] = None
    hst: Optional[float] = None
    currency: Optional[str] = None


def _paperless_url(paperless_id: Optional[int]) -> Optional[str]:
    import os
    base = os.getenv("PAPERLESS_URL", "").rstrip("/")
    if base and paperless_id:
        return f"{base}/documents/{paperless_id}"
    return None


def _out(r: Receipt) -> ReceiptOut:
    pid = r.document.paperless_id if r.document else None
    return ReceiptOut(
        id=r.id, document_id=r.document_id,
        paperless_id=pid,
        paperless_url=_paperless_url(pid),
        vendor=r.vendor, normalized_vendor=r.normalized_vendor,
        date=r.date, pre_tax=r.pre_tax,
        gst=r.gst, qst=getattr(r, "qst", 0.0),
        pst=r.pst, hst=r.hst, total=r.total,
        currency=getattr(r, "currency", "CAD"),
        category_id=r.category_id,
        category_name=r.category.name if r.category else None,
        confidence=r.confidence,
    )


@router.get("/vendors")
def list_vendors(db: Session = Depends(get_db)):
    rows = (db.query(Receipt.vendor)
            .filter(Receipt.vendor.isnot(None))
            .distinct().order_by(Receipt.vendor).all())
    return [r[0] for r in rows if r[0]]


@router.get("/", response_model=list[ReceiptOut])
def list_receipts(
    year: Optional[int] = None,
    category_id: Optional[int] = None,
    vendor: Optional[str] = None,
    limit: int = Query(500, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(Receipt)
    if year:
        q = q.filter(Receipt.date.like(f"{year}-%"))
    if category_id is not None:
        q = q.filter(Receipt.category_id == category_id)
    if vendor:
        q = q.filter(Receipt.vendor.ilike(f"%{vendor}%"))
    return [_out(r) for r in q.order_by(Receipt.date.desc()).offset(offset).limit(limit).all()]


@router.get("/summary")
def summary(year: Optional[int] = None, db: Session = Depends(get_db)):
    q = db.query(Receipt)
    if year:
        q = q.filter(Receipt.date.like(f"{year}-%"))
    rows = q.all()
    cat_totals: dict = {}
    monthly: dict = {}
    for r in rows:
        name = r.category.name if r.category else "Uncategorized"
        cat_totals[name] = round(cat_totals.get(name, 0) + (r.total or 0), 2)
        if r.date and len(r.date) >= 7:
            m = r.date[:7]
            monthly[m] = round(monthly.get(m, 0) + (r.total or 0), 2)
    return {
        "total_receipts": len(rows),
        "total_amount":   round(sum(r.total or 0 for r in rows), 2),
        "total_gst":      round(sum(r.gst   or 0 for r in rows), 2),
        "total_qst":      round(sum(getattr(r,"qst",0) or 0 for r in rows), 2),
        "total_pst":      round(sum(r.pst   or 0 for r in rows), 2),
        "total_hst":      round(sum(r.hst   or 0 for r in rows), 2),
        "vendor_count":   len({r.normalized_vendor for r in rows if r.normalized_vendor}),
        "by_category":    [{"name": k, "total": v} for k, v in sorted(cat_totals.items())],
        "by_month":       [{"month": k, "total": v} for k, v in sorted(monthly.items())],
    }


@router.get("/{receipt_id}", response_model=ReceiptOut)
def get_receipt(receipt_id: int, db: Session = Depends(get_db)):
    r = db.query(Receipt).filter_by(id=receipt_id).first()
    if not r:
        raise HTTPException(404, "Receipt not found")
    return _out(r)


@router.patch("/{receipt_id}", response_model=ReceiptOut)
def update_receipt(receipt_id: int, body: ReceiptUpdate, db: Session = Depends(get_db)):
    r = db.query(Receipt).filter_by(id=receipt_id).first()
    if not r:
        raise HTTPException(404, "Receipt not found")

    # ── Apply changes to DB ────────────────────────────────────────────────
    if body.category_id is not None:
        if body.category_id > 0:
            if not db.query(Category).filter_by(id=body.category_id).first():
                raise HTTPException(400, "Category not found")
            r.category_id = body.category_id
        else:
            r.category_id = None

    for field in ["vendor", "date", "total", "pre_tax", "gst", "qst", "pst", "hst", "currency"]:
        val = getattr(body, field)
        if val is not None:
            if field == "vendor":
                from services.vendor import normalize_vendor
                r.normalized_vendor = normalize_vendor(val)
            setattr(r, field, val)

    r.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(r)

    # ── Sync to Paperless (non-blocking, non-fatal) ────────────────────────
    try:
        from services.paperless_sync import sync_receipt_to_paperless
        sync_result = sync_receipt_to_paperless(r, db)
        logger.info(f"Receipt {receipt_id} synced to Paperless: {sync_result}")
    except Exception as e:
        logger.warning(f"Receipt {receipt_id}: Paperless sync failed (non-fatal): {e}")

    return _out(r)


@router.post("/rescan")
def rescan_receipts(receipt_ids: list[int] = Body(...), db: Session = Depends(get_db)):
    from workers.processor import DocumentProcessor
    processor = DocumentProcessor()
    results = []
    for rid in receipt_ids:
        r = db.query(Receipt).filter_by(id=rid).first()
        if not r or not r.document:
            results.append({"receipt_id": rid, "status": "not_found"})
            continue
        try:
            doc = processor.paperless.get_document(r.document.paperless_id)
            result = processor.process_document(doc, force_reocr=True, db=db)
            results.append({"receipt_id": rid, "status": result.get("status"),
                           "vendor": result.get("vendor")})
        except Exception as e:
            results.append({"receipt_id": rid, "status": "error", "error": str(e)})
    return results


@router.get("/export/csv")
def export_csv(year: int = Query(...), db: Session = Depends(get_db)):
    rows = db.query(Receipt).filter(Receipt.date.like(f"{year}-%")).order_by(Receipt.date).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date","Vendor","Category","Pre-Tax","GST","QST","PST","HST","Total","Currency"])
    for r in rows:
        w.writerow([
            r.date or "", r.vendor or "",
            r.category.name if r.category else "",
            f"{r.pre_tax or 0:.2f}", f"{r.gst or 0:.2f}",
            f"{getattr(r,'qst',0) or 0:.2f}",
            f"{r.pst or 0:.2f}", f"{r.hst or 0:.2f}",
            f"{r.total or 0:.2f}",
            getattr(r, "currency", "CAD") or "CAD",
        ])
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="receipts_{year}.csv"'})


@router.get("/export/pdf")
def export_pdf(year: int = Query(...), db: Session = Depends(get_db)):
    try:
        from services.pdf_report import generate_annual_report
        pdf_bytes = generate_annual_report(db, year)
    except ImportError as e:
        raise HTTPException(500, f"PDF unavailable: {e}")
    except Exception as e:
        logger.exception(f"PDF failed: {e}")
        raise HTTPException(500, str(e))
    return StreamingResponse(iter([pdf_bytes]), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="tax_report_{year}.pdf"'})
