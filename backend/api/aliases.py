from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.database import get_db
from services.aliases import create_alias, delete_alias, list_aliases, suggest_merge_candidates

router = APIRouter(prefix="/api/aliases", tags=["aliases"])


class AliasIn(BaseModel):
    raw_name: str
    canonical_name: str


class AliasOut(BaseModel):
    id: int
    raw_name: str
    normalized_raw: str
    canonical_name: str
    normalized_canonical: str
    class Config:
        from_attributes = True


@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)):
    return suggest_merge_candidates(db)


@router.get("/", response_model=list[AliasOut])
def get_aliases(db: Session = Depends(get_db)):
    return list_aliases(db)


@router.post("/", response_model=AliasOut, status_code=201)
def add_alias(body: AliasIn, db: Session = Depends(get_db)):
    if not body.raw_name.strip() or not body.canonical_name.strip():
        raise HTTPException(400, "Both fields required")
    return create_alias(db, body.raw_name.strip(), body.canonical_name.strip())


@router.delete("/{alias_id}", status_code=204)
def remove_alias(alias_id: int, db: Session = Depends(get_db)):
    if not delete_alias(db, alias_id):
        raise HTTPException(404, "Alias not found")
