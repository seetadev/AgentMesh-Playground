#!/usr/bin/env python3
"""Adversarial Multi-Agent Simulation — CLI entry point.

Usage:
    python simulate.py --scenario honest-baseline
    python simulate.py --scenario sybil-infiltration --adversary-ratio 0.3
    python simulate.py --all --report
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from src.reporting import MetricsCollector, format_report
from src.simulation import Simulation, SimulationConfig


SCENARIOS = {
    "honest-baseline": {
        "adversary_ratio": 0.0,
        "description": "All honest agents - baseline metrics",
    },
    "sybil-infiltration": {
        "adversary_ratio": 0.3,
        "description": "30% Sybil agents inflating identities + prices",
    },
    "collusive-bidding": {
        "adversary_ratio": 0.25,
        "description": "25% colluders fixing prices and excluding honest agents",
    },
    "flood-dos": {
        "adversary_ratio": 0.2,
        "description": "20% flooders launching message-storm DoS",
    },
    "eclipse-attack": {
        "adversary_ratio": 0.2,
        "description": "20% eclipsers dropping honest peer messages",
    },
    "mixed-adversarial": {
        "adversary_ratio": 0.3,
        "description": "30% mixed adversarial strategies (default)",
    },
}


def run_scenario(
    name: str,
    config_override: dict | None = None,
    verbose: bool = True,
) -> SimulationResult:
    scenario = SCENARIOS.get(name, SCENARIOS["mixed-adversarial"])
    params = dict(scenario)
    if config_override:
        params.update(config_override)
    config = SimulationConfig(
        scenario_name=name,
        adversary_ratio=params.get("adversary_ratio", 0.3),
    )
    if verbose:
        print(f"\n  Scenario: {name}")
        print(f"  {params.get('description', '')}")
        print(f"  Adversary ratio: {config.adversary_ratio:.0%}")
        print(f"  Workers: {config.num_workers} "
              f"(adversarial={config.num_adversarial}, "
              f"honest={config.num_honest})")
        print(f"  Rounds: {config.num_rounds}")
    sim = Simulation(config)
    start = time.time()
    result = sim.run()
    elapsed = time.time() - start
    if verbose:
        print(f"  Completed in {elapsed:.2f}s")
        print(format_report(result))
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Adversarial Multi-Agent Simulation for AgentMesh Stack"
    )
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIOS.keys()),
        default="mixed-adversarial",
        help="Which adversarial scenario to simulate",
    )
    parser.add_argument(
        "--adversary-ratio",
        type=float,
        default=None,
        help="Override the ratio of adversarial agents (0.0 to 1.0)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=20,
        help="Number of worker agents (default: 20)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=10,
        help="Number of simulation rounds (default: 10)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all scenarios sequentially",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Export report to JSON",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-round output",
    )
    args = parser.parse_args()

    collector = MetricsCollector()
    config_override = {}
    if args.adversary_ratio is not None:
        config_override["adversary_ratio"] = args.adversary_ratio
    config_override["num_workers"] = args.workers
    config_override["num_rounds"] = args.rounds

    if args.all:
        print(f"\n{'='*60}")
        print("  Running ALL adversarial scenarios")
        print(f"{'='*60}")
        for name in SCENARIOS:
            overrides = dict(config_override)
            overrides["adversary_ratio"] = SCENARIOS[name]["adversary_ratio"]
            result = run_scenario(name, overrides, verbose=not args.quiet)
            collector.add_result(result)
        collector.add_result(run_scenario("honest-baseline", {
            "adversary_ratio": 0.0,
            "num_workers": args.workers,
            "num_rounds": args.rounds,
        }, verbose=not args.quiet))
        print(f"\n{'='*60}")
        print("  COMPARATIVE SUMMARY")
        print(f"{'='*60}")
        for row in collector.summary_table():
            print(
                f"  {row['scenario']:25s} "
                f"completion={row['completion_rate']:>6s}  "
                f"detection={row['detection_rate']:>6s}  "
                f"fp={row['false_positive_rate']:>6s}"
            )
    else:
        result = run_scenario(args.scenario, config_override, verbose=not args.quiet)
        collector.add_result(result)

    if args.report:
        out_path = Path("simulation_report.json")
        collector.export_json(out_path)
        print(f"\n  Report exported to {out_path.resolve()}")


if __name__ == "__main__":
    main()
