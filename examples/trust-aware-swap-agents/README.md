# Trust-Aware Swap Agents (rust-libp2p + Uniswap V4)

Rust libp2p agents coordinating Uniswap V4 swaps on Sepolia testnet with trust-aware networking primitives.

**Main Repository:** [github.com/Patrick-Ehimen/libp2p-v4-swap-agents](https://github.com/Patrick-Ehimen/libp2p-v4-swap-agents)

> This is a **rust-libp2p** implementation demonstrating all 6 layers of the AgentMesh Stack applied to DeFi agent coordination. Built as part of the PLDG Cohort 7 programme.

## AgentMesh Stack Layer Mapping

| # | Layer | Implementation | Key Files |
|---|-------|----------------|-----------|
| 1 | **Communication** | rust-libp2p 0.54: Gossipsub (2 topics) + mDNS + TCP/QUIC + Noise | `agent/src/network.rs` |
| 2 | **Policy Extraction** | Swap intents with price bounds, reputation thresholds from CLI | `agent/src/main.rs` |
| 3 | **Negotiation Engine** | Propose/Accept/Execute coordination protocol with trust gating | `agent/src/coordination.rs` |
| 4 | **Protocol Generator** | CoordinationBook state machine with expiry enforcement | `agent/src/coordination.rs` |
| 5 | **Execution Engine** | 3 modes: Sepolia, Anvil fork, Simulation via Alloy | `agent/src/uniswap.rs`, `agent/src/sim.rs` |
| 6 | **Hybrid Infrastructure** | Uniswap V4 hooks (Ethereum) + Filecoin archival (Synapse SDK) | `contracts/`, `sidecar/`, `agent/src/archival.rs` |

See [AGENTMESH_MAPPING.md](./AGENTMESH_MAPPING.md) for a detailed per-layer breakdown.

## Architecture

```
                         libp2p Gossipsub Network
                    (v4-swap-agents + v4-swap-intents)

  ┌──────────────────────┐           ┌──────────────────────┐
  │      Agent A         │◄─────────►│      Agent B         │
  │                      │           │                      │
  │  ┌────────────────┐  │  Identity │  ┌────────────────┐  │
  │  │ Identity       │  │  Binding  │  │ Identity       │  │
  │  │ (EIP-191)      │◄─┼──────────┼─►│ (EIP-191)      │  │
  │  ├────────────────┤  │           │  ├────────────────┤  │
  │  │ Reputation     │  │  Intents  │  │ Reputation     │  │
  │  │ Store          │◄─┼──────────┼─►│ Store          │  │
  │  ├────────────────┤  │           │  ├────────────────┤  │
  │  │ Coordination   │  │ Proposals │  │ Coordination   │  │
  │  │ Book           │◄─┼──────────┼─►│ Book           │  │
  │  ├────────────────┤  │           │  ├────────────────┤  │
  │  │ Peer Scoring   │  │  Scoring  │  │ Peer Scoring   │  │
  │  │ (P4/P5/P7)     │◄─┼──────────┼─►│ (P4/P5/P7)     │  │
  │  └────────────────┘  │           │  └────────────────┘  │
  └──────────┬───────────┘           └──────────┬───────────┘
             │                                  │
             │         Swap Execution           │
             ▼                                  ▼
  ┌─────────────────────────────────────────────────────────┐
  │              Uniswap V4 PoolManager (Sepolia)           │
  │  ┌───────────────────┐  ┌───────────────────────────┐   │
  │  │ AgentCounter (V1) │  │ AgentCounterV2            │   │
  │  │ - Swap tracking   │  │ - hookData agent tracking │   │
  │  │ - Event emission  │  │ - Dynamic fee rebates     │   │
  │  └───────────────────┘  └───────────────────────────┘   │
  └─────────────────────────────────────────────────────────┘
             │
             ▼
  ┌─────────────────────┐
  │ Filecoin (Calibra.) │  Archival via Synapse SDK sidecar
  │ - Swap logs         │
  │ - Identity proofs   │
  └─────────────────────┘
```

## Features

### Execution Modes
- **Live (Sepolia)** -- Real on-chain transactions
- **Simulation** (`--simulate`) -- Synthetic tx hashes, no `.env` needed
- **Local Anvil** (`--simulate --local`) -- Real swaps against local fork, zero cost
- Runtime toggle: `sim on/off/local`

### Swap Intent Gossip
- Dedicated gossipsub topic (`v4-swap-intents`) for pre-trade coordination
- **PendingSwap pattern**: intent broadcast -> 500ms swarm flush -> swap execution
- Peers see `[INTENT]` before `[SWAP]`, enabling counter-swaps and coordination

### Identity Binding (PeerId <-> EOA)
- EIP-191 `personal_sign` links libp2p PeerId to Ethereum address
- Attestations auto-exchanged on peer connection via gossipsub
- Signature verification prevents Sybil attacks

### Reputation Scoring
- **Composite score** with 4 weighted factors: swap count (40%), identity verified (20%), follow-through rate (25%), recency (15%)
- **Trust levels**: Unknown, Low, Medium, High, Trusted
- **Misbehavior penalties**: invalid messages (-0.05), unfollowed intents (-0.03), expired proposals (-0.02)

### Conditional & Coordinated Swaps
- Reputation-gated swap execution with `cswap` command
- **Propose/Accept/Execute** protocol for multi-agent coordination
- Trust-gated: proposals from Unknown peers are silently ignored
- 60-second expiry with automatic cleanup

### Gossipsub Peer Scoring
- **P4** -- Invalid message deliveries penalty (weight: -10.0)
- **P5** -- Application-specific score fed by composite reputation
- **P7** -- Behaviour penalty for general misbehavior (weight: -1.0)
- 30-second periodic score refresh cycle

### Filecoin Archival
- Node.js sidecar wrapping `@filoz/synapse-sdk` (Calibration testnet)
- Archives swap logs and identity attestations to Filecoin
- CLI retrieval and browser view

## Project Structure

```
trust-aware-swap-agents/
├── agent/                     # Rust libp2p agent
│   ├── Cargo.toml
│   └── src/
│       ├── main.rs            # Event loop, CLI commands, message handling
│       ├── network.rs         # Gossipsub + mDNS, peer scoring params
│       ├── identity.rs        # PeerId <-> EOA identity binding (EIP-191)
│       ├── reputation.rs      # Composite scoring, trust levels, penalties
│       ├── coordination.rs    # Multi-agent swap coordination protocol
│       ├── uniswap.rs         # On-chain swap client (Alloy)
│       ├── archival.rs        # LogEntry, LogArchiver, Filecoin archival
│       ├── sim.rs             # ExecutionMode enum, synthetic tx hashes
│       ├── cli.rs             # clap CLI parser
│       └── tests/             # Unit tests (109 passing)
├── contracts/                 # Foundry - Uniswap V4 Hooks
│   ├── src/
│   │   ├── AgentCounter.sol   # V1 hook (swap tracking, events)
│   │   └── AgentCounterV2.sol # V2 hook (dynamic fees, hookData tracking)
│   ├── script/                # Deployment scripts
│   └── test/                  # Solidity tests (10 passing)
├── sidecar/                   # Node.js Synapse SDK sidecar
│   ├── index.js               # Express: /upload, /retrieve, /view
│   └── package.json
├── documentation/             # Architecture diagrams, technical writeup
├── AGENTMESH_MAPPING.md       # Detailed 6-layer mapping
└── README.md                  # This file
```

## Quick Start

### Prerequisites
- [Rust](https://rustup.rs/) 1.75+
- [Foundry](https://book.getfoundry.sh/getting-started/installation) (for contracts)
- Node.js 18+ (for Filecoin sidecar, optional)

### Simulation Mode (no `.env` needed)

```bash
# Terminal 1 -- Agent A
cd agent && cargo run -- --simulate

# Terminal 2 -- Agent B (use TCP port from Agent A's output)
cd agent && cargo run -- --simulate /ip4/127.0.0.1/tcp/<PORT>
```

### Demo Walkthrough

```
# 1. Identity (automatic on connect)
> who                           # Show your PeerId + EOA
> peers                         # List verified peers with trust levels

# 2. Build reputation
> swap 1                        # Execute swap, build reputation
> swap 1
> swap 1
> reputation                    # Check scores (~0.50 Medium)

# 3. Conditional swap
> cswap 1 a2b --min-rep 0.9    # REJECTED (score too low)
> cswap 1 a2b --min-rep 0.3    # OK (conditions met)

# 4. Coordinated swap
Terminal 1: propose 1 a2b 1 b2a --min-rep 0.0
Terminal 2: accept <proposal_id>
Terminal 1: proposals           # Check lifecycle -> Filled
```

## Commands

| Command | Description |
|---------|-------------|
| `swap <amount>` | Swap TKNA -> TKNB (V1 pool) |
| `swap-b <amount>` | Swap TKNB -> TKNA (V1 pool) |
| `swap-v2 <amount>` | Swap TKNA -> TKNB (V2 pool, fee rebates) |
| `swap-v2-b <amount>` | Swap TKNB -> TKNA (V2 pool, fee rebates) |
| `cswap <amount> <a2b\|b2a> [options]` | Conditional swap (reputation-gated) |
| `intent <amount> <a2b\|b2a> [min] [max]` | Broadcast swap intent |
| `propose <amt> <dir> <seek_amt> [--min-rep]` | Propose coordinated swap |
| `accept <proposal-id>` | Accept a peer's swap proposal |
| `proposals` | List active swap proposals |
| `reputation [peer]` | Show peer reputation scores and trust levels |
| `sim on\|off\|local` | Set execution mode |
| `archive` | Flush log buffer to Filecoin |
| `retrieve <pieceCid>` | Retrieve archived data from Filecoin |
| `who` | Show your PeerId and linked EOA |
| `peers` | List all verified peer identities + trust |

## Deployed Contracts (Sepolia)

| Contract | Address |
|----------|---------|
| AgentCounter Hook (V1) | [`0x5D4505AA950a73379B8E9f1116976783Ba8340C0`](https://sepolia.etherscan.io/address/0x5D4505AA950a73379B8E9f1116976783Ba8340C0) |
| AgentCounterV2 Hook | [`0xA8760B755c67c5C75A8A60ED7E3713eA2448D0C0`](https://sepolia.etherscan.io/address/0xA8760B755c67c5C75A8A60ED7E3713eA2448D0C0) |
| Token A (TKNA) | [`0x7546360e0011Bb0B52ce10E21eF0E9341453fE71`](https://sepolia.etherscan.io/address/0x7546360e0011Bb0B52ce10E21eF0E9341453fE71) |
| Token B (TKNB) | [`0xF6d91478e66CE8161e15Da103003F3BA6d2bab80`](https://sepolia.etherscan.io/address/0xF6d91478e66CE8161e15Da103003F3BA6d2bab80) |

## Test Coverage

109 Rust tests + 10 Solidity tests:

| Module | Tests |
|--------|-------|
| Reputation (scoring, penalties, trust) | 34 |
| Network (gossipsub, topics, P4/P5/P7) | 18 |
| Uniswap (pool keys, V1/V2) | 24 |
| Coordination (proposals, lifecycle) | 10 |
| Simulation (modes, tx hashes) | 9 |
| Archival (log entries, buffer) | 8 |
| Identity (EIP-191, registry) | 6 |
| Solidity (AgentCounter V1 + V2) | 10 |

## Demo Videos

- **Full project demo (4 min):** [youtu.be/3olYjewulnw](https://youtu.be/3olYjewulnw)
- **Cycle 3 -- Trust & Coordination (2 min):** [youtu.be/eU90uIPyoCE](https://youtu.be/eU90uIPyoCE)

## Build & Test

```bash
# Rust agent
cd agent
cargo build
cargo test          # 109 tests
cargo clippy        # Zero warnings

# Solidity contracts (requires Foundry + git submodules)
cd contracts
forge install       # Install v4-core, v4-periphery, etc.
forge build
forge test          # 10 tests
```

## License

MIT
