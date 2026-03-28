"""
Worker Agent — AgentMesh Full-Stack Demo

A worker agent implements layers 1, 3, and 5 of the AgentMesh Stack:

  Layer 1 (Communication): Connects to bootstrap, announces capabilities via
    GossipSub, and registers as a DHT provider for peer discovery.

  Layer 3 (Negotiation): Receives NegotiateRequests from coordinators,
    evaluates proposed terms, and sends back NegotiateOffers (accepting or
    countering as appropriate).

  Layer 5 (Execution): Receives ExecuteStep requests, runs the assigned
    computation, and returns an ExecuteResult with output data and a
    verification hash (supporting Layer 6 hybrid integrity checks).

Usage (standalone):
    python -m src.worker_agent \\
        --name "Validator" --port 9001 \\
        --capability data_validation \\
        --bootstrap /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>

For the all-in-one demo, use demo.py instead.
"""

import argparse
import hashlib
import json
import logging
import random
import time

import multiaddr
import trio
from libp2p import new_host
from libp2p.custom_types import TProtocol
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.network.stream.net_stream import INetStream
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.stream_muxer.mplex.mplex import MPLEX_PROTOCOL_ID, Mplex
from libp2p.tools.async_service.trio_service import background_trio_service

from .common.config import Config, load_config
from .common.identity import load_or_create_identity
from .common.auth import verify_session_token
from .common.messages import (
    AGENTMESH_PROTOCOL_VERSION,
    ANNOUNCE_TOPIC,
    DHT_PROVIDER_KEY,
    EXECUTE_PROTOCOL_ID,
    GOSSIPSUB_PROTOCOL_ID,
    HEALTH_PROTOCOL_ID,
    NEGOTIATE_PROTOCOL_ID,
    AnnounceMessage,
    ExecuteError,
    ExecuteResult,
    ExecuteStep,
    HealthPing,
    HealthPong,
    NegotiateAck,
    NegotiateOffer,
    NegotiateReject,
    NegotiateRequest,
    Policy,
    WorkerCapability,
    WorkerCapabilitySpec,
    recv_msg,
    send_msg,
)
from .common.shutdown import HandlerCounter

_SUPPORTED_PROTOCOL_VERSIONS: frozenset[str] = frozenset({AGENTMESH_PROTOCOL_VERSION})

log = logging.getLogger(__name__)

BOOTSTRAP_ADDR_FILE = ".bootstrap_addr"


def _resolve_bootstrap(addr: str | None) -> str:
    """Return addr if provided, else read from .bootstrap_addr file."""
    if addr:
        return addr
    try:
        with open(BOOTSTRAP_ADDR_FILE) as f:
            resolved = f.read().strip()
        if resolved:
            return resolved
    except FileNotFoundError:
        pass
    import sys
    sys.exit(
        f"Error: --bootstrap not provided and '{BOOTSTRAP_ADDR_FILE}' not found.\n"
        "Pass --bootstrap or start bootstrap_node first (creates .bootstrap_addr)."
    )


# ---------------------------------------------------------------------------
# Layer 5 — Execution simulations
#
# Each function simulates one pipeline stage. In a real system these would
# call actual computation engines, ML models, or external services.
# The key point is: they receive input_data from the prior step and produce
# output_data for the next step (or as the final result).
# ---------------------------------------------------------------------------

def _mock_dataset() -> list[dict]:
    """Generate a reproducible 20-row mock dataset."""
    rng = random.Random(42)
    return [
        {
            "id": i,
            "value": round(rng.gauss(100.0, 15.0), 2),
            "timestamp": f"2026-03-{i + 1:02d}",
        }
        for i in range(20)
    ]


def _execute_validation(params: dict, input_data: dict | None) -> dict:
    """
    Layer 5 — Data Validation worker.
    Checks required fields, counts nulls, returns a validation report + dataset.
    The dataset is passed through as output for downstream stages.
    """
    dataset = (input_data or {}).get("dataset", _mock_dataset())
    required = params.get("required_fields", ["id", "value", "timestamp"])
    tolerance = params.get("null_tolerance", 0.05)

    errors: list[dict] = []
    null_count = 0
    valid_rows = 0

    for row in dataset:
        missing = [f for f in required if f not in row or row[f] is None]
        if missing:
            errors.append({"row": row.get("id", "?"), "missing": missing})
        nulls_in_row = sum(1 for v in row.values() if v is None)
        if nulls_in_row > 0:
            null_count += 1
        else:
            valid_rows += 1

    null_rate = null_count / len(dataset) if dataset else 0.0
    passed = null_rate <= tolerance and len(errors) == 0

    return {
        "stage": "data_validation",
        "passed": passed,
        "total_rows": len(dataset),
        "valid_rows": valid_rows,
        "null_count": null_count,
        "null_rate": round(null_rate, 4),
        "schema_errors": errors,
        "dataset": dataset,  # passed through for downstream steps
    }


def _execute_transformation(params: dict, input_data: dict | None) -> dict:
    """
    Layer 5 — Data Transformation worker.
    Normalises values and passes clean dataset downstream.
    """
    dataset = (input_data or {}).get("dataset", _mock_dataset())
    if params.get("normalize") and dataset:
        values = [row["value"] for row in dataset if "value" in row]
        if values:
            min_v, max_v = min(values), max(values)
            rng = max_v - min_v or 1.0
            dataset = [
                {**row, "value_norm": round((row["value"] - min_v) / rng, 4)}
                for row in dataset
            ]
    return {
        "stage": "data_transformation",
        "transformed": True,
        "rows": len(dataset),
        "dataset": dataset,
    }


def _execute_analytics(params: dict, input_data: dict | None) -> dict:
    """
    Layer 5 — Analytics worker.
    Computes descriptive statistics over the dataset's value column.
    """
    dataset = (input_data or {}).get("dataset", _mock_dataset())
    values = [row["value"] for row in dataset if "value" in row and row["value"] is not None]

    if not values:
        return {"stage": "analytics", "error": "no numeric values found"}

    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n

    return {
        "stage": "analytics",
        "count": n,
        "mean": round(mean, 4),
        "std": round(variance ** 0.5, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "confidence_level": params.get("confidence_level", 0.95),
    }


def _execute_report(params: dict, input_data: dict | None) -> dict:
    """
    Layer 5 — Report Generation worker.
    Formats upstream analytics results into a human-readable report.
    """
    data = input_data or {}
    fmt = params.get("format", "markdown")

    if fmt == "markdown":
        lines = [
            "# AgentMesh Pipeline Report",
            "",
            "## Overview",
            "This report was produced by a fully decentralised multi-agent pipeline",
            "coordinated over libp2p. Each section was computed by a separate worker",
            "agent that negotiated its assignment via the AgentMesh Negotiation Engine.",
            "",
            "## Statistical Summary",
            f"- **Count**:       {data.get('count', 'N/A')} records",
            f"- **Mean**:        {data.get('mean', 'N/A')}",
            f"- **Std Dev**:     {data.get('std', 'N/A')}",
            f"- **Min**:         {data.get('min', 'N/A')}",
            f"- **Max**:         {data.get('max', 'N/A')}",
            f"- **Confidence**:  {data.get('confidence_level', 0.95)}",
            "",
            "## Conclusion",
            "All pipeline stages completed successfully via decentralised P2P coordination.",
            "Data integrity was verified using SHA-256 hashes on each step output.",
        ]
        report_text = "\n".join(lines)
    else:
        report_text = json.dumps(data, indent=2)

    return {
        "stage": "report_generation",
        "format": fmt,
        "report": report_text,
    }


_EXECUTORS = {
    WorkerCapability.DATA_VALIDATION: _execute_validation,
    WorkerCapability.DATA_TRANSFORMATION: _execute_transformation,
    WorkerCapability.ANALYTICS: _execute_analytics,
    WorkerCapability.REPORT_GENERATION: _execute_report,
}


# ---------------------------------------------------------------------------
# Layer 1 — Health check stream handler
# ---------------------------------------------------------------------------

async def _handle_health(
    stream,
    capability: WorkerCapability,
    name: str,
    peer_id: str,
) -> None:
    """
    Layer 1: Respond to a coordinator's HealthPing with a HealthPong.

    This is a simple liveness probe — the coordinator calls it before
    opening a negotiation stream to avoid stalling on unresponsive workers.
    """
    try:
        msg = await recv_msg(stream)
        if not isinstance(msg, HealthPing):
            return
        await send_msg(
            stream,
            HealthPong(sender=peer_id, worker_name=name, capability=capability),
        )
    except Exception as exc:
        log.debug(f"[{name}] Health ping error: {exc}")
    finally:
        await stream.close()


# ---------------------------------------------------------------------------
# Layer 3 — Negotiation stream handler
# ---------------------------------------------------------------------------

async def _handle_negotiate(
    stream: INetStream,
    capability: WorkerCapability,
    name: str,
    cost: float,
    quality: str,
    peer_id: str,
) -> None:
    """
    Layer 3: Handle an incoming NegotiateRequest from a coordinator.

    Protocol:
      1. recv NegotiateRequest
      2. send NegotiateOffer (or NegotiateReject if capability mismatch)
      3. recv NegotiateAck  ← coordinator confirms or declines assignment
      4. close stream
    """
    try:
        msg = await recv_msg(stream)
        if not isinstance(msg, NegotiateRequest):
            log.warning(f"[{name}] Expected NegotiateRequest, got {type(msg)}")
            return

        # Reject unsupported protocol versions before any other logic
        if msg.protocol_version not in _SUPPORTED_PROTOCOL_VERSIONS:
            await send_msg(
                stream,
                NegotiateReject(
                    sender=peer_id,
                    task_id=msg.task_id,
                    reason=(
                        f"Unsupported protocol version {msg.protocol_version!r}; "
                        f"supported: {sorted(_SUPPORTED_PROTOCOL_VERSIONS)}"
                    ),
                ),
            )
            return

        log.info(
            f"[{name}] Negotiate request: task={msg.task_id} "
            f"cap={msg.required_capability.value} "
            f"proto_version={msg.protocol_version}"
        )

        # Reject if the requested capability does not match ours
        if msg.required_capability != capability:
            reject = NegotiateReject(
                sender=peer_id,
                task_id=msg.task_id,
                reason=(
                    f"Capability mismatch: I offer {capability.value}, "
                    f"requested {msg.required_capability.value}"
                ),
            )
            await send_msg(stream, reject)
            return

        # Build offer: accept coordinator's policies, counter if budget is too low
        accepted: list[Policy] = []
        counter: list[Policy] = []

        for p in msg.proposed_policies:
            if p.key == "max_budget_usd" and float(p.value) < cost:
                # Budget too low — propose our actual cost as a counter
                counter.append(Policy(key="max_budget_usd", value=cost, negotiable=True))
            else:
                accepted.append(p)

        # Also declare our own capability-level policies
        accepted.extend([
            Policy(key="max_budget_usd", value=cost,    negotiable=True),
            Policy(key="max_latency_ms", value=500,     negotiable=True),
            Policy(key="quality_tier",   value=quality, negotiable=False),
        ])

        offer = NegotiateOffer(
            sender=peer_id,
            task_id=msg.task_id,
            capability=capability,
            worker_name=name,
            accepted_policies=accepted,
            counter_policies=counter,
            accepted=True,
        )
        await send_msg(stream, offer)
        log.info(f"[{name}] Offer sent for task {msg.task_id}")

        # Wait for coordinator's final acknowledgement
        ack_msg = await recv_msg(stream)
        if isinstance(ack_msg, NegotiateAck):
            if ack_msg.accepted:
                # Verify the session token before accepting the assignment
                if not verify_session_token(
                    ack_msg.session_token, msg.task_id, ack_msg.step_id
                ):
                    log.warning(
                        f"[{name}] Invalid session token on NegotiateAck for step "
                        f"'{ack_msg.step_id}' — rejecting assignment"
                    )
                    return
                log.info(f"[{name}] Assigned to step '{ack_msg.step_id}' ✓")
            else:
                log.info(f"[{name}] Offer declined for task {msg.task_id}")

    except Exception as exc:
        log.warning(f"[{name}] Negotiate error: {exc}")
    finally:
        await stream.close()


# ---------------------------------------------------------------------------
# Layer 5 — Execution stream handler
# ---------------------------------------------------------------------------

async def _handle_execute(
    stream: INetStream,
    capability: WorkerCapability,
    name: str,
    peer_id: str,
) -> None:
    """
    Layer 5: Execute an assigned protocol step.

    Protocol:
      1. recv ExecuteStep (may contain input_data from a previous step)
      2. run the corresponding executor function
      3. send ExecuteResult with output_data and verification_hash
      4. close stream
    """
    try:
        msg = await recv_msg(stream)
        if not isinstance(msg, ExecuteStep):
            log.warning(f"[{name}] Expected ExecuteStep, got {type(msg)}")
            return

        # Layer 6 / Auth: verify the HMAC session token before executing
        if not verify_session_token(msg.session_token, msg.task_id, msg.step_id):
            log.warning(
                f"[{name}] Unauthorized ExecuteStep for step '{msg.step_id}' "
                f"— rejecting (invalid or missing session token)"
            )
            err = ExecuteError(
                sender=peer_id,
                protocol_id=msg.protocol_id,
                step_id=msg.step_id,
                task_id=msg.task_id,
                error="Unauthorized: invalid session token",
            )
            await send_msg(stream, err)
            return

        log.info(f"[{name}] Executing step '{msg.step_id}' ({msg.capability.value})...")
        t_start = time.monotonic()

        executor = _EXECUTORS.get(msg.capability)
        if executor is None:
            err = ExecuteError(
                sender=peer_id,
                protocol_id=msg.protocol_id,
                step_id=msg.step_id,
                task_id=msg.task_id,
                error=f"No executor registered for capability: {msg.capability.value}",
            )
            await send_msg(stream, err)
            return

        # Simulate processing time (replace with real work in production)
        await trio.sleep(0.3)
        output = executor(msg.parameters, msg.input_data)
        elapsed_ms = int((time.monotonic() - t_start) * 1000)

        # Compute integrity hash (Layer 6 support)
        payload = json.dumps(output, sort_keys=True, separators=(",", ":"))
        v_hash = hashlib.sha256(payload.encode()).hexdigest()[:16]

        result = ExecuteResult(
            sender=peer_id,
            protocol_id=msg.protocol_id,
            step_id=msg.step_id,
            task_id=msg.task_id,
            capability=msg.capability,
            worker_name=name,
            output_data=output,
            execution_time_ms=elapsed_ms,
            verification_hash=v_hash,
        )
        await send_msg(stream, result)
        log.info(f"[{name}] Step '{msg.step_id}' done in {elapsed_ms}ms  hash={v_hash}")

    except Exception as exc:
        log.warning(f"[{name}] Execute error: {exc}")
        try:
            err = ExecuteError(
                sender=peer_id,
                protocol_id="unknown",
                step_id="unknown",
                task_id="unknown",
                error=str(exc),
            )
            await send_msg(stream, err)
        except Exception:
            pass
    finally:
        await stream.close()


# ---------------------------------------------------------------------------
# Main worker runner (Layer 1 setup + handler registration)
# ---------------------------------------------------------------------------

async def run(
    name: str,
    port: int,
    capability: WorkerCapability,
    bootstrap_addr: str,
    cost: float = 0.02,
    quality: str = "standard",
    identity_name: str | None = None,
    ready_event: trio.Event | None = None,
    cfg: Config | None = None,
) -> None:
    """Start a worker agent and serve requests until cancelled."""
    if cfg is None:
        cfg = load_config()

    key_pair = load_or_create_identity(identity_name or name.lower().replace(" ", "_"))
    listen_maddr = multiaddr.Multiaddr(f"/ip4/{cfg.listen_ip}/tcp/{port}")

    # Layer 1: Create libp2p host (Noise encryption automatic, Mplex muxer)
    host = new_host(key_pair=key_pair, muxer_opt={MPLEX_PROTOCOL_ID: Mplex})

    # Layer 1: GossipSub for capability announcements
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
            log.info(f"[{name}] Worker started  PeerID={peer_id[:20]}...")
            log.info(f"[{name}] Capability: {capability.value}  cost=${cost}  quality={quality}")

            # Graceful-shutdown handler counter — tracks in-flight streams
            _handler_counter = HandlerCounter()

            # Layer 1 + 3 + 5: Register stream handlers
            async def negotiate_handler(stream: INetStream) -> None:
                async with _handler_counter:
                    await _handle_negotiate(stream, capability, name, cost, quality, peer_id)

            async def execute_handler(stream: INetStream) -> None:
                async with _handler_counter:
                    await _handle_execute(stream, capability, name, peer_id)

            async def health_handler(stream: INetStream) -> None:
                async with _handler_counter:
                    await _handle_health(stream, capability, name, peer_id)

            host.set_stream_handler(TProtocol(NEGOTIATE_PROTOCOL_ID), negotiate_handler)
            host.set_stream_handler(TProtocol(EXECUTE_PROTOCOL_ID), execute_handler)
            host.set_stream_handler(TProtocol(HEALTH_PROTOCOL_ID), health_handler)

            # Layer 1: Connect to bootstrap node (with retry)
            bs_info = info_from_p2p_addr(multiaddr.Multiaddr(bootstrap_addr))
            for attempt in range(3):
                try:
                    await host.connect(bs_info)
                    log.info(f"[{name}] Connected to bootstrap")
                    break
                except Exception as exc:
                    if attempt < 2:
                        log.warning(
                            f"[{name}] Bootstrap connection failed "
                            f"(attempt {attempt + 1}/3): {exc} — retrying"
                        )
                        await trio.sleep(2 ** attempt)
                    else:
                        log.warning(
                            f"[{name}] Bootstrap connection failed after 3 attempts: {exc}"
                        )

            # Layer 1: Register on Kademlia DHT as a worker provider
            dht = KadDHT(host, DHTMode.SERVER)
            for pid in host.get_peerstore().peer_ids():
                await dht.routing_table.add_peer(pid)

            async with background_trio_service(dht):
                # Register under global key AND per-capability key
                cap_dht_key = f"agentmesh/{capability.value}"
                for dht_key in (DHT_PROVIDER_KEY, cap_dht_key):
                    for attempt in range(3):
                        try:
                            await dht.provide(dht_key)
                            log.info(f"[{name}] DHT provider registered: '{dht_key}'")
                            break
                        except Exception as exc:
                            if attempt < 2:
                                log.warning(
                                    f"[{name}] DHT provide '{dht_key}' failed "
                                    f"(attempt {attempt + 1}/3): {exc} — retrying"
                                )
                                await trio.sleep(2 ** attempt)
                            else:
                                log.warning(
                                    f"[{name}] DHT provide '{dht_key}' failed "
                                    f"after 3 attempts: {exc}"
                                )

                # Layer 1: Announce capabilities via GossipSub pub/sub.
                # Periodic re-announcement ensures coordinators that subscribe
                # after the initial publish still discover this worker.
                announce = AnnounceMessage(
                    sender=peer_id,
                    worker_name=name,
                    capabilities=[
                        WorkerCapabilitySpec(
                            capability=capability,
                            cost_per_unit=cost,
                            max_latency_ms=500,
                            quality_tier=quality,
                        )
                    ],
                    multiaddr=full_addr,
                    worker_policies=[
                        Policy(key="max_budget_usd", value=cost,    negotiable=True),
                        Policy(key="quality_tier",   value=quality, negotiable=False),
                    ],
                )
                announce_payload = announce.model_dump_json().encode()

                # Signal readiness after first announce
                await pubsub.publish(ANNOUNCE_TOPIC, announce_payload)
                log.info(f"[{name}] GossipSub announce published")
                if ready_event is not None:
                    ready_event.set()

                # Keep re-announcing + periodically re-providing DHT key
                last_reprovide = time.monotonic()
                try:
                    while True:
                        await trio.sleep(cfg.reannounce_interval)
                        await pubsub.publish(ANNOUNCE_TOPIC, announce_payload)
                        log.debug(f"[{name}] Re-announced capability")

                        # Re-provide DHT keys periodically (default every 12h)
                        now = time.monotonic()
                        if now - last_reprovide >= cfg.dht_reprovide_interval:
                            for dht_key in (DHT_PROVIDER_KEY, cap_dht_key):
                                try:
                                    await dht.provide(dht_key)
                                    log.debug(f"[{name}] DHT re-provided: '{dht_key}'")
                                except Exception as exc:
                                    log.warning(f"[{name}] DHT re-provide '{dht_key}' failed: {exc}")
                            last_reprovide = now
                except trio.Cancelled:
                    # Graceful shutdown: give in-flight handlers time to finish
                    # before the nursery tears them down.
                    log.info(
                        f"[{name}] Shutdown signal received — draining "
                        f"{_handler_counter.count} in-flight handler(s)..."
                    )
                    await _handler_counter.wait_idle(drain_timeout=5.0)
                    log.info(f"[{name}] All handlers drained — shutting down cleanly.")
                    raise


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()
    parser = argparse.ArgumentParser(description="AgentMesh Worker Agent")
    parser.add_argument("--name",       required=True,  help="Human-readable agent name")
    parser.add_argument("--port",       type=int, required=True, help="TCP listen port")
    parser.add_argument("--capability", required=True,
                        choices=[c.value for c in WorkerCapability],
                        help="Capability offered by this worker")
    parser.add_argument("--bootstrap",  default=None, help="Bootstrap node multiaddr (or reads .bootstrap_addr)")
    parser.add_argument("--cost",       type=float, default=0.02, help="Cost per unit in USD")
    parser.add_argument("--quality",    default="standard", choices=["standard", "premium"])
    args = parser.parse_args()

    bootstrap = _resolve_bootstrap(args.bootstrap)
    try:
        trio.run(
            run,
            args.name,
            args.port,
            WorkerCapability(args.capability),
            bootstrap,
            args.cost,
            args.quality,
            None,
            None,
            cfg,
        )
    except KeyboardInterrupt:
        log.info("Worker shutting down.")


if __name__ == "__main__":
    main()
