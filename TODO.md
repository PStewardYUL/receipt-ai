# Code Review Fix Plan

## File 1: `backend/services/paddle_ocr.py` — CRITICAL

- [x] Fix class name: `PaddleOCRCClient` → `PaddleOCRClient`
- [x] Implement `_init_paddle()` — lazy-load PaddleOCR engine with corrupt download cleanup
- [x] Implement `_cleanup_partial_downloads()` — detect and remove corrupt .tar model files
- [x] Implement `_init_clip()` — lazy-load CLIP model + processor
- [x] Implement `_get_ollama()` — lazy-load Ollama client for fallback
- [x] Implement `health_check()` — verify PaddleOCR can be initialized
- [x] Implement `ocr_document(raw_bytes, model, paperless_text)` — PDF handling → image preprocessing → PaddleOCR extraction → bottom-region retry → Ollama fallback
- [x] Implement `_run_paddle_ocr(img_bytes)` — PaddleOCR text extraction with line grouping
- [x] Implement `_ollama_vision_fallback()` — Ollama vision OCR fallback
- [x] Implement `identify_logo(img_bytes, model)` — CLIP zero-shot classification → Ollama fallback
- [x] Implement `_clip_classify(img_bytes)` — CLIP zero-shot against VENDOR_LOGOS
- [x] Implement `parse_receipt(ocr_text, model, vendor_hints, logo_hint)` — deterministic parser → LLM fallback → post-processing
- [x] Implement `_build_from_deterministic(det, ocr_text)` — deterministic-only result builder

## File 2: `backend/services/pdf_report.py` — Minor (QST support)

- [x] Add `total_qst` calculation to summary stats
- [x] Include QST in `total_tax` calculation
- [x] Add QST row to tax breakdown table
- [x] Include QST in category tax totals
- [x] Add QST column header to receipt ledger
- [x] Add QST values to each ledger row
- [x] Add QST total to ledger total row
- [x] Adjust column widths for 9-column ledger (6.9" fits 7.0" usable)

## File 3: `Dockerfile` — Model persistence fix

- [x] Add symlink: `/root/.paddleocr` → `/root/.cache/paddleocr` (persisted via volume mount)
- [x] Ensures PaddleOCR models survive container rebuilds

## File 4: `backend/services/paddle_ocr.py` — Thread safety + retry (post-build fix)

- [x] Add `import threading` 
- [x] Add class-level `_paddle_lock` and `_clip_lock` (threading.Lock)
- [x] `_init_paddle()`: double-check locking pattern + retry loop (up to 3 attempts)
- [x] `_init_clip()`: double-check locking pattern to prevent concurrent model downloads
- [x] Broader error keyword matching: "unexpected end of data", "not a gzip file", "no such file or directory", "truncated", ".tar"

## Verification

- [x] Class name `PaddleOCRClient` matches all import sites (processing.py, processor.py)
- [x] Method signatures match all call sites in processor.py
- [x] PDF ledger column widths fit within letter page (6.9" < 7.0" usable)
- [x] All changes are backward-compatible (no other files need modification)
- [x] Corrupt model download handling prevents "unexpected end of data" errors
- [x] Symlink ensures models persist across `docker compose build` cycles
- [x] Thread safety prevents concurrent model downloads from corrupting each other
- [x] Retry logic (3 attempts) handles transient download failures automatically
