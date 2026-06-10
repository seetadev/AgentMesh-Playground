"""Anomaly detection and adversarial agent classification."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class BehaviorProfile:
    peer_id: str
    message_rate: float = 0.0
    negotiation_success_rate: float = 1.0
    price_deviation: float = 0.0
    task_completion_rate: float = 1.0
    reputation_trend: float = 0.0
    unique_counterparties: int = 0
    anomalous_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class DetectionReport:
    peer_id: str
    classification: str
    confidence: float
    evidence: dict[str, float]
    round_detected: int


class AnomalyDetector:
    def __init__(self):
        self._profiles: dict[str, BehaviorProfile] = {}
        self._price_history: dict[str, list[float]] = defaultdict(list)
        self._message_timestamps: dict[str, list[float]] = defaultdict(list)
        self._history: dict[str, list[float]] = defaultdict(list)

    def observe_negotiation(
        self,
        peer_id: str,
        accepted: bool,
        proposed_price: float,
        market_avg: float,
    ):
        self._price_history[peer_id].append(proposed_price)
        profile = self._profiles.setdefault(peer_id, BehaviorProfile(peer_id=peer_id))
        total = profile.negotiation_success_rate * profile.unique_counterparties + (
            1 if accepted else 0
        )
        profile.unique_counterparties += 1
        profile.negotiation_success_rate = total / max(1, profile.unique_counterparties)
        if market_avg > 0:
            profile.price_deviation = abs(proposed_price - market_avg) / market_avg

    def observe_message(self, peer_id: str, timestamp: float):
        self._message_timestamps[peer_id].append(timestamp)
        profile = self._profiles.setdefault(peer_id, BehaviorProfile(peer_id=peer_id))
        recent = [t for t in self._message_timestamps[peer_id] if timestamp - t < 5.0]
        profile.message_rate = len(recent)

    def observe_task(self, peer_id: str, success: bool):
        self._history[peer_id].append(1.0 if success else 0.0)
        profile = self._profiles.setdefault(peer_id, BehaviorProfile(peer_id=peer_id))
        history = self._history[peer_id][-20:]
        profile.task_completion_rate = sum(history) / max(1, len(history))
        if len(history) >= 5:
            recent = history[-5:]
            older = history[:-5]
            if older:
                profile.reputation_trend = (sum(recent) / len(recent)) - (
                    sum(older) / len(older)
                )

    def classify(self, peer_id: str) -> DetectionReport | None:
        profile = self._profiles.get(peer_id)
        if profile is None:
            return None
        evidence: dict[str, float] = {}
        score = 0.0
        if profile.message_rate > 50:
            evidence["high_message_rate"] = profile.message_rate
            score += 0.3
        if profile.negotiation_success_rate < 0.3:
            evidence["low_negotiation_success"] = profile.negotiation_success_rate
            score += 0.2
        if profile.price_deviation > 2.0:
            evidence["price_deviation"] = profile.price_deviation
            score += 0.25
        if profile.task_completion_rate < 0.3:
            evidence["low_completion_rate"] = profile.task_completion_rate
            score += 0.2
        if profile.reputation_trend < -0.5:
            evidence["negative_reputation_trend"] = profile.reputation_trend
            score += 0.15
        confidence = min(1.0, score)
        if confidence > 0.5:
            classification = "adversarial"
        elif confidence > 0.25:
            classification = "suspicious"
        else:
            classification = "benign"
        return DetectionReport(
            peer_id=peer_id,
            classification=classification,
            confidence=min(1.0, score),
            evidence=evidence,
            round_detected=0,
        )

    def classify_all(self) -> list[DetectionReport]:
        return [
            r for pid in self._profiles
            if (r := self.classify(pid)) is not None
        ]

    def reset(self):
        self._profiles.clear()
        self._price_history.clear()
        self._message_timestamps.clear()
        self._history.clear()
