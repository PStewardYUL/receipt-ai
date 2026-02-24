"""Database models — all tables defined before init_db()."""
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float,
    DateTime, ForeignKey, Text, Index, Boolean
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

DATABASE_PATH = os.getenv("DATABASE_PATH", "/data/receipts.db")
engine = create_engine(f"sqlite:///{DATABASE_PATH}",
    connect_args={"check_same_thread": False}, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Category(Base):
    __tablename__ = "categories"
    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    receipts   = relationship("Receipt", back_populates="category")


class Document(Base):
    __tablename__ = "documents"
    id                    = Column(Integer, primary_key=True, index=True)
    paperless_id          = Column(Integer, unique=True, nullable=False, index=True)
    content_hash          = Column(String(64))
    processed_timestamp   = Column(DateTime)
    vision_model_used     = Column(String(255))
    text_model_used       = Column(String(255))
    ocr_text              = Column(Text)
    ocr_text_hash         = Column(String(64))
    structured_parse_hash = Column(String(64))
    last_status           = Column(String(64), default="pending")
    error_message         = Column(Text)
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow)
    receipt = relationship("Receipt", back_populates="document", uselist=False)
    __table_args__ = (Index("ix_documents_content_hash", "content_hash"),)


class Receipt(Base):
    __tablename__ = "receipts"
    id                = Column(Integer, primary_key=True, index=True)
    document_id       = Column(Integer, ForeignKey("documents.id"), nullable=False, unique=True)
    vendor            = Column(String(512))
    normalized_vendor = Column(String(512), index=True)
    date              = Column(String(20))
    pre_tax           = Column(Float, default=0.0)
    gst               = Column(Float, default=0.0)
    qst               = Column(Float, default=0.0)
    pst               = Column(Float, default=0.0)
    hst               = Column(Float, default=0.0)
    total             = Column(Float, default=0.0)
    currency          = Column(String(3), default="CAD")
    category_id       = Column(Integer, ForeignKey("categories.id"))
    confidence        = Column(Float)
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow)
    document  = relationship("Document", back_populates="receipt")
    category  = relationship("Category", back_populates="receipts")
    __table_args__ = (Index("ix_receipts_date", "date"),)


class VendorAlias(Base):
    __tablename__ = "vendor_aliases"
    id                   = Column(Integer, primary_key=True)
    raw_name             = Column(String(512), nullable=False)
    normalized_raw       = Column(String(512), nullable=False, index=True)
    canonical_name       = Column(String(512), nullable=False)
    normalized_canonical = Column(String(512), nullable=False, index=True)
    created_at           = Column(DateTime, default=datetime.utcnow)


class ReviewFlag(Base):
    __tablename__ = "review_flags"
    id          = Column(Integer, primary_key=True)
    receipt_id  = Column(Integer, ForeignKey("receipts.id"), nullable=False, unique=True)
    reason      = Column(String(255), nullable=False)
    status      = Column(String(32), default="pending")
    reviewed_at = Column(DateTime)
    created_at  = Column(DateTime, default=datetime.utcnow)
    receipt = relationship("Receipt", backref="review_flag", uselist=False)


class ProcessingConfig(Base):
    __tablename__ = "processing_config"
    id         = Column(Integer, primary_key=True)
    key        = Column(String(255), unique=True, nullable=False)
    value      = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)
    # SQLite: add qst/currency columns if upgrading from old schema
    from sqlalchemy import text
    with engine.connect() as conn:
        for col, definition in [("qst", "REAL DEFAULT 0"), ("currency", "TEXT DEFAULT 'CAD'")]:
            try:
                conn.execute(text(f"ALTER TABLE receipts ADD COLUMN {col} {definition}"))
                conn.commit()
            except Exception:
                pass  # column already exists
    db = SessionLocal()
    try:
        defaults = {
            "force_reocr": "false",
            "use_paperless_ocr_first": "true",
            "auto_skip_vision_if_text_exists": "true",
        }
        for key, value in defaults.items():
            if not db.query(ProcessingConfig).filter_by(key=key).first():
                db.add(ProcessingConfig(key=key, value=value))
        db.commit()
    finally:
        db.close()
