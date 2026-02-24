"""
ReceiptAI — Single-container FastAPI application.

Serves:
  /api/*   → REST API (all receipt, category, processing endpoints)
  /*       → React SPA static build (no nginx needed)

Static files are expected at /app/static/ inside the container,
built from the frontend/ directory during Docker image build.
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from models.database import init_db
from api.receipts    import router as receipts_router
from api.categories  import router as categories_router
from api.processing  import router as processing_router
from api.settings    import router as settings_router
from api.aliases     import router as aliases_router
from api.review      import router as review_router
from workers.scheduler import start_scheduler, stop_scheduler

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path("/app/static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database")
    init_db()
    logger.info("Starting background scheduler")
    start_scheduler()
    yield
    logger.info("Shutting down")
    stop_scheduler()


app = FastAPI(
    title="ReceiptAI",
    version="1.1.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes (all under /api/) ───────────────────────────────────────────────
app.include_router(receipts_router)
app.include_router(categories_router)
app.include_router(processing_router)
app.include_router(settings_router)
app.include_router(aliases_router)
app.include_router(review_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.1.0"}


# ── React SPA — serve static build, catch-all for client-side routing ─────────
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        """Catch-all: serve index.html so React Router handles the path."""
        index = STATIC_DIR / "index.html"
        return FileResponse(index)
else:
    logger.warning(
        f"Static dir {STATIC_DIR} not found — UI will not be served. "
        "This is expected in local development (run `npm run dev` separately)."
    )

    @app.get("/", include_in_schema=False)
    def root():
        return {"status": "ok", "note": "No static build found. API is available at /api/"}
