# AOIN — Decentralized Agent-to-Agent Marketplace for Financial Signals

A peer-to-peer system where AI agents trade financial signals. A **Trading Agent** (buyer) discovers an **Alpha Agent** (seller) via Kademlia DHT, pays on-chain via the Machine Payment Protocol (MPP), and receives actionable Long/Short/Neutral signals — all over encrypted libp2p streams.

## Architecture

```
Trading Agent                          Alpha Agent
     |                                      |
     |-- 1. DHT Discovery ----------------->|
     |                                      |
     |-- 2. Signal Request (libp2p) ------->|
     |                                      |
     |<- 3. MPP Payment Challenge ----------|
     |                                      |
     |-- 4. Signed Tempo Transaction ------>|
     |                                      |
     |   [Alpha verifies payment on-chain]  |
     |   [Alpha fetches Alpha Vantage data] |
     |                                      |
     |<- 5. Signal (Long/Short/Neutral) ----|
```

### Layers

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Communication | py-libp2p (Noise + Yamux) | Encrypted P2P streams, Kademlia DHT discovery |
| Settlement | pympp + Tempo Testnet | On-chain micropayments ($0.05/signal) |
| Intelligence | Alpha Vantage API | RSI, MACD, SMA analysis → actionable signals |
| Chat | OpenRouter | Free LLM-powered financial Q&A |

## Setup

### Prerequisites

- Python 3.12+
- Tempo Testnet wallet with funds ([explorer](https://explore.testnet.tempo.xyz))
- Alpha Vantage API key ([free tier](https://www.alphavantage.co/support/#api-key))
- OpenRouter API key (optional, for chat)

### Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install libp2p trio multiaddr "pympp[tempo]" httpx
```

### Configure

```bash
cp .env.example .env
# Edit .env with your keys
```

| Variable | Required | Description |
|----------|----------|-------------|
| `ALPHA_RECIPIENT` | Yes (Alpha) | Tempo wallet address to receive payments |
| `TRADER_PRIVATE_KEY` | Yes (Trader) | Tempo private key for signing payments |
| `ALPHA_VANTAGE_API_KEY` | Yes (Alpha) | Alpha Vantage API key for market data |
| `OPENROUTER_API_KEY` | Optional | OpenRouter key for free chat endpoint |
| `OPENROUTER_MODEL` | Optional | OpenRouter model (e.g. `meta-llama/llama-4-maverick`) |

## Usage

### Direct Connection

**Terminal 1 — Start Alpha Agent:**
```bash
export ALPHA_RECIPIENT=0xYourAddress
export ALPHA_VANTAGE_API_KEY=your_key
python3 src/alpha_agent/main.py -p 9000
```

**Terminal 2 — Buy a signal:**
```bash
export TRADER_PRIVATE_KEY=0xYourKey
python3 src/trading_agent/main.py -d /ip4/127.0.0.1/tcp/9000/p2p/<alpha_peer_id> signal -a AAPL
```

**Free chat (no payment):**
```bash
python3 src/trading_agent/main.py -d /ip4/127.0.0.1/tcp/9000/p2p/<alpha_peer_id> chat "What is RSI?"
```

### DHT Discovery

Instead of hardcoding the Alpha Agent's address, the Trading Agent can discover it via DHT.

**Terminal 1 — Start a test network (1 bootstrap + 4 dummy + 1 alpha):**
```bash
export ALPHA_RECIPIENT=0xYourAddress
export ALPHA_VANTAGE_API_KEY=your_key
python3 src/test_dht_discovery.py
```

**Terminal 2 — Trader discovers Alpha Agent via bootstrap:**
```bash
export TRADER_PRIVATE_KEY=0xYourKey
python3 src/trading_agent/main.py --bootstrap /ip4/127.0.0.1/tcp/10000/p2p/<bootstrap_peer_id> signal -a AAPL
```

The Trader connects to the bootstrap node, queries the DHT for `aoin-signal-v1` providers, and automatically finds the Alpha Agent among all network participants.

## Signal Output

```
[Trader] Signal received:
  Asset:      AAPL
  Price:      $198.45
  Direction:  Long
  Confidence: 72%
  Expiry:     2026-04-05
  Indicators:
    RSI:       42.3
    MACD Hist: 0.8521
    SMA20:     195.20
    Change:    1.2%
  Reasoning:
    - RSI neutral (42.3)
    - MACD bullish & rising
    - Price 1.7% above SMA20
    - Positive momentum (+1.2%)
```

The signal is generated from a weighted scoring system:
- **RSI** (30pts) — oversold/overbought detection
- **MACD Histogram** (30pts) — trend direction and momentum
- **Price vs SMA20** (25pts) — trend confirmation
- **Daily change** (15pts) — short-term momentum

## Transaction History

All transactions are persisted in SQLite (`data/` directory).

```bash
python3 src/view_history.py alpha -n 10    # Alpha Agent transactions
python3 src/view_history.py trader -n 10   # Trader transactions
```

## Project Structure

```
synth-mpp/
├── src/
│   ├── alpha_agent/
│   │   └── main.py              # Alpha Agent — sells signals
│   ├── trading_agent/
│   │   └── main.py              # Trading Agent — buys signals
│   ├── common/
│   │   ├── protocol.py          # Protocol IDs, message framing, constants
│   │   ├── identity.py          # Persistent PeerID generation
│   │   ├── payment.py           # MPP challenge/credential/verification (trio-native)
│   │   ├── alpha_vantage.py     # Market data API + signal generation
│   │   ├── llm.py               # OpenRouter chat client
│   │   ├── db.py                # SQLite persistence
│   │   └── logging_config.py    # Structured logging
│   ├── test_dht_discovery.py    # Multi-node DHT test network
│   └── view_history.py          # Transaction history viewer
├── keys/                        # Persistent agent identities (auto-generated)
├── data/                        # SQLite databases (auto-generated)
├── pympp/                       # pympp SDK (reference)
├── py-libp2p/                   # py-libp2p (reference)
├── pyproject.toml
├── .env.example
└── project.md
```

## Protocols

| Protocol | Type | Description |
|----------|------|-------------|
| `/aoin/signal/v1` | Paid | Financial signal purchase (MPP payment required) |
| `/aoin/chat/v1` | Free | LLM-powered financial Q&A |

## Network Details

| Parameter | Value |
|-----------|-------|
| Chain | Tempo Moderato Testnet |
| Chain ID | 42431 |
| RPC | https://rpc.moderato.tempo.xyz |
| Currency | pathUSD (`0x20c0...0000`) |
| Signal Price | $0.05 USD |
| Explorer | https://explore.testnet.tempo.xyz |

## License

MIT
