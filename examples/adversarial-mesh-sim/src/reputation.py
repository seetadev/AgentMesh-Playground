"""Bayesian and gossip-based reputation scoring for agent trust evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ReputationScore:
    peer_id: str
    alpha: float = 1.0
    beta: float = 1.0
    last_updated: int = 0
    interaction_count: int = 0

    @property
    def score(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        total = self.alpha + self.beta
        if total <= 2:
            return 0.0
        return 1.0 - (2.0 * (self.alpha * self.beta)) / (
            (self.alpha + self.beta) ** 2 * (self.alpha + self.beta + 1)
        )

    def record_success(self, weight: float = 1.0):
        self.alpha += weight
        self.interaction_count += 1

    def record_failure(self, weight: float = 1.0):
        self.beta += weight
        self.interaction_count += 1


@dataclass
class GossipReputationVote:
    voter_id: str
    target_id: str
    score: float
    confidence: float
    round_number: int


class ReputationEngine:
    def __init__(self, decay_factor: float = 0.95, gossip_trust: float = 0.3):
        self._scores: dict[str, ReputationScore] = {}
        self._gossip_votes: list[GossipReputationVote] = []
        self.decay_factor = decay_factor
        self.gossip_trust = gossip_trust

    def get_score(self, peer_id: str) -> ReputationScore:
        if peer_id not in self._scores:
            self._scores[peer_id] = ReputationScore(peer_id=peer_id)
        return self._scores[peer_id]

    def report_success(self, peer_id: str, weight: float = 1.0):
        score = self.get_score(peer_id)
        score.record_success(weight)
        score.last_updated = __import__("time").time()

    def report_failure(self, peer_id: str, weight: float = 1.0):
        score = self.get_score(peer_id)
        score.record_failure(weight)
        score.last_updated = __import__("time").time()

    def ingest_gossip(self, vote: GossipReputationVote):
        self._gossip_votes.append(vote)
        voter_score = self.get_score(vote.voter_id)
        voter_trust = voter_score.score * voter_score.confidence
        effective_weight = vote.confidence * voter_trust * self.gossip_trust
        if effective_weight > 0.01:
            target_score = self.get_score(vote.target_id)
            if vote.score >= 0.5:
                target_score.record_success(effective_weight)
            else:
                target_score.record_failure(effective_weight)

    def decay_all(self):
        now = __import__("time").time()
        for score in self._scores.values():
            if score.interaction_count > 0:
                elapsed = now - score.last_updated
                decay = self.decay_factor ** (elapsed / 60.0)
                score.alpha *= decay
                score.beta *= decay

    def top_peers(self, n: int = 10) -> list[tuple[str, float]]:
        scored = [(pid, s.score) for pid, s in self._scores.items()]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:n]

    def suspected_adversarial(self, threshold: float = 0.4) -> list[str]:
        return [
            pid for pid, s in self._scores.items()
            if s.score < threshold and s.confidence > 0.5
        ]

    def reset(self):
        self._scores.clear()
        self._gossip_votes.clear()
