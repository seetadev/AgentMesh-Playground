"""Tests for the reputation scoring engine."""

import time

import pytest
from src.reputation import GossipReputationVote, ReputationEngine, ReputationScore


class TestReputationScore:
    def test_default_score(self):
        s = ReputationScore(peer_id="test")
        assert s.score == 0.5
        assert s.confidence == 0.0

    def test_success_increases_score(self):
        s = ReputationScore(peer_id="test")
        s.record_success()
        assert s.score > 0.5

    def test_failure_decreases_score(self):
        s = ReputationScore(peer_id="test")
        s.record_failure()
        assert s.score < 0.5

    def test_confidence_grows_with_interactions(self):
        s = ReputationScore(peer_id="test")
        for _ in range(10):
            s.record_success()
        assert s.confidence > 0.5

    def test_weighted_success(self):
        s = ReputationScore(peer_id="test")
        s.record_success(weight=5.0)
        assert s.alpha == 6.0


class TestReputationEngine:
    def setup_method(self):
        self.engine = ReputationEngine()

    def test_new_peer_defaults_to_05(self):
        assert self.engine.get_score("peer1").score == 0.5

    def test_report_success(self):
        self.engine.report_success("peer1")
        assert self.engine.get_score("peer1").score > 0.5

    def test_report_failure(self):
        self.engine.report_failure("peer1")
        assert self.engine.get_score("peer1").score < 0.5

    def test_gossip_vote_propagation(self):
        self.engine.report_success("voter")
        vote = GossipReputationVote(
            voter_id="voter",
            target_id="target",
            score=0.1,
            confidence=0.9,
            round_number=1,
        )
        self.engine.ingest_gossip(vote)
        assert self.engine.get_score("target").score < 0.5

    def test_low_confidence_gossip_ignored(self):
        vote = GossipReputationVote(
            voter_id="unknown",
            target_id="target",
            score=0.1,
            confidence=0.0,
            round_number=1,
        )
        self.engine.ingest_gossip(vote)
        assert self.engine.get_score("target").score == 0.5

    def test_suspected_adversarial_detection(self):
        for _ in range(20):
            self.engine.report_failure("bad-peer")
        suspected = self.engine.suspected_adversarial()
        assert "bad-peer" in suspected

    def test_decay_reduces_scores(self):
        self.engine.report_success("peer1", weight=10.0)
        self.engine.get_score("peer1").last_updated = int(time.time()) - 3600
        score_before = self.engine.get_score("peer1").score
        self.engine.decay_all()
        score_after = self.engine.get_score("peer1").score
        assert score_after <= score_before

    def test_reset_clears_all(self):
        self.engine.report_success("p1")
        self.engine.reset()
        assert self.engine.get_score("p1").score == 0.5

    def test_top_peers_ordering(self):
        self.engine.report_success("good", weight=10)
        self.engine.report_failure("bad", weight=10)
        top = self.engine.top_peers(5)
        assert top[0][0] == "good"
        assert top[-1][0] == "bad"
