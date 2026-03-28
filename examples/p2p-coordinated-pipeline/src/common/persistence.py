"""
Persistence: save completed pipeline results to disk as JSON.

Results are written to results/<protocol_id>.json.  The schema is
intentionally flat so it can be indexed or queried without any
AgentMesh-specific tooling.

Schema version 1 layout:
  {
    "schema_version": "1",
    "saved_at":         "<ISO-8601 UTC timestamp>",
    "protocol_id":      str,
    "task_id":          str,
    "task_description": str,
    "protocol_hash":    str,
    "created_at":       str,
    "steps":            [ <ProtocolStep.model_dump()> … ],
    "results": {
      "<step_id>":      <ExecuteResult.model_dump()>
    },
    "metrics":          <PipelineMetrics.to_dict()> | null
  }
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .messages import ExecuteResult, ExecutionProtocol

if TYPE_CHECKING:
    from .observability import PipelineMetrics

log = logging.getLogger(__name__)

RESULTS_DIR = Path("results")


def save_pipeline_result(
    protocol: ExecutionProtocol,
    results: dict[str, ExecuteResult],
    metrics: Optional["PipelineMetrics"] = None,
) -> Path:
    """
    Persist a completed pipeline run to ``results/<protocol_id>.json``.

    Creates the results/ directory if it does not exist.
    Returns the path of the written file.
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{protocol.protocol_id}.json"

    payload: dict = {
        "schema_version": "1",
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "protocol_id": protocol.protocol_id,
        "task_id": protocol.task_id,
        "task_description": protocol.task_description,
        "protocol_hash": protocol.compute_hash(),
        "created_at": protocol.created_at,
        "steps": [s.model_dump() for s in protocol.steps],
        "results": {
            step_id: r.model_dump()
            for step_id, r in results.items()
        },
        "metrics": metrics.to_dict() if metrics is not None else None,
    }

    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    log.info(f"[persistence] Pipeline result saved → {out_path}")
    return out_path
