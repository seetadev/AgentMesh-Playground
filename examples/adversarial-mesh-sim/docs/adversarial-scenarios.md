# Adversarial Scenarios

This document describes each adversarial scenario in detail, including the
threat model, expected impact, and how the AgentMesh Stack counters it.

## Honest Baseline

All agents behave honestly. Establishes baseline metrics for comparison.

- **Threat**: None (control group)
- **Expected**: High completion rate, low latency, zero false positives
- **Countermeasures**: N/A — validates simulation correctness

## Sybil Infiltration

Adversarial agents create multiple fake identities to sway consensus,
inflate prices, and outvote honest peers in reputation systems.

- **Threat**: Identity-based subversion
- **Expected**: Reputation scores initially volatile; detection engine
  identifies sybils by behavioral clustering
- **Countermeasures**: GossipSub peer scoring, proof-of-work handshake,
  reputation convergence via Bayesian filtering

## Collusive Bidding

A cartel of adversarial agents coordinates to fix prices, exclude honest
agents from negotiations, and split profits.

- **Threat**: Economic subversion
- **Expected**: Price deviation spikes; honest agents get priced out;
  detection flags correlated bidding patterns
- **Countermeasures**: Price anomaly detection, negotiation history
  analysis, random agent selection

## Flood DoS

Adversaries send a high volume of messages (spam offers, repeated
negotiation requests) to exhaust peer resources and drown out legitimate
traffic.

- **Threat**: Resource exhaustion
- **Expected**: Message rates spike; latency increases; honest agents
  may time out
- **Countermeasures**: Per-peer rate limiting, GossipSub flood score
  decay, message queue backpressure

## Eclipse Attack

Adversaries control the routing tables / peer connections of honest
agents, selectively dropping or misdirecting their messages.

- **Threat**: Network-level isolation
- **Expected**: Specific honest agents become unresponsive; their
  reputation drops unfairly; detection identifies connectivity asymmetry
- **Countermeasures**: DHT redundancy, multiple bootstrap peers,
  connection diversity monitoring

## Mixed Adversarial

A combination of all adversarial strategies at once. Tests end-to-end
resilience of the AgentMesh Stack under realistic multi-vector attacks.

- **Threat**: Combined arms
- **Expected**: Degraded but functional system; best-effort task
  completion with graceful degradation
- **Countermeasures**: All of the above, plus circuit breakers and
  escalation to human-in-the-loop
