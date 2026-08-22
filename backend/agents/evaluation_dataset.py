"""
Diagnosis Agent Evaluation Dataset
==================================

Contains labeled messy-error test cases split into:
  - DEV_DATASET: Development and calibration examples (20 cases)
  - HELDOUT_EVALUATION_DATASET: Held-out benchmark examples (20 cases)

Total: 40 real-world style messy payment failure records.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.schemas import FailureClass, RiskLevel


@dataclass(frozen=True)
class LabeledErrorCase:
    """A benchmark test case for payment failure diagnosis."""

    case_id: str
    raw_error: str
    expected_class: FailureClass
    expected_retryability: bool
    expected_risk: RiskLevel
    min_confidence: float
    notes: str = ""


# ---------------------------------------------------------------------------
# Development Dataset (20 cases)
# ---------------------------------------------------------------------------

DEV_DATASET: list[LabeledErrorCase] = [
    # TIMEOUT_TRANSIENT
    LabeledErrorCase(
        case_id="DEV_01",
        raw_error="bank response timed out after 30000ms",
        expected_class=FailureClass.TIMEOUT_TRANSIENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Standard gateway-to-bank read timeout",
    ),
    LabeledErrorCase(
        case_id="DEV_02",
        raw_error="connection reset by peer during acquirer handshake",
        expected_class=FailureClass.TIMEOUT_TRANSIENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.80,
        notes="Transient network drop",
    ),
    # HARD_FUNDS_ISSUE
    LabeledErrorCase(
        case_id="DEV_03",
        raw_error="issuer declined txn, code 51 - insufficient funds",
        expected_class=FailureClass.HARD_FUNDS_ISSUE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.90,
        notes="ISO 8583 Code 51 with explicit insufficient funds text",
    ),
    LabeledErrorCase(
        case_id="DEV_04",
        raw_error="account balance too low for requested transaction amount",
        expected_class=FailureClass.HARD_FUNDS_ISSUE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Explicit balance deficit message",
    ),
    # ISSUER_DECLINE
    LabeledErrorCase(
        case_id="DEV_05",
        raw_error="bank declined transaction (code 05 - do not honour)",
        expected_class=FailureClass.ISSUER_DECLINE,
        expected_retryability=True,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.85,
        notes="ISO 8583 Code 05 generic bank decline",
    ),
    LabeledErrorCase(
        case_id="DEV_06",
        raw_error="transaction not permitted to cardholder by issuer",
        expected_class=FailureClass.ISSUER_DECLINE,
        expected_retryability=True,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.80,
        notes="Issuer policy restriction",
    ),
    # AUTH_BLOCKED
    LabeledErrorCase(
        case_id="DEV_07",
        raw_error="customer auth window expired during 3DS challenge",
        expected_class=FailureClass.AUTH_BLOCKED,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="3DS timeout / customer OTP delay",
    ),
    LabeledErrorCase(
        case_id="DEV_08",
        raw_error="collect request expired - OTP not submitted within 180s",
        expected_class=FailureClass.AUTH_BLOCKED,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="UPI collect auth expiration",
    ),
    # INFRA_OUTAGE
    LabeledErrorCase(
        case_id="DEV_09",
        raw_error="PSP unavailable - 503 downstream host maintenance",
        expected_class=FailureClass.INFRA_OUTAGE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Gateway downstream 503",
    ),
    LabeledErrorCase(
        case_id="DEV_10",
        raw_error="NPCI switch unavailable: error U30 switch host unreachable",
        expected_class=FailureClass.INFRA_OUTAGE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Central switch network partition",
    ),
    # DUPLICATE
    LabeledErrorCase(
        case_id="DEV_11",
        raw_error="duplicate reference detected: transaction already exists with order_ref_4410",
        expected_class=FailureClass.DUPLICATE,
        expected_retryability=False,
        expected_risk=RiskLevel.HIGH,
        min_confidence=0.90,
        notes="Idempotency violation / duplicate reference",
    ),
    LabeledErrorCase(
        case_id="DEV_12",
        raw_error="idempotency key conflict - payment already processed for this request",
        expected_class=FailureClass.DUPLICATE,
        expected_retryability=False,
        expected_risk=RiskLevel.HIGH,
        min_confidence=0.90,
        notes="Duplicate request token",
    ),
    # CUSTOMER_ABANDONMENT
    LabeledErrorCase(
        case_id="DEV_13",
        raw_error="payment abandoned by user - customer closed the checkout modal",
        expected_class=FailureClass.CUSTOMER_ABANDONMENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Checkout UI closed by customer",
    ),
    LabeledErrorCase(
        case_id="DEV_14",
        raw_error="user navigated away before completing UPI payment intent",
        expected_class=FailureClass.CUSTOMER_ABANDONMENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Intent flow drop-off",
    ),
    # FRAUD_SUSPECTED
    LabeledErrorCase(
        case_id="DEV_15",
        raw_error="transaction flagged by fraud engine: velocity check triggered (>6 attempts/min)",
        expected_class=FailureClass.FRAUD_SUSPECTED,
        expected_retryability=False,
        expected_risk=RiskLevel.CRITICAL,
        min_confidence=0.90,
        notes="High velocity / automated card testing pattern",
    ),
    LabeledErrorCase(
        case_id="DEV_16",
        raw_error="risk score exceeded threshold: card on watchlist (score 0.94)",
        expected_class=FailureClass.FRAUD_SUSPECTED,
        expected_retryability=False,
        expected_risk=RiskLevel.CRITICAL,
        min_confidence=0.90,
        notes="Risk engine watchlist hit",
    ),
    # UNKNOWN / AMBIGUOUS
    LabeledErrorCase(
        case_id="DEV_17",
        raw_error="unclassified response: 0x99_ERR_INTERNAL_CORRUPT_PAYLOAD",
        expected_class=FailureClass.UNKNOWN,
        expected_retryability=False,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.20,
        notes="Unrecognized error code structure",
    ),
    LabeledErrorCase(
        case_id="DEV_18",
        raw_error="error",
        expected_class=FailureClass.UNKNOWN,
        expected_retryability=False,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.10,
        notes="Minimal ambiguous error with no diagnostic tokens",
    ),
    LabeledErrorCase(
        case_id="DEV_19",
        raw_error="daily spend limit reached on consumer card",
        expected_class=FailureClass.HARD_FUNDS_ISSUE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Limit exhaustion is classified as funds issue",
    ),
    LabeledErrorCase(
        case_id="DEV_20",
        raw_error="VBV step-up challenge rejected by customer",
        expected_class=FailureClass.AUTH_BLOCKED,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.80,
        notes="Authentication challenge rejected",
    ),
]


# ---------------------------------------------------------------------------
# Held-Out Evaluation Dataset (20 cases)
# ---------------------------------------------------------------------------

HELDOUT_EVALUATION_DATASET: list[LabeledErrorCase] = [
    # TIMEOUT_TRANSIENT
    LabeledErrorCase(
        case_id="EVAL_01",
        raw_error="upstream timeout after 15000ms: no data received from acquirer switch",
        expected_class=FailureClass.TIMEOUT_TRANSIENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Acquirer read timeout",
    ),
    LabeledErrorCase(
        case_id="EVAL_02",
        raw_error="socket closed unexpectedly while waiting for bank settlement ack",
        expected_class=FailureClass.TIMEOUT_TRANSIENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.80,
        notes="Transient TCP socket reset",
    ),
    # HARD_FUNDS_ISSUE
    LabeledErrorCase(
        case_id="EVAL_03",
        raw_error="declined by issuer: credit limit exhausted on credit card",
        expected_class=FailureClass.HARD_FUNDS_ISSUE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Credit line exhaustion",
    ),
    LabeledErrorCase(
        case_id="EVAL_04",
        raw_error="not sufficient funds in account for debit transaction",
        expected_class=FailureClass.HARD_FUNDS_ISSUE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Savings account deficit",
    ),
    # ISSUER_DECLINE
    LabeledErrorCase(
        case_id="EVAL_05",
        raw_error="card issuer declined: restricted card, please contact bank",
        expected_class=FailureClass.ISSUER_DECLINE,
        expected_retryability=True,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.85,
        notes="Issuer restricted card flag",
    ),
    LabeledErrorCase(
        case_id="EVAL_06",
        raw_error="issuer rejected transaction: 05_DECLINE_DO_NOT_HONOR",
        expected_class=FailureClass.ISSUER_DECLINE,
        expected_retryability=True,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.85,
        notes="ISO decline token",
    ),
    # AUTH_BLOCKED
    LabeledErrorCase(
        case_id="EVAL_07",
        raw_error="OTP entry abandoned by user during 3D secure verification",
        expected_class=FailureClass.AUTH_BLOCKED,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="3DS OTP entry abandoned",
    ),
    LabeledErrorCase(
        case_id="EVAL_08",
        raw_error="3DS authentication failed: customer entered incorrect OTP 3 times",
        expected_class=FailureClass.AUTH_BLOCKED,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="OTP entry failure",
    ),
    # INFRA_OUTAGE
    LabeledErrorCase(
        case_id="EVAL_09",
        raw_error="payment gateway down for maintenance - 502 bad gateway",
        expected_class=FailureClass.INFRA_OUTAGE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="502 gateway downtime",
    ),
    LabeledErrorCase(
        case_id="EVAL_10",
        raw_error="acquirer host not responding - switch connection refused",
        expected_class=FailureClass.INFRA_OUTAGE,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Host connection refused",
    ),
    # DUPLICATE
    LabeledErrorCase(
        case_id="EVAL_11",
        raw_error="duplicate order id submitted for payment: ord_829371 already captured",
        expected_class=FailureClass.DUPLICATE,
        expected_retryability=False,
        expected_risk=RiskLevel.HIGH,
        min_confidence=0.90,
        notes="Captured order duplicate",
    ),
    LabeledErrorCase(
        case_id="EVAL_12",
        raw_error="transaction id reuse detected: payment reference already exists",
        expected_class=FailureClass.DUPLICATE,
        expected_retryability=False,
        expected_risk=RiskLevel.HIGH,
        min_confidence=0.90,
        notes="Payment reference reuse",
    ),
    # CUSTOMER_ABANDONMENT
    LabeledErrorCase(
        case_id="EVAL_13",
        raw_error="UPI collect request rejected by customer in mobile app",
        expected_class=FailureClass.CUSTOMER_ABANDONMENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Customer rejected UPI collect intent",
    ),
    LabeledErrorCase(
        case_id="EVAL_14",
        raw_error="session expired — no user activity on payment page for 15m",
        expected_class=FailureClass.CUSTOMER_ABANDONMENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Checkout session timeout",
    ),
    # FRAUD_SUSPECTED
    LabeledErrorCase(
        case_id="EVAL_15",
        raw_error="suspicious activity detected: potential card-testing pattern across multiple bins",
        expected_class=FailureClass.FRAUD_SUSPECTED,
        expected_retryability=False,
        expected_risk=RiskLevel.CRITICAL,
        min_confidence=0.90,
        notes="Card testing attack pattern",
    ),
    LabeledErrorCase(
        case_id="EVAL_16",
        raw_error="device fingerprint mismatch with risk score 0.91 on flagged IP",
        expected_class=FailureClass.FRAUD_SUSPECTED,
        expected_retryability=False,
        expected_risk=RiskLevel.CRITICAL,
        min_confidence=0.90,
        notes="High risk device fingerprint",
    ),
    # UNKNOWN / NOISY
    LabeledErrorCase(
        case_id="EVAL_17",
        raw_error="null pointer in payment processing chain: code 99",
        expected_class=FailureClass.UNKNOWN,
        expected_retryability=False,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.30,
        notes="Corrupt internal code",
    ),
    LabeledErrorCase(
        case_id="EVAL_18",
        raw_error="???!!!@@# unrecognized binary buffer response",
        expected_class=FailureClass.UNKNOWN,
        expected_retryability=False,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.10,
        notes="Garbage binary string",
    ),
    LabeledErrorCase(
        case_id="EVAL_19",
        raw_error="card blocked by issuer: cardholder reported lost or stolen",
        expected_class=FailureClass.ISSUER_DECLINE,
        expected_retryability=True,
        expected_risk=RiskLevel.MEDIUM,
        min_confidence=0.85,
        notes="Card blocked by issuer",
    ),
    LabeledErrorCase(
        case_id="EVAL_20",
        raw_error="read timeout: no data received in 15000ms from upstream switch",
        expected_class=FailureClass.TIMEOUT_TRANSIENT,
        expected_retryability=True,
        expected_risk=RiskLevel.LOW,
        min_confidence=0.85,
        notes="Switch read timeout",
    ),
]

ALL_LABELED_CASES = DEV_DATASET + HELDOUT_EVALUATION_DATASET
