"""
Event Generator
===============

Generates reproducible synthetic payment-failure events for simulation
and benchmarking.

Design decisions:
- Every run with the same ``seed`` produces the *identical* event list.
- Raw gateway error strings are deliberately messy — not pre-classified.
- The ``normalised_failure_class`` is hidden inside the SimEvent and
  excluded from the strategy-engine view.
- Context space is intentionally small:
      normalised_failure_class × value_tier   (9 × 3 = 27 cells)
"""

from __future__ import annotations

import random
import string
import uuid
from datetime import UTC, datetime, timedelta

from backend.simulator.types import (
    CustomerSegment,
    FailureClass,
    SimEvent,
    SimPaymentMethod,
    ValueTier,
)

# ---------------------------------------------------------------------------
# Raw error string pool (deliberately messy)
# ---------------------------------------------------------------------------

# Maps each FailureClass to a list of raw gateway error strings.
# Some strings are ambiguous or could plausibly belong to multiple classes —
# that is intentional, to exercise the classifier / strategy engine.

_RAW_ERRORS: dict[FailureClass, list[str]] = {
    FailureClass.TIMEOUT_TRANSIENT: [
        "bank response timed out",
        "upstream timeout after 30s",
        "gateway response not received",
        "connection reset by peer",
        "read timeout: no data received in 15000ms",
        "socket closed unexpectedly",
        "request timed out, please retry",
        "PSP response delayed beyond threshold",
    ],
    FailureClass.HARD_FUNDS_ISSUE: [
        "issuer declined txn, code 51",
        "insufficient balance in account",
        "not sufficient funds",
        "available credit limit exceeded",
        "daily spend limit reached",
        "monthly UPI limit exhausted",
        "card balance too low for transaction",
        "account balance insufficient: txn amount INR",
    ],
    FailureClass.ISSUER_DECLINE: [
        "issuer declined — do not honour",
        "bank declined transaction (code 05)",
        "card issuer declined the payment",
        "declined by issuing bank, reason unknown",
        "transaction not permitted to cardholder",
        "restricted card, contact issuer",
        "lost or stolen card flag detected",
        "card blocked by issuer",
    ],
    FailureClass.AUTH_BLOCKED: [
        "collect request expired",
        "customer auth window expired",
        "OTP not entered within allowed time",
        "3DS authentication failed",
        "VBV authentication timed out",
        "PIN entry abandoned by user",
        "step-up challenge rejected",
        "authentication cancelled by customer",
    ],
    FailureClass.INFRA_OUTAGE: [
        "PSP unavailable",
        "payment gateway down for maintenance",
        "bank host unreachable",
        "NPCI switch unavailable",
        "acquirer host not responding",
        "network partition detected at switch",
        "downstream service unavailable (503)",
        "settlement host connection refused",
    ],
    FailureClass.DUPLICATE: [
        "duplicate reference detected",
        "transaction already exists with same ref",
        "duplicate order id submitted",
        "payment already processed for this request",
        "idempotency key conflict",
        "transaction id reuse detected",
    ],
    FailureClass.CUSTOMER_ABANDONMENT: [
        "payment abandoned by user",
        "session expired — no user activity",
        "customer closed the payment page",
        "user navigated away before completion",
        "checkout abandoned mid-flow",
        "UPI collect request rejected by customer",
        "customer declined intent to pay",
    ],
    FailureClass.FRAUD_SUSPECTED: [
        "transaction flagged by fraud engine",
        "suspicious activity detected on card",
        "velocity check triggered (>5 attempts/min)",
        "card on watchlist",
        "device fingerprint mismatch with risk score 0.91",
        "potential card-testing pattern detected",
        "risk score exceeded threshold: 0.88",
    ],
    FailureClass.UNKNOWN: [
        "unclassified error from bank",
        "unknown response code 99",
        "internal processing error — contact support",
        "error: GENERIC_DECLINE",
        "unexpected response format from acquirer",
        "null pointer in payment processing chain",
        "error parsing bank response XML",
        "unrecognised status code from PSP",
        "payment failed (no further details available)",
    ],
}

# Realistic failure-class distribution (weights, must sum to 1.0)
_CLASS_WEIGHTS: dict[FailureClass, float] = {
    FailureClass.TIMEOUT_TRANSIENT: 0.18,
    FailureClass.HARD_FUNDS_ISSUE: 0.22,
    FailureClass.ISSUER_DECLINE: 0.14,
    FailureClass.AUTH_BLOCKED: 0.12,
    FailureClass.INFRA_OUTAGE: 0.08,
    FailureClass.DUPLICATE: 0.05,
    FailureClass.CUSTOMER_ABANDONMENT: 0.10,
    FailureClass.FRAUD_SUSPECTED: 0.05,
    FailureClass.UNKNOWN: 0.06,
}

_PAYMENT_METHODS = list(SimPaymentMethod)
_PAYMENT_METHOD_WEIGHTS = [0.35, 0.30, 0.20, 0.10, 0.05]  # CARD, UPI, NETBANKING, WALLET, EMI

_CUSTOMER_SEGMENTS = list(CustomerSegment)
_SEGMENT_WEIGHTS = [0.20, 0.45, 0.20, 0.15]  # NEW, REGULAR, PREMIUM, AT_RISK


def _amount_for_tier(rng: random.Random, tier: ValueTier) -> float:
    if tier == ValueTier.LOW:
        return round(rng.uniform(50.0, 499.99), 2)
    if tier == ValueTier.MID:
        return round(rng.uniform(500.0, 4999.99), 2)
    return round(rng.uniform(5000.0, 99999.99), 2)


def _value_tier(amount: float) -> ValueTier:
    if amount < 500:
        return ValueTier.LOW
    if amount < 5000:
        return ValueTier.MID
    return ValueTier.HIGH


def _make_txn_id(rng: random.Random) -> str:
    suffix = "".join(rng.choices(string.ascii_uppercase + string.digits, k=12))
    return f"TXN_{suffix}"


def _make_customer_id(rng: random.Random) -> str:
    suffix = "".join(rng.choices(string.digits, k=8))
    return f"CUS_{suffix}"


# ---------------------------------------------------------------------------
# EventGenerator
# ---------------------------------------------------------------------------


class EventGenerator:
    """Generates synthetic payment-failure events.

    Parameters
    ----------
    seed:
        RNG seed.  The *same* seed always produces the *identical* event list.
        ``None`` uses OS entropy (non-reproducible).
    n:
        Default number of events to generate per call.
    start_time:
        Timestamp of the first event.  Subsequent events are spread across a
        rolling 24-hour window.
    """

    def __init__(
        self,
        seed: int | None = 42,
        n: int = 500,
        start_time: datetime | None = None,
    ) -> None:
        self.seed = seed
        self.n = n
        self.start_time = start_time or datetime(2026, 8, 1, 0, 0, 0, tzinfo=UTC)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, n: int | None = None, seed: int | None = None) -> list[SimEvent]:
        """Generate ``n`` synthetic failure events.

        Parameters
        ----------
        n:
            Number of events.  Defaults to ``self.n``.
        seed:
            Override seed for this call only.

        Returns
        -------
        list[SimEvent]
            Events in ascending timestamp order.  The list is identical for
            identical ``(n, seed)`` combinations.
        """
        n = n if n is not None else self.n
        rng = random.Random(seed if seed is not None else self.seed)

        # Draw failure classes according to weights
        failure_classes = rng.choices(
            list(_CLASS_WEIGHTS.keys()),
            weights=list(_CLASS_WEIGHTS.values()),
            k=n,
        )

        events: list[SimEvent] = []
        window_seconds = 86_400  # spread events across 24 h

        for i, fc in enumerate(failure_classes):
            # Tier sampled independently of failure class (realistic)
            tier = rng.choices(
                [ValueTier.LOW, ValueTier.MID, ValueTier.HIGH],
                weights=[0.40, 0.40, 0.20],
            )[0]
            amount = _amount_for_tier(rng, tier)

            method = rng.choices(_PAYMENT_METHODS, weights=_PAYMENT_METHOD_WEIGHTS)[0]
            segment = rng.choices(_CUSTOMER_SEGMENTS, weights=_SEGMENT_WEIGHTS)[0]
            raw_error = rng.choice(_RAW_ERRORS[fc])

            # Slightly randomise previous_attempts (0 = first failure)
            prev_attempts = rng.choices([0, 1, 2, 3], weights=[0.60, 0.25, 0.10, 0.05])[0]

            ts = self.start_time + timedelta(seconds=rng.uniform(0, window_seconds))

            event = SimEvent(
                event_id=str(uuid.UUID(int=rng.getrandbits(128))),
                transaction_id=_make_txn_id(rng),
                customer_id=_make_customer_id(rng),
                timestamp=ts,
                amount=amount,
                currency="INR",
                payment_method=method,
                raw_gateway_error=raw_error,
                previous_attempts=prev_attempts,
                customer_segment=segment,
                value_tier=tier,
                normalised_failure_class=fc,
            )
            events.append(event)

        events.sort(key=lambda e: e.timestamp)
        return events

    # ------------------------------------------------------------------
    # Introspection helpers (for tests / debugging)
    # ------------------------------------------------------------------

    def failure_class_distribution(self, events: list[SimEvent]) -> dict[str, int]:
        """Count how many events belong to each failure class."""
        counts: dict[str, int] = {fc.value: 0 for fc in FailureClass}
        for e in events:
            counts[e.normalised_failure_class.value] += 1
        return counts

    def value_tier_distribution(self, events: list[SimEvent]) -> dict[str, int]:
        """Count events per value tier."""
        counts: dict[str, int] = {vt.value: 0 for vt in ValueTier}
        for e in events:
            counts[e.value_tier.value] += 1
        return counts
