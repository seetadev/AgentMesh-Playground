import os
import sys
from pathlib import Path

import multiaddr
import trio

from libp2p import new_host
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.tools.async_service import background_trio_service

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.identity import load_or_create_identity
from common.protocol import (
    SIGNAL_PROTOCOL_ID, CHAT_PROTOCOL_ID, DHT_PROVIDER_KEY,
    send_msg, recv_msg,
)
from common.payment import (
    challenge_from_dict,
    create_credential,
    credential_to_dict,
)
from common.db import init_trader_db, log_trader_tx
from common.logging_config import setup_logging

log = setup_logging("trader")

PRIVATE_KEY = os.environ.get("TRADER_PRIVATE_KEY", "")

db = None


async def buy_signal(host, peer_id, asset: str) -> None:
    if not PRIVATE_KEY:
        log.error("Set TRADER_PRIVATE_KEY env var for paid signals")
        return

    alpha_peer = str(peer_id)

    try:
        stream = await host.new_stream(peer_id, [SIGNAL_PROTOCOL_ID])
    except Exception as e:
        log.error(f"Failed to open stream: {e}")
        log_trader_tx(db, alpha_peer_id=alpha_peer, asset=asset, status="failed", error=f"Stream open failed: {e}")
        return

    try:
        # Step 1: Send signal request
        request = {"asset": asset, "type": "options_signal"}
        log.info(f"Requesting signal for {asset}...")
        await send_msg(stream, request)

        # Step 2: Receive payment challenge
        challenge_msg = await recv_msg(stream)
        if challenge_msg.get("type") == "error":
            error = challenge_msg.get("message")
            log.error(f"Error: {error}")
            log_trader_tx(db, alpha_peer_id=alpha_peer, asset=asset, status="failed", error=error)
            await stream.close()
            return

        challenge = challenge_from_dict(challenge_msg)
        amount_base = int(challenge.request["amount"])
        amount_human = amount_base / 1_000_000
        log.info(f"Payment required: ${amount_human:.2f} USD")

        # Step 3: Sign and send payment credential
        log.info("Signing transaction...")
        credential = await create_credential(challenge, PRIVATE_KEY)
        log.info(f"Payment signed from {credential.source}")
        await send_msg(stream, {"type": "credential", **credential_to_dict(credential)})

        # Step 4: Receive signal
        log.info("Waiting for payment verification...")
        response = await recv_msg(stream)
        await stream.close()

        if response.get("type") == "error":
            error = response.get("message")
            log.error(f"Error: {error}")
            log_trader_tx(db, alpha_peer_id=alpha_peer, asset=asset, status="failed", error=error)
            return

        tx_hash = response.get("receipt")
        direction = response.get("direction")
        confidence = response.get("confidence")
        price = response.get("price")

        log.info(f"Payment confirmed: {tx_hash}")
        print(f"[Trader] Signal received:")
        print(f"  Asset:      {response.get('asset')}")
        print(f"  Price:      ${price}")
        print(f"  Direction:  {direction}")
        print(f"  Confidence: {confidence}%")
        print(f"  Expiry:     {response.get('expiry')}")

        indicators = response.get("indicators", {})
        if indicators:
            print(f"  Indicators:")
            print(f"    RSI:       {indicators.get('rsi')}")
            print(f"    MACD Hist: {indicators.get('macd_histogram')}")
            print(f"    SMA20:     {indicators.get('sma20')}")
            print(f"    Change:    {indicators.get('change_pct')}%")

        reasons = response.get("reasons", [])
        if reasons:
            print(f"  Reasoning:")
            for r in reasons:
                print(f"    - {r}")

        log_trader_tx(
            db, alpha_peer_id=alpha_peer, asset=asset, tx_hash=tx_hash,
            amount=str(amount_base), direction=direction,
            confidence=confidence, price=price, status="success",
        )

    except Exception as e:
        log.error(f"Signal purchase failed: {e}")
        log_trader_tx(db, alpha_peer_id=alpha_peer, asset=asset, status="failed", error=str(e))
        try:
            await stream.close()
        except Exception:
            pass


async def ask_chat(host, peer_id, message: str) -> None:
    try:
        stream = await host.new_stream(peer_id, [CHAT_PROTOCOL_ID])
    except Exception as e:
        log.error(f"Failed to open chat stream: {e}")
        return

    try:
        log.info(f"Asking: {message[:80]}...")
        await send_msg(stream, {"message": message})

        response = await recv_msg(stream)
        await stream.close()

        if response.get("type") == "error":
            log.error(f"Error: {response.get('message')}")
            return

        print(f"[Trader] Response:\n{response.get('message')}")

    except Exception as e:
        log.error(f"Chat failed: {e}")
        try:
            await stream.close()
        except Exception:
            pass


async def discover_alpha_agent(host, dht: KadDHT):
    log.info(f"Searching DHT for '{DHT_PROVIDER_KEY}' providers...")
    providers = await dht.find_providers(DHT_PROVIDER_KEY)

    if not providers:
        log.warning("No Alpha Agents found on DHT")
        return None

    for provider in providers:
        if provider.peer_id == host.get_id():
            continue
        log.info(f"Found Alpha Agent: {provider.peer_id}")
        if provider.peer_id not in host.get_connected_peers():
            try:
                await host.connect(provider)
                log.info("Connected to discovered Alpha Agent")
            except Exception as e:
                log.warning(f"Failed to connect to {provider.peer_id}: {e}")
                continue
        return provider.peer_id

    log.warning("No reachable Alpha Agents found")
    return None


async def run(destination: str | None, bootstrap: str | None,
              mode: str, asset: str, message: str) -> None:
    global db
    db = init_trader_db()
    log.info("Database initialized")

    key_pair = load_or_create_identity("trading_agent")
    listen_addr = [multiaddr.Multiaddr("/ip4/127.0.0.1/tcp/0")]

    host = new_host(key_pair=key_pair)
    async with host.run(listen_addrs=listen_addr), trio.open_nursery() as nursery:
        nursery.start_soon(host.get_peerstore().start_cleanup_task, 60)
        log.info(f"PeerID: {host.get_id().to_string()}")

        peer_id = None

        if destination:
            maddr = multiaddr.Multiaddr(destination)
            info = info_from_p2p_addr(maddr)
            await host.connect(info)
            log.info(f"Connected to Alpha Agent: {info.peer_id}")
            peer_id = info.peer_id
        else:
            if not bootstrap:
                log.error("Need either -d (direct) or --bootstrap (DHT discovery)")
                return

            try:
                info = info_from_p2p_addr(multiaddr.Multiaddr(bootstrap))
                host.get_peerstore().add_addrs(info.peer_id, info.addrs, 3600)
                await host.connect(info)
                log.info(f"Connected to bootstrap peer: {info.peer_id}")
            except Exception as e:
                log.error(f"Failed to connect to bootstrap {bootstrap}: {e}")
                return

            dht = KadDHT(host, DHTMode.CLIENT)
            for pid in host.get_peerstore().peer_ids():
                await dht.routing_table.add_peer(pid)

            async with background_trio_service(dht):
                peer_id = await discover_alpha_agent(host, dht)
                if not peer_id:
                    return

                if mode == "signal":
                    await buy_signal(host, peer_id, asset)
                elif mode == "chat":
                    await ask_chat(host, peer_id, message)
                return

        if mode == "signal":
            await buy_signal(host, peer_id, asset)
        elif mode == "chat":
            await ask_chat(host, peer_id, message)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Trading Agent - buys signals & chats")
    parser.add_argument("-d", "--destination", required=False, default=None,
                        help="Alpha Agent multiaddr (direct connection)")
    parser.add_argument("--bootstrap", type=str,
                        help="Bootstrap peer multiaddr (for DHT discovery)")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    signal_parser = subparsers.add_parser("signal", help="Buy a financial signal (paid)")
    signal_parser.add_argument("-a", "--asset", default="BTC", help="Asset to query")

    chat_parser = subparsers.add_parser("chat", help="Ask a question (free)")
    chat_parser.add_argument("message", help="Your question")

    args = parser.parse_args()

    if not args.destination and not args.bootstrap:
        parser.error("Either -d (direct address) or --bootstrap (DHT discovery) is required")

    try:
        trio.run(
            run,
            args.destination,
            args.bootstrap,
            args.mode,
            getattr(args, "asset", "BTC"),
            getattr(args, "message", ""),
        )
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
