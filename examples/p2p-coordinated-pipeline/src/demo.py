"""
AgentMesh Full-Stack Demo — Single-process all-in-one launcher

Starts all agents in a single process using trio nurseries:
  1. Bootstrap node
  2. Worker agents (one per [[workers]] entry in config.toml)
  3. Coordinator agent — starts after workers are ready

The coordinator executes the full AgentMesh Stack pipeline:
  Layer 1 → Layer 2 → Layer 3 → Layer 4 → Layer 5 → Layer 6

Configuration is read from config.toml in the current working directory.
Copy config.toml.example → config.toml to customise ports, costs, timeouts.
Falls back to built-in defaults when config.toml is absent.

Usage:
    python -m src.demo
    python -m src.demo --task "Validate and analyze a dataset, then generate a report"
    python -m src.demo --task "Analyze data and summarize results" --budget 0.05
"""

import argparse
import logging
import os

import trio
from dotenv import load_dotenv

from .bootstrap_node import run as run_bootstrap
from .worker_agent import run as run_worker, WorkerCapability
from .coordinator_agent import run as run_coordinator
from .common.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("demo")


async def main(task: str, budget: float) -> None:
    cfg = load_config()

    # Events for coordination between demo tasks
    bootstrap_ready = trio.Event()
    worker_ready_events = {w.name: trio.Event() for w in cfg.workers}
    coordinator_done = trio.Event()

    bootstrap_addr_holder: list[str] = []

    async def _start_bootstrap() -> None:
        """Start bootstrap node, signal readiness, then serve forever."""
        async def _inner() -> None:
            import multiaddr as ma
            from libp2p import new_host
            from libp2p.custom_types import TProtocol
            from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
            from libp2p.pubsub.gossipsub import GossipSub
            from libp2p.pubsub.pubsub import Pubsub
            from libp2p.stream_muxer.mplex.mplex import MPLEX_PROTOCOL_ID, Mplex
            from libp2p.tools.async_service.trio_service import background_trio_service
            import json, time

            from .common.identity import load_or_create_identity
            from .common.messages import ANNOUNCE_TOPIC, GOSSIPSUB_PROTOCOL_ID, DHT_PROVIDER_KEY

            key_pair = load_or_create_identity("bootstrap")
            listen_maddr = ma.Multiaddr(f"/ip4/{cfg.listen_ip}/tcp/{cfg.bootstrap_port}")
            host = new_host(key_pair=key_pair, muxer_opt={MPLEX_PROTOCOL_ID: Mplex})
            gossipsub = GossipSub(
                protocols=[TProtocol(GOSSIPSUB_PROTOCOL_ID)],
                degree=3, degree_low=2, degree_high=4,
                time_to_live=60, gossip_window=2, gossip_history=5,
                heartbeat_initial_delay=0.5, heartbeat_interval=2,
            )
            pubsub = Pubsub(host=host, router=gossipsub)

            async with host.run(listen_addrs=[listen_maddr]):
                async with background_trio_service(pubsub), background_trio_service(gossipsub):
                    await pubsub.wait_until_ready()
                    peer_id = host.get_id().pretty()
                    full_addr = (
                        f"/ip4/{cfg.listen_ip}/tcp/{cfg.bootstrap_port}/p2p/{peer_id}"
                    )
                    bootstrap_addr_holder.append(full_addr)

                    # Subscribe so bootstrap relays GossipSub messages between workers + coordinator
                    await pubsub.subscribe(ANNOUNCE_TOPIC)

                    dht = KadDHT(host, DHTMode.SERVER)
                    async with background_trio_service(dht):
                        log.info(f"[Bootstrap] Ready at {full_addr}")
                        bootstrap_ready.set()
                        # Serve until coordinator finishes
                        await coordinator_done.wait()

        await _inner()

    async def _start_worker(
        name: str, port: int, cap: WorkerCapability, cost: float, quality: str
    ) -> None:
        """Wait for bootstrap, then start worker agent."""
        await bootstrap_ready.wait()
        await trio.sleep(cfg.bootstrap_ready_delay)
        bs_addr = bootstrap_addr_holder[0]
        await run_worker(
            name=name,
            port=port,
            capability=cap,
            bootstrap_addr=bs_addr,
            cost=cost,
            quality=quality,
            ready_event=worker_ready_events[name],
            cfg=cfg,
        )

    async def _wait_workers_and_start_coordinator() -> None:
        """Wait for all workers to be ready, then run the coordinator."""
        for event in worker_ready_events.values():
            await event.wait()

        # Extra delay for GossipSub mesh formation
        log.info(
            f"[Demo] All workers ready. "
            f"Waiting {cfg.coordinator_ready_delay}s for mesh formation..."
        )
        await trio.sleep(cfg.coordinator_ready_delay)

        bs_addr = bootstrap_addr_holder[0]
        await run_coordinator(
            task=task,
            port=cfg.coordinator_port,
            bootstrap_addr=bs_addr,
            budget=budget,
            done_event=coordinator_done,
            cfg=cfg,
        )

    async with trio.open_nursery() as nursery:
        # Start bootstrap first
        nursery.start_soon(_start_bootstrap)

        # Start workers (each waits for bootstrap_ready internally)
        for w in cfg.workers:
            nursery.start_soon(
                _start_worker,
                w.name,
                w.port,
                WorkerCapability(w.capability),
                w.cost,
                w.quality,
            )

        # Start coordinator after workers are up
        nursery.start_soon(_wait_workers_and_start_coordinator)

        # Cancel all tasks once coordinator finishes
        async def _shutdown_on_done() -> None:
            await coordinator_done.wait()
            await trio.sleep(0.5)  # allow final log lines to flush
            nursery.cancel_scope.cancel()

        nursery.start_soon(_shutdown_on_done)


def cli() -> None:
    load_dotenv()
    cfg = load_config()
    parser = argparse.ArgumentParser(
        description="AgentMesh Full-Stack Demo — all layers in one process"
    )
    parser.add_argument(
        "--task",
        default=os.getenv("TASK", "Validate, analyze, and report on a dataset"),
        help="Natural-language task for the coordinator",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=float(os.getenv("BUDGET", str(cfg.default_budget))),
        help="Maximum budget in USD",
    )
    args = parser.parse_args()

    worker_ports = ", ".join(str(w.port) for w in cfg.workers)
    print("\n" + "=" * 60)
    print("  AgentMesh Full-Stack Demo")
    print("  libp2p  ·  Multi-Agent  ·  All 6 Layers")
    print("=" * 60)
    print(f"  Task  : {args.task}")
    print(f"  Budget: ${args.budget:.2f}")
    print(
        f"  Ports : bootstrap={cfg.bootstrap_port} "
        f"workers=[{worker_ports}] coordinator={cfg.coordinator_port}"
    )
    print("=" * 60 + "\n")

    try:
        trio.run(main, args.task, args.budget)
    except KeyboardInterrupt:
        print("\n[Demo] Interrupted.")


if __name__ == "__main__":
    cli()
