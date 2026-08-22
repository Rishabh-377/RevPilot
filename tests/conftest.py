"""
Shared pytest fixtures for RevPilot tests.
"""


import pytest

from backend.models.schemas import (
    DiagnosisResult,
    FailureReason,
    GuardrailDecision,
    GuardrailVerdict,
    OutcomeResult,
    OutcomeStatus,
    PaymentFailureEvent,
    PaymentMethod,
    RetryStrategy,
    StrategyDecision,
)


@pytest.fixture
def sample_payment_event() -> PaymentFailureEvent:
    """A realistic payment failure event for testing."""
    return PaymentFailureEvent(
        payment_id="pay_TEST123456",
        merchant_id="merch_RAZORPAY01",
        amount=1500.00,
        currency="INR",
        payment_method=PaymentMethod.credit_card,
        failure_reason=FailureReason.insufficient_funds,
        failure_code="INSUFFICIENT_FUNDS",
        card_last4="4242",
        card_network="visa",
        bank_code="HDFC",
        attempt_number=1,
    )


@pytest.fixture
def sample_diagnosis(sample_payment_event: PaymentFailureEvent) -> DiagnosisResult:
    """A diagnosis result matching the sample payment event."""
    return DiagnosisResult(
        event_id=sample_payment_event.event_id,
        failure_category=FailureReason.insufficient_funds,
        is_retryable=True,
        confidence=0.85,
        reasoning="Insufficient funds detected. Cardholder may have funds later in the day.",
        context_signals={"time_of_day": "morning", "merchant_risk_score": 0.1},
    )



@pytest.fixture
def sample_strategy_decision(
    sample_payment_event: PaymentFailureEvent,
    sample_diagnosis: DiagnosisResult,
) -> StrategyDecision:
    """A strategy decision from the bandit optimizer."""
    return StrategyDecision(
        event_id=sample_payment_event.event_id,
        diagnosis_id=sample_diagnosis.diagnosis_id,
        selected_strategy=RetryStrategy.delayed_retry,
        confidence=0.72,
        exploration=False,
        arm_probabilities={
            "delayed_retry": 0.72,
            "amount_split": 0.45,
            "time_shift": 0.38,
            "immediate_retry": 0.22,
        },
        context_used={"failure_reason": "insufficient_funds", "amount_bucket": "medium"},
    )


@pytest.fixture
def sample_guardrail_approved(
    sample_strategy_decision: StrategyDecision,
    sample_payment_event: PaymentFailureEvent,
) -> GuardrailDecision:
    """A guardrail decision that approves the retry."""
    return GuardrailDecision(
        decision_id=sample_strategy_decision.decision_id,
        event_id=sample_payment_event.event_id,
        verdict=GuardrailVerdict.approved,
        rules_evaluated=["max_retry_count", "max_retry_24h", "amount_bounds", "cooloff_period"],
        rules_triggered=[],
        reason="All guardrail rules passed.",
        retry_count_24h=1,
        max_retry_limit=5,
    )


@pytest.fixture
def sample_guardrail_blocked(
    sample_strategy_decision: StrategyDecision,
    sample_payment_event: PaymentFailureEvent,
) -> GuardrailDecision:
    """A guardrail decision that blocks the retry."""
    return GuardrailDecision(
        decision_id=sample_strategy_decision.decision_id,
        event_id=sample_payment_event.event_id,
        verdict=GuardrailVerdict.blocked,
        rules_evaluated=["max_retry_count", "max_retry_24h", "amount_bounds", "cooloff_period"],
        rules_triggered=["max_retry_24h"],
        reason="Card has exceeded maximum retries in the last 24 hours (5/5).",
        retry_count_24h=5,
        max_retry_limit=5,
    )


@pytest.fixture
def sample_outcome_success(
    sample_payment_event: PaymentFailureEvent,
    sample_strategy_decision: StrategyDecision,
) -> OutcomeResult:
    """A successful recovery outcome."""
    return OutcomeResult(
        event_id=sample_payment_event.event_id,
        decision_id=sample_strategy_decision.decision_id,
        strategy_applied=RetryStrategy.delayed_retry,
        status=OutcomeStatus.success,
        amount_recovered=1500.00,
        latency_ms=1250,
        gateway_response_code="SUCCESS",
    )


@pytest.fixture
def sample_outcome_failure(
    sample_payment_event: PaymentFailureEvent,
    sample_strategy_decision: StrategyDecision,
) -> OutcomeResult:
    """A failed recovery outcome."""
    return OutcomeResult(
        event_id=sample_payment_event.event_id,
        decision_id=sample_strategy_decision.decision_id,
        strategy_applied=RetryStrategy.delayed_retry,
        status=OutcomeStatus.failure,
        amount_recovered=0.0,
        latency_ms=980,
        gateway_response_code="INSUFFICIENT_FUNDS",
    )
