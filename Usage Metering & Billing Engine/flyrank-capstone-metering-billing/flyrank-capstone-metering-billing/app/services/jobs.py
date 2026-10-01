"""Background jobs: DB-backed queue with exponential-backoff retries and a failure alert.

Job kind 'usage_alert': notify a tenant at 80% / 100% of a quota (enqueued by metering via outbox).
After max_attempts the job is marked 'failed' and a CRITICAL 'ALERT' log line is emitted.
"""
import json
import logging
import threading
import urllib.request
from datetime import timedelta

from sqlalchemy import select

from app import clock
from app.config import get_settings
from app.db import SessionLocal
from app.models import Job

log = logging.getLogger("metering.jobs")


def notify(payload: dict) -> None:
    """Delivery sink for usage alerts. Logs; also POSTs to ALERT_WEBHOOK_URL when configured.
    Raises on failure so the job runner retries."""
    log.info("USAGE ALERT tenant=%s metric=%s %s%% (%s/%s)", payload["tenant"], payload["metric"],
             payload["threshold_pct"], payload["used"], payload["limit"])
    url = get_settings().alert_webhook_url
    if url:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()


HANDLERS = {"usage_alert": lambda payload: notify(payload)}


def run_due_jobs(limit: int = 25) -> dict:
    """Run jobs whose run_at has passed. Returns counts (handy for tests and the worker loop)."""
    s_ = get_settings()
    stats = {"done": 0, "retried": 0, "failed": 0}
    now = clock.utcnow()
    with SessionLocal() as s:
        ids = s.scalars(select(Job.id).where(Job.status == "pending", Job.run_at <= now)
                        .order_by(Job.run_at).limit(limit)).all()
    for job_id in ids:
        with SessionLocal.begin() as s:
            job = s.scalar(select(Job).where(Job.id == job_id, Job.status == "pending")
                           .with_for_update(skip_locked=True))  # no-op on SQLite
            if job is None:
                continue
            job.attempts += 1
            try:
                HANDLERS[job.kind](json.loads(job.payload))
                job.status, job.last_error = "done", None
                stats["done"] += 1
            except Exception as exc:  # noqa: BLE001 - any handler failure is retried
                job.last_error = f"{type(exc).__name__}: {exc}"[:500]
                if job.attempts >= job.max_attempts:
                    job.status = "failed"
                    stats["failed"] += 1
                    log.critical("ALERT: job %s (%s) failed permanently after %s attempts: %s",
                                 job.id, job.kind, job.attempts, job.last_error)
                else:
                    job.run_at = clock.utcnow() + timedelta(seconds=s_.job_backoff_seconds * 2 ** (job.attempts - 1))
                    stats["retried"] += 1
    return stats


class Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="job-worker")
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                run_due_jobs()
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
            self._halt.wait(get_settings().job_poll_seconds)

    def stop(self):
        self._halt.set()
