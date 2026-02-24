from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.database import get_db, Category, Receipt

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryIn(BaseModel):
    name: str

class CategoryOut(BaseModel):
    id: int
    name: str
    receipt_count: int = 0
    class Config:
        from_attributes = True


def _out(c: Category, db: Session) -> CategoryOut:
    count = db.query(Receipt).filter_by(category_id=c.id).count()
    return CategoryOut(id=c.id, name=c.name, receipt_count=count)


@router.get("/", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return [_out(c, db) for c in db.query(Category).order_by(Category.name).all()]


@router.post("/", response_model=CategoryOut, status_code=201)
def create_category(body: CategoryIn, db: Session = Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    if db.query(Category).filter_by(name=body.name.strip()).first():
        raise HTTPException(400, "Category already exists")
    c = Category(name=body.name.strip())
    db.add(c)
    db.commit()
    db.refresh(c)
    return _out(c, db)


@router.patch("/{cat_id}", response_model=CategoryOut)
def rename_category(cat_id: int, body: CategoryIn, db: Session = Depends(get_db)):
    c = db.query(Category).filter_by(id=cat_id).first()
    if not c:
        raise HTTPException(404, "Category not found")
    if not body.name.strip():
        raise HTTPException(400, "Name required")
    c.name = body.name.strip()
    db.commit()
    db.refresh(c)
    return _out(c, db)


@router.delete("/{cat_id}", status_code=204)
def delete_category(cat_id: int, db: Session = Depends(get_db)):
    c = db.query(Category).filter_by(id=cat_id).first()
    if not c:
        raise HTTPException(404, "Category not found")
    # Unlink receipts
    db.query(Receipt).filter_by(category_id=cat_id).update({"category_id": None})
    db.delete(c)
    db.commit()
