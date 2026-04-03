# AgentMesh Stack Layer Mapping

How libp2p-v4-swap-agents implements each layer of the AgentMesh Stack.

For the full source code, see the [main repository](https://github.com/Patrick-Ehimen/libp2p-v4-swap-agents).

---

## Layer 1: Communication Layer

**Implementation:** rust-libp2p 0.54

- **Gossipsub** with two topics:
  - `v4-swap-agents` -- swaps, coordination messages, identity attestations
  - `v4-swap-intents` -- pre-trade signals with price bounds
- **mDNS** for automatic local peer discovery
- **TCP + QUIC** dual transports
- **Noise** encryption for all connections
- **Message validation** via `validate_messages()` -- Accept/Reject pipeline feeds P4 scoring

**Key file:** `agent/src/network.rs` (155 LOC)

### How It Works

On startup, each agent creates a libp2p swarm with gossipsub and mDNS behaviours. Peers discover each other via mDNS on the local network (or via manual `dial` command). All structured messages (swaps, intents, proposals, identity attestations) are serialized as JSON and published over gossipsub. The gossipsub config enables `validate_messages()`, meaning every incoming message must be explicitly accepted or rejected -- rejected messages trigger P4 scoring penalties.

---

## Layer 2: Policy Extraction

**Implementation:** Swap intents with price bounds + reputation thresholds

- `intent <amount> <direction> [min_price] [max_price]` -- extracts trading policy from user input
- `cswap <amount> <direction> --min-rep <score>` -- extracts minimum reputation policy
- `propose <amount> <direction> <seek_amount> --min-rep <score>` -- extracts counterparty reputation requirement
- Structured `SwapIntent` messages broadcast over gossipsub before execution (PendingSwap pattern)

**Key file:** `agent/src/main.rs` (intent/cswap command parsing)

### How It Works

When a user issues a command, the CLI parser extracts structured policies: trading parameters (amount, direction, price bounds) and trust requirements (minimum reputation score). These policies are encoded into gossipsub messages (`SwapIntent`, `SwapProposal`) that other agents can evaluate. The PendingSwap pattern ensures policies reach peers 500ms before execution, enabling reactive coordination.

---

## Layer 3: Negotiation Engine

**Implementation:** Propose/Accept/Execute coordination protocol with trust gating

- Agent A broadcasts `SwapProposal` with offer details and minimum reputation threshold
- Agent B evaluates: checks proposer's trust level, verifies own score meets min-rep requirement
- **Trust gating**: proposals from `TrustLevel::Unknown` peers are silently ignored
- Acceptance publishes `SwapAcceptance` message over gossipsub
- State machine: `Pending` -> `Accepted` -> `Filled` | `Expired`
- 60-second proposal expiry with automatic cleanup

**Key file:** `agent/src/coordination.rs` (160 LOC)

### How It Works

The negotiation engine implements a two-party coordination protocol. When Agent A proposes a swap (e.g., "I'll sell 100 TKNA if someone sells me 50 TKNB, minimum reputation 0.3"), the proposal is broadcast to all peers. Agent B receives the proposal, checks:
1. Is the proposer's trust level above Unknown? (trust gating)
2. Does my reputation meet the proposer's minimum? (reputation gate)
3. Do I want this trade? (local decision)

If all checks pass, Agent B broadcasts an acceptance. Agent A sees the acceptance and executes the coordinated swap on-chain.

---

## Layer 4: Protocol Generator

**Implementation:** CoordinationBook with state machine + expiry enforcement

- `SwapProposal` message defines the executable protocol: who swaps what, minimum trust, expiry
- `CoordinationBook` tracks proposal lifecycle with state transitions (`Pending` -> `Accepted` -> `Filled`)
- Expired proposals (60s) trigger penalty (-0.02) on initiator -- enforceable protocol
- Message types form a structured protocol: `SwapProposal`, `SwapAcceptance`, `SwapFill`

**Key file:** `agent/src/coordination.rs` (CoordinationEntry, CoordinationBook)

### How It Works

The `CoordinationBook` acts as a protocol generator by maintaining the state machine for each coordinated swap. Each proposal creates a `CoordinationEntry` with:
- Offer parameters (amount, direction)
- Desired counter-swap parameters
- Minimum reputation requirement
- Expiry timestamp (60 seconds)
- Status tracking (Pending/Accepted/Filled/Expired)

The periodic cleanup cycle (every 30 seconds) enforces protocol rules: expired proposals are removed and initiators receive a reputation penalty, incentivizing follow-through.

---

## Layer 5: Execution Engine

**Implementation:** Multi-mode swap execution via Alloy

- **Live (Sepolia):** Real Uniswap V4 swaps via Alloy RPC to Sepolia
- **Simulation:** Synthetic tx hashes (`0xSIM_...`), no blockchain interaction
- **Local Anvil:** Real swaps against a local Anvil fork of Sepolia, zero cost
- Runtime mode switching: `sim on/off/local`
- **PendingSwap pattern**: intent broadcast -> 500ms flush -> on-chain execution -> result broadcast
- AgentCounterV2 hook provides dynamic fee rebates (0.20% after 5 swaps vs 0.30% base)

**Key files:** `agent/src/uniswap.rs` (200 LOC), `agent/src/sim.rs` (50 LOC)

### How It Works

The execution engine supports three modes via an `ExecutionMode` enum backed by `AtomicU8` for thread-safe runtime switching:
1. **Live**: Constructs a real Uniswap V4 swap transaction using Alloy, sends it to Sepolia, and broadcasts the confirmed tx hash.
2. **Simulate**: Generates a deterministic synthetic tx hash without any RPC call -- useful for demos and testing coordination logic.
3. **Local**: Points RPC to `localhost:8545` (Anvil), executing real swap transactions against a local fork at zero gas cost.

In all modes, the PendingSwap pattern ensures peers see the swap intent before the execution result.

---

## Layer 6: Hybrid Infrastructure

**Implementation:** Ethereum + Filecoin

### Ethereum (Sepolia)
- **Uniswap V4 PoolManager** -- core DEX infrastructure
- **AgentCounter V1** -- tracks swap counts per pool, emits `AgentSwap` events
- **AgentCounterV2** -- decodes agent EOA from hookData, provides dynamic fee rebates after 5 swaps
- **4 deployed contracts** on Sepolia with verified transactions
- Foundry-based deployment pipeline: salt mining -> CREATE2 deploy -> pool creation -> liquidity provision

### Filecoin (Calibration testnet)
- **Node.js sidecar** wrapping `@filoz/synapse-sdk`
- Archives `SwapExecuted` logs and `IdentityAttestation` records
- `archive` command flushes in-memory buffer to Filecoin, returns PieceCID
- `retrieve <pieceCid>` fetches archived data
- Browser view at `http://localhost:3001/view/<pieceCid>`

**Key files:** `contracts/src/AgentCounterV2.sol`, `sidecar/index.js`, `agent/src/archival.rs` (160 LOC)

---

## Cross-Cutting: Trust

Trust spans all 6 layers as the project's core contribution to AgentMesh:

| Layer | Trust Component |
|-------|----------------|
| 1. Communication | Gossipsub peer scoring (P4 invalid messages, P5 app score, P7 behaviour) |
| 2. Policy Extraction | Reputation thresholds encoded in intents and proposals |
| 3. Negotiation | Trust gating -- Unknown peers blocked from coordination |
| 4. Protocol Generator | Min-rep requirements enforced in protocol rules |
| 5. Execution | Conditional execution based on counterparty trust level |
| 6. Infrastructure | On-chain reputation via hook swap counts, Filecoin archival for audit |

### Composite Reputation Scoring

Four-factor weighted model:

| Factor | Weight | Calculation |
|--------|--------|-------------|
| Swap Count | 0.40 | `min(swap_count, 50) / 50` |
| Identity Verified | 0.20 | 1.0 if PeerId <-> EOA verified, 0.0 otherwise |
| Follow-Through Rate | 0.25 | `swap_count / intent_count` |
| Recency | 0.15 | `2^(-hours_since_last_swap / 24)` -- 24h half-life |

**Penalty deductions** (capped at 0.5 total):
- Invalid message: -0.05
- Unfollowed intent: -0.03
- Expired proposal: -0.02

### Trust Levels

| Level | Score Range | Access |
|-------|-------------|--------|
| Unknown | <= 0.0 | Blocked from proposals |
| Low | <= 0.3 | Messaging only |
| Medium | <= 0.6 | Full coordination |
| High | <= 0.85 | Full coordination |
| Trusted | > 0.85 | Full coordination |

---

## Comparison with Other AgentMesh Examples

| Aspect | This Project | p2p-payment-agents | AgentMesh-Stack-Tempo |
|--------|-------------|-------------------|----------------------|
| Language | Rust | Python | Python |
| libp2p impl | rust-libp2p 0.54 | py-libp2p | py-libp2p |
| On-chain | Uniswap V4 (Sepolia) | EIP-3009 USDC (Hardhat) | MPP (Tempo) |
| Reputation | 4-factor composite | N/A | N/A |
| Coordination | Propose/Accept/Execute | Buy/Offer/Pay | Signal buy/sell |
| Archival | Filecoin (Synapse SDK) | N/A | SQLite |
| Peer Scoring | P4/P5/P7 | N/A | N/A |
| Tests | 119 (109 Rust + 10 Sol) | Integration tests | DHT tests |
