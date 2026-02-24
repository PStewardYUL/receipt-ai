from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from models.database import get_db, ProcessingConfig
from datetime import datetime

router = APIRouter(prefix="/api/settings", tags=["settings"])

SETTING_KEYS = [
    "vision_model", "text_model",
    "force_reocr", "use_paperless_ocr_first", "auto_skip_vision_if_text_exists",
]


class Settings(BaseModel):
    vision_model: Optional[str] = None
    text_model: Optional[str] = None
    force_reocr: bool = False
    use_paperless_ocr_first: bool = True
    auto_skip_vision_if_text_exists: bool = True


def _load(db: Session) -> Settings:
    row = {c.key: c.value for c in db.query(ProcessingConfig).all()}
    return Settings(
        vision_model=row.get("vision_model"),
        text_model=row.get("text_model"),
        force_reocr=row.get("force_reocr", "false") == "true",
        use_paperless_ocr_first=row.get("use_paperless_ocr_first", "true") == "true",
        auto_skip_vision_if_text_exists=row.get("auto_skip_vision_if_text_exists", "true") == "true",
    )


def _save(db: Session, s: Settings):
    updates = {
        "vision_model": s.vision_model or "",
        "text_model": s.text_model or "",
        "force_reocr": "true" if s.force_reocr else "false",
        "use_paperless_ocr_first": "true" if s.use_paperless_ocr_first else "false",
        "auto_skip_vision_if_text_exists": "true" if s.auto_skip_vision_if_text_exists else "false",
    }
    for key, value in updates.items():
        row = db.query(ProcessingConfig).filter_by(key=key).first()
        if row:
            row.value = value
            row.updated_at = datetime.utcnow()
        else:
            db.add(ProcessingConfig(key=key, value=value))
    db.commit()


@router.get("/", response_model=Settings)
def get_settings(db: Session = Depends(get_db)):
    return _load(db)


@router.put("/", response_model=Settings)
def save_settings(body: Settings, db: Session = Depends(get_db)):
    _save(db, body)
    return _load(db)
