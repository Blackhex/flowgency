from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from json import JSONDecodeError
from math import ceil
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictStr, computed_field, model_validator

from flowgency.workflows.models import ArtifactRef, FieldValue


MAX_RETAINED_ARTIFACT_BYTES = 1 * 1024 * 1024
MAX_RETAINED_ARTIFACT_ENVELOPE_BYTES = ceil(MAX_RETAINED_ARTIFACT_BYTES / 3) * 4 + 1024
_MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ALLOWED_STORAGE_KEYS = frozenset({"content_b64", "filename", "media_type"})


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


def _canonical_artifact_payload_from_b64(
    filename: str,
    media_type: str,
    content_b64: str,
) -> bytes:
    envelope = {
        "content_b64": content_b64,
        "filename": filename,
        "media_type": media_type,
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_storage_mapping(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError("Artifact envelope must be a JSON object")
    keys = set(data)
    if keys != _ALLOWED_STORAGE_KEYS:
        raise ValueError("Artifact envelope keys are invalid")
    for key in _ALLOWED_STORAGE_KEYS:
        if not isinstance(data[key], str):
            raise ValueError("Artifact envelope values must be strings")
    return {
        "filename": data["filename"],
        "media_type": data["media_type"],
        "content_b64": data["content_b64"],
    }


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
        if len(payload) > MAX_RETAINED_ARTIFACT_ENVELOPE_BYTES:
            raise ValueError("Artifact envelope exceeds the maximum size")
        try:
            decoded = payload.decode("utf-8")
            data = json.loads(decoded)
        except (UnicodeDecodeError, JSONDecodeError) as error:
            raise ValueError("Artifact envelope is not valid JSON") from error
        validated = _validate_storage_mapping(data)
        filename = validated["filename"]
        media_type = validated["media_type"]
        content_b64 = validated["content_b64"]
        if filename != sanitize_artifact_filename(filename):
            raise ValueError("Artifact filename must be sanitized")
        if not _MEDIA_TYPE_RE.match(media_type):
            raise ValueError("Artifact media type is invalid")
        max_encoded = ceil(MAX_RETAINED_ARTIFACT_BYTES / 3) * 4
        if len(content_b64) > max_encoded:
            raise ValueError("Artifact content exceeds the maximum size")
        try:
            content = base64.b64decode(content_b64, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("Artifact content is not valid base64") from error
        digest = hashlib.sha256(
            _canonical_artifact_payload_from_b64(filename, media_type, content_b64)
        ).hexdigest()
        return cls(
            filename=filename,
            media_type=media_type,
            content=content,
            digest=digest,
        )


def iter_internal_artifact_refs(*value_maps: dict[str, FieldValue]) -> tuple[ArtifactRef, ...]:
    refs: list[ArtifactRef] = []
    for values in value_maps:
        for value in values.values():
            if isinstance(value, ArtifactRef) and value.kind == "id":
                refs.append(value)
    return tuple(refs)