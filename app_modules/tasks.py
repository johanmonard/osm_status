from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, Optional


@dataclass
class BackgroundJob:
    job_id: str
    status: str = "pending"
    message: str = ""
    progress: float = 0.0
    result: Optional[dict] = None
    error: Optional[str] = None


class BackgroundJobManager:
    """Lightweight registry tracking long-running operations for Dash callbacks."""

    def __init__(self):
        self._jobs: Dict[str, BackgroundJob] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        target: Callable[..., dict],
        *args,
        **kwargs,
    ) -> BackgroundJob:
        job_id = str(uuid.uuid4())
        job = BackgroundJob(job_id=job_id, status="running")
        print(f"[BackgroundJobManager] Creating job {job_id} for {target.__name__}", flush=True)

        def _progress_callback(progress: float, message: str = ""):
            with self._lock:
                tracked = self._jobs.get(job_id)
                if not tracked:
                    return
                tracked.progress = min(max(progress, 0.0), 1.0)
                tracked.message = message

        def _runner():
            try:
                print(f"[BackgroundJobManager] Job {job_id} started", flush=True)
                result = target(*args, progress_callback=_progress_callback, **kwargs)
                with self._lock:
                    job.status = "completed"
                    job.progress = 1.0
                    job.message = "Done"
                    job.result = result
                print(f"[BackgroundJobManager] Job {job_id} completed", flush=True)
            except Exception as exc:  # pragma: no cover - surfaces error in UI
                with self._lock:
                    job.status = "failed"
                    job.error = str(exc)
                    job.message = "Failed"
                print(f"[BackgroundJobManager] Job {job_id} failed: {exc}", flush=True)

        thread = threading.Thread(target=_runner, daemon=True)
        with self._lock:
            self._jobs[job_id] = job
        thread.start()
        return job

    def get_job(self, job_id: str) -> Optional[BackgroundJob]:
        with self._lock:
            return self._jobs.get(job_id)
