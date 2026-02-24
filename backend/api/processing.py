from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models.database import get_db
from workers.scheduler import trigger_batch, get_job_status
import logging

router = APIRouter(prefix="/api/processing", tags=["processing"])
logger = logging.getLogger(__name__)


class BatchIn(BaseModel):
    force_reocr: bool = False


class SingleIn(BaseModel):
    paperless_id: int
    force_reocr: bool = False


@router.get("/health")
def health_check():
    from services.paperless import PaperlessClient
    from services.paddle_ocr import PaddleOCRClient
    from services.ollama import OllamaClient
    
    # Check Paperless
    try:
        pl_ok = PaperlessClient().health_check()
    except Exception:
        pl_ok = False
    
    # Check PaddleOCR
    try:
        paddle_ok = PaddleOCRClient().health_check()
    except Exception as e:
        logger.warning(f"PaddleOCR health check failed: {e}")
        paddle_ok = False
    
    # Check Ollama (for LLM fallback)
    ol = OllamaClient()
    ol_ok = ol.health_check()
    models = ol.list_models() if ol_ok else []
    
    return {
        "paperless": pl_ok, 
        "paddleocr": paddle_ok,
        "ollama": ol_ok, 
        "ollama_models": models,
        "ocr_system": "paddleocr_with_clip_fallback_ollama"
    }


@router.post("/batch")
def start_batch(body: BatchIn):
    started = trigger_batch(force_reocr=body.force_reocr)
    if not started:
        raise HTTPException(409, "Batch already running")
    return {"status": "started"}


@router.get("/batch/status")
def batch_status():
    return get_job_status()


@router.post("/single")
def process_single(body: SingleIn, db: Session = Depends(get_db)):
    from services.paperless import PaperlessClient
    from workers.processor import DocumentProcessor
    try:
        doc = PaperlessClient().get_document(body.paperless_id)
    except Exception as e:
        raise HTTPException(404, f"Document not found in Paperless: {e}")
    processor = DocumentProcessor()
    result = processor.process_document(doc, force_reocr=body.force_reocr, db=db)
    if result.get("status") == "error":
        raise HTTPException(500, result.get("error", "Processing failed"))
    return result
