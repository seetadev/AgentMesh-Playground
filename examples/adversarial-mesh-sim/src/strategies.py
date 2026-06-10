"""Adversarial strategies for agent misbehavior in simulation."""

from __future__ import annotations

import random
import secrets
import time
from typing import ClassVar

from src.agent import (
    AgentIdentity,
    AgentType,
    BaseAgent,
    Capability,
    NegotiationAction,
    NegotiationContext,
)


_ADVERSARY_REGISTRY: dict[str, type["AdversarialStrategy"]] = {}


def register_strategy(name: str):
    def decorator(cls: type[AdversarialStrategy]):
        _ADVERSARY_REGISTRY[name] = cls
        cls.strategy_name = name
        return cls
    return decorator


def get_strategy(name: str) -> type["AdversarialStrategy"] | None:
    return _ADVERSARY_REGISTRY.get(name)


def list_strategies() -> dict[str, str]:
    return {name: cls.__doc__ or "" for name, cls in _ADVERSARY_REGISTRY.items()}


class AdversarialStrategy(BaseAgent):
    strategy_name: ClassVar[str] = "base"

    def __init__(self, identity: AgentIdentity, malice_level: float = 0.7):
        super().__init__(identity)
        self.malice_level = max(0.0, min(1.0, malice_level))

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        return NegotiationAction(accept=False, reason="Base strategy (no-op)")


@register_strategy("sybil")
class SybilStrategy(AdversarialStrategy):
    """Sybil identity inflation — creates fake identities to sway consensus."""

    def __init__(self, identity: AgentIdentity, malice_level: float = 0.7):
        super().__init__(identity, malice_level)
        self.sock_puppets: list[str] = []
        for i in range(int(10 * malice_level)):
            puppet_id = f"sybil-{secrets.token_hex(4)}-{i}"
            self.sock_puppets.append(puppet_id)

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        if random.random() < self.malice_level:
            inflated_price = ctx.proposed_price * random.uniform(0.5, 2.0)
            return NegotiationAction(
                accept=(random.random() > 0.6),
                counter_price=round(inflated_price, 2),
                reason="sybil-generated-offer",
            )
        return NegotiationAction(accept=True, counter_price=ctx.proposed_price)


@register_strategy("colluder")
class CollusionStrategy(AdversarialStrategy):
    """Collusive bidding — coordinates with other colluders to fix prices."""

    def __init__(self, identity: AgentIdentity, malice_level: float = 0.7):
        super().__init__(identity, malice_level)
        self.colluder_group: set[str] = set()
        self._fixed_price: float = 100.0
        self._rounds_active: int = 0

    def register_colluder(self, peer_id: str):
        self.colluder_group.add(peer_id)

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        self._rounds_active += 1
        if ctx.counterparty_id in self.colluder_group:
            return NegotiationAction(
                accept=True,
                counter_price=self._fixed_price,
                reason="collusion-agreed-price",
            )
        inflated = self._fixed_price * random.uniform(1.5, 3.0)
        return NegotiationAction(
            accept=(random.random() > 0.3),
            counter_price=round(inflated, 2),
            reason="collusion-external-price",
        )


@register_strategy("flooder")
class FloodStrategy(AdversarialStrategy):
    """Message flooding — overwhelms peers with garbage messages (DoS)."""

    def __init__(self, identity: AgentIdentity, malice_level: float = 0.7):
        super().__init__(identity, malice_level)
        self._flood_count: int = 0
        self._max_flood_per_round: int = int(500 * malice_level)

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        self._flood_count += 1
        if self._flood_count < self._max_flood_per_round:
            return NegotiationAction(
                accept=False,
                reason="flood-request",
            )
        return NegotiationAction(
            accept=(random.random() > 0.7),
            counter_price=ctx.proposed_price * 10,
            reason="post-flood-offer",
        )


@register_strategy("eclipser")
class EclipseStrategy(AdversarialStrategy):
    """Eclipse attack — drops or misroutes messages from specific honest peers."""

    def __init__(self, identity: AgentIdentity, malice_level: float = 0.7):
        super().__init__(identity, malice_level)
        self._target_peers: set[str] = set()
        self._dropped_count: int = 0

    def add_target(self, peer_id: str):
        self._target_peers.add(peer_id)

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        if ctx.counterparty_id in self._target_peers:
            self._dropped_count += 1
            return NegotiationAction(
                accept=False,
                reason="connection-timeout",
            )
        return NegotiationAction(
            accept=(random.random() > 0.5),
            counter_price=ctx.proposed_price,
            reason="eclipse-non-target",
        )


@register_strategy("false-reporter")
class FalseReportingStrategy(AdversarialStrategy):
    """Lies about task completion and quality."""

    def __init__(self, identity: AgentIdentity, malice_level: float = 0.7):
        super().__init__(identity, malice_level)
        self._tasks_claimed: int = 0
        self._tasks_actual: int = 0

    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        return NegotiationAction(accept=True, counter_price=ctx.proposed_price)

    def on_complete_task(self, task_id: str, success: bool):
        self._tasks_actual += 1
        if random.random() < self.malice_level:
            super().on_complete_task(task_id, True)
            self._tasks_claimed += 1
        else:
            super().on_complete_task(task_id, success)

    @property
    def false_claim_rate(self) -> float:
        if self._tasks_actual == 0:
            return 0.0
        return (self._tasks_claimed - self._tasks_actual) / self._tasks_actual
