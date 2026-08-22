"""
Tests for Pydantic models and data contracts.
"""

import pytest
from pydantic import ValidationError

from backend.models.schemas import (
    AuditAction,
    AuditEvent,
    BenchmarkResult,
    DiagnosisResult,
    ExceptionRecord,
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


class TestPaymentFailureEvent:
    """Tests for PaymentFailureEvent schema."""

    def test_creation_with_valid_data(self, sample_payment_event: PaymentFailureEvent) -> None:
        assert sample_payment_event.payment_id == "pay_TEST123456"
        assert sample_payment_event.amount == 1500.00
        assert sample_payment_event.failure_reason == FailureReason.insufficient_funds
        assert sample_payment_event.attempt_number == 1
        assert sample_payment_event.event_id  # UUID should be auto-generated

    def test_invalid_amount_zero(self) -> None:
        with pytest.raises(ValidationError):
            PaymentFailureEvent(
                payment_id="pay_X",
                merchant_id="merch_X",
                amount=0,
                payment_method=PaymentMethod.credit_card,
                failure_reason=FailureReason.network_error,
                failure_code="ERR",
                attempt_number=1,
            )

    def test_invalid_amount_negative(self) -> None:
        with pytest.raises(ValidationError):
            PaymentFailureEvent(
                payment_id="pay_X",
                merchant_id="merch_X",
                amount=-100,
                payment_method=PaymentMethod.credit_card,
                failure_reason=FailureReason.network_error,
                failure_code="ERR",
                attempt_number=1,
            )

    def test_invalid_attempt_number(self) -> None:
        with pytest.raises(ValidationError):
            PaymentFailureEvent(
                payment_id="pay_X",
                merchant_id="merch_X",
                amount=100,
                payment_method=PaymentMethod.credit_card,
                failure_reason=FailureReason.network_error,
                failure_code="ERR",
                attempt_number=0,
            )

    def test_default_currency(self) -> None:
        event = PaymentFailureEvent(
            payment_id="pay_X",
            merchant_id="merch_X",
            amount=500,
            payment_method=PaymentMethod.upi,
            failure_reason=FailureReason.network_error,
            failure_code="TIMEOUT",
            attempt_number=1,
        )
        assert event.currency == "INR"

    def test_optional_fields_none(self) -> None:
        event = PaymentFailureEvent(
            payment_id="pay_X",
            merchant_id="merch_X",
            amount=500,
            payment_method=PaymentMethod.upi,
            failure_reason=FailureReason.network_error,
            failure_code="TIMEOUT",
            attempt_number=1,
        )
        assert event.card_last4 is None
        assert event.card_network is None
        assert event.bank_code is None


class TestDiagnosisResult:
    """Tests for DiagnosisResult schema."""

    def test_creation(self, sample_diagnosis: DiagnosisResult) -> None:
        assert sample_diagnosis.is_retryable is True
        assert sample_diagnosis.confidence == 0.85
        assert not hasattr(sample_diagnosis, "suggested_strategies")

    def test_confidence_bounds_low(self) -> None:
        with pytest.raises(ValidationError):
            DiagnosisResult(
                event_id="evt_x",
                failure_category=FailureReason.network_error,
                is_retryable=True,
                confidence=-0.1,
                reasoning="test",
            )

    def test_confidence_bounds_high(self) -> None:
        with pytest.raises(ValidationError):
            DiagnosisResult(
                event_id="evt_x",
                failure_category=FailureReason.network_error,
                is_retryable=True,
                confidence=1.1,
                reasoning="test",
            )



class TestStrategyDecision:
    """Tests for StrategyDecision schema."""

    def test_creation(self, sample_strategy_decision: StrategyDecision) -> None:
        assert sample_strategy_decision.selected_strategy == RetryStrategy.delayed_retry
        assert sample_strategy_decision.exploration is False
        assert "delayed_retry" in sample_strategy_decision.arm_probabilities


class TestGuardrailDecision:
    """Tests for GuardrailDecision schema."""

    def test_approved_verdict(self, sample_guardrail_approved: GuardrailDecision) -> None:
        assert sample_guardrail_approved.verdict == GuardrailVerdict.approved
        assert len(sample_guardrail_approved.rules_triggered) == 0

    def test_blocked_verdict(self, sample_guardrail_blocked: GuardrailDecision) -> None:
        assert sample_guardrail_blocked.verdict == GuardrailVerdict.blocked
        assert "max_retry_24h" in sample_guardrail_blocked.rules_triggered

    def test_escalate_verdict(self) -> None:
        decision = GuardrailDecision(
            decision_id="dec_x",
            event_id="evt_x",
            verdict=GuardrailVerdict.escalate,
            rules_evaluated=["fraud_check"],
            rules_triggered=["fraud_check"],
            reason="Potential fraud detected — escalating to human review.",
            retry_count_24h=0,
            max_retry_limit=5,
        )
        assert decision.verdict == GuardrailVerdict.escalate


class TestOutcomeResult:
    """Tests for OutcomeResult schema."""

    def test_success_outcome(self, sample_outcome_success: OutcomeResult) -> None:
        assert sample_outcome_success.status == OutcomeStatus.success
        assert sample_outcome_success.amount_recovered == 1500.00

    def test_failure_outcome(self, sample_outcome_failure: OutcomeResult) -> None:
        assert sample_outcome_failure.status == OutcomeStatus.failure
        assert sample_outcome_failure.amount_recovered == 0.0

    def test_amount_recovered_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            OutcomeResult(
                event_id="evt_x",
                decision_id="dec_x",
                strategy_applied=RetryStrategy.immediate_retry,
                status=OutcomeStatus.failure,
                amount_recovered=-100,
                latency_ms=100,
            )


class TestAuditEvent:
    """Tests for AuditEvent schema."""

    def test_creation(self) -> None:
        event = AuditEvent(
            event_id="evt_x",
            action=AuditAction.retry_initiated,
            actor="execution_service",
            details={"strategy": "delayed_retry"},
            idempotency_key="idem_12345",
        )
        assert event.action == AuditAction.retry_initiated
        assert event.actor == "execution_service"
        assert event.parent_audit_id is None


class TestBenchmarkResult:
    """Tests for BenchmarkResult schema."""

    def test_creation(self) -> None:
        result = BenchmarkResult(
            strategy_name="thompson_sampling",
            total_events=1000,
            recovery_rate=0.42,
            revenue_recovered=630000.0,
            avg_time_to_recovery_ms=5200.0,
            false_positive_rate=0.03,
            guardrail_block_rate=0.12,
            baseline_recovery_rate=0.25,
            improvement_over_baseline=68.0,
        )
        assert result.total_events == 1000
        assert result.improvement_over_baseline == 68.0


class TestExceptionRecord:
    """Tests for ExceptionRecord schema."""

    def test_creation(self) -> None:
        record = ExceptionRecord(
            component="diagnosis_agent",
            exception_type="TimeoutError",
            message="LLM call timed out after 30s",
            severity="warning",
            handled=True,
            fallback_action="Used taxonomy-based diagnosis instead",
        )
        assert record.handled is True
        assert record.severity == "warning"
        assert record.event_id is None


class TestSerializationRoundtrip:
    """Test that all models survive serialization to dict and back."""

    def test_payment_event_roundtrip(self, sample_payment_event: PaymentFailureEvent) -> None:
        data = sample_payment_event.model_dump()
        restored = PaymentFailureEvent(**data)
        assert restored.event_id == sample_payment_event.event_id
        assert restored.amount == sample_payment_event.amount

    def test_diagnosis_roundtrip(self, sample_diagnosis: DiagnosisResult) -> None:
        data = sample_diagnosis.model_dump()
        restored = DiagnosisResult(**data)
        assert restored.diagnosis_id == sample_diagnosis.diagnosis_id

    def test_strategy_roundtrip(self, sample_strategy_decision: StrategyDecision) -> None:
        data = sample_strategy_decision.model_dump()
        restored = StrategyDecision(**data)
        assert restored.decision_id == sample_strategy_decision.decision_id

    def test_guardrail_roundtrip(self, sample_guardrail_approved: GuardrailDecision) -> None:
        data = sample_guardrail_approved.model_dump()
        restored = GuardrailDecision(**data)
        assert restored.guardrail_id == sample_guardrail_approved.guardrail_id

    def test_outcome_roundtrip(self, sample_outcome_success: OutcomeResult) -> None:
        data = sample_outcome_success.model_dump()
        restored = OutcomeResult(**data)
        assert restored.outcome_id == sample_outcome_success.outcome_id
