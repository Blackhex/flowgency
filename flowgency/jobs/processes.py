from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessStopEvidence:
    job_id: str
    generation: str
    confirmed: bool
    reason: str


def may_clear_active_work(record, evidence: ProcessStopEvidence) -> bool:
    return (
        evidence.confirmed
        and record.active_run is not None
        and record.active_run.job_id == evidence.job_id
        and record.active_run.generation == evidence.generation
    )
