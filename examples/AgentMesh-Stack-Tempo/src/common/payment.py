"""Trio-compatible MPP payment helpers.

pympp's ChargeIntent.verify() uses asyncio.sleep internally, which is
incompatible with trio. This module provides trio-native RPC verification
while reusing pympp's Challenge/Credential/Receipt types for serialization.
"""

import httpx
import trio

from mpp import Challenge, Credential, Receipt

from common.protocol import TEMPO_CHAIN_ID, TEMPO_RPC_URL, TEMPO_CURRENCY, SIGNAL_PRICE

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

MAX_RECEIPT_ATTEMPTS = 20
RECEIPT_POLL_INTERVAL = 0.5

# Server-side defaults
SECRET_KEY = "aoin-alpha-agent-secret"
REALM = "aoin.local"


def create_challenge(recipient: str, amount: str = SIGNAL_PRICE) -> Challenge:
    """Create an MPP payment challenge (server-side)."""
    from mpp._units import parse_units

    base_amount = str(parse_units(amount, 6))
    request = {
        "amount": base_amount,
        "currency": TEMPO_CURRENCY,
        "recipient": recipient,
        "methodDetails": {"chainId": TEMPO_CHAIN_ID},
    }
    return Challenge.create(
        secret_key=SECRET_KEY,
        realm=REALM,
        method="tempo",
        intent="charge",
        request=request,
    )


def challenge_to_dict(challenge: Challenge) -> dict:
    """Serialize a Challenge to a dict for sending over stream."""
    return {
        "www_authenticate": challenge.to_www_authenticate(REALM),
    }


def challenge_from_dict(data: dict) -> Challenge:
    """Deserialize a Challenge from a stream dict."""
    return Challenge.from_www_authenticate(data["www_authenticate"])


RPC_MAX_RETRIES = 3
RPC_RETRY_DELAY = 1.0


async def _rpc_call(client: httpx.AsyncClient, method: str, params: list) -> str:
    """Make a JSON-RPC call with retry logic."""
    last_error = None
    for attempt in range(RPC_MAX_RETRIES):
        try:
            resp = await client.post(
                TEMPO_RPC_URL,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
            )
            resp.raise_for_status()
            result = resp.json()
            if "error" in result:
                raise RuntimeError(f"RPC error: {result['error']}")
            return result["result"]
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
            last_error = e
            if attempt < RPC_MAX_RETRIES - 1:
                await trio.sleep(RPC_RETRY_DELAY * (attempt + 1))
    raise RuntimeError(f"RPC call {method} failed after {RPC_MAX_RETRIES} retries: {last_error}")


def _encode_transfer(recipient: str, amount: int) -> str:
    """Encode a TIP-20 transfer(address,uint256) call."""
    selector = "a9059cbb"
    to_padded = recipient[2:].lower().zfill(64)
    amount_padded = hex(amount)[2:].zfill(64)
    return f"0x{selector}{to_padded}{amount_padded}"


async def create_credential(challenge: Challenge, private_key: str) -> Credential:
    """Create a payment credential by signing a Tempo transaction (client-side).

    Trio-native reimplementation — bypasses pympp's asyncio.gather in _rpc.py
    by making sequential RPC calls with httpx (which works under trio via anyio).
    """
    from pytempo import Call, TempoTransaction
    from mpp.methods.tempo.account import TempoAccount
    from mpp.methods.tempo._attribution import encode as encode_attribution

    account = TempoAccount.from_key(private_key)
    request = challenge.request
    amount = int(request["amount"])
    currency = request["currency"]
    recipient = request["recipient"]

    transfer_data = _encode_transfer(recipient, amount)

    # Fetch tx params sequentially (trio-safe, no asyncio.gather)
    async with httpx.AsyncClient(timeout=30.0) as client:
        chain_id_hex = await _rpc_call(client, "eth_chainId", [])
        nonce_hex = await _rpc_call(client, "eth_getTransactionCount", [account.address, "pending"])
        gas_hex = await _rpc_call(client, "eth_gasPrice", [])

    chain_id = int(chain_id_hex, 16)
    nonce = int(nonce_hex, 16)
    gas_price = int(gas_hex, 16)

    memo = encode_attribution(server_id=challenge.realm, client_id=None)

    tx = TempoTransaction.create(
        chain_id=chain_id,
        gas_limit=1_000_000,
        max_fee_per_gas=gas_price,
        max_priority_fee_per_gas=gas_price,
        nonce=nonce,
        nonce_key=0,
        fee_token=currency,
        awaiting_fee_payer=False,
        calls=(Call.create(to=currency, value=0, data=transfer_data),),
    )

    signed_tx = tx.sign(account.private_key)
    raw_tx_hex = "0x" + signed_tx.encode().hex()

    return Credential(
        challenge=challenge.to_echo(),
        payload={"type": "transaction", "signature": raw_tx_hex},
        source=f"did:pkh:eip155:{chain_id}:{account.address}",
    )


def credential_to_dict(credential: Credential) -> dict:
    """Serialize a Credential to a dict for sending over stream."""
    return {
        "authorization": credential.to_authorization(),
    }


def credential_from_dict(data: dict) -> Credential:
    """Deserialize a Credential from a stream dict."""
    return Credential.from_authorization(data["authorization"])


async def verify_payment(credential: Credential, expected_request: dict) -> Receipt:
    """Verify a payment credential on-chain (trio-compatible).

    Broadcasts the signed transaction and polls for receipt confirmation
    using trio.sleep instead of asyncio.sleep.
    """
    payload = credential.payload
    if not isinstance(payload, dict) or payload.get("type") != "transaction":
        raise ValueError(f"Unsupported credential type: {payload.get('type')}")

    raw_tx = payload["signature"]

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check if fee payer is needed
        method_details = expected_request.get("methodDetails", {})
        use_fee_payer = method_details.get("feePayer", False)

        if use_fee_payer:
            # Forward to testnet fee payer service
            sign_resp = await client.post(
                "https://sponsor.moderato.tempo.xyz",
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_signRawTransaction",
                    "params": [raw_tx],
                    "id": 1,
                },
            )
            sign_resp.raise_for_status()
            sign_result = sign_resp.json()
            if "error" in sign_result:
                raise ValueError(f"Fee payer failed: {sign_result['error']}")
            raw_tx = sign_result["result"]

        # Broadcast transaction
        resp = await client.post(
            TEMPO_RPC_URL,
            json={
                "jsonrpc": "2.0",
                "method": "eth_sendRawTransaction",
                "params": [raw_tx],
                "id": 1,
            },
        )
        resp.raise_for_status()
        result = resp.json()

        if "error" in result:
            raise ValueError(f"Transaction failed: {result['error']}")

        tx_hash = result["result"]
        print(f"[Payment] Transaction broadcast: {tx_hash}")

        # Poll for receipt
        for attempt in range(MAX_RECEIPT_ATTEMPTS):
            receipt_resp = await client.post(
                TEMPO_RPC_URL,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getTransactionReceipt",
                    "params": [tx_hash],
                    "id": 1,
                },
            )
            receipt_resp.raise_for_status()
            receipt_result = receipt_resp.json()

            receipt_data = receipt_result.get("result")
            if receipt_data:
                if receipt_data.get("status") != "0x1":
                    raise ValueError("Transaction reverted")

                # Verify transfer logs match expected params
                if not _verify_transfer_logs(receipt_data, expected_request):
                    raise ValueError("Transfer logs don't match expected payment")

                print(f"[Payment] Confirmed in block {receipt_data.get('blockNumber')}")
                return Receipt.success(tx_hash)

            if attempt < MAX_RECEIPT_ATTEMPTS - 1:
                await trio.sleep(RECEIPT_POLL_INTERVAL)

        raise ValueError("Transaction receipt not found after polling")


def _verify_transfer_logs(receipt_data: dict, request: dict) -> bool:
    """Check if receipt contains a Transfer log matching expected params."""
    expected_currency = request["currency"]
    expected_recipient = request["recipient"]
    expected_amount = int(request["amount"])

    for log in receipt_data.get("logs", []):
        if log.get("address", "").lower() != expected_currency.lower():
            continue

        topics = log.get("topics", [])
        if len(topics) < 3:
            continue

        if topics[0] != TRANSFER_TOPIC:
            continue

        to_address = "0x" + topics[2][-40:]
        if to_address.lower() != expected_recipient.lower():
            continue

        data = log.get("data", "0x")
        if len(data) >= 66:
            amount = int(data, 16)
            if amount == expected_amount:
                return True

    return False
