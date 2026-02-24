from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.database import get_db, ReviewFlag, Receipt
from services.review import get_review_queue, resolve_flag
from datetime import datetime

router = APIRouter(prefix="/api/review", tags=["review"])


class ResolveIn(BaseModel):
    action: str  # "approved" | "rejected"


class FlagIn(BaseModel):
    receipt_id: int


@router.get("/")
def list_queue(status: str = "pending", db: Session = Depends(get_db)):
    return get_review_queue(db, status=status)


@router.get("/count")
def review_count(db: Session = Depends(get_db)):
    return {"pending": db.query(ReviewFlag).filter_by(status="pending").count()}


@router.post("/{flag_id}/resolve")
def resolve(flag_id: int, body: ResolveIn, db: Session = Depends(get_db)):
    if body.action not in ("approved", "rejected"):
        raise HTTPException(400, "action must be 'approved' or 'rejected'")
    if not resolve_flag(db, flag_id, body.action):
        raise HTTPException(404, "Flag not found")
    return {"status": body.action}


@router.post("/flag", status_code=201)
def manual_flag(body: FlagIn, db: Session = Depends(get_db)):
    r = db.query(Receipt).filter_by(id=body.receipt_id).first()
    if not r:
        raise HTTPException(404, "Receipt not found")
    existing = db.query(ReviewFlag).filter_by(receipt_id=r.id).first()
    if existing:
        return {"flag_id": existing.id}
    flag = ReviewFlag(receipt_id=r.id, reason="manual", status="pending")
    db.add(flag)
    db.commit()
    return {"flag_id": flag.id}
