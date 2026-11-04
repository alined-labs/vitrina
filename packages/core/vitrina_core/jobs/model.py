"""The Job resource as the API shows it (openapi.yaml ``Job``, ``JobError``; FR-083)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from pydantic import MISSING, BaseModel, Field, model_validator

JobKind = Literal[
    "voice_import",
    "product_import",
    "creative",
    "reel",
    "brief",
    "erasure",
    "eval_run",
    "replay",
    "campaign_send",
    "public_showcase",
]
# Queue-only kinds: they run in the worker but GET /jobs/{id} never shows them.
InternalKind = Literal["process_inbound", "maintenance"]
QueueKind = JobKind | InternalKind
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]

API_KINDS: frozenset[str] = frozenset(get_args(JobKind))


# What a client reads when a job fails in a way no task worded for it. The real error goes to
# the log; ``code`` stays what the dashboard and API clients branch on.
JOB_FAILURE_TEXT = {
    "INTERNAL_ERROR": "Algo falló de nuestro lado. Intenta de nuevo.",
    "PROVIDER_ERROR": "El servicio externo no pudo completar la solicitud. Intenta de nuevo.",
    "PROVIDER_UNAVAILABLE": "El servicio no está disponible en este momento. Intenta más tarde.",
    "TIMEOUT": "El trabajo tardó demasiado y se detuvo. Intenta de nuevo.",
    "UNKNOWN_TASK": "Este trabajo no se puede ejecutar en este momento.",
}


class JobErrorDetail(BaseModel):
    code: str | MISSING = MISSING
    message: str | MISSING = MISSING
    retryable: bool | MISSING = MISSING

    @model_validator(mode="after")
    def _hide_internal_text(self) -> JobErrorDetail:
        """Rows written before the worker stopped storing raw errors can hold SQL."""
        if self.code == "INTERNAL_ERROR":
            self.message = JOB_FAILURE_TEXT["INTERNAL_ERROR"]
        return self


class Job(BaseModel):
    id: UUID
    kind: JobKind
    status: JobStatus
    stage: str | MISSING = MISSING
    progress: Annotated[float, Field(ge=0, le=1)] | MISSING = MISSING
    result_ref: str | None = None
    error: JobErrorDetail | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[Any, Any]) -> Job:
        values: dict[str, Any] = {
            key: row[key]
            for key in (
                "id",
                "kind",
                "status",
                "result_ref",
                "created_at",
                "started_at",
                "finished_at",
            )
        }
        if row.get("stage"):
            values["stage"] = row["stage"]
        if row.get("progress") is not None:
            values["progress"] = float(row["progress"])
        if row.get("error"):
            values["error"] = JobErrorDetail.model_validate(row["error"])
        return cls.model_validate(values)
