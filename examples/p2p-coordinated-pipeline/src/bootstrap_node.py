"""
Bootstrap Node — AgentMesh Full-Stack Demo

A well-known entry point for the AgentMesh network. It:
  - Listens on a stable address so other agents can find it
  - Forms the initial GossipSub mesh
  - Maintains a DHT routing table for peer discovery
  - Periodically broadcasts a mesh summary for situational awareness

Usage:
    python -m src.bootstrap_node --port 9000

The printed multiaddr should be passed to all other agents via --bootstrap.
"""

import json
import logging
import time

import multiaddr
import trio
from libp2p import new_host
from libp2p.custom_types import TProtocol
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.stream_muxer.mplex.mplex import MPLEX_PROTOCOL_ID, Mplex
from libp2p.tools.async_service.trio_service import background_trio_service

from .common.config import Config, load_config
from .common.identity import load_or_create_identity
from .common.messages import ANNOUNCE_TOPIC, GOSSIPSUB_PROTOCOL_ID, DHT_PROVIDER_KEY

log = logging.getLogger(__name__)

BOOTSTRAP_ADDR_FILE = ".bootstrap_addr"


async def run(
    port: int,
    ready_event: trio.Event | None = None,
    cfg: Config | None = None,
) -> tuple[str, str]:
    """
    Start the bootstrap node.

    Returns:
        (peer_id, full_addr) for use by other agents.
    """
    if cfg is None:
        cfg = load_config()

    key_pair = load_or_create_identity("bootstrap")
    listen_maddr = multiaddr.Multiaddr(f"/ip4/{cfg.listen_ip}/tcp/{port}")

    host = new_host(key_pair=key_pair, muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
    gossipsub = GossipSub(
        protocols=[TProtocol(GOSSIPSUB_PROTOCOL_ID)],
        degree=3,
        degree_low=2,
        degree_high=4,
        time_to_live=60,
        gossip_window=2,
        gossip_history=5,
        heartbeat_initial_delay=0.5,
        heartbeat_interval=2,
    )
    pubsub = Pubsub(host=host, router=gossipsub)

    async with host.run(listen_addrs=[listen_maddr]):
        async with background_trio_service(pubsub), background_trio_service(gossipsub):
            await pubsub.wait_until_ready()

            peer_id = host.get_id().pretty()
            full_addr = f"/ip4/{cfg.listen_ip}/tcp/{port}/p2p/{peer_id}"

            log.info(f"[Bootstrap] Started  PeerID={peer_id[:20]}...")
            log.info(f"[Bootstrap] Address: {full_addr}")

            # Subscribe to announce topic to observe the mesh
            subscription = await pubsub.subscribe(ANNOUNCE_TOPIC)

            # Write address file so workers/coordinator can auto-discover
            import os
            if os.path.exists(BOOTSTRAP_ADDR_FILE):
                log.info(f"[Bootstrap] Overwriting stale {BOOTSTRAP_ADDR_FILE}")
            with open(BOOTSTRAP_ADDR_FILE, "w") as f:
                f.write(full_addr)
            log.info(f"[Bootstrap] Address written to {BOOTSTRAP_ADDR_FILE}")

            # DHT server mode — helps route peer lookups
            dht = KadDHT(host, DHTMode.SERVER)
            async with background_trio_service(dht):
                log.info("[Bootstrap] DHT server running")

                if ready_event is not None:
                    ready_event.set()

                async def _log_announcements() -> None:
                    while True:
                        msg = await subscription.get()
                        try:
                            data = json.loads(msg.data.decode())
                            name = data.get("worker_name", data.get("name", "?"))
                            mtype = data.get("type", "?")
                            log.info(f"[Bootstrap] GossipSub: type={mtype} from={name}")
                        except Exception:
                            pass

                async def _broadcast_mesh() -> None:
                    while True:
                        await trio.sleep(cfg.mesh_broadcast_interval)
                        connected = len(list(host.get_connected_peers()))
                        summary = json.dumps({
                            "type": "mesh_summary",
                            "connected_peers": connected,
                            "timestamp": time.time(),
                        }).encode()
                        await pubsub.publish(ANNOUNCE_TOPIC, summary)
                        log.info(f"[Bootstrap] Mesh summary: {connected} connected peer(s)")

                async with trio.open_nursery() as nursery:
                    nursery.start_soon(_log_announcements)
                    nursery.start_soon(_broadcast_mesh)

    return peer_id, full_addr


def main() -> None:
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()
    parser = argparse.ArgumentParser(description="AgentMesh Bootstrap Node")
    parser.add_argument("--port", type=int, default=cfg.bootstrap_port, help="TCP listen port")
    args = parser.parse_args()

    log.info(f"[Bootstrap] Starting on port {args.port}...")
    try:
        trio.run(run, args.port, None, cfg)
    except KeyboardInterrupt:
        log.info("[Bootstrap] Shutting down.")
    else:
        # Print prominent banner after run() returns (Ctrl+C path won't reach here)
        try:
            with open(BOOTSTRAP_ADDR_FILE) as f:
                addr = f.read().strip()
            print(f"\n{'─' * 60}")
            print(f"  Bootstrap address: {addr}")
            print(f"  (written to {BOOTSTRAP_ADDR_FILE})")
            print(f"{'─' * 60}\n")
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    main()
