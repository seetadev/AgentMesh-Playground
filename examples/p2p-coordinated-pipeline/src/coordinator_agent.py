"""
Coordinator Agent — AgentMesh Full-Stack Demo

The coordinator is the orchestrating brain of the AgentMesh Stack. It runs
all six layers in sequence for a given task:

  Layer 1 (Communication):  Connect to bootstrap, subscribe to GossipSub
    announce topic, collect worker capability advertisements.

  Layer 2 (Policy Extraction):  Parse the task description into a structured
    PolicySet with required capabilities and constraints.

  Layer 3 (Negotiation Engine):  For each required capability, open direct
    libp2p streams to candidate workers, exchange NegotiateRequest/Offer/Ack
    messages, and select the best offer using the NegotiationEngine.

    Negotiation wire protocol on a single stream:
      Coordinator → Worker : NegotiateRequest
      Worker → Coordinator : NegotiateOffer  (or NegotiateReject)
      Coordinator → Worker : NegotiateAck    (accepted=True/False)
      [stream closed]

  Layer 4 (Protocol Generator):  Compile accepted assignments into an ordered
    ExecutionProtocol with data-flow chaining between steps.

  Layer 5 (Execution Engine):  Dispatch each protocol step to its assigned
    worker over a new direct libp2p stream, chaining outputs as inputs.

  Layer 6 (Hybrid Infrastructure):  Verify output hashes locally (primary)
    with extension points for on-chain Filecoin/FEVM attestation.

Usage (standalone):
    python -m src.coordinator_agent \\
        --task "Validate, analyze, and report on a dataset" \\
        --port 9004 \\
        --bootstrap /ip4/127.0.0.1/tcp/9000/p2p/<PEER_ID>

For the all-in-one demo, use demo.py instead.
"""

import argparse
import json
import logging
from typing import Optional

import multiaddr
import trio
from libp2p import new_host
from libp2p.custom_types import TProtocol
from libp2p.kad_dht.kad_dht import DHTMode, KadDHT
from libp2p.network.stream.net_stream import INetStream
from libp2p.peer.id import ID as PeerID
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.stream_muxer.mplex.mplex import MPLEX_PROTOCOL_ID, Mplex
from libp2p.tools.async_service.trio_service import background_trio_service

from .common.config import Config, load_config
from .common.identity import load_or_create_identity
from .common.messages import (
    ANNOUNCE_TOPIC,
    DHT_PROVIDER_KEY,
    GOSSIPSUB_PROTOCOL_ID,
    NEGOTIATE_PROTOCOL_ID,
    AnnounceMessage,
    ExecuteResult,
    ExecutionProtocol,
    MessageType,
    NegotiateAck,
    NegotiateOffer,
    NegotiateReject,
    NegotiateRequest,
    PolicySet,
    ProtocolStep,
    WorkerCapability,
    recv_msg,
    send_msg,
)
from .common.health import ping_worker
from .common.observability import PipelineMetrics, make_trace_logger
from .common.persistence import save_pipeline_result
from .layers.attestation import build_attestation_backend
from .layers.execution import ExecutionEngine
from .layers.negotiation import NegotiationEngine
from .layers.policy import PolicyExtractor
from .layers.protocol_gen import ProtocolGenerator

log = logging.getLogger(__name__)

BOOTSTRAP_ADDR_FILE = ".bootstrap_addr"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _banner(title: str, char: str = "─", width: int = 60) -> None:
    print(f"\n{char * width}")
    print(f"  {title}")
    print(f"{char * width}")


def _on_step_complete(step: ProtocolStep, result: ExecuteResult) -> None:
    """Callback invoked by the ExecutionEngine after each step completes."""
    print(f"\n  ✓ Step {step.sequence}: [{step.capability.value}]  worker={step.worker_name}")
    print(f"    Execution time : {result.execution_time_ms}ms")
    print(f"    Integrity hash : {result.verification_hash}")

    out = result.output_data
    stage = out.get("stage", "")

    if stage == "data_validation":
        passed = "PASSED ✓" if out.get("passed") else "FAILED ✗"
        print(f"    Validation     : {passed}")
        print(f"    Rows           : {out.get('valid_rows')}/{out.get('total_rows')} valid")
        if out.get("schema_errors"):
            print(f"    Errors         : {len(out['schema_errors'])}")
    elif stage == "data_transformation":
        print(f"    Rows processed : {out.get('rows')}")
    elif stage == "analytics":
        print(
            f"    count={out.get('count')}  mean={out.get('mean')}  "
            f"std={out.get('std')}  min={out.get('min')}  max={out.get('max')}"
        )
    elif stage == "report_generation":
        print(f"\n{'─' * 60}")
        print(out.get("report", "(no report)"))
        print(f"{'─' * 60}")


# ---------------------------------------------------------------------------
# Layer 3 — Single-stream negotiation with retry
#
# The full negotiation exchange happens on ONE stream:
#   Coordinator sends  → NegotiateRequest
#   Worker sends back  → NegotiateOffer  (or NegotiateReject)
#   Coordinator sends  → NegotiateAck
#   [both sides close the stream]
#
# This keeps the protocol simple and avoids a second round-trip.
# ---------------------------------------------------------------------------

async def _negotiate(
    host,
    peer_id: str,
    worker_peer_id: str,
    policy: PolicySet,
    capability: WorkerCapability,
    negotiate_timeout: float,
    negotiate_retry_attempts: int,
) -> Optional[tuple[NegotiateOffer, INetStream]]:
    """
    Run the three-message negotiation exchange with one worker.

    Stream creation is retried up to negotiate_retry_attempts times with
    exponential backoff on transient connection failures.

    Returns (NegotiateOffer, stream) on success so the caller can send
    the NegotiateAck on the same stream.  Returns None on failure or reject.
    """
    worker_pid = PeerID.from_base58(worker_peer_id)
    stream = None

    for attempt in range(negotiate_retry_attempts):
        try:
            stream = await host.new_stream(worker_pid, [TProtocol(NEGOTIATE_PROTOCOL_ID)])
            break
        except Exception as exc:
            if attempt < negotiate_retry_attempts - 1:
                log.warning(
                    f"[Coordinator] Stream to {worker_peer_id[:16]}... failed "
                    f"(attempt {attempt + 1}/{negotiate_retry_attempts}): {exc} — retrying"
                )
                await trio.sleep(2 ** attempt)
            else:
                log.warning(
                    f"[Coordinator] Cannot reach {worker_peer_id[:16]}... after "
                    f"{negotiate_retry_attempts} attempt(s): {exc}"
                )
                return None

    if stream is None:
        return None

    try:
        # Step 1: Send our requirements
        request = NegotiateRequest(
            sender=peer_id,
            task_id=policy.task_id,
            task_description=policy.task_description,
            required_capability=capability,
            proposed_policies=policy.policies,
        )
        await send_msg(stream, request)

        # Step 2: Wait for the worker's response
        response = None
        with trio.move_on_after(negotiate_timeout):
            response = await recv_msg(stream)

            if isinstance(response, NegotiateReject):
                log.info(
                    f"[Coordinator] {worker_peer_id[:16]}... rejected: {response.reason}"
                )
                await stream.close()
                return None

            if isinstance(response, NegotiateOffer):
                log.info(
                    f"[Coordinator] {response.worker_name} accepted "
                    f"protocol v{response.protocol_version}"
                )
                # Return offer + open stream (caller must send ack then close)
                return response, stream

        # Timeout
        log.warning(
            f"[Coordinator] Negotiate timeout for {worker_peer_id[:16]}..."
        )
        await stream.close()
        return None

    except Exception as exc:
        log.warning(f"[Coordinator] Negotiate error: {exc}")
        try:
            await stream.close()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Main coordinator runner
# ---------------------------------------------------------------------------

async def run(
    task: str,
    port: int,
    bootstrap_addr: str,
    budget: float = 0.10,
    identity_name: str = "coordinator",
    done_event: Optional[trio.Event] = None,
    cfg: Optional[Config] = None,
) -> None:
    """Run the coordinator through all six AgentMesh Stack layers."""
    if cfg is None:
        cfg = load_config()

    key_pair = load_or_create_identity(identity_name)
    listen_maddr = multiaddr.Multiaddr(f"/ip4/{cfg.listen_ip}/tcp/{port}")

    # ── Layer 1: Build libp2p host (Noise encryption + Mplex muxer) ─────────
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
            log.info(f"[Coordinator] Started  PeerID={peer_id[:20]}...")

            # Connect to bootstrap node
            bs_info = info_from_p2p_addr(multiaddr.Multiaddr(bootstrap_addr))
            await host.connect(bs_info)
            log.info("[Coordinator] Connected to bootstrap")

            # ── DHT init (used as fallback if GossipSub misses workers) ─────
            dht = KadDHT(host, DHTMode.SERVER)
            # Seed routing table from peerstore (bootstrap is already connected)
            for pid in host.get_peerstore().peer_ids():
                await dht.routing_table.add_peer(pid)

            async with background_trio_service(dht):
                # Subscribe to the GossipSub announcement topic
                subscription = await pubsub.subscribe(ANNOUNCE_TOPIC)
                log.info(f"[Coordinator] Listening on GossipSub '{ANNOUNCE_TOPIC}'")

                # ── Layer 2: Extract policies from natural-language task ──────
                extractor = PolicyExtractor()
                policy = extractor.extract(task, budget=budget)

                # Observability: trace-tagged logger + pipeline metrics
                tlog = make_trace_logger(log, policy.task_id)
                metrics = PipelineMetrics(task_id=policy.task_id)

                _banner("AgentMesh Full-Stack Demo", "═")
                print(f"  Task : {task}")
                print(f"  ID   : {policy.task_id}")
                print(f"  Caps : {[c.value for c in policy.required_capabilities]}")
                print(f"  Policies:")
                for p in policy.policies:
                    tag = "negotiable" if p.negotiable else "fixed"
                    print(f"    {p.key} = {p.value}  ({tag})")

                # ── Layer 1: Collect worker announcements via GossipSub ───────
                _banner("Layer 1 — Discovering Workers", "─")
                print(
                    f"  Waiting {cfg.discovery_window}s for GossipSub announcements..."
                )
                _discovery_t0 = trio.current_time()

                # workers_by_cap: capability → [(peer_id_str, AnnounceMessage)]
                workers_by_cap: dict[
                    WorkerCapability, list[tuple[str, AnnounceMessage]]
                ] = {}

                async def _collect() -> None:
                    while True:
                        pubsub_msg = await subscription.get()
                        try:
                            data = json.loads(pubsub_msg.data.decode())
                            if data.get("type") != MessageType.ANNOUNCE.value:
                                continue
                            announce = AnnounceMessage.model_validate(data)
                            for spec in announce.capabilities:
                                bucket = workers_by_cap.setdefault(spec.capability, [])
                                # Deduplicate by sender peer ID
                                if not any(pid == announce.sender for pid, _ in bucket):
                                    bucket.append((announce.sender, announce))
                                    log.info(
                                        f"[Coordinator] Found {announce.worker_name} "
                                        f"({spec.capability.value}) @ ${spec.cost_per_unit}"
                                    )
                                    # Pre-connect so we can open streams later
                                    try:
                                        wi = info_from_p2p_addr(
                                            multiaddr.Multiaddr(announce.multiaddr)
                                        )
                                        await host.connect(wi)
                                    except Exception:
                                        pass
                        except Exception:
                            pass  # Ignore mesh summaries and malformed messages

                with trio.move_on_after(cfg.discovery_window):
                    await _collect()

                metrics.discovery_duration_s = trio.current_time() - _discovery_t0
                total = sum(len(v) for v in workers_by_cap.values())
                tlog.info(
                    f"Discovery complete: {total} worker(s) in "
                    f"{metrics.discovery_duration_s:.1f}s"
                )
                print(
                    f"  Discovered {total} worker(s) across "
                    f"{len(workers_by_cap)} capability group(s)"
                )

                # ── DHT fallback for capabilities with no GossipSub candidates ─
                missing_caps = [
                    cap
                    for cap in policy.required_capabilities
                    if not workers_by_cap.get(cap)
                ]
                if missing_caps:
                    log.info(
                        f"[Coordinator] DHT fallback for: "
                        f"{[c.value for c in missing_caps]}"
                    )
                    for cap in missing_caps:
                        dht_key = f"agentmesh/{cap.value}"
                        providers: list = []
                        with trio.move_on_after(5):
                            providers = await dht.find_providers(dht_key)
                        for pi in providers:
                            pid_str = pi.peer_id.pretty()
                            bucket = workers_by_cap.setdefault(cap, [])
                            if not any(p == pid_str for p, _ in bucket):
                                # Pre-connect and add a minimal placeholder
                                try:
                                    await host.connect(pi)
                                except Exception:
                                    pass
                                # Build a minimal AnnounceMessage placeholder
                                # (_negotiate only reads worker_peer_id from the
                                #  first element of the tuple)
                                from .common.messages import (
                                    WorkerCapabilitySpec, AnnounceMessage as AM,
                                    MessageType as MT,
                                )
                                placeholder = AM(
                                    type=MT.ANNOUNCE,
                                    sender=pid_str,
                                    worker_name=pid_str[:12],
                                    capabilities=[
                                        WorkerCapabilitySpec(
                                            capability=cap,
                                            cost_per_unit=0.0,
                                            max_latency_ms=5000,
                                            quality_tier="standard",
                                        )
                                    ],
                                    multiaddr="",
                                )
                                bucket.append((pid_str, placeholder))
                                log.info(
                                    f"[Coordinator] DHT peer {pid_str[:16]}... "
                                    f"added for {cap.value}"
                                )

                # ── Layer 3: Negotiate with workers per required capability ────
                _banner("Layer 3 — Negotiation Engine", "─")
                engine = NegotiationEngine()
                _negotiation_t0 = trio.current_time()

                # Map: capability → (best_offer, open_stream)
                best_per_cap: dict[
                    WorkerCapability, tuple[NegotiateOffer, INetStream]
                ] = {}
                # All streams that received offers (need ack or close).
                # _drain_streams() is called in every exit path (normal, early-exit,
                # and cancellation via trio.Cancelled) so streams never leak.
                all_streams: list[tuple[NegotiateOffer, INetStream]] = []

                async def _drain_streams() -> None:
                    """Close any negotiate streams not yet explicitly closed."""
                    for _offer, _stream in all_streams:
                        try:
                            await _stream.close()
                        except Exception:
                            pass
                    all_streams.clear()

                for cap in policy.required_capabilities:
                    candidates = workers_by_cap.get(cap, [])
                    if not candidates:
                        log.warning(
                            f"[Coordinator] No workers found for capability: {cap.value}"
                        )
                        continue

                    print(f"\n  Capability: {cap.value} ({len(candidates)} candidate(s))")
                    scored: list[tuple[float, NegotiateOffer, INetStream]] = []

                    for worker_pid_str, _announce in candidates:
                        # Health check before opening negotiation stream
                        alive = await ping_worker(host, worker_pid_str, peer_id)
                        if not alive:
                            tlog.warning(
                                f"Health check failed for {worker_pid_str[:16]}... — skipping"
                            )
                            print(f"    {worker_pid_str[:16]}...: health check failed — skipped")
                            metrics.health_pings_failed += 1
                            continue
                        metrics.health_pings_sent += 1

                        metrics.negotiations_attempted += 1
                        result = await _negotiate(
                            host, peer_id, worker_pid_str, policy, cap,
                            cfg.negotiate_timeout, cfg.negotiate_retry_attempts,
                        )
                        if result is None:
                            metrics.negotiations_rejected += 1
                            continue
                        offer, stream = result
                        ok, score = engine.evaluate(offer, policy)
                        tag = f"score={score:.1f}" if ok else "policy mismatch — rejected"
                        print(f"    {offer.worker_name}: {tag}")
                        all_streams.append((offer, stream))
                        if ok:
                            metrics.negotiations_accepted += 1
                            scored.append((score, offer, stream))
                        else:
                            metrics.negotiations_rejected += 1

                    if not scored:
                        print(f"    ✗ No acceptable offer for {cap.value}")
                        continue

                    # Pick the highest-scoring offer
                    scored.sort(key=lambda t: t[0], reverse=True)
                    best_score, best_offer, best_stream = scored[0]
                    best_per_cap[cap] = (best_offer, best_stream)
                    print(f"    → Selected: {best_offer.worker_name}  (score={best_score:.1f})")

                if not best_per_cap:
                    print("\n  ✗ No workers could be assigned — aborting.")
                    await _drain_streams()
                    if done_event:
                        done_event.set()
                    return

                metrics.negotiation_duration_s = trio.current_time() - _negotiation_t0

                # ── Layer 4: Generate the ExecutionProtocol ──────────────────
                _banner("Layer 4 — Protocol Generator", "─")
                gen = ProtocolGenerator()
                assignments = {cap: offer for cap, (offer, _) in best_per_cap.items()}
                protocol = gen.generate(policy, assignments)

                metrics.protocol_id = protocol.protocol_id
                print(f"  Protocol ID : {protocol.protocol_id}")
                print(f"  Hash        : {protocol.compute_hash()}")
                print(f"  Steps       : {len(protocol.steps)}")
                for step in protocol.steps:
                    arrow = f"← {step.input_from}" if step.input_from else "(entry point)"
                    parallel_tag = (
                        "  [parallel wave]"
                        if sum(1 for s in protocol.steps if s.sequence == step.sequence) > 1
                        else ""
                    )
                    print(
                        f"    wave={step.sequence}. [{step.capability.value}]  "
                        f"worker={step.worker_name}  {arrow}{parallel_tag}"
                    )

                # Send NegotiateAck on every open stream (accept winners, reject others).
                # The inner try/finally ensures all streams are closed even if the
                # coordinator is cancelled mid-execution (graceful shutdown).
                winner_ids = {offer.sender for offer, _ in best_per_cap.values()}
                for offer, stream in all_streams:
                    cap_match = next(
                        (s for s in protocol.steps if s.worker_peer_id == offer.sender),
                        None,
                    )
                    accepted = offer.sender in winner_ids
                    ack = NegotiateAck(
                        sender=peer_id,
                        task_id=policy.task_id,
                        step_id=cap_match.step_id if cap_match else "",
                        accepted=accepted,
                        session_token=(
                            cap_match.session_token
                            if cap_match and accepted
                            else ""
                        ),
                    )
                    try:
                        await send_msg(stream, ack)
                        await stream.close()
                        all_streams.remove((offer, stream))
                    except Exception:
                        pass

                # Drain any streams not explicitly closed above (cancellation path)
                await _drain_streams()

                # ── Layer 5 + 6: Execute the protocol ────────────────────────
                _banner("Layer 5 — Execution Engine  (Layer 6: Hybrid Verification)", "─")
                exec_engine = ExecutionEngine(
                    host,
                    attestation=build_attestation_backend(),
                    execute_timeout=cfg.execute_timeout,
                    retry_attempts=cfg.execute_retry_attempts,
                )

                print(f"  Executing {len(protocol.steps)} step(s) in pipeline order...")
                _execution_t0 = trio.current_time()
                results = await exec_engine.run(
                    protocol, on_step=_on_step_complete, metrics=metrics
                )
                metrics.execution_duration_s = trio.current_time() - _execution_t0

                # Persist results to disk before printing summary
                saved_path = save_pipeline_result(protocol, results, metrics)

                # ── Summary ──────────────────────────────────────────────────
                _banner("Pipeline Complete", "═")
                total_ms = sum(r.execution_time_ms for r in results.values())
                print(f"  Steps completed  : {len(results)}")
                print(f"  Total exec time  : {total_ms}ms")
                print(f"  Wall time        : {metrics.execution_duration_s*1000:.0f}ms")
                print(f"  Integrity checks : all passed ✓")
                print(f"  Results saved    : {saved_path}")
                print(f"  Negotiations     : {metrics.negotiations_accepted}/"
                      f"{metrics.negotiations_attempted} accepted")
                print()

                tlog.info(
                    f"Pipeline complete: {len(results)} steps, "
                    f"{total_ms}ms execution, saved to {saved_path}"
                )

                if done_event:
                    done_event.set()


def _resolve_bootstrap(addr: Optional[str]) -> str:
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = load_config()
    parser = argparse.ArgumentParser(description="AgentMesh Coordinator Agent")
    parser.add_argument(
        "--task",
        default="Validate, analyze, and report on a dataset",
        help="Natural-language task description",
    )
    parser.add_argument("--port",      type=int, default=cfg.coordinator_port, help="TCP listen port")
    parser.add_argument("--bootstrap", default=None, help="Bootstrap node multiaddr (or reads .bootstrap_addr)")
    parser.add_argument("--budget",    type=float, default=cfg.default_budget, help="Max budget in USD")
    args = parser.parse_args()

    bootstrap = _resolve_bootstrap(args.bootstrap)
    try:
        trio.run(run, args.task, args.port, bootstrap, args.budget, "coordinator", None, cfg)
    except KeyboardInterrupt:
        log.info("Coordinator shutting down.")


if __name__ == "__main__":
    main()
