"""Metrics collection, aggregation, and report generation."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from src.simulation import RoundResult, SimulationConfig, SimulationResult


class MetricsCollector:
    def __init__(self):
        self.results: list[SimulationResult] = []

    def add_result(self, result: SimulationResult):
        self.results.append(result)

    def summary_table(self) -> list[dict]:
        rows = []
        for r in self.results:
            rows.append({
                "scenario": r.scenario_name,
                "adversary_ratio": r.config.adversary_ratio,
                "rounds": len(r.rounds),
                "tasks_proposed": r.total_tasks_proposed,
                "tasks_completed": r.total_tasks_completed,
                "completion_rate": f"{r.completion_rate:.1%}",
                "detection_rate": f"{r.detection_rate:.1%}",
                "false_positive_rate": f"{r.false_positive_rate:.1%}",
                "avg_negotiation_success": f"{r.avg_negotiation_success_rate:.1%}",
                "true_adversarial": r.true_adversarial_count,
                "detected": r.total_adversarial_detected,
                "false_positives": r.total_false_positives,
            })
        return rows

    def comparative_analysis(self) -> dict:
        if len(self.results) < 2:
            return {}
        baseline = next(
            (r for r in self.results if r.scenario_name == "honest-baseline"), None
        )
        if not baseline:
            return {}
        analysis = {}
        for r in self.results:
            if r.scenario_name == "honest-baseline":
                continue
            analysis[r.scenario_name] = {
                "completion_rate_delta": r.completion_rate - baseline.completion_rate,
                "detection_rate_vs_random": r.detection_rate - r.config.adversary_ratio,
                "false_positive_rate_delta": r.false_positive_rate - baseline.false_positive_rate,
            }
        return analysis

    def export_json(self, path: str | Path):
        data = {
            "summary": self.summary_table(),
            "comparative_analysis": self.comparative_analysis(),
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)


def format_report(result: SimulationResult) -> str:
    lines = [
        f"== Scenario: {result.scenario_name} ==",
        f"  Config: {result.config.num_workers} workers, "
        f"{result.config.adversary_ratio:.0%} adversarial, "
        f"{result.config.num_rounds} rounds",
        f"  Tasks: {result.total_tasks_completed} completed / "
        f"{result.total_tasks_failed} failed / "
        f"{result.total_tasks_proposed} proposed",
        f"  Completion Rate:    {result.completion_rate:.1%}",
        f"  Detection Rate:     {result.detection_rate:.1%}",
        f"  False Positive Rate: {result.false_positive_rate:.1%}",
        f"  Negotiation Success: {result.avg_negotiation_success_rate:.1%}",
        "",
        f"  Round Details:",
    ]
    for r in result.rounds:
        lines.append(
            f"    Round {r.round_number:2d}: "
            f"proposed={r.tasks_proposed:2d} "
            f"negotiated={r.tasks_negotiated:2d} "
            f"completed={r.tasks_completed:2d} "
            f"failed={r.tasks_failed:2d} "
            f"detected={r.adversarial_detected:2d} "
            f"fp={r.false_positives:2d} "
            f"avg_rep={r.avg_reputation:.2f}"
        )
    lines.append(f"  Top reputations:")
    top = [(pid, s) for pid, s in sorted(
        result.final_reputation_scores.items(),
        key=lambda x: x[1], reverse=True
    )[:5]]
    for pid, score in top:
        lines.append(f"    {pid}: {score:.3f}")
    lines.append("== END ==")
    return "\n".join(lines)
