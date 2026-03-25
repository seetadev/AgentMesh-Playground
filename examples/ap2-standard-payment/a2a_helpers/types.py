"""Minimal A2A protocol types for AP2 demo."""
import enum
from typing import Any, Optional
from pydantic import BaseModel, Field

# AP2 Extension
AP2_EXTENSION_URI = "https://github.com/google-agentic-commerce/ap2/tree/v0.1"

class TaskState(str, enum.Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input-required"

# Parts (discriminated by "kind")
class TextPart(BaseModel):
    kind: str = "text"
    text: str

class DataPart(BaseModel):
    kind: str = "data"
    data: dict[str, Any]

# Union type for parts
Part = TextPart | DataPart

class Message(BaseModel):
    message_id: str = Field(..., alias="messageId")
    context_id: Optional[str] = Field(None, alias="contextId")
    task_id: Optional[str] = Field(None, alias="taskId")
    role: str = "user"  # "user" or "agent"
    parts: list[dict[str, Any]]  # Use dict for flexibility, parse as needed
    extensions: Optional[list[str]] = None

    model_config = {"populate_by_name": True}

class Artifact(BaseModel):
    artifact_id: str = Field(..., alias="artifactId")
    name: Optional[str] = None
    description: Optional[str] = None
    parts: list[dict[str, Any]]
    extensions: Optional[list[str]] = None

    model_config = {"populate_by_name": True}

class TaskStatus(BaseModel):
    state: TaskState
    message: Optional[str] = None

class Task(BaseModel):
    task_id: str = Field(..., alias="taskId")
    context_id: Optional[str] = Field(None, alias="contextId")
    status: TaskStatus
    artifacts: Optional[list[Artifact]] = None
    messages: Optional[list[Message]] = None

    model_config = {"populate_by_name": True}

# AgentCard types
class AgentExtension(BaseModel):
    uri: str
    description: Optional[str] = None
    required: bool = False
    params: Optional[dict[str, Any]] = None

class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: Optional[list[str]] = None

class AgentCapabilities(BaseModel):
    streaming: bool = False
    push_notifications: bool = False
    extensions: Optional[list[AgentExtension]] = None

class AgentCard(BaseModel):
    name: str
    description: str
    url: str
    version: str = "0.1.0"
    capabilities: AgentCapabilities = AgentCapabilities()
    skills: list[AgentSkill] = []

# JSON-RPC 2.0 types
class JsonRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str
    method: str
    params: Optional[dict[str, Any]] = None

class JsonRpcResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    result: Optional[Any] = None
    error: Optional[dict[str, Any]] = None

class JsonRpcError(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None
