# p2p-coordinated-pipeline — libp2p Multi-Agent Coordination

A complete, runnable example of the **AgentMesh Stack** — a modular, layered architecture for secure decentralised multi-agent coordination using **libp2p**.

This example demonstrates all six layers of the stack in a single coherent pipeline: agents discover each other, health-check candidates, extract policies from a task description, negotiate assignments with session-token authorization, generate an executable protocol with parallel-aware dependency ordering, and execute it in a verifiable, chained data-flow with pluggable on-chain attestation.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       AgentMesh Stack                           │
├─────────────────────────────────────────────────────────────────┤
│  Layer 1 — Communication (libp2p)                               │
│    • Kademlia DHT peer discovery                                 │
│    • GossipSub capability announcements (pub/sub mesh)           │
│    • Noise/TLS encryption on all streams (automatic)            │
│    • Mplex stream multiplexing                                   │
│    • Health-check ping/pong before negotiation                  │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2 — Policy Extraction                                     │
│    • Natural-language task → structured PolicySet                │
│    • Keyword-based OR LLM-powered (Anthropic API) detection      │
│    • Builds constraints: budget, latency, quality, format        │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3 — Negotiation Engine                                    │
│    • Policy-aware multi-agent coordination                       │
│    • Direct libp2p streams: NegotiateRequest → Offer → Ack       │
│    • Protocol version handshake with NegotiateReject on mismatch │
│    • Hard constraints (non-negotiable) as adversarial filters    │
│    • Counter-offer support with tolerance bounds                 │
│    • HMAC session tokens minted per step, delivered in Ack       │
├─────────────────────────────────────────────────────────────────┤
│  Layer 4 — Protocol Generator                                    │
│    • Negotiated assignments → ExecutionProtocol                  │
│    • DAG dependency graph (DATA_VALIDATION→ANALYTICS→REPORT)     │
│    • Topological sort: same-wave steps run in parallel           │
│    • Data-flow chaining: each step's output → next step's input  │
│    • Content-addressable hash (on-chain attestation ready)       │
├─────────────────────────────────────────────────────────────────┤
│  Layer 5 — Execution Engine                                      │
│    • Dispatches steps to workers over direct libp2p streams      │
│    • Topological wave execution: parallel steps run concurrently │
│    • Session token verification before every execution step      │
│    • Per-step SHA-256 integrity hashes                           │
├─────────────────────────────────────────────────────────────────┤
│  Layer 6 — Hybrid Infrastructure                                 │
│    • Primary: off-chain via libp2p (fast, fully P2P)             │
│    • Pluggable attestation backends (Local / RPC / Filecoin FEVM)│
│    • Result persistence: results/<protocol_id>.json             │
│    • Observability: PipelineMetrics + trace-tagged logging       │
│    • Graceful shutdown: HandlerCounter drains in-flight requests │
└─────────────────────────────────────────────────────────────────┘
```

---

## Demo Scenario: Data Processing Pipeline

A coordinator receives a task: *"Validate, analyze, and report on a dataset."*

The AgentMesh Stack handles everything:

```
Coordinator (Layer 2)
  → Extracts: needs DATA_VALIDATION + ANALYTICS + REPORT_GENERATION
  → Policies: budget=$0.10, latency≤5000ms, format=json, quality=standard

Coordinator (Layer 1 — health checks + discovery)
  → Pings each candidate worker; skips unresponsive peers
  → Discovered DataValidator   (data_validation) @ $0.01  ✓ alive
  → Discovered AnalyticsEngine (analytics)        @ $0.02  ✓ alive
  → Discovered ReportWriter    (report_generation)@ $0.015 ✓ alive

Coordinator (Layer 3 — version-checked negotiation)
  → Protocol v1.0 accepted by all workers
       DataValidator:   offer $0.01  ✓  score=147.8
       AnalyticsEngine: offer $0.02  ✓  score=141.3
       ReportWriter:    offer $0.015 ✓  score=144.7
  → Session tokens minted per step (HMAC-SHA256)

Coordinator (Layer 4 — DAG protocol)
  → Protocol generated: proto_a3f7b2c1
       wave=1. [data_validation]   → DataValidator    (entry)
       wave=2. [analytics]         → AnalyticsEngine  ← step_1_data_validation
       wave=3. [report_generation] → ReportWriter     ← step_2_analytics

Coordinator (Layer 5 + 6 — wave execution + attestation)
  → Wave 1: DataValidator validates 20 rows ✓  [attested]
  → Wave 2: AnalyticsEngine computes stats  ✓  [attested]
  → Wave 3: ReportWriter formats report     ✓  [attested]
  → Results saved to results/proto_a3f7b2c1.json
```

### Parallel Wave Demo

Request only `DATA_VALIDATION` + `REPORT_GENERATION` (skip `ANALYTICS`). Because
`REPORT_GENERATION`'s normal dependency (`ANALYTICS`) is absent, both capabilities
become independent entry points and are assigned `wave=1` — the execution engine
runs them concurrently:

```
wave=1. [data_validation]    → DataValidator     (entry)  [parallel wave]
wave=1. [report_generation]  → ReportWriter      (entry)  [parallel wave]
```

---

## Project Structure

```
p2p-coordinated-pipeline/
├── src/
│   ├── common/
│   │   ├── messages.py          # All Pydantic message types + wire framing
│   │   ├── identity.py          # Persistent Ed25519 PeerID management
│   │   ├── config.py            # Typed config (env vars / config.toml)
│   │   ├── auth.py              # HMAC-SHA256 session token mint + verify
│   │   ├── health.py            # Health-check client (ping_worker)
│   │   ├── observability.py     # PipelineMetrics dataclass + TraceAdapter
│   │   ├── persistence.py       # Save pipeline results to results/ dir
│   │   └── shutdown.py          # HandlerCounter for graceful shutdown
│   ├── layers/
│   │   ├── policy.py            # Layer 2: PolicyExtractor
│   │   ├── negotiation.py       # Layer 3: NegotiationEngine
│   │   ├── protocol_gen.py      # Layer 4: ProtocolGenerator (DAG-based)
│   │   ├── execution.py         # Layer 5+6: ExecutionEngine (wave-parallel)
│   │   └── attestation.py       # Layer 6: Attestation backends
│   ├── bootstrap_node.py        # Bootstrap: DHT + GossipSub entry point
│   ├── worker_agent.py          # Worker: capabilities, negotiation, execution
│   ├── coordinator_agent.py     # Coordinator: orchestrates all 6 layers
│   └── demo.py                  # All-in-one demo launcher
├── results/                     # Auto-created: pipeline result JSON files
├── keys/                        # Auto-generated persistent agent identities
├── requirements.txt
├── .env.example
└── README.md
```

---

## Protocols

| Protocol | Type | Description |
|----------|------|-------------|
| `/agentmesh/negotiate/v1` | Direct stream | NegotiateRequest → NegotiateOffer/Reject → NegotiateAck |
| `/agentmesh/execute/v1` | Direct stream | ExecuteStep (with session token) → ExecuteResult/Error |
| `/agentmesh/health/v1` | Direct stream | HealthPing → HealthPong (liveness check before negotiation) |
| `agentmesh-announce` | GossipSub topic | Worker capability announcements (heartbeat every 5 s) |
| `agentmesh-worker-v1` | DHT key | Worker provider registration for DHT discovery fallback |

---

## Setup

### Prerequisites

- Python 3.12+

### Install

```bash
cd examples/p2p-coordinated-pipeline
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment (optional)

```bash
cp .env.example .env
# Edit .env to set AGENTMESH_TOKEN_SECRET and optional attestation vars
```

---

## Usage

### All-in-one Demo (recommended)

Launches bootstrap + 3 workers + coordinator in one process:

```bash
cd examples/p2p-coordinated-pipeline
python3 -m src.demo
```

With a custom task:
```bash
python3 -m src.demo --task "Validate and analyze a dataset, then generate a summary report"
python3 -m src.demo --task "Analyze data and produce a report" --budget 0.05
```

### Multi-terminal Mode

Run each agent in a separate terminal to observe the inter-agent communication:

**Terminal 1 — Bootstrap:**
```bash
python3 -m src.bootstrap_node --port 9000
# Copy the printed multiaddr: /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>
```

**Terminal 2 — Validation Worker:**
```bash
python3 -m src.worker_agent \
    --name "DataValidator" --port 9001 \
    --capability data_validation --cost 0.01 \
    --bootstrap /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>
```

**Terminal 3 — Analytics Worker:**
```bash
python3 -m src.worker_agent \
    --name "AnalyticsEngine" --port 9002 \
    --capability analytics --cost 0.02 \
    --bootstrap /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>
```

**Terminal 4 — Report Worker:**
```bash
python3 -m src.worker_agent \
    --name "ReportWriter" --port 9003 \
    --capability report_generation --cost 0.015 --quality premium \
    --bootstrap /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>
```

**Terminal 5 — Coordinator (after workers are up):**
```bash
python3 -m src.coordinator_agent \
    --task "Validate, analyze, and report on a dataset" \
    --port 9004 \
    --bootstrap /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>
```

---

## Expected Output

```
════════════════════════════════════════════════════════════
  AgentMesh Full-Stack Demo
════════════════════════════════════════════════════════════
  Task : Validate, analyze, and report on a dataset
  ID   : a3f7b2c1
  Caps : ['data_validation', 'analytics', 'report_generation']
  Policies:
    max_budget_usd = 0.1  (negotiable)
    max_latency_ms = 5000  (negotiable)
    output_format = json  (fixed)
    quality_tier = standard  (negotiable)

────────────────────────────────────────────────────────────
  Layer 1 — Discovering Workers
────────────────────────────────────────────────────────────
  Waiting 8s for GossipSub announcements...
  Discovered DataValidator (data_validation) @ $0.01
  Discovered AnalyticsEngine (analytics) @ $0.02
  Discovered ReportWriter (report_generation) @ $0.015
  Discovered 3 worker(s) across 3 capability group(s)

────────────────────────────────────────────────────────────
  Layer 3 — Negotiation Engine
────────────────────────────────────────────────────────────
  Capability: data_validation (1 candidate)
    [health] DataValidator ✓
    DataValidator accepted protocol v1.0  score=147.8
    → Selected: DataValidator

  Capability: analytics (1 candidate)
    [health] AnalyticsEngine ✓
    AnalyticsEngine accepted protocol v1.0  score=141.3
    → Selected: AnalyticsEngine

  Capability: report_generation (1 candidate)
    [health] ReportWriter ✓
    ReportWriter accepted protocol v1.0  score=144.7
    → Selected: ReportWriter

────────────────────────────────────────────────────────────
  Layer 4 — Protocol Generator
────────────────────────────────────────────────────────────
  Protocol ID : proto_a3f7b2c1
  Hash        : 8f3a2c1d4e5b6f7a
  Steps       : 3
    wave=1. [data_validation]   worker=DataValidator    (entry)
    wave=2. [analytics]         worker=AnalyticsEngine  ← step_1_data_validation
    wave=3. [report_generation] worker=ReportWriter     ← step_2_analytics

────────────────────────────────────────────────────────────
  Layer 5 — Execution Engine  (Layer 6: Hybrid Verification)
────────────────────────────────────────────────────────────
  Executing 3 step(s)...

  ✓ Step 1: [data_validation]  worker=DataValidator
    Execution time : 312ms
    Integrity hash : 4a8b2c3d
    Validation     : PASSED ✓
    Rows           : 20/20 valid

  ✓ Step 2: [analytics]  worker=AnalyticsEngine
    Execution time : 305ms
    Integrity hash : 9f1e5a2b
    Count=20  mean=98.7432  std=14.8821  min=71.34  max=128.67

  ✓ Step 3: [report_generation]  worker=ReportWriter
    Execution time : 301ms
    Integrity hash : 2c7d4e8f

────────────────────────────────────────────────────────────
# AgentMesh Pipeline Report

## Overview
This report was produced by a fully decentralised multi-agent pipeline
coordinated over libp2p...

## Statistical Summary
- Count:       20 records
- Mean:        98.7432
- Std Dev:     14.8821
- Min:         71.34
- Max:         128.67
────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════
  Pipeline Complete
════════════════════════════════════════════════════════════
  Steps completed : 3
  Total time      : 918ms
  All integrity checks passed ✓
  Results saved   : results/proto_a3f7b2c1.json
```

---

## py-libp2p Features Used

| Feature | How it's used |
|---------|---------------|
| `new_host` | Create hosts with unique Ed25519 identities |
| `Noise` encryption | Automatic on all streams (zero-config) |
| `Mplex` muxer | Multiplex negotiate / execute / health streams per connection |
| `GossipSub` | Worker capability announcements on `agentmesh-announce` topic |
| `KadDHT` | DHT provider registration and lookup (`agentmesh-worker-v1`) |
| `set_stream_handler` | Register handlers for negotiate, execute, and health protocols |
| `new_stream` | Open direct streams from coordinator to workers |
| `info_from_p2p_addr` | Parse multiaddrs for peer connection |

---

## Security Model

| Mechanism | Implementation |
|-----------|----------------|
| Transport encryption | Noise protocol on every libp2p stream |
| Step authorization | HMAC-SHA256 session tokens (`AGENTMESH_TOKEN_SECRET`) |
| Protocol versioning | `protocol_version` field in Negotiate messages; workers reject unknown versions |
| Liveness verification | Health ping before every negotiation attempt |

Session tokens are minted by the `ProtocolGenerator` (one per step), delivered to the worker in the `NegotiateAck`, and re-presented by the coordinator in every `ExecuteStep`. Workers verify the token with `hmac.compare_digest` before executing.

---

## Observability & Persistence

**Metrics** (`PipelineMetrics`) are collected throughout the pipeline:
- discovery, negotiation, and execution phase durations
- per-step execution times
- health ping success / failure counts
- negotiation accepted / rejected counts

All metrics and step results are saved to `results/<protocol_id>.json` at the end of every successful run.

Logging uses `TraceAdapter` which prefixes every log line with a short `[task_id]` so multi-pipeline traces are easy to filter.

---

## Attestation Backends (Layer 6)

The execution engine supports three pluggable backends controlled by environment variables:

| Backend | Env vars required | Behaviour |
|---------|-------------------|-----------|
| `LocalHashBackend` | _(none)_ | In-memory records; default |
| `RPCAttestationBackend` | `ATTESTATION_RPC_URL` | JSON-RPC `attest` call after every step; falls back to local on error |
| `FilecoinFEVMBackend` | `FEVM_RPC_URL` + `FEVM_CONTRACT` + `FEVM_PRIVATE_KEY` | Submits step hash to a deployed FVM actor (requires `web3`) |

See `.env.example` for configuration details and `src/layers/attestation.py` for the extension point comments.

---

## Extending This Example

### Add a new worker capability

1. Add the capability to `WorkerCapability` enum in `messages.py`
2. Add detection keywords to `policy.py`
3. Add data-dependency entries to `_STAGE_DATA_DEPS` in `protocol_gen.py`
4. Add an executor function in `worker_agent.py` and register it in `_EXECUTORS`

### Enable LLM-powered policy extraction

Set `ANTHROPIC_API_KEY` and extend `PolicyExtractor.extract()` in `policy.py`
to call the Anthropic API for richer capability detection.

### Enable on-chain attestation

Set `FEVM_RPC_URL`, `FEVM_CONTRACT`, and `FEVM_PRIVATE_KEY` in your `.env` file. The `FilecoinFEVMBackend` in `src/layers/attestation.py` has inline comments showing exactly which `web3.py` calls to uncomment.

### Add a new attestation backend

Subclass `AttestationBackend` in `src/layers/attestation.py`, implement `attest()`, and add a selection branch to `build_attestation_backend()`.

---

## License

MIT
