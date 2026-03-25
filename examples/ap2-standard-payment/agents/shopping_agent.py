"""AP2 Shopping Agent - orchestrates the complete purchase flow.

This is the main agent that drives the AP2 payment flow:
1. Discovers merchants via AgentCards
2. Connects user to Credentials Provider
3. Sends IntentMandate to merchants
4. Compares competing CartMandates
5. Selects best offer, rejects others
6. Obtains payment token from Credentials Provider
7. Creates and signs PaymentMandate
8. Sends payment to selected merchant
9. Handles OTP challenge if needed
10. Receives PaymentReceipt

Usage:
    python -m agents.shopping_agent --port 8000 --budget 400 \
        --merchants http://localhost:8001 http://localhost:8002 \
        --credentials-provider http://localhost:8003
"""
import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

# Ensure project root is on path
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ap2_types.mandate import (
    IntentMandate, CartMandate, PaymentMandate, PaymentMandateContents,
    CART_MANDATE_DATA_KEY, INTENT_MANDATE_DATA_KEY, PAYMENT_MANDATE_DATA_KEY,
)
from ap2_types.payment_request import PaymentCurrencyAmount, PaymentItem, PaymentResponse
from ap2_types.payment_receipt import PAYMENT_RECEIPT_DATA_KEY
from a2a_helpers.client import A2AClient
from a2a_helpers.message_builder import MessageBuilder
from a2a_helpers.types import AP2_EXTENSION_URI
from signing import sign_payment_mandate

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

P = "[SHOPPING]"  # Log prefix


def _extract_data(parts: list[dict], key: str) -> Any:
    """Extract a data value from A2A message parts."""
    for part in parts:
        if part.get("kind") == "data" and key in part.get("data", {}):
            return part["data"][key]
    return None


def _extract_text(parts: list[dict]) -> str:
    """Extract concatenated text from A2A message parts."""
    return " ".join(p.get("text", "") for p in parts if p.get("kind") == "text")


async def run_shopping_flow(
    merchant_urls: list[str],
    credentials_provider_url: str,
    budget: float,
    user_email: str = "johndoe@example.com",
):
    """Execute the complete AP2 payment flow."""

    context_id = f"payment_session_{uuid4().hex[:8]}"
    logger.info(f"{P} ========================================")
    logger.info(f"{P} AP2 Standard Payment Demo")
    logger.info(f"{P} ========================================")
    logger.info("")

    # ── Step 1: Discovery ──────────────────────────────────────────
    logger.info(f"{P} Step 1: DISCOVERY - Fetching AgentCards")
    merchants = []
    for url in merchant_urls:
        try:
            async with A2AClient(url) as client:
                card = await client.get_agent_card()
            # Check AP2 extension
            extensions = card.get("capabilities", {}).get("extensions", [])
            ap2_ext = next((e for e in extensions if e.get("uri") == AP2_EXTENSION_URI), None)
            if ap2_ext and "merchant" in ap2_ext.get("params", {}).get("roles", []):
                merchants.append({"url": url, "card": card, "name": card.get("name", url)})
                logger.info(f"{P}   Found merchant: {card['name']} at {url}")
            else:
                logger.info(f"{P}   Skipping {url} - no AP2 merchant role")
        except Exception as e:
            logger.info(f"{P}   Failed to reach {url}: {e}")

    if not merchants:
        logger.info(f"{P} ERROR: No merchants found. Aborting.")
        return

    # Discover Credentials Provider
    try:
        async with A2AClient(credentials_provider_url) as client:
            cp_card = await client.get_agent_card()
        logger.info(f"{P}   Found credentials provider: {cp_card.get('name', '?')}")
    except Exception as e:
        logger.info(f"{P} ERROR: Cannot reach Credentials Provider: {e}")
        return

    logger.info("")

    # ── Step 2: Setup - Connect User ───────────────────────────────
    logger.info(f"{P} Step 2: SETUP - Connecting user to Credentials Provider")
    connect_msg = (
        MessageBuilder()
        .set_context_id(context_id)
        .add_text("connect user")
        .add_data("user_id", user_email)
        .build()
    )
    async with A2AClient(credentials_provider_url) as client:
        connect_resp = await client.send_message(connect_msg)

    cp_result = connect_resp.get("result", {})
    # CP returns task directly or wrapped
    cp_messages = cp_result.get("messages", [])
    if cp_messages:
        text = _extract_text(cp_messages[0].get("parts", []))
        logger.info(f"{P}   {text}")
    else:
        logger.info(f"{P}   User {user_email} connected")
    logger.info("")

    # ── Step 3: Intent - Send IntentMandate to all merchants ───────
    logger.info(f"{P} Step 3: INTENT - Broadcasting IntentMandate")
    intent = IntentMandate(
        user_cart_confirmation_required=True,
        natural_language_description="Book a videographer for an event, 4 hours",
        requires_refundability=True,
        intent_expiry="2026-03-26T15:00:00Z",
    )

    intent_msg = (
        MessageBuilder()
        .set_context_id(context_id)
        .add_data(INTENT_MANDATE_DATA_KEY, intent.model_dump(mode="json"))
        .build()
    )

    logger.info(f'{P}   Intent: "{intent.natural_language_description}" (budget: ${budget:.2f})')

    # Send to all merchants concurrently
    cart_offers: list[dict[str, Any]] = []

    async def send_intent(merchant: dict) -> Optional[dict]:
        try:
            async with A2AClient(merchant["url"]) as client:
                resp = await client.send_message(intent_msg)
            result = resp.get("result", {})
            task = result.get("task", result)
            return {"merchant": merchant, "task": task}
        except Exception as e:
            logger.info(f"{P}   Failed to send intent to {merchant['name']}: {e}")
            return None

    results = await asyncio.gather(*[send_intent(m) for m in merchants])
    for r in results:
        if r:
            cart_offers.append(r)
    logger.info("")

    # ── Step 4: Cart Comparison ────────────────────────────────────
    logger.info(f"{P} Step 4: CART COMPARISON - Evaluating offers")
    valid_offers = []
    for offer in cart_offers:
        task = offer["task"]
        artifacts = task.get("artifacts", [])
        if not artifacts:
            logger.info(f"{P}   {offer['merchant']['name']}: No cart returned")
            continue

        # Extract CartMandate from artifact
        cart_data = _extract_data(artifacts[0].get("parts", []), CART_MANDATE_DATA_KEY)
        if not cart_data:
            logger.info(f"{P}   {offer['merchant']['name']}: No CartMandate in artifact")
            continue

        contents = cart_data.get("contents", {})
        total = contents.get("payment_request", {}).get("details", {}).get("total", {}).get("amount", {})
        price = total.get("value", 0)
        currency = total.get("currency", "USD")
        has_jwt = bool(cart_data.get("merchant_authorization"))

        if price <= budget:
            valid_offers.append({
                "merchant": offer["merchant"],
                "task": task,
                "cart_data": cart_data,
                "price": price,
                "currency": currency,
            })
            logger.info(f"{P}   {offer['merchant']['name']}: ${price:.2f} {currency} - WITHIN BUDGET (JWT: {has_jwt})")
        else:
            logger.info(f"{P}   {offer['merchant']['name']}: ${price:.2f} {currency} - OVER BUDGET (rejecting)")

    if not valid_offers:
        logger.info(f"{P} ERROR: No valid offers within budget. Aborting.")
        return

    logger.info("")

    # ── Step 5: Selection ──────────────────────────────────────────
    # Pick cheapest
    selected = min(valid_offers, key=lambda x: x["price"])
    rejected = [o for o in cart_offers if o["merchant"]["url"] != selected["merchant"]["url"]]

    logger.info(f"{P} Step 5: SELECTION - Choosing best offer")
    logger.info(f"{P}   Selected: {selected['merchant']['name']} at ${selected['price']:.2f}")

    # Reject others
    for offer in rejected:
        task_id = offer["task"].get("taskId", "")
        if task_id:
            try:
                async with A2AClient(offer["merchant"]["url"]) as client:
                    await client.cancel_task(task_id)
                logger.info(f"{P}   Rejected: {offer['merchant']['name']}")
            except Exception:
                pass
    logger.info("")

    # ── Step 6: Payment Method ─────────────────────────────────────
    logger.info(f"{P} Step 6: PAYMENT METHOD - Getting token from Credentials Provider")
    token_msg = (
        MessageBuilder()
        .set_context_id(context_id)
        .add_text("provide_token get_token")
        .add_data("method_preference", "VISA")
        .build()
    )
    async with A2AClient(credentials_provider_url) as client:
        token_resp = await client.send_message(token_msg)

    token_result = token_resp.get("result", {})
    # Extract payment credential
    token_artifacts = token_result.get("artifacts", [])
    payment_credential = None
    if token_artifacts:
        cred_data = token_artifacts[0].get("parts", [{}])[0].get("data", {})
        payment_credential = cred_data.get("payment_credential", {})

    if payment_credential:
        details = payment_credential.get("details", {})
        logger.info(f"{P}   Token: {details.get('network', '?')} ending {details.get('last_four', '?')}")
        logger.info(f"{P}   Token ID: {details.get('token', '?')}")
    else:
        logger.info(f"{P}   WARNING: No payment credential returned, using fallback")
        payment_credential = {
            "method_name": "CARD",
            "details": {"token": "tok_fallback", "last_four": "0000", "network": "UNKNOWN"},
        }
    logger.info("")

    # ── Step 7: User Approval ──────────────────────────────────────
    logger.info(f"{P} Step 7: USER APPROVAL - Presenting cart on trusted surface")
    logger.info(f"{P}   +------------------------------------------+")
    logger.info(f"{P}   | CONFIRM PURCHASE                         |")
    logger.info(f"{P}   |                                          |")
    logger.info(f"{P}   | Merchant: {selected['merchant']['name']:<29}|")
    items = selected["cart_data"].get("contents", {}).get("payment_request", {}).get("details", {}).get("display_items", [])
    for item in items:
        label = item.get("label", "?")[:35]
        logger.info(f"{P}   | Item: {label:<33}|")
    logger.info(f"{P}   | Total: ${selected['price']:.2f} {selected['currency']:<27}|")
    cred_details = payment_credential.get("details", {})
    logger.info(f"{P}   | Payment: {cred_details.get('network', '?')} ending {cred_details.get('last_four', '?'):<18}|")
    logger.info(f"{P}   | Refund period: 30 days                  |")
    logger.info(f"{P}   |                                          |")
    logger.info(f"{P}   | [Approved - simulated biometric auth]    |")
    logger.info(f"{P}   +------------------------------------------+")
    logger.info("")

    # ── Step 8: PaymentMandate Creation ────────────────────────────
    logger.info(f"{P} Step 8: PAYMENT MANDATE - Creating and signing")
    cart_mandate = CartMandate(**selected["cart_data"])
    cart_contents = cart_mandate.contents
    order_id = cart_contents.payment_request.details.id

    pay_resp = PaymentResponse(
        request_id=order_id,
        method_name=payment_credential.get("method_name", "CARD"),
        details=cred_details,
    )
    pmc = PaymentMandateContents(
        payment_mandate_id=f"pm_{uuid4().hex[:8]}",
        payment_details_id=order_id,
        payment_details_total=PaymentItem(
            label="Total",
            amount=PaymentCurrencyAmount(currency=selected["currency"], value=selected["price"]),
            refund_period=30,
        ),
        payment_response=pay_resp,
        merchant_agent=selected["merchant"]["name"],
    )

    # Sign with simulated user key
    user_auth = sign_payment_mandate(cart_mandate, pmc)
    payment_mandate = PaymentMandate(
        payment_mandate_contents=pmc,
        user_authorization=user_auth,
    )

    logger.info(f"{P}   PaymentMandate ID: {pmc.payment_mandate_id}")
    logger.info(f"{P}   User SD-JWT-VC: {user_auth[:50]}...")
    logger.info("")

    # ── Step 9: Payment Execution ──────────────────────────────────
    logger.info(f"{P} Step 9: PAYMENT EXECUTION - Sending to {selected['merchant']['name']}")
    payment_msg = (
        MessageBuilder()
        .set_context_id(context_id)
        .add_data(PAYMENT_MANDATE_DATA_KEY, payment_mandate.model_dump(mode="json"))
        .build()
    )

    async with A2AClient(selected["merchant"]["url"]) as client:
        payment_resp = await client.send_message(payment_msg)

    pay_result = payment_resp.get("result", {})
    pay_task = pay_result.get("task", pay_result)
    pay_status = pay_task.get("status", {}).get("state", "unknown")

    # Check for OTP challenge
    if pay_status == "input-required":
        logger.info("")
        logger.info(f"{P} Step 10: OTP CHALLENGE - 3D Secure equivalent")
        challenge_msg = pay_task.get("messages", [{}])[0] if pay_task.get("messages") else {}
        challenge_text = _extract_text(challenge_msg.get("parts", []))
        logger.info(f"{P}   Challenge: {challenge_text or 'Enter OTP'}")
        logger.info(f"{P}   User enters: 123456 (simulated)")

        # Send OTP response
        otp_msg = (
            MessageBuilder()
            .set_context_id(context_id)
            .add_text("challenge_response")
            .add_data("challenge_response", {"otp": "123456"})
            .build()
        )
        async with A2AClient(selected["merchant"]["url"]) as client:
            otp_resp = await client.send_message(otp_msg)
        pay_result = otp_resp.get("result", {})
        pay_task = pay_result.get("task", pay_result)
        pay_status = pay_task.get("status", {}).get("state", "unknown")
        logger.info(f"{P}   OTP verified, continuing...")
        logger.info("")

    # ── Step 11: Escrow ────────────────────────────────────────────
    logger.info(f"{P} Step 11: ESCROW - Checking hold status")
    pay_artifacts = pay_task.get("artifacts", [])
    receipt_data = None
    for art in pay_artifacts:
        for part in art.get("parts", []):
            if part.get("kind") == "data":
                data = part.get("data", {})
                if PAYMENT_RECEIPT_DATA_KEY in data:
                    receipt_data = data[PAYMENT_RECEIPT_DATA_KEY]

    if receipt_data:
        logger.info(f"{P}   Payment ID: {receipt_data.get('payment_id', 'N/A')}")
        logger.info(f"{P}   Amount: ${receipt_data.get('amount', {}).get('value', 0):.2f} {receipt_data.get('amount', {}).get('currency', 'USD')}")
    else:
        logger.info(f"{P}   Payment completed (status: {pay_status})")
    logger.info("")

    # ── Step 12: Service Delivery ──────────────────────────────────
    logger.info(f"{P} Step 12: SERVICE DELIVERY - Marking as delivered")
    logger.info(f"{P}   (Simulated: videography service completed)")
    logger.info("")

    # ── Step 13: Receipt ───────────────────────────────────────────
    logger.info(f"{P} Step 13: RECEIPT - Final confirmation")
    if receipt_data:
        status = receipt_data.get("payment_status", {})
        if isinstance(status, dict):
            if "merchant_confirmation_id" in status:
                logger.info(f"{P}   Status: SUCCESS")
                logger.info(f"{P}   Merchant confirmation: {status['merchant_confirmation_id']}")
                logger.info(f"{P}   PSP confirmation: {status.get('psp_confirmation_id', 'N/A')}")
            elif "error_message" in status:
                logger.info(f"{P}   Status: ERROR - {status['error_message']}")
            elif "failure_message" in status:
                logger.info(f"{P}   Status: FAILED - {status['failure_message']}")
    else:
        logger.info(f"{P}   Payment flow completed (status: {pay_status})")

    logger.info("")
    logger.info(f"{P} ========================================")
    logger.info(f"{P} AP2 Standard Payment Demo COMPLETE")
    logger.info(f"{P} ========================================")


def main():
    parser = argparse.ArgumentParser(description="AP2 Shopping Agent")
    parser.add_argument("--port", type=int, default=8000, help="Port (unused - shopping agent is client-only)")
    parser.add_argument("--budget", type=float, default=400.0, help="Maximum budget in USD")
    parser.add_argument("--merchants", nargs="+", default=["http://localhost:8001", "http://localhost:8002"],
                        help="Merchant agent URLs")
    parser.add_argument("--credentials-provider", type=str, default="http://localhost:8003",
                        dest="credentials_provider", help="Credentials Provider URL")
    parser.add_argument("--user", type=str, default="johndoe@example.com", help="User email")
    args = parser.parse_args()

    asyncio.run(run_shopping_flow(
        merchant_urls=args.merchants,
        credentials_provider_url=args.credentials_provider,
        budget=args.budget,
        user_email=args.user,
    ))


if __name__ == "__main__":
    main()
