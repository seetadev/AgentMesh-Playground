import os
import sys
from pathlib import Path

import multiaddr
import trio

from libp2p import new_host
from libp2p.network.stream.exceptions import StreamEOF
from libp2p.network.stream.net_stream import INetStream
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.tools.async_service import background_trio_service

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.identity import load_or_create_identity
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
from common.db import init_alpha_db, log_alpha_tx
from common.logging_config import setup_logging

log = setup_logging("alpha")

RECIPIENT = os.environ.get("ALPHA_RECIPIENT", "")

db = None


async def signal_handler(stream: INetStream) -> None:
    peer_id = str(stream.muxed_conn.peer_id)
    asset = "UNKNOWN"
    try:
        # Step 1: Receive signal request
        request = await recv_msg(stream)
        asset = request.get("asset", "UNKNOWN")
        log.info(f"Signal request from {peer_id[:20]}... for {asset}")

        # Step 2: Send MPP payment challenge
        challenge = create_challenge(recipient=RECIPIENT)
        log.info(f"Payment required: {challenge.request['amount']} base units")
        await send_msg(stream, {"type": "challenge", **challenge_to_dict(challenge)})

        # Step 3: Receive payment credential
        cred_msg = await recv_msg(stream)
        if cred_msg.get("type") != "credential":
            await send_msg(stream, {"type": "error", "message": "Expected credential"})
            log_alpha_tx(db, peer_id=peer_id, asset=asset, status="failed", error="No credential received")
            return

        credential = credential_from_dict(cred_msg)
        payer = credential.source or "unknown"
        log.info(f"Payment from {payer}")

        # Step 4: Verify payment on-chain
        receipt = await verify_payment(credential, challenge.request)
        tx_hash = receipt.reference
        log.info(f"Payment verified: {tx_hash}")

        # Step 5: Fetch market data and generate signal
        log.info(f"Fetching market data for {asset}...")

        quote = await get_quote(asset)

        rsi, macd, sma = [], [], []
        try:
            await trio.sleep(15)
            rsi = await get_rsi(asset)
        except Exception as e:
            log.warning(f"RSI fetch skipped: {e}")
        try:
            await trio.sleep(15)
            macd = await get_macd(asset)
        except Exception as e:
            log.warning(f"MACD fetch skipped: {e}")
        try:
            await trio.sleep(15)
            sma = await get_sma(asset)
        except Exception as e:
            log.warning(f"SMA fetch skipped: {e}")

        signal_data = generate_signal(quote, rsi, macd, sma)

        signal = {
            "type": "signal",
            "receipt": tx_hash,
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
        log.info(f"Signal delivered: {asset} -> {signal_data['direction']} ({signal_data['confidence']}%)")

        log_alpha_tx(
            db, peer_id=peer_id, asset=asset, tx_hash=tx_hash,
            amount=challenge.request["amount"], payer_address=payer,
            direction=signal_data["direction"], confidence=signal_data["confidence"],
            status="success",
        )

    except StreamEOF:
        log.warning(f"Stream closed by {peer_id[:20]}...")
        log_alpha_tx(db, peer_id=peer_id, asset=asset, status="failed", error="Stream closed")
    except Exception as e:
        log.error(f"Error: {e}")
        log_alpha_tx(db, peer_id=peer_id, asset=asset, status="failed", error=str(e))
        try:
            await send_msg(stream, {"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await stream.close()


async def chat_handler(stream: INetStream) -> None:
    peer_id = str(stream.muxed_conn.peer_id)
    try:
        request = await recv_msg(stream)
        message = request.get("message", "")
        log.info(f"Chat from {peer_id[:20]}...: {message[:80]}")

        response = await chat(message)
        await send_msg(stream, {"type": "chat_response", "message": response})
        log.info(f"Chat response sent")

    except StreamEOF:
        log.warning(f"Chat stream closed by {peer_id[:20]}...")
    except Exception as e:
        log.error(f"Chat error: {e}")
        try:
            await send_msg(stream, {"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await stream.close()


async def run(port: int, bootstrap: list[str] | None) -> None:
    global db

    if not RECIPIENT:
        log.error("Set ALPHA_RECIPIENT env var to your Tempo wallet address")
        sys.exit(1)

    db = init_alpha_db()
    log.info("Database initialized")

    key_pair = load_or_create_identity("alpha_agent")
    listen_addr = [multiaddr.Multiaddr(f"/ip4/127.0.0.1/tcp/{port}")]

    host = new_host(key_pair=key_pair)
    async with host.run(listen_addrs=listen_addr), trio.open_nursery() as nursery:
        nursery.start_soon(host.get_peerstore().start_cleanup_task, 60)

        host.set_stream_handler(SIGNAL_PROTOCOL_ID, signal_handler)
        host.set_stream_handler(CHAT_PROTOCOL_ID, chat_handler)

        if bootstrap:
            from libp2p.peer.peerinfo import info_from_p2p_addr
            for addr in bootstrap:
                try:
                    info = info_from_p2p_addr(multiaddr.Multiaddr(addr))
                    host.get_peerstore().add_addrs(info.peer_id, info.addrs, 3600)
                    await host.connect(info)
                    log.info(f"Connected to bootstrap peer: {info.peer_id}")
                except Exception as e:
                    log.warning(f"Failed to connect to bootstrap {addr}: {e}")

        dht = KadDHT(host, DHTMode.SERVER)
        for pid in host.get_peerstore().peer_ids():
            await dht.routing_table.add_peer(pid)

        async with background_trio_service(dht):
            await dht.provide(DHT_PROVIDER_KEY)
            log.info(f"Advertised on DHT as '{DHT_PROVIDER_KEY}'")

            peer_id = host.get_id().to_string()
            log.info(f"PeerID: {peer_id}")
            log.info(f"Recipient: {RECIPIENT}")
            log.info(f"Listening: /ip4/127.0.0.1/tcp/{port}/p2p/{peer_id}")
            log.info(f"Protocols: {SIGNAL_PROTOCOL_ID} (paid), {CHAT_PROTOCOL_ID} (free)")
            log.info("Ready for connections")

            await trio.sleep_forever()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Alpha Agent - sells financial signals")
    parser.add_argument("-p", "--port", type=int, default=9000, help="listen port")
    parser.add_argument("--bootstrap", type=str, nargs="*", help="Bootstrap peer multiaddrs")
    args = parser.parse_args()

    try:
        trio.run(run, args.port, args.bootstrap)
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
