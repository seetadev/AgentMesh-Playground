"""
Observability: structured logging (TraceAdapter) and pipeline metrics.

TraceAdapter prefixes every log line with [task_id[:8]] so log lines from
concurrent pipeline runs stay distinguishable in a shared log stream.

PipelineMetrics accumulates counters and timings for one pipeline run and
can be serialised to a plain dict for inclusion in persistence records.
"""
import dataclasses
import logging
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PipelineMetrics:
    """Accumulates counters and timings for one pipeline run."""

    task_id: str
    protocol_id: str = ""

    # Negotiation counters
    negotiations_attempted: int = 0
    negotiations_accepted: int = 0
    negotiations_rejected: int = 0

    # Execution counters
    steps_dispatched: int = 0
    steps_succeeded: int = 0
    steps_failed: int = 0
    steps_retried: int = 0

    # Health check counters
    health_pings_sent: int = 0
    health_pings_failed: int = 0

    # Phase durations (seconds)
    discovery_duration_s: float = 0.0
    negotiation_duration_s: float = 0.0
    execution_duration_s: float = 0.0

    # Per-step timings: step_id → execution_time_ms
    step_timings_ms: dict[str, int] = field(default_factory=dict)

    def record_step(self, step_id: str, execution_time_ms: int) -> None:
        """Record a completed step's execution time."""
        self.step_timings_ms[step_id] = execution_time_ms

    def total_execution_ms(self) -> int:
        """Sum of all recorded per-step execution times."""
        return sum(self.step_timings_ms.values())

    def to_dict(self) -> dict:
        """Convert to a plain dict suitable for JSON serialisation."""
        return dataclasses.asdict(self)


class TraceAdapter(logging.LoggerAdapter):
    """
    Logger adapter that prefixes every log record with ``[trace_id]``.

    ``trace_id`` is the first 8 characters of the task_id so log lines
    from concurrent pipeline runs remain distinguishable.
    """

    def __init__(self, logger: logging.Logger, task_id: str) -> None:
        super().__init__(logger, extra={})
        self._prefix = f"[{task_id[:8]}]"

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        return f"{self._prefix} {msg}", kwargs


def make_trace_logger(logger: logging.Logger, task_id: str) -> TraceAdapter:
    """Return a TraceAdapter that prefixes every message with ``[task_id[:8]]``."""
    return TraceAdapter(logger, task_id)
