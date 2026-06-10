"""Simulation engine orchestrating multi-agent adversarial scenarios."""

from __future__ import annotations

import random
import secrets
import time
from dataclasses import dataclass, field

from src.agent import (
    AgentIdentity,
    AgentRole,
    AgentStats,
    AgentType,
    BaseAgent,
    Capability,
    HonestAgent,
    HonestCoordinator,
    NegotiationContext,
)
from src.detection import AnomalyDetector
from src.reputation import GossipReputationVote, ReputationEngine
from src.strategies import (
    AdversarialStrategy,
    CollusionStrategy,
    EclipseStrategy,
    FloodStrategy,
    SybilStrategy,
    FalseReportingStrategy,
    get_strategy,
)


@dataclass
class SimulationConfig:
    num_coordinators: int = 2
    num_workers: int = 20
    num_rounds: int = 10
    adversary_ratio: float = 0.3
    scenario_name: str = "mixed-adversarial"
    task_types: list[str] = field(default_factory=lambda: [
        "data-validation",
        "analytics",
        "report-generation",
        "anomaly-detection",
        "data-aggregation",
    ])

    def __post_init__(self):
        self.num_adversarial = int(self.num_workers * self.adversary_ratio)
        self.num_honest = self.num_workers - self.num_adversarial


@dataclass
class RoundResult:
    round_number: int
    tasks_proposed: int = 0
    tasks_negotiated: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    adversarial_detected: int = 0
    false_positives: int = 0
    avg_negotiation_latency: float = 0.0
    avg_reputation: float = 1.0


@dataclass
class SimulationResult:
    scenario_name: str
    config: SimulationConfig
    rounds: list[RoundResult] = field(default_factory=list)
    total_tasks_proposed: int = 0
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0
    total_adversarial_detected: int = 0
    total_false_positives: int = 0
    true_adversarial_count: int = 0
    avg_negotiation_success_rate: float = 0.0
    avg_detection_rate: float = 0.0
    avg_false_positive_rate: float = 0.0
    avg_latency: float = 0.0
    final_reputation_scores: dict[str, float] = field(default_factory=dict)

    @property
    def completion_rate(self) -> float:
        total = self.total_tasks_completed + self.total_tasks_failed
        if total == 0:
            return 0.0
        return self.total_tasks_completed / total

    @property
    def detection_rate(self) -> float:
        if self.true_adversarial_count == 0:
            return 0.0
        return self.total_adversarial_detected / self.true_adversarial_count

    @property
    def false_positive_rate(self) -> float:
        honest_count = self.config.num_honest
        if honest_count == 0:
            return 0.0
        return self.total_false_positives / honest_count


class Simulation:
    def __init__(self, config: SimulationConfig):
        self.config = config
        self.agents: dict[str, BaseAgent] = {}
        self.identities: dict[str, AgentIdentity] = {}
        self.reputation = ReputationEngine()
        self.detector = AnomalyDetector()
        self.round_num = 0
        self._adversarial_ids: set[str] = set()
        self._setup_agents()

    def _setup_agents(self):
        random.seed(42)
        for i in range(self.config.num_coordinators):
            ident = AgentIdentity(
                agent_id=f"coord-{i}",
                role=AgentRole.COORDINATOR,
                agent_type=AgentType.HONEST,
            )
            self.identities[ident.agent_id] = ident
            self.agents[ident.agent_id] = HonestCoordinator(ident)

        adversary_types = [AgentType.SYBIL, AgentType.COLLUDER,
                           AgentType.FLOODER, AgentType.ECLIPSER]
        strategy_map = {
            AgentType.SYBIL: "sybil",
            AgentType.COLLUDER: "colluder",
            AgentType.FLOODER: "flooder",
            AgentType.ECLIPSER: "eclipser",
        }
        worker_idx = 0
        for i in range(self.config.num_adversarial):
            aid = f"worker-adv-{i}"
            atype = adversary_types[i % len(adversary_types)]
            ident = AgentIdentity(
                agent_id=aid,
                role=AgentRole.WORKER,
                agent_type=atype,
                capabilities=[
                    Capability(
                        name=random.choice(self.config.task_types),
                        price=random.uniform(5, 50),
                    )
                ],
            )
            self.identities[aid] = ident
            strategy_cls = get_strategy(strategy_map[atype])
            if strategy_cls:
                agent = strategy_cls(ident, malice_level=random.uniform(0.5, 0.95))
            else:
                agent = HonestAgent(ident)
            self.agents[aid] = agent
            self._adversarial_ids.add(aid)
            worker_idx += 1

        for i in range(self.config.num_honest):
            aid = f"worker-honest-{i}"
            ident = AgentIdentity(
                agent_id=aid,
                role=AgentRole.WORKER,
                agent_type=AgentType.HONEST,
                capabilities=[
                    Capability(
                        name=random.choice(self.config.task_types),
                        price=random.uniform(5, 50),
                    )
                ],
            )
            self.identities[aid] = ident
            self.agents[aid] = HonestAgent(ident)

        colluders = [
            aid for aid in self._adversarial_ids
            if self.identities[aid].agent_type == AgentType.COLLUDER
            and isinstance(self.agents.get(aid), CollusionStrategy)
        ]
        for cid in colluders:
            agent = self.agents[cid]
            for other in colluders:
                if other != cid:
                    agent.register_colluder(other)

    def run(self) -> SimulationResult:
        result = SimulationResult(scenario_name=self.config.scenario_name, config=self.config)
        self.result = result
        for r in range(self.config.num_rounds):
            self.round_num = r + 1
            round_result = self._run_round()
            result.rounds.append(round_result)
        self._finalize_result(result)
        return result

    def _run_round(self) -> RoundResult:
        round_result = RoundResult(round_number=self.round_num)
        honest_ids = [aid for aid in self.agents if aid not in self._adversarial_ids]
        task = random.choice(self.config.task_types)
        coordinators = [aid for aid in self.agents
                        if self.identities[aid].role == AgentRole.COORDINATOR]
        adv_ids = [aid for aid in self.agents if aid in self._adversarial_ids]
        worker_pool = honest_ids + adv_ids
        random.shuffle(worker_pool)
        for coord_id in coordinators:
            for worker_id in worker_pool[:4]:
                worker_price = (
                    self.identities[worker_id].capabilities[0].price
                    if self.identities[worker_id].capabilities
                    else 25.0
                )
                ctx = NegotiationContext(
                    task_id=f"task-{self.round_num}-{worker_id}",
                    proposed_price=worker_price,
                    requested_capability=task,
                    counterparty_id=worker_id,
                    round_number=self.round_num,
                )
                round_result.tasks_proposed += 1
                action = self.agents[coord_id].on_negotiate(ctx)
                worker_action = self.agents[worker_id].on_negotiate(ctx)
                prices = [
                    self.identities[aid].capabilities[0].price
                    for aid in self.agents
                    if aid not in self._adversarial_ids
                    and self.identities[aid].capabilities
                ]
                market_avg = sum(prices) / max(1, len(prices))
                self.detector.observe_negotiation(
                    worker_id, worker_action.accept,
                    ctx.proposed_price, market_avg,
                )
                self.detector.observe_negotiation(
                    coord_id, action.accept,
                    ctx.proposed_price, market_avg,
                )
                if action.accept and worker_action.accept:
                    round_result.tasks_negotiated += 1
                    success = random.random() > 0.15
                    self.agents[coord_id].on_complete_task(ctx.task_id, success)
                    self.agents[worker_id].on_complete_task(ctx.task_id, success)
                    if success:
                        round_result.tasks_completed += 1
                        self.reputation.report_success(worker_id)
                        self.reputation.report_success(coord_id)
                    else:
                        round_result.tasks_failed += 1
                        self.reputation.report_failure(worker_id)
                    self.detector.observe_task(worker_id, success)
                else:
                    if isinstance(self.agents[worker_id], AdversarialStrategy):
                        self.reputation.report_failure(worker_id)
        adversarial_detected, false_positives = 0, 0
        reports = self.detector.classify_all()
        for report in reports:
            if report.classification in ("adversarial", "suspicious"):
                if report.peer_id in self._adversarial_ids:
                    adversarial_detected += 1
                else:
                    false_positives += 1
        round_result.adversarial_detected = adversarial_detected
        round_result.false_positives = false_positives
        avg_rep = 0.0
        count = 0
        for aid in self.agents:
            s = self.reputation.get_score(aid)
            avg_rep += s.score
            count += 1
        round_result.avg_reputation = avg_rep / max(1, count)
        self.reputation.decay_all()
        return round_result

    def _finalize_result(self, result: SimulationResult):
        for r in result.rounds:
            result.total_tasks_proposed += r.tasks_proposed
            result.total_tasks_completed += r.tasks_completed
            result.total_tasks_failed += r.tasks_failed
            result.total_adversarial_detected += r.adversarial_detected
            result.total_false_positives += r.false_positives
        result.true_adversarial_count = len(self._adversarial_ids)
        result.avg_negotiation_success_rate = (
            result.total_tasks_completed / max(1, result.total_tasks_proposed)
        )
        result.avg_detection_rate = result.detection_rate
        result.avg_false_positive_rate = result.false_positive_rate
        for aid in self.agents:
            result.final_reputation_scores[aid] = self.reputation.get_score(aid).score
