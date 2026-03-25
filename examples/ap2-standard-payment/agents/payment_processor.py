"""AP2 Payment Processor Agent.

Validates mandates, authorizes payments, and manages escrow.
Exposes an A2A JSON-RPC server on a configurable port (default 8004).

Run with:
    python -m agents.payment_processor --port 8004
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

# ---------------------------------------------------------------------------
# Path setup so imports work when run as ``python -m agents.payment_processor``
# from the project root **or** from the agents/ directory.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from a2a_helpers import A2AServer, ArtifactBuilder, MessageBuilder
from a2a_helpers.types import TaskState
from ap2_types import (
    CART_MANDATE_DATA_KEY,
    PAYMENT_MANDATE_DATA_KEY,
    PAYMENT_RECEIPT_DATA_KEY,
    CartMandate,
    PaymentMandate,
    PaymentReceipt,
    PaymentCurrencyAmount,
    Success,
    Error,
    Failure,
)
from escrow import EscrowManager
from signing import verify_cart_mandate, verify_payment_mandate

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

PREFIX = "[PaymentProcessor]"

# ---------------------------------------------------------------------------
# Agent card loader
# ---------------------------------------------------------------------------

def _load_agent_card() -> dict[str, Any]:
    card_path = os.path.join(_PROJECT_ROOT, "agent_cards", "payment_processor.json")
    with open(card_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_data_part(parts: list[dict[str, Any]], key: str) -> Optional[Any]:
    """Return the value for *key* from the first DataPart that contains it."""
    for part in parts:
        if part.get("kind") == "data" and key in part.get("data", {}):
            return part["data"][key]
    return None


def _extract_text(parts: list[dict[str, Any]]) -> str:
    """Concatenate all TextPart texts."""
    texts = []
    for part in parts:
        if part.get("kind") == "text":
            texts.append(part.get("text", ""))
    return " ".join(texts)


def _build_task(
    task_id: str,
    context_id: Optional[str],
    state: TaskState,
    *,
    artifacts: Optional[list[dict[str, Any]]] = None,
    messages: Optional[list[dict[str, Any]]] = None,
    status_message: Optional[str] = None,
) -> dict[str, Any]:
    """Build a Task dict matching A2A wire format."""
    task: dict[str, Any] = {
        "taskId": task_id,
        "status": {"state": state.value},
    }
    if context_id:
        task["contextId"] = context_id
    if status_message:
        task["status"]["message"] = status_message
    if artifacts:
        task["artifacts"] = artifacts
    if messages:
        task["messages"] = messages
    return task


# ---------------------------------------------------------------------------
# PaymentProcessorAgent
# ---------------------------------------------------------------------------

class PaymentProcessorAgent:
    """Core logic for the Payment Processor."""

    def __init__(self):
        self.escrow = EscrowManager()
        # task_id -> task dict
        self._tasks: dict[str, dict[str, Any]] = {}
        # context_id -> pending payment info (for OTP challenge flow)
        self._pending: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # message/send handler
    # ------------------------------------------------------------------

    async def handle_message_send(self, params: dict[str, Any]) -> dict[str, Any]:
        message = params.get("message", {})
        parts = message.get("parts", [])
        context_id = message.get("contextId") or str(uuid4())
        text = _extract_text(parts)
        task_id = str(uuid4())

        # --- OTP challenge response flow ---
        challenge_response = _extract_data_part(parts, "challenge_response")
        if challenge_response is not None:
            return await self._handle_otp_response(
                context_id, challenge_response, task_id
            )

        # --- Release escrow ---
        if "release_escrow" in text.lower():
            return await self._handle_release_escrow(parts, context_id, task_id)

        # --- Refund ---
        if "refund" in text.lower():
            return await self._handle_refund(parts, context_id, task_id)

        # --- Check if OTP challenge is requested ---
        enable_otp = "enable_otp_challenge" in text.lower()

        # --- Process payment (default path) ---
        payment_mandate_data = _extract_data_part(parts, PAYMENT_MANDATE_DATA_KEY)
        cart_mandate_data = _extract_data_part(parts, CART_MANDATE_DATA_KEY)

        if payment_mandate_data is None:
            task = _build_task(
                task_id,
                context_id,
                TaskState.FAILED,
                status_message="Missing PaymentMandate in message",
            )
            self._tasks[task_id] = task
            logger.warning(f"{PREFIX} Missing PaymentMandate in message")
            return task

        return await self._process_payment(
            payment_mandate_data,
            cart_mandate_data,
            context_id,
            task_id,
            enable_otp=enable_otp,
        )

    # ------------------------------------------------------------------
    # Payment processing
    # ------------------------------------------------------------------

    async def _process_payment(
        self,
        payment_mandate_data: dict[str, Any],
        cart_mandate_data: Optional[dict[str, Any]],
        context_id: str,
        task_id: str,
        *,
        enable_otp: bool = False,
    ) -> dict[str, Any]:
        logger.info(f"{PREFIX} Processing payment for context {context_id}")

        # Parse models
        try:
            payment_mandate = PaymentMandate.model_validate(payment_mandate_data)
        except Exception as exc:
            logger.error(f"{PREFIX} Invalid PaymentMandate: {exc}")
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message=f"Invalid PaymentMandate: {exc}",
            )
            self._tasks[task_id] = task
            return task

        cart_mandate: Optional[CartMandate] = None
        if cart_mandate_data:
            try:
                cart_mandate = CartMandate.model_validate(cart_mandate_data)
            except Exception as exc:
                logger.error(f"{PREFIX} Invalid CartMandate: {exc}")
                task = _build_task(
                    task_id, context_id, TaskState.FAILED,
                    status_message=f"Invalid CartMandate: {exc}",
                )
                self._tasks[task_id] = task
                return task

        # --- Verify CartMandate merchant_authorization JWT ---
        if cart_mandate and cart_mandate.merchant_authorization:
            merchant_name = cart_mandate.contents.merchant_name
            try:
                cart_claims = verify_cart_mandate(
                    cart_mandate.merchant_authorization, merchant_name
                )
                logger.info(
                    f"{PREFIX} CartMandate JWT verified for merchant={merchant_name}"
                )
            except Exception as exc:
                logger.error(
                    f"{PREFIX} CartMandate JWT verification failed: {exc}"
                )
                task = _build_task(
                    task_id, context_id, TaskState.FAILED,
                    status_message=f"CartMandate verification failed: {exc}",
                )
                self._tasks[task_id] = task
                return task

        # --- Verify PaymentMandate user_authorization ---
        if payment_mandate.user_authorization:
            try:
                pm_claims = verify_payment_mandate(
                    payment_mandate.user_authorization
                )
                logger.info(f"{PREFIX} PaymentMandate user_authorization verified")
            except Exception as exc:
                logger.error(
                    f"{PREFIX} PaymentMandate verification failed: {exc}"
                )
                task = _build_task(
                    task_id, context_id, TaskState.FAILED,
                    status_message=f"PaymentMandate verification failed: {exc}",
                )
                self._tasks[task_id] = task
                return task

        # --- Extract amount ---
        pmc = payment_mandate.payment_mandate_contents
        total_amount = pmc.payment_details_total.amount.value
        currency = pmc.payment_details_total.amount.currency

        # --- Simulate network authorization ---
        if total_amount > 10000:
            logger.warning(
                f"{PREFIX} Amount ${total_amount:.2f} exceeds limit, declining"
            )
            receipt = PaymentReceipt(
                payment_mandate_id=pmc.payment_mandate_id,
                payment_id=f"txn_{uuid4().hex[:8]}",
                amount=PaymentCurrencyAmount(currency=currency, value=total_amount),
                payment_status=Failure(
                    failure_message=(
                        f"Payment declined: amount ${total_amount:.2f} exceeds"
                        " authorization limit of $10,000"
                    )
                ),
            )
            artifact = (
                ArtifactBuilder()
                .set_name("PaymentReceipt")
                .set_description("Payment declined")
                .add_data(PAYMENT_RECEIPT_DATA_KEY, receipt.model_dump(mode="json"))
                .build()
            )
            task = _build_task(
                task_id, context_id, TaskState.COMPLETED,
                artifacts=[artifact],
                status_message="Payment declined - amount exceeds limit",
            )
            self._tasks[task_id] = task
            return task

        # --- OTP challenge flow ---
        if enable_otp:
            logger.info(f"{PREFIX} OTP challenge requested for context {context_id}")
            self._pending[context_id] = {
                "payment_mandate": payment_mandate_data,
                "cart_mandate": cart_mandate_data,
                "total_amount": total_amount,
                "currency": currency,
                "task_id": task_id,
            }
            otp_message = (
                MessageBuilder()
                .set_role("agent")
                .set_context_id(context_id)
                .add_text(
                    f"OTP verification required for payment of"
                    f" ${total_amount:.2f} {currency}."
                    f" Please provide the OTP sent to your registered device."
                )
                .build()
            )
            task = _build_task(
                task_id, context_id, TaskState.INPUT_REQUIRED,
                messages=[otp_message],
                status_message="OTP verification required",
            )
            self._tasks[task_id] = task
            return task

        # --- Authorize & create escrow hold ---
        return self._complete_payment(
            pmc.payment_mandate_id, total_amount, currency, context_id, task_id
        )

    # ------------------------------------------------------------------
    # Complete payment (creates escrow + receipt)
    # ------------------------------------------------------------------

    def _complete_payment(
        self,
        payment_mandate_id: str,
        total_amount: float,
        currency: str,
        context_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        payment_id = f"txn_{uuid4().hex[:8]}"

        escrow_hold = self.escrow.hold(
            amount=total_amount,
            currency=currency,
            payment_id=payment_id,
        )
        logger.info(
            f"{PREFIX} Escrow hold created: {payment_id}"
            f" ${total_amount:.2f} {currency}"
        )

        logger.info(
            f"{PREFIX} Payment authorized: {payment_id}"
            f" ${total_amount:.2f} {currency}"
        )

        receipt = PaymentReceipt(
            payment_mandate_id=payment_mandate_id,
            payment_id=payment_id,
            amount=PaymentCurrencyAmount(currency=currency, value=total_amount),
            payment_status=Success(
                merchant_confirmation_id=f"mconf_{uuid4().hex[:8]}",
                psp_confirmation_id=f"psp_{uuid4().hex[:8]}",
                network_confirmation_id=f"net_{uuid4().hex[:8]}",
            ),
        )

        artifact = (
            ArtifactBuilder()
            .set_name("PaymentReceipt")
            .set_description("Payment authorized with escrow hold")
            .add_data(PAYMENT_RECEIPT_DATA_KEY, receipt.model_dump(mode="json"))
            .build()
        )

        task = _build_task(
            task_id, context_id, TaskState.COMPLETED,
            artifacts=[artifact],
            status_message="Payment authorized successfully",
        )
        self._tasks[task_id] = task
        return task

    # ------------------------------------------------------------------
    # OTP response handler
    # ------------------------------------------------------------------

    async def _handle_otp_response(
        self,
        context_id: str,
        challenge_response: dict[str, Any],
        task_id: str,
    ) -> dict[str, Any]:
        pending = self._pending.get(context_id)
        if not pending:
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message="No pending OTP challenge for this context",
            )
            self._tasks[task_id] = task
            return task

        otp = str(challenge_response.get("otp", ""))
        if otp != "123456":
            logger.warning(f"{PREFIX} Invalid OTP for context {context_id}")
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message="Invalid OTP code",
            )
            self._tasks[task_id] = task
            return task

        logger.info(f"{PREFIX} OTP verified for context {context_id}")

        # Use the original task_id from the pending entry
        original_task_id = pending["task_id"]
        payment_mandate = PaymentMandate.model_validate(pending["payment_mandate"])
        pmc = payment_mandate.payment_mandate_contents

        result = self._complete_payment(
            pmc.payment_mandate_id,
            pending["total_amount"],
            pending["currency"],
            context_id,
            original_task_id,
        )
        del self._pending[context_id]
        return result

    # ------------------------------------------------------------------
    # Release escrow
    # ------------------------------------------------------------------

    async def _handle_release_escrow(
        self,
        parts: list[dict[str, Any]],
        context_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        payment_id = _extract_data_part(parts, "payment_id")
        if not payment_id:
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message="Missing payment_id for escrow release",
            )
            self._tasks[task_id] = task
            return task

        try:
            release = self.escrow.release(payment_id)
            logger.info(f"{PREFIX} Escrow released for {payment_id}")
            artifact = (
                ArtifactBuilder()
                .set_name("EscrowRelease")
                .set_description("Escrow funds released")
                .add_data("escrow_release", release.model_dump(mode="json"))
                .build()
            )
            task = _build_task(
                task_id, context_id, TaskState.COMPLETED,
                artifacts=[artifact],
                status_message=f"Escrow released for {payment_id}",
            )
        except ValueError as exc:
            logger.error(f"{PREFIX} Escrow release failed: {exc}")
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message=str(exc),
            )

        self._tasks[task_id] = task
        return task

    # ------------------------------------------------------------------
    # Refund
    # ------------------------------------------------------------------

    async def _handle_refund(
        self,
        parts: list[dict[str, Any]],
        context_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        payment_id = _extract_data_part(parts, "payment_id")
        if not payment_id:
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message="Missing payment_id for refund",
            )
            self._tasks[task_id] = task
            return task

        try:
            refund = self.escrow.refund(payment_id, reason="user_requested")
            logger.info(f"{PREFIX} Refund processed for {payment_id}")
            artifact = (
                ArtifactBuilder()
                .set_name("EscrowRefund")
                .set_description("Escrow funds refunded")
                .add_data("escrow_refund", refund.model_dump(mode="json"))
                .build()
            )
            task = _build_task(
                task_id, context_id, TaskState.COMPLETED,
                artifacts=[artifact],
                status_message=f"Refund processed for {payment_id}",
            )
        except ValueError as exc:
            logger.error(f"{PREFIX} Refund failed: {exc}")
            task = _build_task(
                task_id, context_id, TaskState.FAILED,
                status_message=str(exc),
            )

        self._tasks[task_id] = task
        return task

    # ------------------------------------------------------------------
    # tasks/get handler
    # ------------------------------------------------------------------

    async def handle_tasks_get(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId")
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")
        logger.info(f"{PREFIX} tasks/get -> {task_id}")
        return task

    # ------------------------------------------------------------------
    # tasks/cancel handler
    # ------------------------------------------------------------------

    async def handle_tasks_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        task_id = params.get("taskId")
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task not found: {task_id}")

        current_state = task["status"]["state"]
        if current_state in (TaskState.COMPLETED.value, TaskState.CANCELED.value):
            raise ValueError(
                f"Cannot cancel task in state '{current_state}'"
            )

        task["status"]["state"] = TaskState.CANCELED.value
        task["status"]["message"] = "Task canceled by request"
        logger.info(f"{PREFIX} tasks/cancel -> {task_id}")

        # If there was a pending OTP challenge, clean it up
        context_id = task.get("contextId")
        if context_id and context_id in self._pending:
            del self._pending[context_id]

        return task


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

def create_server(port: int = 8004) -> A2AServer:
    """Create and configure the PaymentProcessor A2A server."""
    agent_card = _load_agent_card()
    # Update the URL to reflect the actual port
    agent_card["url"] = f"http://localhost:{port}"

    agent = PaymentProcessorAgent()
    server = A2AServer(agent_card, name="PaymentProcessor")
    server.register_method("message/send", agent.handle_message_send)
    server.register_method("tasks/get", agent.handle_tasks_get)
    server.register_method("tasks/cancel", agent.handle_tasks_cancel)

    return server


def main():
    parser = argparse.ArgumentParser(description="AP2 Payment Processor Agent")
    parser.add_argument(
        "--port", type=int, default=8004, help="Port to listen on (default: 8004)"
    )
    args = parser.parse_args()

    logger.info(f"{PREFIX} Starting on port {args.port}")
    server = create_server(port=args.port)
    server.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
