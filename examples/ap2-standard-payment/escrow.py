"""Simulated escrow manager for AP2 payment demo.

Provides hold/release/refund lifecycle matching the P2P version
for apples-to-apples comparison.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EscrowHold(BaseModel):
    """Represents funds held in escrow."""
    payment_id: str
    amount: float
    currency: str = "USD"
    status: str = "held"
    release_condition: str = "service_delivered"
    hold_expiry: str = Field(
        default_factory=lambda: (
            datetime.now(timezone.utc) + timedelta(days=7)
        ).isoformat()
    )
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class EscrowRelease(BaseModel):
    """Represents released escrow funds."""
    payment_id: str
    amount: float
    currency: str = "USD"
    status: str = "released"
    released_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class EscrowRefund(BaseModel):
    """Represents refunded escrow funds."""
    payment_id: str
    amount: float
    currency: str = "USD"
    status: str = "refunded"
    reason: str = "hold_expired"
    refunded_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class EscrowManager:
    """In-memory escrow manager."""

    def __init__(self):
        self._holds: dict[str, EscrowHold] = {}

    def hold(
        self,
        amount: float,
        currency: str = "USD",
        release_condition: str = "service_delivered",
        hold_days: int = 7,
        payment_id: Optional[str] = None,
    ) -> EscrowHold:
        """Create an escrow hold."""
        pid = payment_id or f"txn_{uuid4().hex[:8]}"
        escrow = EscrowHold(
            payment_id=pid,
            amount=amount,
            currency=currency,
            release_condition=release_condition,
            hold_expiry=(
                datetime.now(timezone.utc) + timedelta(days=hold_days)
            ).isoformat(),
        )
        self._holds[pid] = escrow
        logger.info(f"Escrow HOLD: {pid} - ${amount:.2f} {currency}")
        return escrow

    def release(self, payment_id: str) -> EscrowRelease:
        """Release escrow funds (service delivered)."""
        hold = self._holds.get(payment_id)
        if not hold:
            raise ValueError(f"No escrow hold found for {payment_id}")

        hold.status = "released"
        result = EscrowRelease(
            payment_id=payment_id,
            amount=hold.amount,
            currency=hold.currency,
        )
        logger.info(f"Escrow RELEASED: {payment_id} - ${hold.amount:.2f}")
        return result

    def refund(self, payment_id: str, reason: str = "hold_expired") -> EscrowRefund:
        """Refund escrow funds."""
        hold = self._holds.get(payment_id)
        if not hold:
            raise ValueError(f"No escrow hold found for {payment_id}")

        hold.status = "refunded"
        result = EscrowRefund(
            payment_id=payment_id,
            amount=hold.amount,
            currency=hold.currency,
            reason=reason,
        )
        logger.info(f"Escrow REFUNDED: {payment_id} - ${hold.amount:.2f} ({reason})")
        return result

    def get_hold(self, payment_id: str) -> Optional[EscrowHold]:
        """Get an escrow hold by payment ID."""
        return self._holds.get(payment_id)

    def check_expired(self) -> list[EscrowRefund]:
        """Check for expired holds and auto-refund them."""
        now = datetime.now(timezone.utc)
        refunds = []
        for pid, hold in list(self._holds.items()):
            if hold.status != "held":
                continue
            try:
                expiry = datetime.fromisoformat(hold.hold_expiry)
                if now > expiry:
                    refund = self.refund(pid, reason="hold_expired")
                    refunds.append(refund)
            except (ValueError, TypeError):
                pass
        return refunds
