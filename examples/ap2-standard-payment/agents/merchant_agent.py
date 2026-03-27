"""AP2 Merchant Agent - builds carts, signs CartMandates, initiates payments.

Runs as an A2A server that:
1. Receives IntentMandates from Shopping Agents
2. Builds CartMandates with W3C PaymentRequest data
3. Signs CartMandates with merchant JWT
4. Forwards PaymentMandates to Payment Processor
5. Returns PaymentReceipts

Usage:
    python -m agents.merchant_agent --port 8001 --name "QuickShoot Studios" --price 350
"""
import argparse
import json
import logging
import sys
import os
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

# Ensure project root is on path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ap2_types.mandate import (
    IntentMandate, CartContents, CartMandate, PaymentMandate,
    CART_MANDATE_DATA_KEY, INTENT_MANDATE_DATA_KEY, PAYMENT_MANDATE_DATA_KEY,
)
from ap2_types.payment_request import (
    PaymentCurrencyAmount, PaymentItem, PaymentMethodData,
    PaymentDetailsInit, PaymentRequest, PaymentOptions,
)
from ap2_types.payment_receipt import PAYMENT_RECEIPT_DATA_KEY
from a2a_helpers.server import A2AServer
from a2a_helpers.client import A2AClient
from a2a_helpers.message_builder import MessageBuilder, ArtifactBuilder
from a2a_helpers.types import AP2_EXTENSION_URI
from signing import sign_cart_mandate

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)
logger = logging.getLogger(__name__)


class MerchantAgent:
    """AP2 Merchant Agent implementation."""

    def __init__(
        self,
        name: str = "QuickShoot Studios",
        price: float = 350.00,
        port: int = 8001,
        payment_processor_url: str = "http://localhost:8004",
    ):
        self.name = name
        self.price = price
        self.port = port
        self.payment_processor_url = payment_processor_url

        # In-memory stores
        self._carts: dict[str, CartMandate] = {}  # cart_id -> CartMandate
        self._tasks: dict[str, dict[str, Any]] = {}  # task_id -> task dict
        self._context_carts: dict[str, str] = {}  # context_id -> cart_id

    def _load_agent_card(self) -> dict[str, Any]:
        """Load AgentCard from JSON file or build dynamically."""
        card_path = Path(PROJECT_ROOT) / "agent_cards"
        # Try to find a matching card file
        for fname in os.listdir(card_path):
            fpath = card_path / fname
            if fpath.suffix == ".json":
                with open(fpath) as f:
                    card = json.load(f)
                if card.get("name") == self.name:
                    card["url"] = f"http://localhost:{self.port}"
                    return card

        # Build dynamically if no file found
        return {
            "name": self.name,
            "description": f"{self.name} - videography services",
            "url": f"http://localhost:{self.port}",
            "version": "0.1.0",
            "capabilities": {
                "extensions": [{
                    "uri": AP2_EXTENSION_URI,
                    "description": "AP2 Merchant role",
                    "required": True,
                    "params": {"roles": ["merchant"]},
                }]
            },
            "skills": [
                {"id": "search_catalog", "name": "Search Catalog", "description": "Search for videography services"},
                {"id": "create_cart", "name": "Create Cart", "description": "Build and sign a CartMandate"},
            ],
        }

    def _build_cart_mandate(self, intent: dict[str, Any], context_id: str) -> CartMandate:
        """Build a CartMandate from an IntentMandate."""
        cart_id = f"cart_{self.name.lower().replace(' ', '_')}_{uuid4().hex[:6]}"

        amount = PaymentCurrencyAmount(currency="USD", value=self.price)
        item = PaymentItem(
            label=f"Event Videography - 4 hours ({self.name})",
            amount=amount,
            refund_period=30,
        )
        method = PaymentMethodData(
            supported_methods="CARD",
            data={"payment_processor_url": self.payment_processor_url},
        )
        details = PaymentDetailsInit(
            id=f"order_{cart_id}",
            display_items=[item],
            total=PaymentItem(label="Total", amount=amount, refund_period=30),
        )
        payment_request = PaymentRequest(
            method_data=[method],
            details=details,
            options=PaymentOptions(request_shipping=False),
        )

        cart_contents = CartContents(
            id=cart_id,
            user_cart_confirmation_required=True,
            payment_request=payment_request,
            cart_expiry="2026-03-26T18:00:00Z",
            merchant_name=self.name,
        )

        # Sign with merchant JWT
        merchant_jwt = sign_cart_mandate(cart_contents, self.name)

        cart_mandate = CartMandate(
            contents=cart_contents,
            merchant_authorization=merchant_jwt,
        )

        # Store
        self._carts[cart_id] = cart_mandate
        self._context_carts[context_id] = cart_id
        logger.info(f"[{self.name}] Built CartMandate: {cart_id} - ${self.price:.2f}")

        return cart_mandate

    async def _handle_message_send(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle message/send JSON-RPC method."""
        message = params.get("message", {})
        parts = message.get("parts", [])
        context_id = message.get("contextId", str(uuid4()))
        task_id = str(uuid4())

        # Extract text and data from parts
        text_parts = []
        data_parts = {}
        for part in parts:
            if part.get("kind") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("kind") == "data":
                data_parts.update(part.get("data", {}))

        full_text = " ".join(text_parts).lower()

        # Route to handler based on content
        if INTENT_MANDATE_DATA_KEY in data_parts:
            return await self._handle_intent(data_parts, context_id, task_id)
        elif PAYMENT_MANDATE_DATA_KEY in data_parts:
            return await self._handle_payment(data_parts, context_id, task_id)
        elif "update_cart" in full_text or "shipping_address" in data_parts:
            return await self._handle_cart_update(data_parts, context_id, task_id)
        else:
            # Default: treat as a general query
            task = {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "completed"},
                "artifacts": [],
                "messages": [{
                    "messageId": str(uuid4()),
                    "role": "agent",
                    "parts": [{"kind": "text", "text": f"[{self.name}] Ready to serve. Send an IntentMandate to get started."}],
                }],
            }
            self._tasks[task_id] = task
            return {"task": task}

    async def _handle_intent(self, data_parts: dict, context_id: str, task_id: str) -> dict[str, Any]:
        """Handle incoming IntentMandate - build and return CartMandate."""
        intent_data = data_parts[INTENT_MANDATE_DATA_KEY]
        logger.info(f"[{self.name}] Received IntentMandate: {intent_data.get('natural_language_description', 'N/A')}")

        # Build CartMandate
        cart_mandate = self._build_cart_mandate(intent_data, context_id)

        # Build CartMandate artifact
        artifact = (
            ArtifactBuilder()
            .set_artifact_id(f"artifact_{cart_mandate.contents.id}")
            .set_name(f"{self.name} Cart")
            .add_data(CART_MANDATE_DATA_KEY, cart_mandate.model_dump(mode="json"))
            .build()
        )

        task = {
            "taskId": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "artifacts": [artifact],
            "messages": [{
                "messageId": str(uuid4()),
                "role": "agent",
                "parts": [{"kind": "text", "text": f"[{self.name}] CartMandate ready: ${self.price:.2f} for Event Videography - 4 hours"}],
            }],
        }
        self._tasks[task_id] = task
        logger.info(f"[{self.name}] Sent CartMandate: {cart_mandate.contents.id}")
        return {"task": task}

    async def _handle_payment(self, data_parts: dict, context_id: str, task_id: str) -> dict[str, Any]:
        """Handle incoming PaymentMandate - forward to Payment Processor."""
        payment_mandate_data = data_parts[PAYMENT_MANDATE_DATA_KEY]
        logger.info(f"[{self.name}] Received PaymentMandate - forwarding to Payment Processor")

        # Get the CartMandate for this context
        cart_id = self._context_carts.get(context_id)
        cart_mandate = self._carts.get(cart_id) if cart_id else None

        if not cart_mandate:
            task = {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "failed", "message": f"No cart found for context {context_id}"},
                "artifacts": [],
            }
            self._tasks[task_id] = task
            return {"task": task}

        # Forward to Payment Processor
        processor_msg = (
            MessageBuilder()
            .set_context_id(context_id)
            .set_role("user")
            .add_text("process_payment")
            .add_data(PAYMENT_MANDATE_DATA_KEY, payment_mandate_data)
            .add_data(CART_MANDATE_DATA_KEY, cart_mandate.model_dump(mode="json"))
            .build()
        )

        try:
            async with A2AClient(self.payment_processor_url) as client:
                response = await client.send_message(processor_msg)

            result = response.get("result", {})
            processor_task = result.get("task", {})

            # Extract PaymentReceipt from processor response
            artifacts = processor_task.get("artifacts", [])

            task = {
                "taskId": task_id,
                "contextId": context_id,
                "status": processor_task.get("status", {"state": "completed"}),
                "artifacts": artifacts,
                "messages": [{
                    "messageId": str(uuid4()),
                    "role": "agent",
                    "parts": [{"kind": "text", "text": f"[{self.name}] Payment processed"}],
                }],
            }
            self._tasks[task_id] = task
            logger.info(f"[{self.name}] Payment processed, status: {task['status']['state']}")
            return {"task": task}

        except Exception as e:
            logger.error(f"[{self.name}] Payment Processor error: {e}")
            task = {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "failed", "message": f"Payment Processor error: {str(e)}"},
                "artifacts": [],
            }
            self._tasks[task_id] = task
            return {"task": task}

    async def _handle_cart_update(self, data_parts: dict, context_id: str, task_id: str) -> dict[str, Any]:
        """Handle cart update (e.g., shipping address added)."""
        cart_id = self._context_carts.get(context_id)
        cart_mandate = self._carts.get(cart_id) if cart_id else None

        if not cart_mandate:
            task = {
                "taskId": task_id,
                "contextId": context_id,
                "status": {"state": "failed", "message": "No cart to update"},
                "artifacts": [],
            }
            self._tasks[task_id] = task
            return {"task": task}

        # Re-sign the CartMandate (in real AP2, this would update shipping/tax)
        new_jwt = sign_cart_mandate(cart_mandate.contents, self.name)
        cart_mandate.merchant_authorization = new_jwt
        self._carts[cart_id] = cart_mandate

        artifact = (
            ArtifactBuilder()
            .set_artifact_id(f"artifact_{cart_id}_updated")
            .set_name(f"{self.name} Cart (Updated)")
            .add_data(CART_MANDATE_DATA_KEY, cart_mandate.model_dump(mode="json"))
            .build()
        )

        task = {
            "taskId": task_id,
            "contextId": context_id,
            "status": {"state": "completed"},
            "artifacts": [artifact],
            "messages": [{
                "messageId": str(uuid4()),
                "role": "agent",
                "parts": [{"kind": "text", "text": f"[{self.name}] Cart updated and re-signed"}],
            }],
        }
        self._tasks[task_id] = task
        return {"task": task}

    async def _handle_tasks_get(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/get JSON-RPC method."""
        task_id = params.get("taskId", "")
        task = self._tasks.get(task_id)
        if task:
            return {"task": task}
        return {"error": f"Task not found: {task_id}"}

    async def _handle_tasks_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle tasks/cancel JSON-RPC method."""
        task_id = params.get("taskId", "")
        task = self._tasks.get(task_id)
        if task:
            task["status"] = {"state": "canceled"}
            logger.info(f"[{self.name}] Task {task_id} canceled")
            return {"task": task}
        return {"error": f"Task not found: {task_id}"}

    def run(self):
        """Start the Merchant Agent server."""
        agent_card = self._load_agent_card()
        server = A2AServer(agent_card, name=self.name)

        server.register_method("message/send", self._handle_message_send)
        server.register_method("tasks/get", self._handle_tasks_get)
        server.register_method("tasks/cancel", self._handle_tasks_cancel)

        logger.info(f"[{self.name}] Merchant Agent starting on port {self.port}")
        logger.info(f"[{self.name}] Price: ${self.price:.2f} | Processor: {self.payment_processor_url}")
        server.run(host="0.0.0.0", port=self.port)


def main():
    parser = argparse.ArgumentParser(description="AP2 Merchant Agent")
    parser.add_argument("--port", type=int, default=8001, help="Port to listen on")
    parser.add_argument("--name", type=str, default="QuickShoot Studios", help="Merchant name")
    parser.add_argument("--price", type=float, default=350.00, help="Service price")
    parser.add_argument("--processor", type=str, default="http://localhost:8004", help="Payment Processor URL")
    args = parser.parse_args()

    agent = MerchantAgent(
        name=args.name,
        price=args.price,
        port=args.port,
        payment_processor_url=args.processor,
    )
    agent.run()


if __name__ == "__main__":
    main()
