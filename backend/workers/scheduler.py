"""
Background scheduler — nightly batch + manual trigger.

Fixes:
- Lock released in finally block of worker thread (not caller)
- try/except around t.start() releases lock if thread fails to launch
- Nightly job uses trigger_batch() directly (not a raw lambda with lock logic)
"""
import logging
import os
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler = BackgroundScheduler(timezone="UTC")
_lock      = threading.Lock()
_job: dict = {"running": False, "started": None, "stats": None, "error": None}


def _run_batch_thread(force_reocr: bool = False):
    """Worker function — always releases lock in finally."""
    from workers.processor import run_batch  # late import avoids circular
    _job.update(running=True, started=datetime.utcnow().isoformat(), stats=None, error=None)
    try:
        stats = run_batch(force_reocr=force_reocr)
        _job["stats"] = stats
        logger.info(f"Batch complete: {stats}")
    except Exception as e:
        logger.exception(f"Batch failed: {e}")
        _job["error"] = str(e)
    finally:
        _job["running"] = False
        _lock.release()


def trigger_batch(force_reocr: bool = False) -> bool:
    """
    Trigger a batch run in a background thread.
    Returns False if a batch is already running.
    """
    if not _lock.acquire(blocking=False):
        return False
    try:
        t = threading.Thread(
            target=_run_batch_thread,
            args=(force_reocr,),
            daemon=True,
            name="receipt-batch",
        )
        t.start()
        return True
    except Exception as e:
        # Thread failed to launch — release lock so future calls can proceed
        logger.error(f"Failed to start batch thread: {e}")
        _lock.release()
        return False


def get_job_status() -> dict:
    return dict(_job)


def start_scheduler():
    hour = int(os.getenv("BATCH_HOUR", "2"))
    # Use trigger_batch directly — it handles the lock correctly
    _scheduler.add_job(trigger_batch, "cron", hour=hour, minute=0, id="nightly_batch")
    _scheduler.start()
    logger.info(f"Scheduler started — nightly batch at {hour:02d}:00 UTC")


def stop_scheduler():
    _scheduler.shutdown(wait=False)
