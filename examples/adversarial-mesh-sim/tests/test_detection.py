"""Tests for the anomaly detection engine."""

import pytest
from src.detection import AnomalyDetector


class TestAnomalyDetector:
    def setup_method(self):
        self.detector = AnomalyDetector()

    def test_honest_agent_classified_benign(self):
        for _ in range(10):
            self.detector.observe_negotiation(
                "honest-peer", True, 50.0, 50.0
            )
            self.detector.observe_task("honest-peer", True)
            self.detector.observe_message("honest-peer", 0.0)
        report = self.detector.classify("honest-peer")
        assert report is not None
        assert report.classification == "benign"

    def test_flooder_detected(self):
        for i in range(100):
            self.detector.observe_message("flooder", float(i) * 0.01)
        report = self.detector.classify("flooder")
        assert report is not None
        assert report.classification in ("adversarial", "suspicious")

    def test_low_completion_detected(self):
        for _ in range(20):
            self.detector.observe_task("sloppy-peer", False)
        report = self.detector.classify("sloppy-peer")
        assert report is not None
        assert report.classification in ("adversarial", "suspicious", "benign")
        assert report.confidence > 0.0

    def test_price_deviation_detected(self):
        for _ in range(5):
            self.detector.observe_negotiation(
                "price-spoofer", True, 5000.0, 50.0
            )
        report = self.detector.classify("price-spoofer")
        assert report is not None
        assert report.classification in ("adversarial", "suspicious", "benign")
        assert "price_deviation" in report.evidence

    def test_classify_all_returns_reports(self):
        self.detector.observe_task("p1", True)
        self.detector.observe_task("p2", False)
        self.detector.observe_task("p2", False)
        self.detector.observe_task("p2", False)
        self.detector.observe_negotiation("p3", True, 100, 50)
        reports = self.detector.classify_all()
        assert len(reports) >= 2

    def test_reset_clears_state(self):
        self.detector.observe_task("p1", False)
        self.detector.reset()
        assert self.detector.classify("p1") is None

    def test_adversarial_behavior_accumulates_evidence(self):
        self.detector.observe_message("attacker", 0.0)
        for _ in range(60):
            self.detector.observe_message("attacker", 0.01)
        for _ in range(5):
            self.detector.observe_negotiation("attacker", False, 999.0, 50.0)
        for _ in range(10):
            self.detector.observe_task("attacker", False)
        report = self.detector.classify("attacker")
        assert report is not None
        assert len(report.evidence) >= 2
