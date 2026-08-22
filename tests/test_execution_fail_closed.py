"""
tests/test_execution_fail_closed.py
===================================
Comprehensive security, authorization, and fail-closed gate tests for C-4:
  1. Approved action executes via simulator adapter
  2. Blocked action does NOT execute
  3. Escalated action does NOT execute
  4. Unknown verdict does NOT execute
  5. Malformed verdict does NOT execute
  6. Invalid action does NOT execute (empty, None, corrupted, unsupported)
  7. Mismatched event_id does NOT execute (Entity Binding)
  8. Mismatched decision_id does NOT execute (Entity Binding)
  9. Missing guardrail does NOT execute (Direct Bypass Attempt)
  10. Direct execution bypass does NOT execute
  11. Duplicate approved execution remains idempotent
  12. API timeout remains UNKNOWN/FAILURE and never becomes SUCCESS automatically
  13. Transaction confusion tests (Approval A + Event B, Approval A + Decision B)
  14. Formal Financial Safety Invariant Property Test
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.models.schemas import (
    GuardrailDecision,
    GuardrailVerdict,
    OutcomeStatus,
    PaymentFailureEvent,
    PaymentMethod,
    StrategyDecision,
)
from backend.services.execution import ExecutionService
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import SimAction, SimOutcome

# ---------------------------------------------------------------------------
# Test Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_event(event_id: str = "evt_001", payment_id: str = "pay_001", amount: float = 1500.0) -> PaymentFailureEvent:
    return PaymentFailureEvent(
        event_id=event_id,
        payment_id=payment_id,
        merchant_id="merch_test",
        amount=amount,
        currency="INR",
        payment_method=PaymentMethod.upi,
        failure_code="bank timeout",
        raw_gateway_error="bank timeout",
        attempt_number=1,
    )


def make_decision(
    event_id: str = "evt_001",
    decision_id: str = "dec_001",
    selected_action: str = SimAction.DELAYED_RETRY.value,
) -> StrategyDecision:
    try:
        return StrategyDecision(
            decision_id=decision_id,
            event_id=event_id,
            context="TIMEOUT_TRANSIENT+MID",
            candidate_actions=[selected_action],
            sampled_probabilities={selected_action: 0.8},
            posterior_means={selected_action: 0.8},
            ev_scores={selected_action: 500.0},
            selected_action=selected_action,
            selected_ev=500.0,
            reasoning="EV optimization selected action",
            confidence=0.85,
        )
    except Exception:
        return StrategyDecision.model_construct(
            decision_id=decision_id,
            event_id=event_id,
            context="TIMEOUT_TRANSIENT+MID",
            candidate_actions=[selected_action],
            sampled_probabilities={selected_action: 0.8},
            posterior_means={selected_action: 0.8},
            ev_scores={selected_action: 500.0},
            selected_action=selected_action,
            selected_ev=500.0,
            reasoning="EV optimization selected action",
            confidence=0.85,
        )


def make_guardrail(
    event_id: str = "evt_001",
    decision_id: str = "dec_001",
    verdict: GuardrailVerdict = GuardrailVerdict.approved,
    guardrail_id: str = "gr_001",
) -> GuardrailDecision:
    return GuardrailDecision(
        guardrail_id=guardrail_id,
        decision_id=decision_id,
        event_id=event_id,
        verdict=verdict,
        rules_evaluated=["rule_max_retries", "rule_fraud_check"],
        rules_triggered=[] if verdict == GuardrailVerdict.approved else ["rule_mock"],
        reason="Approved for execution" if verdict == GuardrailVerdict.approved else "Blocked by guardrail",
        retry_count_24h=0,
        max_retry_limit=3,
    )


# ---------------------------------------------------------------------------
# Test Suite 1: Fail-Closed Verdict Authorization
# ---------------------------------------------------------------------------


class TestFailClosedVerdictAuthorization:
    """Verify that ONLY GuardrailVerdict.approved can reach outcome execution."""

    def test_approved_action_executes(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        mock_engine.simulate_outcome.return_value = SimOutcome(
            event_id="evt_001",
            action=SimAction.DELAYED_RETRY,
            success=True,
            recovered_value=1500.0,
            cost=5.5,
            net_value=1494.5,
            resolution_delay_ms=250,
            rule_triggered="DELAYED_RETRY_SUCCESS",
        )
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert mock_engine.simulate_outcome.called, "Execution MUST proceed on approved verdict"
        assert res.status == OutcomeStatus.success
        assert res.amount_recovered == 1500.0
        assert res.gateway_response_code == "200_OK_RECOVERED"

    def test_blocked_action_does_not_execute(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()
        gr = make_guardrail(verdict=GuardrailVerdict.blocked)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, "OutcomeEngine MUST NOT be called when blocked"
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "GUARDRAIL_BLOCKED"

    def test_escalated_action_does_not_execute(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision(selected_action=SimAction.HUMAN_ESCALATION.value)
        gr = make_guardrail(verdict=GuardrailVerdict.escalate)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, "OutcomeEngine MUST NOT be called on escalation"
        assert res.status == OutcomeStatus.pending
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "ESCALATED_HUMAN_REVIEW"

    def test_unknown_or_custom_verdict_does_not_execute(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()
        gr = make_guardrail(verdict=GuardrailVerdict.approved)
        # Mutate to an invalid/unauthorized verdict string
        gr.verdict = "UNKNOWN_VERDICT"  # type: ignore

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, "OutcomeEngine MUST NOT be called on unknown verdict"
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "GUARDRAIL_VERDICT_UNAUTHORIZED"

    def test_missing_verdict_does_not_execute(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()
        gr = make_guardrail(verdict=GuardrailVerdict.approved)
        gr.verdict = None  # type: ignore

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, "OutcomeEngine MUST NOT be called when verdict is None"
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "GUARDRAIL_VERDICT_UNAUTHORIZED"


# ---------------------------------------------------------------------------
# Test Suite 2: Entity Binding Verification
# ---------------------------------------------------------------------------


class TestEntityBinding:
    """Verify that approval is strictly bound to the exact event and decision."""

    def test_mismatched_event_id_aborts(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event(event_id="evt_REAL")
        dec = make_decision(event_id="evt_REAL")
        gr = make_guardrail(event_id="evt_DIFFERENT", decision_id=dec.decision_id, verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, "Execution MUST be aborted on mismatched event_id"
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "ENTITY_BINDING_MISMATCH"

    def test_mismatched_decision_id_aborts(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event(event_id="evt_REAL")
        dec = make_decision(event_id="evt_REAL", decision_id="dec_ACTUAL")
        gr = make_guardrail(event_id="evt_REAL", decision_id="dec_DIFFERENT", verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, "Execution MUST be aborted on mismatched decision_id"
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "ENTITY_BINDING_MISMATCH"

    def test_decision_event_mismatch_aborts(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event(event_id="evt_001")
        dec = make_decision(event_id="evt_002", decision_id="dec_001")
        gr = make_guardrail(event_id="evt_001", decision_id="dec_001", verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.abandoned
        assert res.gateway_response_code == "ENTITY_BINDING_MISMATCH"

    def test_transaction_confusion_matrix(self) -> None:
        """Test combinations of Cross-Event / Cross-Decision confusion."""
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev_a = make_event(event_id="evt_A", payment_id="pay_A")
        ev_b = make_event(event_id="evt_B", payment_id="pay_B")
        dec_a = make_decision(event_id="evt_A", decision_id="dec_A")
        dec_b = make_decision(event_id="evt_B", decision_id="dec_B")
        gr_a = make_guardrail(event_id="evt_A", decision_id="dec_A", verdict=GuardrailVerdict.approved)

        # Confusion 1: Approval A + Event B
        res1 = svc.execute_sync(event=ev_b, decision=dec_a, guardrail=gr_a)
        assert not mock_engine.simulate_outcome.called
        assert res1.status == OutcomeStatus.abandoned
        assert res1.gateway_response_code == "ENTITY_BINDING_MISMATCH"

        # Confusion 2: Approval A + Decision B
        res2 = svc.execute_sync(event=ev_a, decision=dec_b, guardrail=gr_a)
        assert not mock_engine.simulate_outcome.called
        assert res2.status == OutcomeStatus.abandoned
        assert res2.gateway_response_code == "ENTITY_BINDING_MISMATCH"


# ---------------------------------------------------------------------------
# Test Suite 3: Action Validation (No Silent Defaulting)
# ---------------------------------------------------------------------------


class TestActionValidation:
    """Verify that invalid/corrupted/unsupported actions safely abort without executing."""

    @pytest.mark.parametrize(
        "bad_action",
        [
            "",
            "   ",
            "INVALID_ACTION_NAME",
            "DROP TABLE transactions;",
            "unsupported_custom_retry",
            "12345",
        ],
    )
    def test_invalid_action_aborts_without_defaulting_to_delayed_retry(self, bad_action: str) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision(selected_action=bad_action)
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called, (
            f"Action {bad_action} must NOT be silently defaulted to DELAYED_RETRY or executed"
        )
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert "INVALID_ACTION" in res.gateway_response_code

    def test_null_action_aborts(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()
        dec.selected_action = None  # type: ignore
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "INVALID_ACTION_EMPTY"

    def test_human_escalation_approved_does_not_mutate_financials(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision(selected_action=SimAction.HUMAN_ESCALATION.value)
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert not mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.pending
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "ESCALATED_HUMAN_REVIEW"


# ---------------------------------------------------------------------------
# Test Suite 4: Direct Execution Bypass Attempts
# ---------------------------------------------------------------------------


class TestDirectExecutionBypass:
    """Verify that calling ExecutionService directly with invalid inputs safely aborts."""

    def test_direct_bypass_with_none_guardrail(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()

        res = svc.execute_sync(event=ev, decision=dec, guardrail=None)  # type: ignore

        assert not mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.abandoned
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "GUARDRAIL_MISSING_OR_MALFORMED"

    def test_direct_bypass_with_none_event(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        dec = make_decision()
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=None, decision=dec, guardrail=gr)  # type: ignore

        assert not mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.abandoned
        assert res.gateway_response_code == "INVALID_EVENT"

    def test_direct_bypass_with_none_decision(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=None, guardrail=gr)  # type: ignore

        assert not mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.abandoned
        assert res.gateway_response_code == "INVALID_DECISION"


# ---------------------------------------------------------------------------
# Test Suite 5: Idempotency & Fault Injection
# ---------------------------------------------------------------------------


class TestIdempotencyAndFaultInjection:
    """Verify idempotency protection and safe fault simulation."""

    def test_duplicate_approved_execution_is_idempotent(self) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        mock_engine.simulate_outcome.return_value = SimOutcome(
            event_id="evt_001",
            action=SimAction.IMMEDIATE_RETRY,
            success=True,
            recovered_value=1000.0,
            cost=2.0,
            net_value=998.0,
            resolution_delay_ms=50,
            rule_triggered="SUCCESS",
        )
        svc = ExecutionService(outcome_engine=mock_engine, enforce_idempotency=True)

        ev = make_event()
        dec = make_decision(selected_action=SimAction.IMMEDIATE_RETRY.value)
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        # First execution succeeds
        res1 = svc.execute_sync(event=ev, decision=dec, guardrail=gr)
        assert res1.status == OutcomeStatus.success
        assert mock_engine.simulate_outcome.call_count == 1

        # Second duplicate execution is blocked by idempotency guard
        res2 = svc.execute_sync(event=ev, decision=dec, guardrail=gr)
        assert res2.status == OutcomeStatus.abandoned
        assert res2.gateway_response_code == "DUPLICATE_EXECUTION_BLOCKED"
        assert res2.amount_recovered == 0.0
        assert mock_engine.simulate_outcome.call_count == 1, "Duplicate execution MUST NOT call outcome engine"

    def test_api_timeout_remains_failure_never_success(self) -> None:
        from backend.services.execution import NetworkTimeoutException
        mock_engine = MagicMock(spec=OutcomeEngine)
        mock_engine.simulate_outcome.side_effect = NetworkTimeoutException("Adapter timeout simulated")
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event()
        dec = make_decision()
        gr = make_guardrail(verdict=GuardrailVerdict.approved)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        assert mock_engine.simulate_outcome.called
        assert res.status == OutcomeStatus.failure
        assert res.amount_recovered == 0.0
        assert res.gateway_response_code == "ERR_ADAPTER_NETWORK_TIMEOUT"


# ---------------------------------------------------------------------------
# Test Suite 6: Formal Financial Safety Invariant
# ---------------------------------------------------------------------------


class TestFinancialSafetyInvariant:
    """Formal property test verifying the Core Security Invariant:
    
    FOR ALL execution requests:
      execution_called == TRUE
      ONLY IF
        guardrail.verdict == APPROVED
        AND guardrail.event_id == event.event_id
        AND guardrail.decision_id == decision.decision_id
        AND action is valid
        AND idempotency check passes
      Otherwise:
        execution_called == FALSE
    """

    @pytest.mark.parametrize(
        "verdict,ev_id,gr_ev_id,dec_id,gr_dec_id,action,force_err,expect_called",
        [
            # 1. Valid approved request -> MUST execute
            (GuardrailVerdict.approved, "E1", "E1", "D1", "D1", SimAction.IMMEDIATE_RETRY.value, False, True),
            (GuardrailVerdict.approved, "E2", "E2", "D2", "D2", SimAction.DELAYED_RETRY.value, False, True),
            # 2. Blocked verdict -> MUST NOT execute
            (GuardrailVerdict.blocked, "E1", "E1", "D1", "D1", SimAction.IMMEDIATE_RETRY.value, False, False),
            # 3. Escalated verdict -> MUST NOT execute
            (GuardrailVerdict.escalate, "E1", "E1", "D1", "D1", SimAction.HUMAN_ESCALATION.value, False, False),
            # 4. Mismatched event_id -> MUST NOT execute
            (GuardrailVerdict.approved, "E1", "E2", "D1", "D1", SimAction.IMMEDIATE_RETRY.value, False, False),
            # 5. Mismatched decision_id -> MUST NOT execute
            (GuardrailVerdict.approved, "E1", "E1", "D1", "D2", SimAction.IMMEDIATE_RETRY.value, False, False),
            # 6. Invalid action -> MUST NOT execute
            (GuardrailVerdict.approved, "E1", "E1", "D1", "D1", "INVALID_NON_EXISTENT_ACTION", False, False),
            # 7. Empty action -> MUST NOT execute
            (GuardrailVerdict.approved, "E1", "E1", "D1", "D1", "", False, False),
            # 8. Force network timeout error -> OutcomeEngine called, timeout raised, safe failure returned
            (GuardrailVerdict.approved, "E1", "E1", "D1", "D1", SimAction.IMMEDIATE_RETRY.value, True, True),
        ],
    )
    def test_financial_safety_invariant_matrix(
        self,
        verdict: GuardrailVerdict,
        ev_id: str,
        gr_ev_id: str,
        dec_id: str,
        gr_dec_id: str,
        action: str,
        force_err: bool,
        expect_called: bool,
    ) -> None:
        mock_engine = MagicMock(spec=OutcomeEngine)
        if force_err:
            from backend.services.execution import NetworkTimeoutException
            mock_engine.simulate_outcome.side_effect = NetworkTimeoutException("Adapter timeout simulated")
        else:
            mock_engine.simulate_outcome.return_value = SimOutcome(
                event_id=ev_id,
                action=SimAction.IMMEDIATE_RETRY,
                success=True,
                recovered_value=1000.0,
                cost=2.0,
                net_value=998.0,
                resolution_delay_ms=10,
                rule_triggered="TEST",
            )
        svc = ExecutionService(outcome_engine=mock_engine)

        ev = make_event(event_id=ev_id)
        dec = make_decision(event_id=ev_id, decision_id=dec_id, selected_action=action)
        gr = make_guardrail(event_id=gr_ev_id, decision_id=gr_dec_id, verdict=verdict)

        res = svc.execute_sync(event=ev, decision=dec, guardrail=gr)

        if expect_called:
            assert mock_engine.simulate_outcome.called, "OutcomeEngine should have been invoked"
            assert res.status in {OutcomeStatus.success, OutcomeStatus.failure}
        else:
            assert not mock_engine.simulate_outcome.called, (
                f"OutcomeEngine MUST NOT be invoked for invalid/unapproved state! Result code: {res.gateway_response_code}"
            )
            assert res.amount_recovered == 0.0


class TestInvalidActionValidation:
    def test_pydantic_validation_exposes_invalid_action(self) -> None:
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as excinfo:
            StrategyDecision(
                event_id="evt_001",
                context="TIMEOUT_TRANSIENT+MID",
                candidate_actions=["INVALID_ACTION"],
                selected_action="INVALID_ACTION",
            )
        assert "Invalid selected_action" in str(excinfo.value)

    def test_diagnosis_result_has_no_recovery_action_authority(self) -> None:
        from backend.models.schemas import DiagnosisResult
        fields = DiagnosisResult.model_fields.keys()
        for forbidden in ["selected_action", "recommended_action", "suggested_strategies", "authorized_amount", "execute_strategy"]:
            assert forbidden not in fields, f"DiagnosisResult must not contain recovery-action authority: {forbidden}"


class TestConcurrencyIdempotency:
    def test_concurrent_guardrail_requests(self) -> None:
        import concurrent.futures

        from backend.models.schemas import StrategyDecision
        from backend.services.guardrail import GuardrailEngine

        engine = GuardrailEngine()
        ev = make_event(payment_id="pay_dup_001")
        dec = StrategyDecision(
            event_id="evt_001",
            context="TIMEOUT_TRANSIENT+MID",
            candidate_actions=[SimAction.IMMEDIATE_RETRY.value],
            selected_action=SimAction.IMMEDIATE_RETRY.value,
        )

        num_threads = 10
        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(engine.evaluate, ev, dec) for _ in range(num_threads)]
            results = [f.result() for f in futures]

        approved_count = sum(1 for r in results if r.verdict == GuardrailVerdict.approved)
        blocked_count = sum(1 for r in results if r.verdict == GuardrailVerdict.blocked)

        assert approved_count == 1
        assert blocked_count == num_threads - 1
