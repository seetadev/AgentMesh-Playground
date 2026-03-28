"""
Layer 4: Protocol Generator

Converts negotiated worker assignments into a deterministic, ordered
ExecutionProtocol. The generated protocol:

  - Orders steps by topological wave (independent steps get the same sequence
    number and can run concurrently in the Execution Engine)
  - Sets input_from to the closest upstream data-source step
  - Sets depends_on to all upstream steps that must complete first
  - Merges coordinator policies with any worker counter-proposals per step
  - Produces a content-addressable hash for integrity (on-chain attestation ready)

In production, the protocol can be serialised and submitted to Filecoin/FEVM
for verifiable on-chain execution attestation before workers begin processing.
"""

from ..common.auth import generate_session_token
from ..common.messages import (
    ExecutionProtocol,
    NegotiateOffer,
    Policy,
    PolicySet,
    ProtocolStep,
    WorkerCapability,
)

# Canonical execution order for multi-stage data pipelines
_STAGE_ORDER: list[WorkerCapability] = [
    WorkerCapability.DATA_VALIDATION,
    WorkerCapability.DATA_TRANSFORMATION,
    WorkerCapability.ANALYTICS,
    WorkerCapability.REPORT_GENERATION,
]

# Data-dependency graph: maps each capability to the capabilities whose
# output it directly depends on.  An empty list means the stage is an
# entry point (no upstream data required).
_STAGE_DATA_DEPS: dict[WorkerCapability, list[WorkerCapability]] = {
    WorkerCapability.DATA_VALIDATION: [],
    WorkerCapability.DATA_TRANSFORMATION: [WorkerCapability.DATA_VALIDATION],
    WorkerCapability.ANALYTICS: [
        WorkerCapability.DATA_TRANSFORMATION,
        WorkerCapability.DATA_VALIDATION,
    ],
    WorkerCapability.REPORT_GENERATION: [WorkerCapability.ANALYTICS],
}

# Default execution parameters injected into each step by capability type
_DEFAULT_PARAMS: dict[WorkerCapability, dict] = {
    WorkerCapability.DATA_VALIDATION: {
        "strict_schema": True,
        "null_tolerance": 0.05,
        "required_fields": ["id", "value", "timestamp"],
    },
    WorkerCapability.DATA_TRANSFORMATION: {
        "output_format": "json",
        "normalize": True,
        "remove_nulls": True,
    },
    WorkerCapability.ANALYTICS: {
        "metrics": ["count", "mean", "std", "min", "max"],
        "confidence_level": 0.95,
    },
    WorkerCapability.REPORT_GENERATION: {
        "format": "markdown",
        "include_summary": True,
        "verbosity": "standard",
    },
}


def _transitive_deps(
    cap: WorkerCapability,
    present: set[WorkerCapability],
    _cache: dict | None = None,
) -> set[WorkerCapability]:
    """
    Return the set of *assigned* capabilities that `cap` transitively depends on.
    Only capabilities that appear in `present` are included.
    """
    if _cache is None:
        _cache = {}
    if cap in _cache:
        return _cache[cap]
    result: set[WorkerCapability] = set()
    for dep in _STAGE_DATA_DEPS.get(cap, []):
        if dep in present:
            result.add(dep)
            result |= _transitive_deps(dep, present, _cache)
    _cache[cap] = result
    return result


def _primary_data_source(
    cap: WorkerCapability,
    present: set[WorkerCapability],
) -> WorkerCapability | None:
    """
    Return the *closest* assigned predecessor in the data-dependency chain.
    This is used to set `input_from` for data chaining.
    Returns None if `cap` is an entry point (no assigned predecessors).
    """
    # Walk direct deps in _STAGE_ORDER priority (most specific first)
    for dep in _STAGE_DATA_DEPS.get(cap, []):
        if dep in present:
            return dep
    return None


class ProtocolGenerator:
    """
    Compiles negotiated assignments into an ordered ExecutionProtocol.

    Steps are grouped into topological waves: all steps in the same wave
    share the same `sequence` value and have no mutual dependencies, so
    the Execution Engine can run them concurrently.

    Usage:
        gen = ProtocolGenerator()
        protocol = gen.generate(policy, assignments)
        # protocol.steps is ready for the Execution Engine
        # protocol.compute_hash() gives a deterministic fingerprint
    """

    def generate(
        self,
        policy: PolicySet,
        assignments: dict[WorkerCapability, NegotiateOffer],
    ) -> ExecutionProtocol:
        """
        Build an ExecutionProtocol from a set of negotiated worker assignments.

        Steps are ordered by topological wave (Kahn's algorithm).
        Steps in the same wave can execute in parallel; each step's
        input_from and depends_on fields encode the data-flow graph.
        """
        present = set(assignments.keys())

        # Build step_id for each assigned capability
        cap_to_step_id: dict[WorkerCapability, str] = {}
        for cap in assignments:
            idx = _STAGE_ORDER.index(cap) if cap in _STAGE_ORDER else 99
            cap_to_step_id[cap] = f"step_{idx + 1}_{cap.value}"

        # Compute in-degree (number of present deps) per capability
        in_degree: dict[WorkerCapability, int] = {}
        for cap in present:
            in_degree[cap] = len(
                [d for d in _STAGE_DATA_DEPS.get(cap, []) if d in present]
            )

        steps: list[ProtocolStep] = []
        wave = 1
        resolved: set[WorkerCapability] = set()
        remaining = set(present)

        while remaining:
            # All steps with zero remaining in-degree are ready this wave
            ready = [c for c in remaining if in_degree[c] == 0]
            if not ready:
                raise RuntimeError(
                    f"Cycle or unsatisfiable dependency among: {remaining}"
                )

            # Sort within a wave by canonical stage order for determinism
            ready.sort(
                key=lambda c: _STAGE_ORDER.index(c) if c in _STAGE_ORDER else 99
            )

            for cap in ready:
                offer = assignments[cap]
                step_id = cap_to_step_id[cap]

                # Data-flow chaining: find closest assigned predecessor
                primary_src = _primary_data_source(cap, present)
                input_from = cap_to_step_id[primary_src] if primary_src else None

                # All transitive assigned deps must complete first
                trans = _transitive_deps(cap, present)
                depends_on = sorted(cap_to_step_id[d] for d in trans)

                # Merge coordinator policies with worker counter-proposals
                merged: dict[str, Policy] = {p.key: p for p in policy.policies}
                for cp in offer.counter_policies:
                    merged[cp.key] = cp
                agreed = list(merged.values())

                steps.append(
                    ProtocolStep(
                        step_id=step_id,
                        sequence=wave,
                        capability=cap,
                        worker_peer_id=offer.sender,
                        worker_name=offer.worker_name,
                        input_from=input_from,
                        depends_on=depends_on,
                        session_token=generate_session_token(policy.task_id, step_id),
                        parameters=_DEFAULT_PARAMS.get(cap, {}),
                        agreed_policies=agreed,
                    )
                )

            # Update in-degree for the next wave
            resolved |= set(ready)
            remaining -= set(ready)
            for cap in remaining:
                in_degree[cap] = len(
                    [d for d in _STAGE_DATA_DEPS.get(cap, []) if d in present and d not in resolved]
                )

            wave += 1

        return ExecutionProtocol(
            task_id=policy.task_id,
            task_description=policy.task_description,
            steps=steps,
        )
