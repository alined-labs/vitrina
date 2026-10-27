"""Long-running work (T008, FR-083): the Job resource, the Postgres queue and the worker."""

from vitrina_core.jobs.model import (
    API_KINDS,
    InternalKind,
    Job,
    JobErrorDetail,
    JobKind,
    JobStatus,
    QueueKind,
)
from vitrina_core.jobs.queue import ClaimedJob, JobQueue, JobReader, PostgresJobQueue
from vitrina_core.jobs.worker import (
    TASKS_ENTRY_POINT,
    Handler,
    JobContext,
    JobFailedError,
    Task,
    TaskRegistry,
    Worker,
    backoff,
    load_tasks,
)

__all__ = [
    "API_KINDS",
    "TASKS_ENTRY_POINT",
    "ClaimedJob",
    "Handler",
    "InternalKind",
    "Job",
    "JobContext",
    "JobErrorDetail",
    "JobFailedError",
    "JobKind",
    "JobQueue",
    "JobReader",
    "JobStatus",
    "PostgresJobQueue",
    "QueueKind",
    "Task",
    "TaskRegistry",
    "Worker",
    "backoff",
    "load_tasks",
]
