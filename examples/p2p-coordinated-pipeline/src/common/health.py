"""
Health check client: ping workers before assigning them negotiation work.

The coordinator calls ``ping_worker()`` for each discovered worker candidate
before opening a negotiation stream.  Workers that do not respond within
``HEALTH_PING_TIMEOUT`` seconds are skipped silently, preventing the
negotiation phase from stalling on unresponsive peers.

Worker-side handler registration is done in worker_agent.py via
``host.set_stream_handler(TProtocol(HEALTH_PROTOCOL_ID), health_handler)``.
"""
import logging
from typing import Optional

import trio
from libp2p.custom_types import TProtocol
from libp2p.peer.id import ID as PeerID

from .messages import HEALTH_PROTOCOL_ID, HealthPing, HealthPong, recv_msg, send_msg  # noqa: F401

log = logging.getLogger(__name__)

HEALTH_PING_TIMEOUT: float = 3.0  # seconds


async def ping_worker(
    host,
    peer_id_str: str,
    coordinator_peer_id: str,
    timeout: float = HEALTH_PING_TIMEOUT,
) -> bool:
    """
    Send a HealthPing to a worker and wait for a HealthPong.

    Returns True if the worker responds within ``timeout``, False on timeout
    or any connection failure (does not raise).
    """
    try:
        pid = PeerID.from_base58(peer_id_str)
        stream = await host.new_stream(pid, [TProtocol(HEALTH_PROTOCOL_ID)])
        response: Optional[HealthPong] = None
        try:
            with trio.move_on_after(timeout):
                await send_msg(stream, HealthPing(sender=coordinator_peer_id))
                response = await recv_msg(stream)
        finally:
            await stream.close()

        if isinstance(response, HealthPong):
            log.debug(
                f"[Health] {peer_id_str[:16]}... ({response.worker_name}) healthy"
            )
            return True
        log.debug(f"[Health] {peer_id_str[:16]}... health check timed out")
        return False
    except Exception as exc:
        log.debug(f"[Health] {peer_id_str[:16]}... unreachable: {exc}")
        return False
