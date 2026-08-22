"""
tests/test_pipeline.py
======================

Comprehensive end-to-end integration tests for the RevPilot recovery pipeline:
  1. Full 10-stage execution and audit trail generation
  2. Audit event structure at each stage (timestamp, event_id, stage, input/output ref, decision, reason, latency, status)
  3. Path 1: Success Path (Approved retry succeeds & learns)
  4. Path 2: Human-Review Path (Escalation triggered, marked pending)
  5. Path 3: Blocked Path (Guardrail blocks on retry limits or fraud, marked abandoned)
  6. Path 4: Diagnosis Unknown Path (Ambiguous/noisy error diagnosed as UNKNOWN, safely handled)
  7. Path 5: Execution Failure Path (Approved retry fails during execution, learns failure)
  8. Batch Processing of 500 Events
  9. Error Resilience (Continues processing batch after single event failure)
"""

from __future__ import annotations

import pytest

from backend.models.schemas import (
    AuditEvent,
    FailureClass,
    GuardrailVerdict,
    OutcomeStatus,
    PaymentFailureEvent,
    PaymentMethod,
    RiskLevel,
    ValueTier,
)
from backend.services.pipeline import (
    EventPipelineResult,
    PipelineBatchSummary,
    RevPilotPipeline,
)
from backend.simulator.event_generator import EventGenerator
from backend.simulator.types import SimAction, SimEvent


@pytest.fixture
def pipeline() -> RevPilotPipeline:
    return RevPilotPipeline(seed=42)


class TestPipelineFlowAndAudit:
    def test_full_10_stage_flow_emits_structured_audits(self, pipeline: RevPilotPipeline) -> None:
        event = PaymentFailureEvent(
            payment_id="pay_flow_01",
            merchant_id="merch_01",
            amount=1800.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out after 30s",
            raw_gateway_error="bank response timed out after 30s",
            attempt_number=1,
        )

        result = pipeline.process_event(event)

        assert isinstance(result, EventPipelineResult)
        assert result.event_id == event.event_id
        assert result.stage_reached == "completed"
        assert result.diagnosis is not None
        assert result.strategy is not None
        assert result.guardrail is not None
        assert result.outcome is not None

        # Verify audit trail contains records for all stages
        stages_emitted = [a.stage for a in result.audit_events]
        expected_stages = [
            "schema_validation",
            "diagnosis",
            "context_creation",
            "strategy",
            "guardrail",
            "execution",
            "statistical_update",
        ]
        for stg in expected_stages:
            assert stg in stages_emitted, f"Missing audit stage: {stg}"

        # Verify audit fields structure
        for ae in result.audit_events:
            assert isinstance(ae, AuditEvent)
            assert ae.event_id == event.event_id
            assert ae.timestamp is not None
            assert ae.stage != ""
            assert ae.status != ""
            assert ae.latency_ms >= 0.0


class TestFiveExecutionPaths:
    def test_path_1_success_path(self, pipeline: RevPilotPipeline) -> None:
        """Transient timeout on attempt 1 gets approved, executes, and recovers."""
        event = PaymentFailureEvent(
            payment_id="pay_succ_01",
            merchant_id="merch_01",
            amount=2000.0,
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
        )
        result = pipeline.process_event(event)

        assert result.guardrail_verdict == GuardrailVerdict.approved
        assert result.diagnosis.normalized_failure_class == FailureClass.TIMEOUT_TRANSIENT
        assert result.outcome.status in {OutcomeStatus.success, OutcomeStatus.failure}
        if result.success:
            assert result.amount_recovered == 2000.0
            assert result.net_value > 0

    def test_path_2_human_review_path(self, pipeline: RevPilotPipeline) -> None:
        """Duplicate transaction requires human escalation, guardrail escalates, marked pending."""
        event = PaymentFailureEvent(
            payment_id="pay_dupe_01",
            merchant_id="merch_01",
            amount=5000.0,
            payment_method=PaymentMethod.credit_card,
            failure_code="duplicate reference detected: ord_1092 already processed",
            attempt_number=1,
        )
        # Override bandit state to force HUMAN_ESCALATION selection for DUPLICATE+HIGH context
        context_key = "DUPLICATE+HIGH"
        for act in pipeline.bandit.candidate_actions:
            arm = pipeline.bandit.state.get_arm(context_key, act)
            if act == SimAction.HUMAN_ESCALATION.value:
                arm.alpha = 100.0
                arm.beta = 1.0
            else:
                arm.alpha = 1.0
                arm.beta = 100.0

        result = pipeline.process_event(event)

        assert result.diagnosis.normalized_failure_class == FailureClass.DUPLICATE
        assert result.selected_action == SimAction.HUMAN_ESCALATION.value
        assert result.guardrail_verdict == GuardrailVerdict.escalate
        assert result.outcome.status == OutcomeStatus.pending
        assert result.amount_recovered == 0.0
        assert result.success is False

    def test_path_3_blocked_path_on_max_retries(self, pipeline: RevPilotPipeline) -> None:
        """Attempt 4 exceeds max retries limit (3), guardrail blocks unconditionally."""
        event = PaymentFailureEvent(
            payment_id="pay_block_01",
            merchant_id="merch_01",
            amount=1500.0,
            payment_method=PaymentMethod.credit_card,
            failure_code="insufficient balance in account",
            attempt_number=4,  # Exceeds max 3
        )
        result = pipeline.process_event(event)

        assert result.guardrail_verdict == GuardrailVerdict.blocked
        assert result.outcome.status == OutcomeStatus.abandoned
        assert result.amount_recovered == 0.0
        assert result.success is False
        assert "exceeds max" in result.guardrail.reason

    def test_path_3_blocked_path_on_fraud(self, pipeline: RevPilotPipeline) -> None:
        """Fraud suspected is unconditionally blocked by guardrails."""
        event = PaymentFailureEvent(
            payment_id="pay_fraud_01",
            merchant_id="merch_01",
            amount=12000.0,
            payment_method=PaymentMethod.credit_card,
            failure_code="transaction flagged by fraud engine: velocity spike on card",
            attempt_number=1,
        )
        result = pipeline.process_event(event)

        assert result.diagnosis.normalized_failure_class == FailureClass.FRAUD_SUSPECTED
        assert result.guardrail_verdict == GuardrailVerdict.blocked
        assert result.outcome.status == OutcomeStatus.abandoned
        assert result.amount_recovered == 0.0

    def test_path_4_diagnosis_unknown_path(self, pipeline: RevPilotPipeline) -> None:
        """Corrupted/unknown error string safely diagnosed as UNKNOWN and handled."""
        event = PaymentFailureEvent(
            payment_id="pay_unk_01",
            merchant_id="merch_01",
            amount=1000.0,
            payment_method=PaymentMethod.upi,
            failure_code="0x99_UNKNOWN_CORRUPT_PAYLOAD",
            attempt_number=1,
        )
        result = pipeline.process_event(event)

        assert result.diagnosis.normalized_failure_class == FailureClass.UNKNOWN
        assert result.diagnosis.confidence < 0.60
        assert result.stage_reached == "completed"

    def test_path_5_execution_failure_path(self, pipeline: RevPilotPipeline) -> None:
        """Approved action fails during execution adapter simulation (force error)."""
        event = PaymentFailureEvent(
            payment_id="pay_fail_01",
            merchant_id="merch_01",
            amount=2500.0,
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
        )
        from unittest.mock import patch
        from backend.services.execution import NetworkTimeoutException

        with patch.object(pipeline.execution_service.outcome_engine, "simulate_outcome", side_effect=NetworkTimeoutException("Simulated execution timeout")):
            result = pipeline.process_event(event)

        assert result.guardrail_verdict == GuardrailVerdict.approved
        assert result.outcome.status == OutcomeStatus.failure
        assert result.amount_recovered == 0.0
        assert result.success is False
        assert result.outcome.gateway_response_code == "ERR_ADAPTER_NETWORK_TIMEOUT"


class TestBatchProcessingAndResilience:
    def test_batch_processing_500_events(self, pipeline: RevPilotPipeline) -> None:
        """Process a full batch of 500 synthetic records through the integrated pipeline."""
        gen = EventGenerator(seed=20260821, n=500)
        events = gen.generate(n=500, seed=20260821)

        summary = pipeline.process_batch(events, batch_id="batch_full_500")

        assert isinstance(summary, PipelineBatchSummary)
        assert summary.total_events == 500
        assert summary.processed_events == 500
        assert summary.successful_recoveries > 0
        assert summary.blocked_count >= 0
        assert summary.escalated_count >= 0
        assert 0.0 < summary.recovery_rate <= 1.0
        assert summary.gross_recovered_revenue > 0.0
        assert summary.net_recovered_revenue > 0.0
        assert summary.throughput_eps > 0.0
        assert summary.avg_latency_ms >= 0.0
        assert summary.reflection_record is not None
        assert summary.reflection_summary is not None
        assert len(summary.reflection_summary) > 0

    def test_resilience_continues_after_single_event_failure(self, pipeline: RevPilotPipeline) -> None:
        """Verify pipeline does not crash on malformed event and continues processing."""
        valid_ev1 = PaymentFailureEvent(
            payment_id="pay_res_01",
            merchant_id="merch_01",
            amount=1000.0,
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
        )
        # Corrupted item that will fail schema validation or cause processing error
        corrupt_item = {"invalid_field_no_amount": "bad_data"}
        valid_ev2 = PaymentFailureEvent(
            payment_id="pay_res_02",
            merchant_id="merch_01",
            amount=2000.0,
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
        )

        summary = pipeline.process_batch([valid_ev1, corrupt_item, valid_ev2], batch_id="batch_resilience")

        assert summary.total_events == 3
        # Should process the 2 valid events (or handle the corrupt one cleanly)
        assert summary.processed_events >= 2
        assert summary.successful_recoveries >= 1


class TestAuditIdempotency:
    def test_audit_idempotency_deduplication(self) -> None:
        from backend.services.audit import AuditService
        from backend.models.schemas import AuditEvent

        service = AuditService()
        
        # 1. Separate stages for the same event must remain distinct
        event1_diag = AuditEvent(
            event_id="event_test_001",
            stage="diagnosis",
            input_reference="ref1",
            output_reference="ref2",
            decision="DIAGNOSED",
            reason="Timeout",
        )
        event1_gr = AuditEvent(
            event_id="event_test_001",
            stage="guardrail",
            input_reference="ref2",
            output_reference="ref3",
            decision="APPROVED",
            reason="All checks pass",
        )
        
        service.log(event1_diag)
        service.log(event1_gr)
        assert service.count() == 2
        
        # 2. Duplicate stage emission for the same event must be deduplicated
        event1_diag_duplicate = AuditEvent(
            event_id="event_test_001",
            stage="diagnosis",
            input_reference="ref1",
            output_reference="ref2",
            decision="DIAGNOSED",
            reason="Timeout",
        )
        service.log(event1_diag_duplicate)
        assert service.count() == 2  # Deduplicated!
        
        # 3. Same stage for a different event must remain distinct
        event2_diag = AuditEvent(
            event_id="event_test_002",
            stage="diagnosis",
            input_reference="ref1",
            output_reference="ref2",
            decision="DIAGNOSED",
            reason="Timeout",
        )
        service.log(event2_diag)
        assert service.count() == 3  # Distinct!
        
        # 4. Verify append-only behavior is intact (unique events continue to append)
        assert [e.event_id for e in service.get_all()] == ["event_test_001", "event_test_001", "event_test_002"]
        assert [e.stage for e in service.get_all()] == ["diagnosis", "guardrail", "diagnosis"]
