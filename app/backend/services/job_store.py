"""
Almacenamiento en memoria para trabajos asíncronos de optimización.

Diseño deliberadamente simple: los jobs viven en un dict Python en RAM
porque la optimización es una operación de vida corta (minutos) y no
necesita persistencia entre reinicios del servidor. No se usa Redis ni BD
para no agregar dependencias de infraestructura.

Ciclo de vida de un job:
  pending → running → done
                    ↘ error
"""

from typing import Dict, Any, Optional


class JobEntry:
    """Entrada de un job de optimización con su estado y resultado."""

    def __init__(self):
        # Estado del job: "pending" | "running" | "done" | "error"
        self.status: str = "pending"
        # Mensaje de etapa actual (mostrado en la UI mientras el job corre)
        self.stage: str = "Iniciando..."
        # Resultado final de la optimización (solo cuando status == "done")
        self.result: Optional[Dict[str, Any]] = None
        # Mensaje de error (solo cuando status == "error")
        self.error: Optional[str] = None


# Almacén global: job_id (UUID) → JobEntry
# Se limpia al reiniciar el servidor. Los resultados se devuelven al
# frontend por polling, así que no se necesita persistencia larga.
_jobs: Dict[str, JobEntry] = {}


def create_job(job_id: str) -> JobEntry:
    """Registra un nuevo job en estado 'pending' y lo retorna."""
    job = JobEntry()
    _jobs[job_id] = job
    return job


def get_job(job_id: str) -> Optional[JobEntry]:
    """Retorna el JobEntry asociado al job_id, o None si no existe."""
    return _jobs.get(job_id)


def update_job_stage(job_id: str, stage: str):
    """Actualiza el mensaje de etapa visible para el frontend durante la ejecución."""
    job = get_job(job_id)
    if job:
        job.stage = stage


def mark_job_running(job_id: str):
    """Marca el job como en ejecución activa."""
    job = get_job(job_id)
    if job:
        job.status = "running"


def mark_job_done(job_id: str, result: Dict[str, Any]):
    """Marca el job como completado y almacena el resultado completo."""
    job = get_job(job_id)
    if job:
        job.status = "done"
        job.result = result
        job.stage = "Completado"


def mark_job_error(job_id: str, error_msg: str):
    """Marca el job como fallido con un mensaje de error descriptivo."""
    job = get_job(job_id)
    if job:
        job.status = "error"
        job.error = error_msg
        job.stage = "Error"
