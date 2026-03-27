"""Fluent builders for A2A Messages and Artifacts."""
import uuid
from typing import Any, Optional

from a2a_helpers.types import AP2_EXTENSION_URI


class MessageBuilder:
    """Build A2A Messages with AP2 DataParts."""

    def __init__(self):
        self._message_id = str(uuid.uuid4())
        self._context_id: Optional[str] = None
        self._task_id: Optional[str] = None
        self._role = "user"
        self._parts: list[dict[str, Any]] = []
        self._extensions = [AP2_EXTENSION_URI]

    def set_message_id(self, mid: str) -> "MessageBuilder":
        self._message_id = mid
        return self

    def set_context_id(self, cid: str) -> "MessageBuilder":
        self._context_id = cid
        return self

    def set_task_id(self, tid: str) -> "MessageBuilder":
        self._task_id = tid
        return self

    def set_role(self, role: str) -> "MessageBuilder":
        self._role = role
        return self

    def add_text(self, text: str) -> "MessageBuilder":
        self._parts.append({"kind": "text", "text": text})
        return self

    def add_data(self, key: str, value: Any) -> "MessageBuilder":
        """Add a DataPart with a single key-value pair."""
        self._parts.append({"kind": "data", "data": {key: value}})
        return self

    def add_data_dict(self, data: dict[str, Any]) -> "MessageBuilder":
        """Add a DataPart with a full data dictionary."""
        self._parts.append({"kind": "data", "data": data})
        return self

    def build(self) -> dict[str, Any]:
        msg = {
            "messageId": self._message_id,
            "role": self._role,
            "parts": self._parts,
            "extensions": self._extensions,
        }
        if self._context_id:
            msg["contextId"] = self._context_id
        if self._task_id:
            msg["taskId"] = self._task_id
        return msg


class ArtifactBuilder:
    """Build A2A Artifacts (for CartMandate, PaymentReceipt, etc.)."""

    def __init__(self):
        self._artifact_id = str(uuid.uuid4())
        self._name: Optional[str] = None
        self._description: Optional[str] = None
        self._parts: list[dict[str, Any]] = []

    def set_artifact_id(self, aid: str) -> "ArtifactBuilder":
        self._artifact_id = aid
        return self

    def set_name(self, name: str) -> "ArtifactBuilder":
        self._name = name
        return self

    def set_description(self, desc: str) -> "ArtifactBuilder":
        self._description = desc
        return self

    def add_data(self, key: str, value: Any) -> "ArtifactBuilder":
        self._parts.append({"kind": "data", "data": {key: value}})
        return self

    def add_data_dict(self, data: dict[str, Any]) -> "ArtifactBuilder":
        self._parts.append({"kind": "data", "data": data})
        return self

    def build(self) -> dict[str, Any]:
        artifact = {
            "artifactId": self._artifact_id,
            "parts": self._parts,
        }
        if self._name:
            artifact["name"] = self._name
        if self._description:
            artifact["description"] = self._description
        return artifact
