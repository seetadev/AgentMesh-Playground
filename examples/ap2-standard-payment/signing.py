"""Simulated cryptographic signing for AP2 mandates.

In production AP2:
- CartMandate.merchant_authorization is a JWT signed by merchant's private key
- PaymentMandate.user_authorization is an SD-JWT-VC signed by user's hardware-backed key

This module simulates both using PyJWT with HS256 for demo purposes.
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import jwt  # PyJWT


# Simulated keys (in production, these would be RSA/EC key pairs)
MERCHANT_KEYS = {
    "QuickShoot Studios": "merchant_secret_quickshoot_12345",
    "Premium Films": "merchant_secret_premium_67890",
}
USER_DEVICE_KEY = "user_device_secret_key_simulated"


def _canonical_json(obj: Any) -> str:
    """Create canonical JSON representation for hashing."""
    if hasattr(obj, "model_dump"):
        data = obj.model_dump(mode="json")
    elif isinstance(obj, dict):
        data = obj
    else:
        data = obj
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def hash_object(obj: Any) -> str:
    """SHA-256 hash of canonical JSON representation."""
    return hashlib.sha256(_canonical_json(obj).encode()).hexdigest()


def sign_cart_mandate(cart_contents: Any, merchant_name: str) -> str:
    """Simulate merchant signing a CartMandate with JWT.

    In production: RSA/EC signature with merchant's private key.
    Here: HS256 JWT for demonstration.

    Args:
        cart_contents: CartContents Pydantic model or dict
        merchant_name: Name of the merchant (used to look up key)

    Returns:
        Base64url-encoded JWT string
    """
    key = MERCHANT_KEYS.get(merchant_name, f"merchant_secret_{merchant_name}")

    # Get cart_expiry for JWT exp
    if hasattr(cart_contents, "cart_expiry"):
        expiry = cart_contents.cart_expiry
    elif isinstance(cart_contents, dict):
        expiry = cart_contents.get("cart_expiry", "")
    else:
        expiry = ""

    try:
        exp_timestamp = datetime.fromisoformat(expiry).timestamp()
    except (ValueError, TypeError):
        exp_timestamp = datetime.now(timezone.utc).timestamp() + 900  # 15 min default

    payload = {
        "iss": merchant_name,
        "sub": "cart_mandate",
        "aud": "payment_processor",
        "iat": datetime.now(timezone.utc).timestamp(),
        "exp": exp_timestamp,
        "jti": str(uuid4()),
        "cart_hash": hash_object(cart_contents),
    }

    return jwt.encode(payload, key, algorithm="HS256")


def verify_cart_mandate(token: str, merchant_name: str) -> dict:
    """Verify merchant's CartMandate JWT signature.

    Args:
        token: The JWT string from CartMandate.merchant_authorization
        merchant_name: Name of the merchant

    Returns:
        Decoded JWT payload

    Raises:
        jwt.InvalidTokenError: If verification fails
    """
    key = MERCHANT_KEYS.get(merchant_name, f"merchant_secret_{merchant_name}")
    return jwt.decode(
        token, key, algorithms=["HS256"],
        audience="payment_processor",
        options={"verify_exp": True},
    )


def sign_payment_mandate(
    cart_mandate: Any,
    payment_mandate_contents: Any,
) -> str:
    """Simulate user signing PaymentMandate with SD-JWT-VC.

    In production: Hardware-backed key + biometric auth creates a
    Verifiable Presentation (sd-jwt-vc) signing over cart and payment hashes.

    Here: Simulated HS256 JWT for demonstration.

    Args:
        cart_mandate: CartMandate model or dict
        payment_mandate_contents: PaymentMandateContents model or dict

    Returns:
        Base64url-encoded simulated SD-JWT-VC string
    """
    payload = {
        "iss": "user_credentials_provider",
        "sub": "payment_mandate",
        "aud": "merchant_payment_processor",
        "iat": datetime.now(timezone.utc).timestamp(),
        "exp": datetime.now(timezone.utc).timestamp() + 3600,
        "nonce": str(uuid4()),
        "transaction_data": [
            hash_object(cart_mandate),
            hash_object(payment_mandate_contents),
        ],
    }

    return jwt.encode(payload, USER_DEVICE_KEY, algorithm="HS256")


def verify_payment_mandate(token: str) -> dict:
    """Verify user's PaymentMandate SD-JWT-VC signature.

    Args:
        token: The simulated SD-JWT-VC string

    Returns:
        Decoded payload

    Raises:
        jwt.InvalidTokenError: If verification fails
    """
    return jwt.decode(
        token, USER_DEVICE_KEY, algorithms=["HS256"],
        audience="merchant_payment_processor",
        options={"verify_exp": True},
    )
