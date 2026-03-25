# AP2 Standard Payment Demo (HTTP/A2A)

A faithful implementation of Google's **Agent Payments Protocol (AP2)** using the standard A2A (Agent-to-Agent) protocol over HTTP/JSON-RPC. This serves as the **centralized baseline** to compare against the [P2P version](../ap2_payment_agents/) built with py-libp2p.

## What is AP2?

AP2 is Google's open protocol that enables AI agents to securely make payments on behalf of humans. It solves: *"When an AI agent clicks 'Buy', how do we prove the user actually authorized it?"*

AP2 uses three **cryptographically signed mandates** (Verifiable Digital Credentials):

| Mandate | Purpose | Signed By |
|---------|---------|-----------|
| **IntentMandate** | Captures user's purchase intent in natural language | User's device |
| **CartMandate** | Merchant-signed guarantee of items and price | Merchant |
| **PaymentMandate** | Payment authorization for the payment network | User's device |

## Architecture

```
┌──────────────┐    HTTP/JSON-RPC    ┌──────────────────┐
│  Shopping     │◄──────────────────►│  Merchant A       │
│  Agent        │                    │  (QuickShoot)     │──┐
│  (port 8000)  │                    │  (port 8001)      │  │
│               │    HTTP/JSON-RPC    ├──────────────────┤  │ HTTP
│               │◄──────────────────►│  Merchant B       │  │
│               │                    │  (Premium Films)  │  │
│               │                    │  (port 8002)      │──┤
│               │                    └──────────────────┘  │
│               │    HTTP/JSON-RPC    ┌──────────────────┐  │
│               │◄──────────────────►│  Credentials      │  │
│               │                    │  Provider         │  │
│               │                    │  (port 8003)      │  │
└──────────────┘                    └──────────────────┘  │
                                     ┌──────────────────┐  │
                                     │  Payment          │◄─┘
                                     │  Processor        │
                                     │  (port 8004)      │
                                     └──────────────────┘
```

| Agent | AP2 Role | Port | Responsibility |
|-------|----------|------|----------------|
| Shopping Agent | `shopper` | 8000 | Orchestrates the full purchase flow |
| Merchant A | `merchant` | 8001 | QuickShoot Studios - $350 videography |
| Merchant B | `merchant` | 8002 | Premium Films - $450 videography |
| Credentials Provider | `credentials-provider` | 8003 | Digital wallet - payment methods/tokens |
| Payment Processor | `payment-processor` | 8004 | Validates mandates, authorizes payments |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full demo (launches all 5 agents automatically)
python quick_start.py
```

## The Payment Flow

```
Step 1:  DISCOVERY      Shopping Agent fetches AgentCards from all agents
Step 2:  SETUP          User connects to Credentials Provider (digital wallet)
Step 3:  INTENT         IntentMandate sent to both merchants concurrently
Step 4:  CART           Merchants return signed CartMandates ($350 vs $450)
Step 5:  SELECTION      QuickShoot selected (cheapest), Premium Films rejected
Step 6:  PAYMENT METHOD Token obtained from Credentials Provider (VISA 4242)
Step 7:  USER APPROVAL  Cart presented on trusted surface, user approves
Step 8:  MANDATE        PaymentMandate created and signed with SD-JWT-VC
Step 9:  EXECUTION      PaymentMandate sent to Merchant -> Payment Processor
Step 10: CHALLENGE      OTP challenge (3D Secure equivalent) if required
Step 11: ESCROW         Funds held in escrow pending delivery
Step 12: DELIVERY       Service completed, escrow released
Step 13: RECEIPT        PaymentReceipt confirms successful payment
```

## Manual Execution (5 Terminals)

```bash
# Terminal 1 - Payment Processor
python -m agents.payment_processor --port 8004

# Terminal 2 - Credentials Provider
python -m agents.credentials_provider --port 8003

# Terminal 3 - Merchant A
python -m agents.merchant_agent --port 8001 --name "QuickShoot Studios" --price 350

# Terminal 4 - Merchant B
python -m agents.merchant_agent --port 8002 --name "Premium Films" --price 450

# Terminal 5 - Shopping Agent (starts the flow)
python -m agents.shopping_agent --budget 400 \
    --merchants http://localhost:8001 http://localhost:8002 \
    --credentials-provider http://localhost:8003
```

## Running Tests

```bash
python integration_test.py
# 18 assertions covering AgentCards, CartMandates, payment flow, etc.
```

## Side-by-Side Comparison with P2P Version

This is the key value proposition — the same payment flow on two architectures:

| Aspect | Standard AP2 (this project) | P2P AP2 (`ap2_payment_agents/`) |
|--------|----------------------------|----------------------------------|
| **Transport** | HTTP/JSON-RPC 2.0 (A2A) | py-libp2p streams + GossipSub |
| **Discovery** | AgentCards at `/.well-known/agent.json` | Bootstrap node + pubsub subscription |
| **Architecture** | 5 agents, role-based servers | 4 agents, direct peer-to-peer |
| **Trust Model** | Signed mandates (JWT, SD-JWT-VC) | Peer identity + simulated signatures |
| **Data Format** | A2A Messages/Artifacts with DataParts | Length-prefixed JSON over streams |
| **Encryption** | TLS (HTTPS in production) | Noise protocol (built into libp2p) |
| **Single Point of Failure** | Server endpoints | None |
| **Privacy** | Intermediaries route messages | Only participants see data |
| **Standards** | W3C PaymentRequest, A2A, AP2 | AP2-inspired custom protocol |
| **Ecosystem** | Compatible with Visa/Mastercard/PayPal | Decentralized, no network dependency |

### When to Use Which?

| Use Case | Standard AP2 | P2P AP2 |
|----------|-------------|---------|
| Enterprise payments with compliance | Best choice | |
| Integration with existing payment networks | Best choice | |
| Censorship-resistant transactions | | Best choice |
| No central server dependency | | Best choice |
| Regulatory audit trail | Best choice | |
| Privacy-first transactions | | Best choice |

## AP2 Concepts Demonstrated

### AgentCard Discovery
Each agent serves its capabilities at `/.well-known/agent.json`:
```json
{
  "capabilities": {
    "extensions": [{
      "uri": "https://github.com/google-agentic-commerce/ap2/tree/v0.1",
      "params": { "roles": ["merchant"] }
    }]
  }
}
```

### A2A Data Containers
AP2 mandates travel inside A2A Messages and Artifacts:

| AP2 Object | A2A Container | DataPart Key |
|------------|--------------|--------------|
| IntentMandate | Message | `ap2.mandates.IntentMandate` |
| CartMandate | Artifact | `ap2.mandates.CartMandate` |
| PaymentMandate | Message | `ap2.mandates.PaymentMandate` |
| PaymentReceipt | Artifact | `ap2.PaymentReceipt` |

### Simulated Cryptography
- **CartMandate**: Merchant signs with JWT (HS256 in demo, RSA/EC in production)
- **PaymentMandate**: User signs with SD-JWT-VC (simulated with HS256)
- Both include SHA-256 hashes of cart contents for tamper detection

## Project Structure

```
ap2_standard_payment/
├── ap2_types/              # AP2 Pydantic models (mandates, W3C types, receipts)
├── a2a_helpers/            # Minimal A2A JSON-RPC server + client + builder
├── agents/                 # 4 agent implementations
│   ├── shopping_agent.py   # Orchestrator (client-only, no server)
│   ├── merchant_agent.py   # Configurable merchant (name, price, port)
│   ├── credentials_provider.py  # Digital wallet simulation
│   └── payment_processor.py     # Payment network simulation
├── agent_cards/            # AgentCard JSON files for each agent
├── signing.py              # Simulated JWT/SD-JWT-VC signing
├── escrow.py               # Escrow hold/release/refund
├── quick_start.py          # One-command launcher
├── integration_test.py     # 18-assertion test suite
├── requirements.txt
└── pyproject.toml
```

## Dependencies

- `pydantic` >= 2.0 — AP2 type models
- `httpx` >= 0.27 — Async HTTP client for A2A communication
- `starlette` >= 0.37 — ASGI framework for agent servers
- `uvicorn` >= 0.29 — ASGI server
- `PyJWT` >= 2.8 — Simulated mandate signing

No LLM API keys required. No real payment processing. Fully self-contained.

## References

- [AP2 Protocol](https://github.com/google-agentic-commerce/AP2) — Official specification
- [A2A Protocol](https://a2a-protocol.org) — Agent-to-Agent communication
- [W3C Payment Request API](https://www.w3.org/TR/payment-request/) — Payment data structures
- [P2P Version](../ap2_payment_agents/) — Our decentralized alternative
