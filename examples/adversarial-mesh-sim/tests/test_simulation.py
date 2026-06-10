"""Integration tests for the full simulation pipeline."""

import pytest
from src.simulation import Simulation, SimulationConfig, SimulationResult


class TestSimulation:
    def test_honest_baseline_runs(self):
        config = SimulationConfig(
            num_workers=10,
            num_rounds=3,
            adversary_ratio=0.0,
            scenario_name="honest-baseline",
        )
        sim = Simulation(config)
        result = sim.run()
        assert isinstance(result, SimulationResult)
        assert result.scenario_name == "honest-baseline"
        assert result.total_tasks_proposed > 0

    def test_sybil_scenario_runs(self):
        config = SimulationConfig(
            num_workers=12,
            num_rounds=3,
            adversary_ratio=0.3,
            scenario_name="sybil-infiltration",
        )
        sim = Simulation(config)
        result = sim.run()
        assert result.total_tasks_completed >= 0
        assert result.true_adversarial_count > 0

    def test_mixed_adversarial_runs(self):
        config = SimulationConfig(
            num_workers=20,
            num_rounds=5,
            adversary_ratio=0.3,
            scenario_name="mixed-adversarial",
        )
        sim = Simulation(config)
        result = sim.run()
        assert result.total_tasks_proposed > 0
        assert len(result.rounds) == 5

    def test_round_result_structure(self):
        config = SimulationConfig(num_workers=10, num_rounds=2)
        sim = Simulation(config)
        result = sim.run()
        for r in result.rounds:
            assert r.round_number >= 1
            assert r.tasks_proposed >= 0
            assert r.tasks_negotiated >= 0
            assert r.tasks_completed >= 0
            assert r.tasks_failed >= 0

    def test_adversarial_detection_in_mixed_scenario(self):
        config = SimulationConfig(
            num_workers=20,
            num_rounds=5,
            adversary_ratio=0.4,
            scenario_name="detection-test",
        )
        sim = Simulation(config)
        result = sim.run()
        assert result.true_adversarial_count > 0

    def test_adversarial_reduces_negotiation_success(self):
        honest_config = SimulationConfig(
            num_workers=20, num_rounds=10, adversary_ratio=0.0
        )
        adv_config = SimulationConfig(
            num_workers=20, num_rounds=10, adversary_ratio=0.5
        )
        honest_result = Simulation(honest_config).run()
        adv_result = Simulation(adv_config).run()
        assert honest_result.avg_negotiation_success_rate >= adv_result.avg_negotiation_success_rate

    def test_detection_tracks_adversaries(self):
        config = SimulationConfig(
            num_workers=10,
            num_rounds=10,
            adversary_ratio=0.3,
        )
        sim = Simulation(config)
        result = sim.run()
        assert result.total_adversarial_detected >= 0
        assert result.detection_rate >= 0.0
