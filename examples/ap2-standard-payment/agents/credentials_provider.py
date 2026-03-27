"""AP2 Credentials Provider Agent.

Simulated digital wallet that manages user payment methods
and provides tokenized credentials via A2A protocol.

Run with:  python -m agents.credentials_provider --port 8003
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from a2a_helpers.server import A2AServer
from a2a_helpers.message_builder import MessageBuilder, ArtifactBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PREFIX = "[CredentialsProvider]"

# ---------------------------------------------------------------------------
# Mock user database
# ---------------------------------------------------------------------------
MOCK_USERS: dict[str, dict[str, Any]] = {
    "johndoe@example.com": {
        "name": "John Doe",
        "payment_methods": [
            {
                "method_name": "CARD",
                "last_four": "4242",
                "network": "VISA",
                "token": "tok_simulated_dpan_4242",
            },
            {
                "method_name": "CARD",
                "last_four": "8888",
                "network": "MASTERCARD",
                "token": "tok_simulated_dpan_8888",
            },
        ],
        "phone_last_four": "1234",
    }
}

# ---------------------------------------------------------------------------
# Session & task stores
# ---------------------------------------------------------------------------
# context_id -> {"user_id": str, "otp": str | None}
_sessions: dict[str, dict[str, Any]] = {}

# task_id -> task dict (for tasks/get retrieval)
_tasks: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _extract_text(parts: list[dict[str, Any]]) -> str:
    """Concatenate all text parts into a single lowercase string."""
    fragments = []
    for p in parts:
        if p.get("kind") == "text":
            fragments.append(p.get("text", ""))
    return " ".join(fragments).lower()


def _extract_data(parts: list[dict[str, Any]], key: str) -> Any:
    """Return the value for *key* from the first DataPart that contains it."""
    for p in parts:
        if p.get("kind") == "data" and key in p.get("data", {}):
            return p["data"][key]
    return None


def _build_task(
    task_id: str,
    context_id: str | None,
    state: str,
    artifacts: list[dict[str, Any]] | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "taskId": task_id,
        "status": {"state": state},
    }
    if context_id:
        task["contextId"] = context_id
    if artifacts:
        task["artifacts"] = artifacts
    if messages:
        task["messages"] = messages
    return task


def _select_payment_method(
    methods: list[dict[str, Any]], preference: str | None
) -> dict[str, Any]:
    """Pick the best matching payment method based on preference."""
    if preference:
        pref = preference.upper()
        for m in methods:
            if pref in (m["network"].upper(), m["method_name"].upper()):
                return m
    # Default to first method
    return methods[0]


# ---------------------------------------------------------------------------
# JSON-RPC handlers
# ---------------------------------------------------------------------------

async def handle_message_send(params: dict[str, Any]) -> dict[str, Any]:
    """Handle message/send requests."""
    message = params.get("message", {})
    parts = message.get("parts", [])
    context_id = message.get("contextId")
    text = _extract_text(parts)
    task_id = str(uuid.uuid4())

    logger.info(f"{PREFIX} message/send  text={text!r}  context_id={context_id}")

    # --- Connect User ---
    if "connect" in text:
        user_id = _extract_data(parts, "user_id")
        if not user_id or user_id not in MOCK_USERS:
            task = _build_task(task_id, context_id, "failed")
            reply = (
                MessageBuilder()
                .set_role("agent")
                .set_context_id(context_id or "")
                .add_text(f"User '{user_id}' not found.")
                .build()
            )
            task["messages"] = [reply]
            _tasks[task_id] = task
            return task

        user = MOCK_USERS[user_id]
        # Store session
        if context_id:
            _sessions[context_id] = {"user_id": user_id, "otp": None}

        method_summary = ", ".join(
            f"{m['network']} ending {m['last_four']}" for m in user["payment_methods"]
        )
        reply = (
            MessageBuilder()
            .set_role("agent")
            .set_context_id(context_id or "")
            .add_text(
                f"Connected as {user['name']}. "
                f"Available payment methods: {method_summary}."
            )
            .build()
        )
        task = _build_task(task_id, context_id, "completed", messages=[reply])
        _tasks[task_id] = task
        logger.info(f"{PREFIX} User '{user_id}' connected. task_id={task_id}")
        return task

    # --- Resolve session user for remaining operations ---
    session = _sessions.get(context_id or "")
    if not session:
        reply = (
            MessageBuilder()
            .set_role("agent")
            .set_context_id(context_id or "")
            .add_text("No user connected for this session. Send 'connect' first.")
            .build()
        )
        task = _build_task(task_id, context_id, "failed", messages=[reply])
        _tasks[task_id] = task
        return task

    user_id = session["user_id"]
    user = MOCK_USERS[user_id]

    # --- List Payment Methods ---
    if "payment_methods" in text or "list_methods" in text:
        artifact = (
            ArtifactBuilder()
            .set_name("payment_methods")
            .set_description("Available payment methods for this user")
            .add_data("payment_methods", user["payment_methods"])
            .build()
        )
        task = _build_task(task_id, context_id, "completed", artifacts=[artifact])
        _tasks[task_id] = task
        logger.info(f"{PREFIX} Listed payment methods for '{user_id}'. task_id={task_id}")
        return task

    # --- Provide Payment Token ---
    if "provide_token" in text or "get_token" in text:
        preference = _extract_data(parts, "method_preference")
        method = _select_payment_method(user["payment_methods"], preference)
        credential_data = {
            "payment_credential": {
                "method_name": method["method_name"],
                "details": {
                    "token": method["token"],
                    "last_four": method["last_four"],
                    "network": method["network"],
                },
            }
        }
        artifact = (
            ArtifactBuilder()
            .set_name("payment_credential")
            .set_description("Tokenized payment credential")
            .add_data_dict(credential_data)
            .build()
        )
        task = _build_task(task_id, context_id, "completed", artifacts=[artifact])
        _tasks[task_id] = task
        logger.info(
            f"{PREFIX} Provided token for '{method['network']} {method['last_four']}'. "
            f"task_id={task_id}"
        )
        return task

    # --- Generate OTP ---
    if "generate_otp" in text:
        session["otp"] = "123456"
        reply = (
            MessageBuilder()
            .set_role("agent")
            .set_context_id(context_id or "")
            .add_text(f"OTP sent to phone ending {user['phone_last_four']}")
            .build()
        )
        task = _build_task(task_id, context_id, "completed", messages=[reply])
        _tasks[task_id] = task
        logger.info(f"{PREFIX} OTP generated for '{user_id}'. task_id={task_id}")
        return task

    # --- Unknown operation ---
    reply = (
        MessageBuilder()
        .set_role("agent")
        .set_context_id(context_id or "")
        .add_text(
            "Unknown operation. Supported: connect, payment_methods, "
            "list_methods, provide_token, get_token, generate_otp."
        )
        .build()
    )
    task = _build_task(task_id, context_id, "failed", messages=[reply])
    _tasks[task_id] = task
    return task


async def handle_tasks_get(params: dict[str, Any]) -> dict[str, Any]:
    """Handle tasks/get requests."""
    task_id = params.get("taskId") or params.get("task_id", "")
    logger.info(f"{PREFIX} tasks/get  task_id={task_id}")
    task = _tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    return task


async def handle_tasks_cancel(params: dict[str, Any]) -> dict[str, Any]:
    """Handle tasks/cancel requests."""
    task_id = params.get("taskId") or params.get("task_id", "")
    logger.info(f"{PREFIX} tasks/cancel  task_id={task_id}")
    task = _tasks.get(task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    task["status"] = {"state": "canceled"}
    return task


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AP2 Credentials Provider Agent")
    parser.add_argument("--port", type=int, default=8003, help="Port to listen on")
    args = parser.parse_args()

    # Load AgentCard
    card_path = Path(__file__).resolve().parent.parent / "agent_cards" / "credentials_provider.json"
    with open(card_path) as f:
        agent_card = json.load(f)

    # Update URL to reflect actual port
    agent_card["url"] = f"http://localhost:{args.port}"

    server = A2AServer(agent_card=agent_card, name="CredentialsProvider")
    server.register_method("message/send", handle_message_send)
    server.register_method("tasks/get", handle_tasks_get)
    server.register_method("tasks/cancel", handle_tasks_cancel)

    logger.info(f"{PREFIX} Starting on port {args.port}")
    server.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
