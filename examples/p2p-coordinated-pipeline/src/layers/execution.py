"""
Layer 5: Execution Engine  +  Layer 6: Hybrid Infrastructure

Dispatches ExecutionProtocol steps to their assigned worker agents over
direct libp2p streams. Results from each step are chained as input to
dependent steps, implementing a composable multi-agent pipeline.

Parallel execution:
  Steps that share the same topological wave (same `sequence` value and no
  mutual depends_on edges) are dispatched concurrently via a trio nursery.
  Steps with unsatisfied depends_on edges wait for the prior wave to finish.

Hybrid Infrastructure (Layer 6):
  - Primary path:    off-chain via libp2p (fast, fully P2P)
  - Verification:    local SHA-256 hash check on each result
  - Attestation:     pluggable AttestationBackend (local, RPC, or Filecoin/FEVM)

The separation of primary and verification paths means the system stays fast
under normal conditions while providing auditability on demand.
"""

import hashlib
import json
import logging
from typing import Callable, Optional

import trio

from libp2p.custom_types import TProtocol
from libp2p.peer.id import ID as PeerID

from ..common.messages import (
    EXECUTE_PROTOCOL_ID,
    ExecuteError,
    ExecuteResult,
    ExecuteStep,
    ExecutionProtocol,
    ProtocolStep,
    recv_msg,
    send_msg,
)
from ..common.observability import PipelineMetrics
from .attestation import AttestationBackend, LocalHashBackend

log = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Orchestrates topological-wave execution of a protocol's steps.

    Steps that share the same `sequence` value and whose `depends_on` deps
    are all already resolved are dispatched concurrently via a trio nursery.
    Each step's output is passed as input_data to the step identified by
    `input_from`, forming a verifiable data-flow pipeline.

    Usage:
        engine = ExecutionEngine(host,
                                 attestation=build_attestation_backend(),
                                 execute_timeout=30,
                                 retry_attempts=2)
        results = await engine.run(protocol, on_step=print_progress)
    """

    def __init__(
        self,
        host,
        attestation: Optional[AttestationBackend] = None,
        execute_timeout: float = 30.0,
        retry_attempts: int = 2,
    ):
        self.host = host
        self.attestation: AttestationBackend = attestation or LocalHashBackend()
        self.execute_timeout = execute_timeout
        self.retry_attempts = retry_attempts
        self._peer_id = host.get_id().pretty()

    async def run(
        self,
        protocol: ExecutionProtocol,
        on_step: Optional[Callable[[ProtocolStep, ExecuteResult], None]] = None,
        metrics: Optional[PipelineMetrics] = None,
    ) -> dict[str, ExecuteResult]:
        """
        Execute all steps respecting their dependency order.

        Steps whose `depends_on` deps are all satisfied are dispatched
        concurrently within a trio nursery.  The nursery waits for all
        concurrent steps before advancing to the next wave.

        Returns:
            A dict mapping step_id → ExecuteResult for all completed steps.
        """
        results: dict[str, ExecuteResult] = {}
        remaining: dict[str, ProtocolStep] = {s.step_id: s for s in protocol.steps}

        while remaining:
            # Collect steps whose every dependency has already been resolved
            ready = [
                s for s in remaining.values()
                if all(dep in results for dep in s.depends_on)
            ]
            if not ready:
                raise RuntimeError(
                    f"[ExecutionEngine] Unsatisfiable dependency in protocol "
                    f"'{protocol.protocol_id}'. Remaining steps: "
                    f"{list(remaining.keys())}"
                )

            wave_results: dict[str, ExecuteResult] = {}

            async def _run_one(step: ProtocolStep) -> None:
                # Resolve primary data input from the named predecessor
                input_data: Optional[dict] = None
                if step.input_from and step.input_from in results:
                    input_data = results[step.input_from].output_data

                if metrics:
                    metrics.steps_dispatched += 1
                try:
                    result = await self._dispatch(
                        protocol.protocol_id, protocol.task_id, step, input_data, metrics
                    )
                except Exception:
                    if metrics:
                        metrics.steps_failed += 1
                    raise
                wave_results[step.step_id] = result

                if metrics:
                    metrics.steps_succeeded += 1
                    metrics.record_step(step.step_id, result.execution_time_ms)

                # Layer 6: integrity check + attestation
                await self._verify_integrity(result, protocol.protocol_id)

                if on_step:
                    on_step(step, result)

            # Dispatch all ready steps concurrently
            async with trio.open_nursery() as nursery:
                for step in ready:
                    nursery.start_soon(_run_one, step)

            # Merge wave results and remove finished steps
            results.update(wave_results)
            for step in ready:
                del remaining[step.step_id]

        return results

    async def _dispatch(
        self,
        protocol_id: str,
        task_id: str,
        step: ProtocolStep,
        input_data: Optional[dict],
        metrics: Optional[PipelineMetrics] = None,
    ) -> ExecuteResult:
        """
        Open a libp2p stream to the worker and execute the step.
        Retries on timeout up to self.retry_attempts times with exponential backoff.
        Deliberate worker errors (ExecuteError) are not retried.
        """
        worker_pid = PeerID.from_base58(step.worker_peer_id)
        msg = ExecuteStep(
            sender=self._peer_id,
            protocol_id=protocol_id,
            step_id=step.step_id,
            task_id=task_id,
            capability=step.capability,
            session_token=step.session_token,
            parameters=step.parameters,
            input_data=input_data,
        )

        last_exc: Optional[Exception] = None
        for attempt in range(self.retry_attempts):
            response = None
            stream = await self.host.new_stream(
                worker_pid, [TProtocol(EXECUTE_PROTOCOL_ID)]
            )
            try:
                with trio.move_on_after(self.execute_timeout):
                    await send_msg(stream, msg)
                    response = await recv_msg(stream)
            finally:
                await stream.close()

            if isinstance(response, ExecuteError):
                # Deliberate worker error — do not retry
                raise RuntimeError(f"Worker error on {step.step_id}: {response.error}")

            if isinstance(response, ExecuteResult):
                return response

            # response is None → timeout
            if attempt < self.retry_attempts - 1:
                log.warning(
                    f"[ExecutionEngine] Step '{step.step_id}': timeout on attempt "
                    f"{attempt + 1}/{self.retry_attempts}, retrying..."
                )
                if metrics:
                    metrics.steps_retried += 1
                await trio.sleep(2 ** attempt)
            else:
                last_exc = RuntimeError(
                    f"Step '{step.step_id}': worker timed out after "
                    f"{self.retry_attempts} attempt(s)"
                )

        raise last_exc or RuntimeError(f"Step '{step.step_id}': dispatch failed")

    async def _verify_integrity(
        self, result: ExecuteResult, protocol_id: str
    ) -> None:
        """
        Layer 6 — Hybrid infrastructure verification.

        1. Recomputes the output hash locally and compares to the worker's hash.
        2. Submits (step_id, hash) to the configured AttestationBackend for an
           auditable record (local, RPC, or on-chain Filecoin/FEVM).
        """
        payload = json.dumps(result.output_data, sort_keys=True, separators=(",", ":"))
        computed = hashlib.sha256(payload.encode()).hexdigest()[:16]
        if result.verification_hash and result.verification_hash != computed:
            raise ValueError(
                f"Integrity check FAILED for {result.step_id}: "
                f"worker={result.verification_hash}, local={computed}"
            )

        await self.attestation.attest(
            step_id=result.step_id,
            protocol_id=protocol_id,
            worker_name=result.worker_name,
            output_hash=computed,
        )
