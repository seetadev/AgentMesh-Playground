# Adversarial Multi-Agent Simulation

> Stress-test your P2P agent network against malicious behavior.

A simulation framework for evaluating **AgentMesh Stack** resilience under adversarial conditions. Run configurable scenarios with honest and malicious agents to measure how well negotiation, reputation, and communication hold up under attack.

## Why This Matters

All existing AgentMesh examples assume cooperative, honest agents. In production, decentralized agent networks face:

- **Sybil attacks** — fake identities overwhelming the network
- **Eclipse attacks** — isolating honest peers from the network
- **Collusion** — coordinated dishonest behavior during negotiation
- **Flooding / DoS** — resource exhaustion via message storms
- **False reporting** — lying about work completion or quality

This framework lets you quantify resilience before deploying.

## Scenarios

| Scenario | Adversarial Strategy | What It Tests |
|---|---|---|
| `honest-baseline` | None (all honest) | Baseline negotiation latency & success rate |
| `sybil-infiltration` | 30% Sybil agents | Peer discovery & reputation filtering |
| `eclipse-attack` | Adversary controls routing | DHT resilience & redundant connectivity |
| `collusive-bidding` | Malicious agents collude on price | Negotiation engine & scoring fairness |
| `flood-dos` | Message storm from adversarial peers | GossipSub peer scoring & rate limiting |
| `mixed-adversarial` | Random mix of all strategies | End-to-end system robustness |

## Quick Start

```bash
cd examples/adversarial-mesh-sim
pip install -r requirements.txt

# Run the baseline (all honest agents)
python simulate.py --scenario honest-baseline

# Run with sybil infiltration
python simulate.py --scenario sybil-infiltration

# Run all scenarios and generate a report
python simulate.py --all --report
```

## Simulation Output

Each run produces:

- **Negotiation success rate** — % of deals completed
- **Average negotiation latency** — time to reach agreement
- **Reputation convergence** — how quickly dishonest agents are identified
- **Detection rate** — % of adversarial agents flagged before damage
- **False positive rate** — honest agents incorrectly flagged
- **Protocol integrity** — % of workflows executed without deviation

## Architecture

```
src/
├── simulate.py             ← Entry point: CLI runner
├── simulation.py           ← Engine: orchestrates agent lifecycle & rounds
├── agent.py                ← Agent base + HonestAgent + adversarial variants
├── reputation.py           ← Trust scoring engine (Bayesian + gossip-based)
├── detection.py            ← Anomaly detection & adversarial classification
├── reporting.py            ← Metrics collection, aggregation & report generation
└── strategies.py           ← Adversarial behavior strategies
```

## Extending

Add a new adversarial strategy in `strategies.py`:

```python
@register_strategy("my-attack")
class MyAttackStrategy(AdversarialStrategy):
    def on_negotiate(self, ctx: NegotiationContext) -> NegotiationAction:
        # Your malicious logic here
        ...
```

Then run it:

```bash
python simulate.py --scenario my-attack --adversary-ratio 0.25
```

## Key Metrics

| Metric | Description |
|---|---|
| Utility vs Security Score | Trade-off between task completion and security |
| Negotiation Latency | Time to reach agreement under attack |
| Cost Efficiency | Resources consumed vs tasks completed |
| Robustness | % of honest agents that complete successfully |
| Generalization | Performance across different adversarial types |
