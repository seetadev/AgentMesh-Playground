"""Integration test with assertions for the AP2 Standard Payment demo.

Starts all agents, runs the flow, and verifies correctness.
Exit code 0 = all pass, 1 = failure.
"""
import sys
import asyncio
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).parent)
sys.path.insert(0, PROJECT_ROOT)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} {f'- {detail}' if detail else ''}")


async def wait_for_server(port: int, timeout: float = 10.0) -> bool:
    import httpx
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r = await client.get(f"http://localhost:{port}/.well-known/agent.json")
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def run_tests():
    import httpx
    from ap2_types.mandate import (
        IntentMandate, CartMandate, PaymentMandate, PaymentMandateContents,
        CART_MANDATE_DATA_KEY, INTENT_MANDATE_DATA_KEY, PAYMENT_MANDATE_DATA_KEY,
    )
    from ap2_types.payment_request import PaymentCurrencyAmount, PaymentItem, PaymentResponse
    from ap2_types.payment_receipt import PAYMENT_RECEIPT_DATA_KEY
    from a2a_helpers.message_builder import MessageBuilder
    from a2a_helpers.types import AP2_EXTENSION_URI
    from signing import sign_payment_mandate

    procs = []
    ports = {"pp": 19004, "cp": 19003, "ma": 19001, "mb": 19002}

    try:
        # Start agents
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.payment_processor", "--port", str(ports["pp"])],
            cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.credentials_provider", "--port", str(ports["cp"])],
            cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
        await asyncio.sleep(2)
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.merchant_agent", "--port", str(ports["ma"]),
             "--name", "QuickShoot Studios", "--price", "350",
             "--processor", f"http://localhost:{ports['pp']}"],
            cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.merchant_agent", "--port", str(ports["mb"]),
             "--name", "Premium Films", "--price", "450",
             "--processor", f"http://localhost:{ports['pp']}"],
            cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ))
        await asyncio.sleep(2)

        # Verify servers started
        for name, port in ports.items():
            ready = await wait_for_server(port, timeout=8)
            check(f"Agent {name} started on port {port}", ready)

        context_id = "test_integration_ctx"

        async with httpx.AsyncClient(timeout=15) as client:
            # Test 1: AgentCards have AP2 extension
            print("\n=== AgentCard Tests ===")
            for name, port in ports.items():
                r = await client.get(f"http://localhost:{port}/.well-known/agent.json")
                card = r.json()
                extensions = card.get("capabilities", {}).get("extensions", [])
                has_ap2 = any(e.get("uri") == AP2_EXTENSION_URI for e in extensions)
                check(f"AgentCard {name} has AP2 extension", has_ap2)

            # Test 2: IntentMandate -> CartMandate
            print("\n=== Intent -> Cart Tests ===")
            intent = IntentMandate(
                natural_language_description="Book a videographer",
                intent_expiry="2026-03-26T15:00:00Z",
            )
            intent_msg = MessageBuilder().set_context_id(context_id).add_data(
                INTENT_MANDATE_DATA_KEY, intent.model_dump(mode="json")
            ).build()

            # Merchant A (should return $350 cart)
            r = await client.post(f"http://localhost:{ports['ma']}/a2a", json={
                "jsonrpc": "2.0", "id": 1, "method": "message/send",
                "params": {"message": intent_msg},
            })
            result_a = r.json().get("result", {}).get("task", {})
            arts_a = result_a.get("artifacts", [])
            check("Merchant A returns CartMandate artifact", len(arts_a) > 0)

            cart_data_a = None
            if arts_a:
                for part in arts_a[0].get("parts", []):
                    if part.get("kind") == "data" and CART_MANDATE_DATA_KEY in part.get("data", {}):
                        cart_data_a = part["data"][CART_MANDATE_DATA_KEY]
            check("CartMandate A has contents", cart_data_a is not None and "contents" in (cart_data_a or {}))
            if cart_data_a:
                price_a = cart_data_a["contents"]["payment_request"]["details"]["total"]["amount"]["value"]
                check("CartMandate A price is $350", price_a == 350.0, f"got {price_a}")
                check("CartMandate A has merchant JWT", bool(cart_data_a.get("merchant_authorization")))

            # Merchant B (should return $450 cart)
            r = await client.post(f"http://localhost:{ports['mb']}/a2a", json={
                "jsonrpc": "2.0", "id": 2, "method": "message/send",
                "params": {"message": intent_msg},
            })
            result_b = r.json().get("result", {}).get("task", {})
            arts_b = result_b.get("artifacts", [])
            check("Merchant B returns CartMandate artifact", len(arts_b) > 0)
            if arts_b:
                for part in arts_b[0].get("parts", []):
                    if part.get("kind") == "data" and CART_MANDATE_DATA_KEY in part.get("data", {}):
                        cart_data_b = part["data"][CART_MANDATE_DATA_KEY]
                        price_b = cart_data_b["contents"]["payment_request"]["details"]["total"]["amount"]["value"]
                        check("CartMandate B price is $450", price_b == 450.0, f"got {price_b}")

            # Test 3: Credentials Provider
            print("\n=== Credentials Provider Tests ===")
            connect_msg = MessageBuilder().set_context_id(context_id).add_text("connect user").add_data("user_id", "johndoe@example.com").build()
            r = await client.post(f"http://localhost:{ports['cp']}/a2a", json={
                "jsonrpc": "2.0", "id": 3, "method": "message/send",
                "params": {"message": connect_msg},
            })
            cp_result = r.json().get("result", {})
            check("CP connect succeeds", cp_result.get("status", {}).get("state") == "completed")

            token_msg = MessageBuilder().set_context_id(context_id).add_text("provide_token get_token").add_data("method_preference", "VISA").build()
            r = await client.post(f"http://localhost:{ports['cp']}/a2a", json={
                "jsonrpc": "2.0", "id": 4, "method": "message/send",
                "params": {"message": token_msg},
            })
            token_result = r.json().get("result", {})
            token_arts = token_result.get("artifacts", [])
            check("CP returns payment token artifact", len(token_arts) > 0)

            # Test 4: Payment flow
            print("\n=== Payment Flow Tests ===")
            if cart_data_a:
                cart_mandate = CartMandate(**cart_data_a)
                pay_resp = PaymentResponse(request_id="order_001", method_name="CARD", details={"token": "tok_test"})
                pmc = PaymentMandateContents(
                    payment_mandate_id="pm_test", payment_details_id="order_001",
                    payment_details_total=PaymentItem(label="Total", amount=PaymentCurrencyAmount(currency="USD", value=350.0)),
                    payment_response=pay_resp, merchant_agent="QuickShoot Studios",
                )
                user_auth = sign_payment_mandate(cart_mandate, pmc)
                pm = PaymentMandate(payment_mandate_contents=pmc, user_authorization=user_auth)
                check("PaymentMandate created with user_authorization", bool(pm.user_authorization))

                pay_msg = MessageBuilder().set_context_id(context_id).add_data(
                    PAYMENT_MANDATE_DATA_KEY, pm.model_dump(mode="json")
                ).build()
                r = await client.post(f"http://localhost:{ports['ma']}/a2a", json={
                    "jsonrpc": "2.0", "id": 5, "method": "message/send",
                    "params": {"message": pay_msg},
                })
                pay_result = r.json().get("result", {}).get("task", {})
                pay_status = pay_result.get("status", {}).get("state")
                check("Payment completes successfully", pay_status == "completed", f"got {pay_status}")

    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=3)
            except:
                p.kill()

    # Summary
    print(f"\n{'=' * 50}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 50}")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
