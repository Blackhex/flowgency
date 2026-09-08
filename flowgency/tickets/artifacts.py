from __future__ import annotations

import base64
import hashlib
import json
import re

from pydantic import BaseModel, ConfigDict, StrictStr, computed_field, model_validator

from flowgency.workflows.models import ArtifactRef, FieldValue


MAX_RETAINED_ARTIFACT_BYTES = 1 * 1024 * 1024
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_artifact_filename(filename: str) -> str:
    collapsed = filename.replace("\\", "/").split("/")[-1].strip()
    cleaned = _CONTROL_RE.sub("", collapsed)
    if cleaned in {"", ".", ".."}:
        raise ValueError("Artifact filename must have a visible basename")
    return cleaned[:255]


def _canonical_artifact_payload(filename: str, media_type: str, content: bytes) -> bytes:
    envelope = {
        "content_b64": base64.b64encode(content).decode("ascii"),
        "filename": filename,
        "media_type": media_type,
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


class RetainedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    filename: StrictStr
    media_type: StrictStr
    content: bytes
    digest: StrictStr

    @model_validator(mode="after")
    def _validate_artifact(self) -> "RetainedArtifact":
        if self.filename != sanitize_artifact_filename(self.filename):
            raise ValueError("Artifact filename must be sanitized")
        if not _MEDIA_TYPE_RE.match(self.media_type):
            raise ValueError("Artifact media type is invalid")
        if len(self.content) > MAX_RETAINED_ARTIFACT_BYTES:
            raise ValueError("Artifact content exceeds the maximum size")
        expected = hashlib.sha256(
            _canonical_artifact_payload(self.filename, self.media_type, self.content)
        ).hexdigest()
        if self.digest != expected:
            raise ValueError("Artifact digest does not match canonical content")
        return self

    @classmethod
    def create(
        cls,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> "RetainedArtifact":
        safe_name = sanitize_artifact_filename(filename)
        digest = hashlib.sha256(
            _canonical_artifact_payload(safe_name, media_type, content)
        ).hexdigest()
        return cls(
            filename=safe_name,
            media_type=media_type,
            content=content,
            digest=digest,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size(self) -> int:
        return len(self.content)

    def ref(self) -> ArtifactRef:
        return ArtifactRef(kind="id", value=self.digest)

    def download_metadata(self) -> dict[str, str | int]:
        return {
            "content_disposition": "attachment",
            "content_length": self.size,
            "filename": self.filename,
            "media_type": self.media_type,
            "x_content_type_options": "nosniff",
        }

    def to_storage_bytes(self) -> bytes:
        return _canonical_artifact_payload(self.filename, self.media_type, self.content)

    @classmethod
    def from_storage_bytes(cls, payload: bytes) -> "RetainedArtifact":
        data = json.loads(payload.decode("utf-8"))
        content = base64.b64decode(data["content_b64"], validate=True)
        return cls.create(data["filename"], data["media_type"], content)


def iter_internal_artifact_refs(*value_maps: dict[str, FieldValue]) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []
    for values in value_maps:
        for value in values.values():
            if isinstance(value, ArtifactRef) and value.kind == "id":
                refs.append(value)
    return tuple(refs)