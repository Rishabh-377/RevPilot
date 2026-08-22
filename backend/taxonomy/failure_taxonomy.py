"""
Failure Taxonomy
================

Structured classification of payment failure reasons with metadata
about retryability, default recovery strategies, and base recovery rates.

This taxonomy is used by:
    - DiagnosisAgent: to classify raw failure codes
    - ThompsonSamplingBandit: to filter eligible arms
    - GuardrailEngine: to block retries on non-retryable failures
    - Simulator: to generate realistic failure distributions
"""

from backend.models.schemas import FailureReason, RetryStrategy

# ---------------------------------------------------------------------------
# Taxonomy Data
# ---------------------------------------------------------------------------

FAILURE_TAXONOMY: dict[FailureReason, dict] = {
    FailureReason.insufficient_funds: {
        "description": "Cardholder's account has insufficient balance for the transaction.",
        "retryable": True,
        "default_strategies": [
            RetryStrategy.delayed_retry,
            RetryStrategy.amount_split,
            RetryStrategy.time_shift,
        ],
        "base_recovery_rate": 0.35,
    },
    FailureReason.card_expired: {
        "description": "The card has passed its expiration date.",
        "retryable": False,
        "default_strategies": [RetryStrategy.card_switch],
        "base_recovery_rate": 0.05,
    },
    FailureReason.bank_declined: {
        "description": "The issuing bank declined the transaction without a specific reason.",
        "retryable": True,
        "default_strategies": [
            RetryStrategy.delayed_retry,
            RetryStrategy.time_shift,
        ],
        "base_recovery_rate": 0.25,
    },
    FailureReason.network_error: {
        "description": "Transaction failed due to a network timeout or connectivity issue.",
        "retryable": True,
        "default_strategies": [RetryStrategy.immediate_retry],
        "base_recovery_rate": 0.75,
    },
    FailureReason.fraud_suspected: {
        "description": "The transaction was flagged for suspected fraud by the issuer or gateway.",
        "retryable": False,
        "default_strategies": [RetryStrategy.no_action],
        "base_recovery_rate": 0.0,
    },
    FailureReason.authentication_failed: {
        "description": "3DS or OTP authentication failed or was abandoned by the cardholder.",
        "retryable": True,
        "default_strategies": [
            RetryStrategy.immediate_retry,
            RetryStrategy.delayed_retry,
        ],
        "base_recovery_rate": 0.40,
    },
    FailureReason.limit_exceeded: {
        "description": "Transaction exceeds the cardholder's daily/monthly spending limit.",
        "retryable": True,
        "default_strategies": [
            RetryStrategy.delayed_retry,
            RetryStrategy.amount_split,
            RetryStrategy.time_shift,
        ],
        "base_recovery_rate": 0.30,
    },
    FailureReason.invalid_card: {
        "description": "Card number is invalid, has been cancelled, or does not exist.",
        "retryable": False,
        "default_strategies": [RetryStrategy.card_switch],
        "base_recovery_rate": 0.02,
    },
    FailureReason.issuer_unavailable: {
        "description": "Issuing bank's systems are temporarily unavailable.",
        "retryable": True,
        "default_strategies": [
            RetryStrategy.delayed_retry,
            RetryStrategy.time_shift,
        ],
        "base_recovery_rate": 0.60,
    },
    FailureReason.duplicate_transaction: {
        "description": "A duplicate of this transaction was already processed.",
        "retryable": False,
        "default_strategies": [RetryStrategy.no_action],
        "base_recovery_rate": 0.0,
    },
}


def classify(failure_code: str) -> FailureReason:
    """Classify a raw gateway failure code into a FailureReason category.

    Args:
        failure_code: Raw error code from the payment gateway.

    Returns:
        The matching FailureReason enum member.

    Raises:
        NotImplementedError: Classification logic not yet implemented.
    """
    raise NotImplementedError("classify not yet implemented")
