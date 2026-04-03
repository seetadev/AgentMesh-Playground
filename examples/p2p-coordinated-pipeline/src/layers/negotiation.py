"""
Layer 3: Negotiation Engine

Policy-aware multi-agent coordination. Evaluates worker offers against the
coordinator's PolicySet and selects the optimal assignment for each capability.

Key design principles:
  - Non-negotiable policies (negotiable=False) are hard constraints — any
    violation immediately disqualifies the offer.
  - Negotiable policies can be countered by workers within tolerance bounds.
  - Scoring rewards cheaper and faster workers, penalises counter-offers.
  - Adversarial resilience: workers that counter non-negotiable policies are
    rejected, protecting the coordinator's security requirements.
"""

from ..common.messages import Policy, PolicySet, NegotiateOffer, WorkerCapability


# Quality tier ordering for comparison
_QUALITY_TIERS = {"standard": 0, "premium": 1}


class NegotiationEngine:
    """
    Evaluates and selects worker offers based on policy compatibility.

    Usage:
        engine = NegotiationEngine()
        offers = [offer_from_worker_a, offer_from_worker_b]
        best = engine.select_best(offers, coordinator_policy, WorkerCapability.ANALYTICS)
    """

    def evaluate(
        self, offer: NegotiateOffer, policy: PolicySet
    ) -> tuple[bool, float]:
        """
        Evaluate a worker's offer against the coordinator's PolicySet.

        Returns:
            (acceptable, score) — if acceptable is False, score is 0.
            Higher score = better fit.
        """
        score = 100.0

        # Check all policies the worker accepted
        for p in offer.accepted_policies:
            if not self._satisfies(p, policy):
                return False, 0.0
            score += self._bonus(p, policy)

        # Check counter-policies (worker's proposed modifications)
        for p in offer.counter_policies:
            if not self._counter_ok(p, policy):
                return False, 0.0
            score -= 10.0  # small penalty for countering any term

        return True, score

    def select_best(
        self,
        offers: list[NegotiateOffer],
        policy: PolicySet,
        capability: WorkerCapability,
    ) -> NegotiateOffer | None:
        """
        Return the highest-scoring acceptable offer for a given capability.
        Returns None if no offer satisfies the coordinator's policies.
        """
        candidates: list[tuple[float, NegotiateOffer]] = []
        for offer in offers:
            if offer.capability != capability:
                continue
            acceptable, score = self.evaluate(offer, policy)
            if acceptable:
                candidates.append((score, offer))
        if not candidates:
            return None
        return max(candidates, key=lambda x: x[0])[1]

    # ------------------------------------------------------------------
    # Internal constraint checkers
    # ------------------------------------------------------------------

    def _satisfies(self, worker_policy: Policy, coord: PolicySet) -> bool:
        """
        Check whether a worker's stated policy value satisfies the coordinator's constraint.
        Non-negotiable coordinator policies act as hard filters.
        """
        coord_value = coord.get(worker_policy.key)
        if coord_value is None:
            return True  # Coordinator has no constraint on this key

        k, v = worker_policy.key, worker_policy.value
        if k == "max_budget_usd":
            return float(v) <= float(coord_value)
        if k == "max_latency_ms":
            return int(v) <= int(coord_value)
        if k == "output_format":
            return str(v) == str(coord_value)
        if k == "quality_tier":
            return _QUALITY_TIERS.get(str(v), 0) >= _QUALITY_TIERS.get(str(coord_value), 0)
        return True

    def _counter_ok(self, counter: Policy, coord: PolicySet) -> bool:
        """
        Validate a worker's counter-proposal.
        - Any counter on a non-negotiable coordinator policy is rejected.
        - Cost counters are accepted up to 20% above the stated budget.
        """
        # Reject counters on non-negotiable policies (adversarial resilience)
        for p in coord.policies:
            if p.key == counter.key and not p.negotiable:
                return False

        coord_value = coord.get(counter.key)
        if coord_value is None:
            return True

        if counter.key == "max_budget_usd":
            return float(counter.value) <= float(coord_value) * 1.2  # 20% tolerance

        return True

    def _bonus(self, policy: Policy, coord: PolicySet) -> float:
        """Score bonus for favourable policy values (cheaper/faster = higher score)."""
        coord_value = coord.get(policy.key)
        if coord_value is None:
            return 0.0
        if policy.key == "max_budget_usd" and float(coord_value) > 0:
            ratio = float(coord_value) / max(float(policy.value), 1e-9)
            return min(ratio * 10.0, 30.0)   # up to +30 for very cheap workers
        if policy.key == "max_latency_ms" and int(coord_value) > 0:
            ratio = int(coord_value) / max(int(policy.value), 1)
            return min(ratio * 5.0, 20.0)    # up to +20 for very fast workers
        return 0.0
