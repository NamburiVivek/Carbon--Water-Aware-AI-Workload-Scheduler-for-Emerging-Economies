"""
workloads/queue.py
In-memory job queue with thread-safe operations.
For production, replace the backing store with Redis or a database.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional

from workloads.job import JobStatus, ScheduledJob


class JobQueue:
    """Thread-safe in-memory store for ScheduledJob objects."""

    def __init__(self) -> None:
        self._jobs: Dict[str, ScheduledJob] = {}
        self._lock = threading.RLock()

    # ── Write operations ───────────────────────────────────────────────────

    def enqueue(self, job: ScheduledJob) -> None:
        with self._lock:
            self._jobs[job.request.job_id] = job

    def update(self, job: ScheduledJob) -> None:
        with self._lock:
            if job.request.job_id not in self._jobs:
                raise KeyError(f"Job {job.request.job_id} not found in queue")
            job.updated_at = datetime.now(timezone.utc)
            self._jobs[job.request.job_id] = job

    def remove(self, job_id: str) -> Optional[ScheduledJob]:
        with self._lock:
            return self._jobs.pop(job_id, None)

    # ── Read operations ────────────────────────────────────────────────────

    def get(self, job_id: str) -> Optional[ScheduledJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_all(self) -> List[ScheduledJob]:
        with self._lock:
            return list(self._jobs.values())

    def list_by_status(self, status: JobStatus) -> List[ScheduledJob]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == status]

    def pending_jobs(self) -> List[ScheduledJob]:
        return self.list_by_status(JobStatus.PENDING)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)


# Singleton instance shared across the application
job_queue = JobQueue()
