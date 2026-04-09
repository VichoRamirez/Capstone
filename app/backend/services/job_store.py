"""
Almacenamiento en memoria para trabajos asíncronos de optimización.
"""

from typing import Dict, Any, Optional

class JobEntry:
    def __init__(self):
        self.status: str = "pending"  # "pending", "running", "done", "error"
        self.stage: str = "Iniciando..."
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None

# In-memory store: job_id -> JobEntry
_jobs: Dict[str, JobEntry] = {}

def create_job(job_id: str) -> JobEntry:
    job = JobEntry()
    _jobs[job_id] = job
    return job

def get_job(job_id: str) -> Optional[JobEntry]:
    return _jobs.get(job_id)

def update_job_stage(job_id: str, stage: str):
    job = get_job(job_id)
    if job:
        job.stage = stage

def mark_job_running(job_id: str):
    job = get_job(job_id)
    if job:
        job.status = "running"
        
def mark_job_done(job_id: str, result: Dict[str, Any]):
    job = get_job(job_id)
    if job:
        job.status = "done"
        job.result = result
        job.stage = "Completado"

def mark_job_error(job_id: str, error_msg: str):
    job = get_job(job_id)
    if job:
        job.status = "error"
        job.error = error_msg
        job.stage = "Error"
