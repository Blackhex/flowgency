from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from agency.configuration.paths import job_store_root

from .models import JobRecord
from .store import active_jobs, read_job, write_job


_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class JobAuthorityError(ValueError):
    pass


@dataclass(frozen=True)
class JobAuthorityRef:
    store_root: Path
    team_id: str
    job_id: str
    immutable_digest: str

    @property
    def path(self) -> Path:
        return (self.store_root / self.team_id / f"{self.job_id}.yaml").resolve(strict=False)

    def worker_args(self) -> list[str]:
        return [
            "--store-root",
            str(self.store_root),
            "--team-id",
            self.team_id,
            "--job-id",
            self.job_id,
            "--immutable-digest",
            self.immutable_digest,
        ]


class JobStore:
    def __init__(self, memory_store: Path):
        self.memory_store = Path(memory_store).resolve()
        self.root = job_store_root(self.memory_store)

    @classmethod
    def from_store_root(cls, store_root: Path) -> "JobStore":
        resolved = Path(store_root).resolve()
        if resolved.name != ".jobs":
            raise JobAuthorityError(
                "job authority store root must be the canonical .jobs directory"
            )
        store = cls(resolved.parent)
        if store.root != resolved:
            raise JobAuthorityError(
                "job authority store root does not match the trusted canonical path"
            )
        return store

    @staticmethod
    def _team_id(team_id: str) -> str:
        if not isinstance(team_id, str) or not _IDENTIFIER.fullmatch(team_id):
            raise ValueError("team_id must be a canonical stable identifier")
        return team_id

    @staticmethod
    def _job_id(job_id: str) -> str:
        if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
            raise ValueError("job_id must be a canonical safe filename segment")
        return job_id

    def path(self, team_id: str, job_id: str) -> Path:
        team_id = self._team_id(team_id)
        job_id = self._job_id(job_id)
        path = (self.root / team_id / f"{job_id}.yaml").resolve()
        if path.parent != (self.root / team_id).resolve():
            raise ValueError("job path escaped the authoritative store")
        return path

    def team_root(self, team_id: str) -> Path:
        team_id = self._team_id(team_id)
        return (self.root / team_id).resolve(strict=False)

    def artifact_root(self, team_id: str, job_id: str) -> Path:
        root = self.team_root(team_id)
        job_id = self._job_id(job_id)
        target = (root / "artifacts" / job_id).resolve(strict=False)
        if target.parent != (root / "artifacts").resolve(strict=False):
            raise ValueError("artifact path escaped the authoritative store")
        return target

    def reference(
        self,
        team_id: str,
        job_id: str,
        immutable_digest: str,
    ) -> JobAuthorityRef:
        self.path(team_id, job_id)
        if not isinstance(immutable_digest, str) or not _DIGEST.fullmatch(immutable_digest):
            raise ValueError("immutable_digest must be a SHA-256 digest")
        return JobAuthorityRef(self.root, team_id, job_id, immutable_digest)

    def create(self, record: JobRecord) -> JobAuthorityRef:
        reference = self.reference(
            record.spec.team_key,
            record.spec.job_id,
            record.authority_digest,
        )
        if reference.path.exists():
            raise FileExistsError(reference.path)
        write_job(reference.path, record)
        return reference

    def read(self, reference: JobAuthorityRef) -> JobRecord:
        expected = self.reference(
            reference.team_id,
            reference.job_id,
            reference.immutable_digest,
        )
        if Path(reference.store_root).resolve() != self.root:
            raise JobAuthorityError("job authority store does not match the trusted root")
        try:
            return read_job(expected.path, expected_digest=expected.immutable_digest)
        except (TypeError, ValueError) as exc:
            raise JobAuthorityError("immutable job authority failed integrity validation") from exc

    def write(self, reference: JobAuthorityRef, record: JobRecord) -> None:
        if record.spec.team_key != reference.team_id or record.spec.job_id != reference.job_id:
            raise JobAuthorityError("job identity does not match its authority reference")
        if record.authority_digest != reference.immutable_digest:
            raise JobAuthorityError("immutable job authority digest changed")
        write_job(reference.path, record)

    def paths(self, team_id: str) -> tuple[Path, ...]:
        directory = self.team_root(team_id)
        return tuple(sorted(directory.glob("*.yaml"))) if directory.is_dir() else ()

    def active(self, team_id: str, agent_name: str | None = None) -> list[JobRecord]:
        return active_jobs(self.paths(team_id), agent_name)


__all__ = ["JobAuthorityError", "JobAuthorityRef", "JobStore"]