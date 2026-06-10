"""Agent base classes and adversarial variants for the adversarial-mesh-sim."""

from __future__ import annotations

import enum
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable


@enum.unique
class AgentRole(enum.Enum):
    COORDINATOR = "coordinator"
    WORKER = "worker"


@enum.unique
class AgentType(enum.Enum):
    HONEST = "honest"
    SYBIL = "sybil"
    ECLIPSER = "eclipser"
    COLLUDER = "colluder"
    FLOODER = "flooder"


@dataclass
class NegotiationContext:
    task_id: str
    proposed_price: float
    requested_capability: str
    counterparty_id: str
    round_number: int


@dataclass
class NegotiationAction:
    accept: bool
    counter_price: float | None = None
    reason: str = ""


@dataclass
class Capability:
    name: str
    price: float
    quality: float = 1.0
    latency_ms: int = 100


@dataclass
class AgentStats:
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_earned: float = 0.0
    messages_sent: int = 0
    messages_received: int = 0
    successful_negotiations: int = 0
    failed_negotiations: int = 0
    reputation_score: float = 1.0
    is_detected_adversarial: bool = False


@dataclass
class AgentIdentity:
    agent_id: str
    role: AgentRole
    agent_type: AgentType
    capabilities: list[Capability] = field(default_factory=list)
    stats: AgentStats = field(default_factory=AgentStats)
    peer_id: str = ""

    def __post_init__(self):
        if not self.peer_id:
            self.peer_id = f"12D3KooW{secrets.token_hex(20)}"


AdversarialStrategyFn = Callable[[NegotiationContext], NegotiationAction]


class BaseAgent:
    def __init__(self, identity: AgentIdentity):
        self.identity = identity
        self._reputation_scores: dict[str, float] = {}
        self._known_peers: dict[str, AgentIdentity] = {}
        self._blacklist: set[str] = set()

    @property
    def agent_id(self) -> str:
        return self.identity.agent_id

    @property
    def is_honest(self) -> bool:
        return self.identity.agent_type == AgentType.HONEST

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        raise NotImplementedError

    def on_complete_task(self, task_id: str, success: bool):
        if success:
            self.identity.stats.tasks_completed += 1
        else:
            self.identity.stats.tasks_failed += 1

    def record_message(self, sent: bool = True):
        if sent:
            self.identity.stats.messages_sent += 1
        else:
            self.identity.stats.messages_received += 1

    def update_reputation(self, peer_id: str, delta: float):
        current = self._reputation_scores.get(peer_id, 1.0)
        self._reputation_scores[peer_id] = max(0.0, min(2.0, current + delta))
        if self._reputation_scores[peer_id] < 0.3:
            self._blacklist.add(peer_id)

    def get_reputation(self, peer_id: str) -> float:
        return self._reputation_scores.get(peer_id, 1.0)

    def is_blacklisted(self, peer_id: str) -> bool:
        return peer_id in self._blacklist


class HonestAgent(BaseAgent):
    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        now = time.time()
        if ctx.proposed_price <= 0:
            return NegotiationAction(accept=False, reason="Invalid price")
        if ctx.counterparty_id in self._blacklist:
            return NegotiationAction(accept=False, reason="Counterparty blacklisted")
        if self.get_reputation(ctx.counterparty_id) < 0.4:
            return NegotiationAction(accept=False, reason="Low reputation score")
        return NegotiationAction(accept=True, counter_price=ctx.proposed_price)


class HonestCoordinator(HonestAgent):
    def __init__(self, identity: AgentIdentity):
        super().__init__(identity)
        self._task_history: dict[str, list[str]] = {}

    def assign_task(self, task_id: str, worker_id: str):
        self._task_history.setdefault(task_id, []).append(worker_id)

    def verify_completion(self, task_id: str, worker_id: str, claimed_output: str) -> bool:
        rep = self.get_reputation(worker_id)
        if rep < 0.3:
            return False
        return True
