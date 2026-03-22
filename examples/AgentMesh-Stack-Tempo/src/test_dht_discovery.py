"""
DHT Test Network — 6 persistent nodes.

Spins up and keeps running:
  1 bootstrap node (port 10000) — DHT relay, no services
  4 dummy nodes (ports 10001-10004) — join DHT, don't provide anything
  1 Alpha Agent (port 10005) — advertises "aoin-signal-v1", handles signals & chat

Use the bootstrap node address to connect the Trading Agent from another terminal:
  python3 src/trading_agent/main.py --bootstrap /ip4/127.0.0.1/tcp/10000/p2p/<bootstrap_peer_id> chat "hello"

Run:
  python3 src/test_dht_discovery.py
"""

import os
import secrets
import sys
from pathlib import Path

import trio
from multiaddr import Multiaddr

from libp2p import new_host
from libp2p.crypto.ed25519 import create_new_key_pair
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.tools.async_service import background_trio_service
from libp2p.network.stream.exceptions import StreamEOF
from libp2p.network.stream.net_stream import INetStream

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.protocol import (
    SIGNAL_PROTOCOL_ID, CHAT_PROTOCOL_ID, DHT_PROVIDER_KEY,
    send_msg, recv_msg,
)
from common.payment import (
    create_challenge,
    challenge_to_dict,
    credential_from_dict,
    verify_payment,
)
from common.llm import chat
from common.alpha_vantage import get_quote, get_rsi, get_macd, get_sma, generate_signal


BOOTSTRAP_PORT = 10000
DUMMY_PORTS = [10001, 10002, 10003, 10004]
ALPHA_PORT = 10005

RECIPIENT = os.environ.get("ALPHA_RECIPIENT", "")


def make_key():
    return create_new_key_pair(secrets.token_bytes(32))


async def connect_to(host, addr_str):
    from libp2p.peer.peerinfo import info_from_p2p_addr
    info = info_from_p2p_addr(Multiaddr(addr_str))
    host.get_peerstore().add_addrs(info.peer_id, info.addrs, 3600)
    await host.connect(info)
    return info.peer_id


# ── Alpha Agent handlers (same as alpha_agent/main.py) ──────────

async def signal_handler(stream: INetStream) -> None:
    peer_id = stream.muxed_conn.peer_id
    try:
        request = await recv_msg(stream)
        print(f"[Alpha] Signal request from {peer_id}: {request}")

        challenge = create_challenge(recipient=RECIPIENT)
        print(f"[Alpha] Payment required: {challenge.request['amount']} base units")
        await send_msg(stream, {"type": "challenge", **challenge_to_dict(challenge)})

        cred_msg = await recv_msg(stream)
        if cred_msg.get("type") != "credential":
            await send_msg(stream, {"type": "error", "message": "Expected credential"})
            return

        credential = credential_from_dict(cred_msg)
        print(f"[Alpha] Received payment from {credential.source}")

        receipt = await verify_payment(credential, challenge.request)
        print(f"[Alpha] Payment verified: {receipt.reference}")

        asset = request.get("asset", "UNKNOWN")
        print(f"[Alpha] Fetching market data for {asset}...")

        quote = await get_quote(asset)
        rsi, macd, sma = [], [], []
        try:
            await trio.sleep(15)
            rsi = await get_rsi(asset)
        except Exception as e:
            print(f"[Alpha] RSI fetch skipped: {e}")
        try:
            await trio.sleep(15)
            macd = await get_macd(asset)
        except Exception as e:
            print(f"[Alpha] MACD fetch skipped: {e}")
        try:
            await trio.sleep(15)
            sma = await get_sma(asset)
        except Exception as e:
            print(f"[Alpha] SMA fetch skipped: {e}")

        signal_data = generate_signal(quote, rsi, macd, sma)

        signal = {
            "type": "signal",
            "receipt": receipt.reference,
            "asset": asset,
            "price": quote["price"],
            "direction": signal_data["direction"],
            "confidence": signal_data["confidence"],
            "expiry": signal_data["expiry"],
            "reasons": signal_data["reasons"],
            "indicators": signal_data["indicators"],
            "source": "alpha-agent-v1",
        }
        await send_msg(stream, signal)
        print(f"[Alpha] Signal delivered: {asset} -> {signal_data['direction']} ({signal_data['confidence']}%)")

    except StreamEOF:
        print(f"[Alpha] Stream closed by {peer_id}")
    except Exception as e:
        print(f"[Alpha] Error: {e}")
        try:
            await send_msg(stream, {"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await stream.close()


async def chat_handler(stream: INetStream) -> None:
    peer_id = stream.muxed_conn.peer_id
    try:
        request = await recv_msg(stream)
        message = request.get("message", "")
        print(f"[Alpha] Chat from {peer_id}: {message[:80]}...")

        response = await chat(message)
        await send_msg(stream, {"type": "chat_response", "message": response})
        print(f"[Alpha] Chat response sent to {peer_id}")

    except StreamEOF:
        print(f"[Alpha] Chat stream closed by {peer_id}")
    except Exception as e:
        print(f"[Alpha] Chat error: {e}")
        try:
            await send_msg(stream, {"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await stream.close()


# ── Node runners ─────────────────────────────────────────────────

async def run_bootstrap(bootstrap_ready, bootstrap_addr):
    host = new_host(key_pair=make_key())
    listen = [Multiaddr(f"/ip4/127.0.0.1/tcp/{BOOTSTRAP_PORT}")]

    async with host.run(listen_addrs=listen), trio.open_nursery() as nursery:
        nursery.start_soon(host.get_peerstore().start_cleanup_task, 60)
        dht = KadDHT(host, DHTMode.SERVER)

        async with background_trio_service(dht):
            peer_id = host.get_id().to_string()
            addr = f"/ip4/127.0.0.1/tcp/{BOOTSTRAP_PORT}/p2p/{peer_id}"
            bootstrap_addr.append(addr)

            print(f"[Bootstrap]  {peer_id[:20]}...  port={BOOTSTRAP_PORT}")
            bootstrap_ready.set()
            await trio.sleep_forever()


async def run_dummy(port, bootstrap_addr, ready):
    host = new_host(key_pair=make_key())
    listen = [Multiaddr(f"/ip4/127.0.0.1/tcp/{port}")]

    async with host.run(listen_addrs=listen), trio.open_nursery() as nursery:
        nursery.start_soon(host.get_peerstore().start_cleanup_task, 60)
        await connect_to(host, bootstrap_addr[0])

        dht = KadDHT(host, DHTMode.SERVER)
        for pid in host.get_peerstore().peer_ids():
            await dht.routing_table.add_peer(pid)

        async with background_trio_service(dht):
            peer_id = host.get_id().to_string()
            print(f"[Dummy-{port}] {peer_id[:20]}...  port={port}")
            ready.set()
            await trio.sleep_forever()


async def run_alpha(bootstrap_addr, alpha_ready):
    if not RECIPIENT:
        print("[Alpha] WARNING: ALPHA_RECIPIENT not set, payment verification will fail")

    host = new_host(key_pair=make_key())
    listen = [Multiaddr(f"/ip4/127.0.0.1/tcp/{ALPHA_PORT}")]

    async with host.run(listen_addrs=listen), trio.open_nursery() as nursery:
        nursery.start_soon(host.get_peerstore().start_cleanup_task, 60)
        await connect_to(host, bootstrap_addr[0])

        host.set_stream_handler(SIGNAL_PROTOCOL_ID, signal_handler)
        host.set_stream_handler(CHAT_PROTOCOL_ID, chat_handler)

        dht = KadDHT(host, DHTMode.SERVER)
        for pid in host.get_peerstore().peer_ids():
            await dht.routing_table.add_peer(pid)

        async with background_trio_service(dht):
            await dht.provide(DHT_PROVIDER_KEY)

            peer_id = host.get_id().to_string()
            print(f"[Alpha]      {peer_id[:20]}...  port={ALPHA_PORT}  *** SIGNAL PROVIDER ***")
            alpha_ready.set()
            await trio.sleep_forever()


async def main():
    print()
    print("=" * 65)
    print("  AOIN DHT Test Network")
    print("  6 nodes: 1 bootstrap + 4 dummy + 1 alpha agent")
    print("=" * 65)
    print()

    bootstrap_ready = trio.Event()
    bootstrap_addr = []
    alpha_ready = trio.Event()
    dummy_ready_events = [trio.Event() for _ in DUMMY_PORTS]

    async with trio.open_nursery() as nursery:
        # 1. Bootstrap
        nursery.start_soon(run_bootstrap, bootstrap_ready, bootstrap_addr)
        await bootstrap_ready.wait()

        # 2. Dummy nodes
        for i, port in enumerate(DUMMY_PORTS):
            nursery.start_soon(run_dummy, port, bootstrap_addr, dummy_ready_events[i])
        for ev in dummy_ready_events:
            await ev.wait()

        # 3. Alpha Agent
        nursery.start_soon(run_alpha, bootstrap_addr, alpha_ready)
        await alpha_ready.wait()

        print()
        print("=" * 65)
        print("  Network ready! All 6 nodes running.")
        print()
        print("  Connect the Trading Agent from another terminal:")
        print(f"  python3 src/trading_agent/main.py \\")
        print(f"    --bootstrap {bootstrap_addr[0]} \\")
        print(f"    chat \"What is RSI?\"")
        print("=" * 65)
        print()

        await trio.sleep_forever()


if __name__ == "__main__":
    try:
        trio.run(main)
    except KeyboardInterrupt:
        print("\n[Network] Shutting down all nodes.")
