"""Scan jobs: stored in SQLite, run one at a time by a background worker.

One worker on purpose: all scans share one Instagram session / API token, and
parallel scans on one session are what gets accounts rate-limited or banned.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

from .. import auth
from ..config import Settings
from ..pipeline import ScanError, ScanOptions, make_client, run_scan

log = logging.getLogger("brandscan.jobs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    target TEXT NOT NULL,
    options TEXT NOT NULL,
    status TEXT NOT NULL,          -- queued | running | done | partial | failed | cancelled
    progress TEXT NOT NULL DEFAULT '{}',
    message TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, created_at);
"""
ACTIVE = ("queued", "running")


class JobStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self.lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            # Scans interrupted by a restart go back in the queue (they resume from their checkpoint).
            c.execute("UPDATE jobs SET status='queued' WHERE status='running'")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        job = dict(row)
        for key in ("options", "progress", "files", "summary"):
            job[key] = json.loads(job[key])
        return job

    def create(self, owner: str, target: str, options: dict) -> dict:
        now = time.time()
        job_id = uuid.uuid4().hex[:12]
        with self.lock, self._conn() as c:
            c.execute("INSERT INTO jobs (id, owner, target, options, status, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, 'queued', ?, ?)", (job_id, owner, target, json.dumps(options), now, now))
        return self.get(job_id)

    def get(self, job_id: str, owner: str | None = None) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        job = self._to_dict(row)
        if job and owner is not None and job["owner"] != owner:
            return None
        return job

    def list(self, owner: str, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE owner=? ORDER BY created_at DESC LIMIT ?",
                             (owner, limit)).fetchall()
        return [self._to_dict(r) for r in rows]

    def update(self, job_id: str, **fields) -> None:
        for key in ("options", "progress", "files", "summary"):
            if key in fields:
                fields[key] = json.dumps(fields[key])
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock, self._conn() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def claim_next(self) -> dict | None:
        with self.lock, self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            c.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (time.time(), row["id"]))
        return self.get(row["id"])

    def queue_position(self, job: dict) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running') AND created_at < ?",
                             (job["created_at"],)).fetchone()[0]


class Worker(threading.Thread):
    def __init__(self, store: JobStore, settings: Settings):
        super().__init__(name="scan-worker", daemon=True)
        self.store = store
        self.settings = settings
        self.wake = threading.Event()
        self.cancelled: set[str] = set()
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()
        self.wake.set()

    def cancel(self, job_id: str) -> None:
        self.cancelled.add(job_id)

    def run(self) -> None:
        while not self._halt.is_set():
            job = self.store.claim_next()
            if job is None:
                self.wake.wait(5)
                self.wake.clear()
                continue
            self._run_job(job)

    def _run_job(self, job: dict) -> None:
        job_id = job["id"]
        if job_id in self.cancelled:
            self.store.update(job_id, status="cancelled", message="Cancelled.")
            return
        log.info("Job %s: scanning @%s", job_id, job["target"])
        settings = replace(self.settings)
        opts = ScanOptions(target=job["target"], **job["options"])

        def progress(event: dict) -> None:
            self.store.update(job_id, progress=event)

        try:
            cookies = None
            if settings.source == "web":
                cookies = auth.session_from_settings(settings, interactive=False)
            result = run_scan(settings, opts, make_client(settings, cookies), progress,
                              should_stop=lambda: job_id in self.cancelled)
        except (ScanError, SystemExit) as exc:
            self.store.update(job_id, status="failed", message=str(exc))
            return
        except Exception:
            log.exception("Job %s crashed", job_id)
            self.store.update(job_id, status="failed", message="Internal error; see the server log.")
            return
        finally:
            self.cancelled.discard(job_id)

        status = "done" if result.completed else "partial"
        self.store.update(job_id, status=status, message=result.message, files=result.files, summary={
            "username": result.target["username"], "full_name": result.target["full_name"],
            "followers": result.target["followers"], "posts_scanned": result.posts_scanned,
            "brands": len(result.brands), "entities": len(result.entities),
        })
        log.info("Job %s: %s (%d brands)", job_id, status, len(result.brands))
