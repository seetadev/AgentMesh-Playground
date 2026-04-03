"""
AgentMesh Stack — Protocol Message Types (Layer 1 + 3 + 4 + 5)

All messages use Pydantic for type safety and JSON serialization.
Wire format: 4-byte big-endian length prefix + JSON payload.
This is consistent with the other examples in this repository.

Layer mapping:
  AnnounceMessage          → Layer 1 (Communication)
  NegotiateRequest/Offer   → Layer 3 (Negotiation Engine)
  ExecuteStep/Result       → Layer 5 (Execution Engine)
  PolicySet/Policy         → Layer 2 (Policy Extraction) data types
  ExecutionProtocol        → Layer 4 (Protocol Generator) data types
"""

import hashlib
import json
import logging
import struct
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

log = logging.getLogger(__name__)

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Protocol constants (Layer 1)
# ---------------------------------------------------------------------------

ANNOUNCE_TOPIC = "agentmesh-announce"          # GossipSub pub/sub topic
GOSSIPSUB_PROTOCOL_ID = "/meshsub/1.0.0"       # GossipSub protocol version
NEGOTIATE_PROTOCOL_ID = "/agentmesh/negotiate/v1"  # Direct negotiation stream
EXECUTE_PROTOCOL_ID = "/agentmesh/execute/v1"      # Direct execution stream
DHT_PROVIDER_KEY = "agentmesh-worker-v1"           # DHT content key for worker discovery

AGENTMESH_PROTOCOL_VERSION = "1.0"            # AgentMesh message protocol version
HEALTH_PROTOCOL_ID = "/agentmesh/health/v1"   # Health check stream


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class WorkerCapability(str, Enum):
    """Capabilities a worker agent can offer."""
    DATA_VALIDATION = "data_validation"
    DATA_TRANSFORMATION = "data_transformation"
    ANALYTICS = "analytics"
    REPORT_GENERATION = "report_generation"


class MessageType(str, Enum):
    """All message types in the AgentMesh protocol."""
    # Layer 1 — Communication
    ANNOUNCE = "announce"
    # Layer 1 — Health checks
    HEALTH_PING = "health_ping"
    HEALTH_PONG = "health_pong"
    # Layer 3 — Negotiation Engine
    NEGOTIATE_REQUEST = "negotiate_request"
    NEGOTIATE_OFFER = "negotiate_offer"
    NEGOTIATE_ACK = "negotiate_ack"
    NEGOTIATE_REJECT = "negotiate_reject"
    # Layer 5 — Execution Engine
    EXECUTE_STEP = "execute_step"
    EXECUTE_RESULT = "execute_result"
    EXECUTE_ERROR = "execute_error"


# ---------------------------------------------------------------------------
# Layer 2 — Policy types (used by PolicyExtractor and NegotiationEngine)
# ---------------------------------------------------------------------------

class Policy(BaseModel):
    """A single named constraint. negotiable=False means it cannot be countered."""
    key: str
    value: Any
    negotiable: bool = True


class PolicySet(BaseModel):
    """
    Structured policies extracted from a user's task description (Layer 2).
    Defines the constraints under which agents operate.
    """
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    task_description: str
    required_capabilities: list[WorkerCapability]
    policies: list[Policy] = []

    def get(self, key: str, default: Any = None) -> Any:
        for p in self.policies:
            if p.key == key:
                return p.value
        return default


# ---------------------------------------------------------------------------
# Base message envelope
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """Base envelope for all protocol messages."""
    type: MessageType
    sender: str  # Peer ID of the sender
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    msg_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])


# ---------------------------------------------------------------------------
# Layer 1 — Communication messages (GossipSub announcements)
# ---------------------------------------------------------------------------

class WorkerCapabilitySpec(BaseModel):
    """Describes one capability a worker offers, with pricing and SLA."""
    capability: WorkerCapability
    cost_per_unit: float   # USD per execution
    max_latency_ms: int    # Worst-case latency SLA
    quality_tier: str      # "standard" or "premium"


class AnnounceMessage(Message):
    """
    Worker broadcasts its capabilities and address via GossipSub.
    This is how coordinators discover available workers (Layer 1).
    """
    type: MessageType = MessageType.ANNOUNCE
    worker_name: str
    capabilities: list[WorkerCapabilitySpec]
    multiaddr: str
    worker_policies: list[Policy] = []


# ---------------------------------------------------------------------------
# Layer 1 — Health check messages (direct libp2p streams)
# ---------------------------------------------------------------------------

class HealthPing(Message):
    """
    Coordinator → Worker: liveness probe sent before opening a negotiate stream.
    Round-trip latency serves as a secondary signal; the presence of a HealthPong
    is the primary liveness indicator.
    """
    type: MessageType = MessageType.HEALTH_PING


class HealthPong(Message):
    """
    Worker → Coordinator: response to a HealthPing confirming the worker is
    alive and ready to accept negotiation requests.
    """
    type: MessageType = MessageType.HEALTH_PONG
    worker_name: str
    capability: WorkerCapability


# ---------------------------------------------------------------------------
# Layer 3 — Negotiation Engine messages (direct libp2p streams)
# ---------------------------------------------------------------------------

class NegotiateRequest(Message):
    """
    Coordinator → Worker: "Can you handle this capability under these terms?"
    Opens a direct libp2p stream for point-to-point negotiation.
    """
    type: MessageType = MessageType.NEGOTIATE_REQUEST
    protocol_version: str = AGENTMESH_PROTOCOL_VERSION
    task_id: str
    task_description: str
    required_capability: WorkerCapability
    proposed_policies: list[Policy]  # Coordinator's desired terms


class NegotiateOffer(Message):
    """
    Worker → Coordinator: "Yes, I can — here are my terms."
    May include counter_policies if the worker needs to adjust any terms.
    """
    type: MessageType = MessageType.NEGOTIATE_OFFER
    protocol_version: str = AGENTMESH_PROTOCOL_VERSION
    task_id: str
    capability: WorkerCapability
    worker_name: str
    accepted_policies: list[Policy]   # Policies the worker accepts as-is
    counter_policies: list[Policy] = []  # Worker's proposed modifications
    accepted: bool = True


class NegotiateAck(Message):
    """
    Coordinator → Worker: "Agreement confirmed — you are assigned to this step."
    Finalises negotiation on this stream before closing it.
    session_token is the HMAC token the worker must present in ExecuteStep.
    """
    type: MessageType = MessageType.NEGOTIATE_ACK
    task_id: str
    step_id: str
    accepted: bool  # True = assigned, False = rejected after offer
    session_token: str = ""  # Non-empty only when accepted=True


class NegotiateReject(Message):
    """Either party rejects the negotiation outright."""
    type: MessageType = MessageType.NEGOTIATE_REJECT
    task_id: str
    reason: str


# ---------------------------------------------------------------------------
# Layer 4 — Protocol Generator data types (not wire messages)
# ---------------------------------------------------------------------------

class ProtocolStep(BaseModel):
    """
    One step in an ExecutionProtocol, assigned to a specific worker.
    input_from creates a directed data-flow edge from the primary predecessor.
    depends_on lists ALL steps that must complete before this one starts
    (enables parallel execution of independent steps in the same wave).
    sequence is the topological wave number — steps sharing the same value
    can run concurrently.
    """
    step_id: str
    sequence: int
    capability: WorkerCapability
    worker_peer_id: str   # Base58 peer ID of the assigned worker
    worker_name: str
    input_from: Optional[str] = None  # step_id of the primary data-source predecessor
    depends_on: list[str] = []        # all step_ids that must complete first
    session_token: str = ""           # HMAC token minted by coordinator, verified by worker
    parameters: dict[str, Any] = {}
    agreed_policies: list[Policy] = []


class ExecutionProtocol(BaseModel):
    """
    The compiled, executable protocol produced by the Protocol Generator (Layer 4).
    Represents a validated workflow that can be shared, stored, or submitted on-chain.
    """
    protocol_id: str = Field(
        default_factory=lambda: f"proto_{uuid.uuid4().hex[:8]}"
    )
    task_id: str
    task_description: str
    steps: list[ProtocolStep]
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def compute_hash(self) -> str:
        """Deterministic content hash for integrity verification."""
        data = json.dumps(
            {
                "task_id": self.task_id,
                "steps": [
                    {"step_id": s.step_id, "worker": s.worker_peer_id, "seq": s.sequence}
                    for s in self.steps
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(data.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Layer 5 — Execution Engine messages (direct libp2p streams)
# ---------------------------------------------------------------------------

class ExecuteStep(Message):
    """
    Coordinator → Worker: "Execute your assigned step with this input data."
    Opens a new direct libp2p stream per step (separate from negotiation).
    session_token must match the token issued in NegotiateAck for this step.
    """
    type: MessageType = MessageType.EXECUTE_STEP
    protocol_id: str
    step_id: str
    task_id: str
    capability: WorkerCapability
    session_token: str = ""            # Verified by worker before executing
    parameters: dict[str, Any] = {}
    input_data: Optional[dict] = None  # Output from the previous step (chained)


class ExecuteResult(Message):
    """
    Worker → Coordinator: "Step complete — here is the output."
    Includes a verification_hash for Layer 6 hybrid integrity checks.
    """
    type: MessageType = MessageType.EXECUTE_RESULT
    protocol_id: str
    step_id: str
    task_id: str
    capability: WorkerCapability
    worker_name: str
    output_data: dict[str, Any]
    execution_time_ms: int
    verification_hash: str = ""  # SHA-256[:16] of output_data for integrity


class ExecuteError(Message):
    """Worker → Coordinator: step execution failed."""
    type: MessageType = MessageType.EXECUTE_ERROR
    protocol_id: str
    step_id: str
    task_id: str
    error: str


# ---------------------------------------------------------------------------
# Deserialization registry
# ---------------------------------------------------------------------------

_MESSAGE_REGISTRY: dict[MessageType, type[Message]] = {
    MessageType.ANNOUNCE: AnnounceMessage,
    MessageType.HEALTH_PING: HealthPing,
    MessageType.HEALTH_PONG: HealthPong,
    MessageType.NEGOTIATE_REQUEST: NegotiateRequest,
    MessageType.NEGOTIATE_OFFER: NegotiateOffer,
    MessageType.NEGOTIATE_ACK: NegotiateAck,
    MessageType.NEGOTIATE_REJECT: NegotiateReject,
    MessageType.EXECUTE_STEP: ExecuteStep,
    MessageType.EXECUTE_RESULT: ExecuteResult,
    MessageType.EXECUTE_ERROR: ExecuteError,
}


# ---------------------------------------------------------------------------
# Wire framing helpers (4-byte big-endian length prefix + JSON payload)
# ---------------------------------------------------------------------------

_FRAME_HEADER = 4
_MAX_MSG_SIZE = 1024 * 1024  # 1 MB


async def send_msg(stream, msg: Message) -> None:
    """Serialize and send a length-prefixed message over a libp2p stream."""
    payload = msg.model_dump_json().encode()
    await stream.write(struct.pack(">I", len(payload)) + payload)


async def recv_msg(stream) -> Optional[Message]:
    """Read one length-prefixed message from a libp2p stream."""
    header = await stream.read(_FRAME_HEADER)
    if not header or len(header) < _FRAME_HEADER:
        return None
    length = struct.unpack(">I", header)[0]
    if length > _MAX_MSG_SIZE:
        raise ValueError(f"Message size {length} exceeds limit")
    payload = await stream.read(length)
    if not payload or len(payload) < length:
        return None
    try:
        raw = json.loads(payload)
        return _MESSAGE_REGISTRY[MessageType(raw["type"])].model_validate(raw)
    except Exception as exc:
        log.debug(f"[recv_msg] Decode error: {exc}")
        return None
