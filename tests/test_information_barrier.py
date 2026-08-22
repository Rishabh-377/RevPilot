"""
tests/test_information_barrier.py
==================================
Regression tests for C-1: Simulator Circularity Fix.

Root Cause
----------
Before the fix, process_event() passed diagnosis.normalized_failure_class
(the model prediction) to ExecutionService.execute_sync().  ExecutionService
then built a SimEvent with that class and queried GroundTruth against it,
making diagnosis errors invisible.

Fix
---
Stage 1 captures event_data.normalised_failure_class from the original
SimEvent BEFORE stripping to PaymentFailureEvent.  Stage 6 passes this
preserved _true_failure_class to execute_sync().
DiagnosisAgent and StrategyEngine never receive _true_failure_class.
"""
from __future__ import annotations
import inspect
import pytest
from backend.models.schemas import (
    FailureClass, GuardrailDecision, GuardrailVerdict,
    OutcomeStatus, PaymentFailureEvent, PaymentMethod, StrategyDecision,
)
from backend.services.execution import ExecutionService
from backend.services.pipeline import RevPilotPipeline
from backend.simulator.event_generator import EventGenerator
from backend.simulator.ground_truth import GroundTruth, _P as GROUND_TRUTH_P
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import (
    CustomerSegment,
    FailureClass as SimFailureClass,
    SimAction, SimEvent, SimPaymentMethod, ValueTier,
)



def _make_sim_event(true_class, raw_error, amount=1500.0, attempt=0):
    return SimEvent(
        transaction_id="txn_barrier",
        customer_id="cust_barrier",
        amount=amount,
        payment_method=SimPaymentMethod.CARD,
        raw_gateway_error=raw_error,
        previous_attempts=attempt,
        customer_segment=CustomerSegment.REGULAR,
        value_tier=ValueTier.MID,
        normalised_failure_class=true_class,
    )


def _approved_guardrail(event_id, decision_id):
    return GuardrailDecision(
        decision_id=decision_id, event_id=event_id,
        verdict=GuardrailVerdict.approved,
        rules_evaluated=[], rules_triggered=[], reason="approved for test",
        retry_count_24h=0, max_retry_limit=3,
    )


def _strategy(event_id, context, action, ev=100.0):
    return StrategyDecision(
        event_id=event_id, context=context,
        candidate_actions=[action.value],
        sampled_probabilities={action.value: 0.5},
        posterior_means={action.value: 0.5},
        ev_scores={action.value: ev},
        selected_action=action.value, selected_ev=ev,
        exploration_flag=False, reasoning="test", confidence=0.5,
    )


class TestParameterNameRegression:
    """Guard that the renamed parameter cannot silently revert."""

    def test_execute_sync_has_true_failure_class(self):
        sig = inspect.signature(ExecutionService.execute_sync)
        assert "true_failure_class" in sig.parameters, (
            "execute_sync must accept true_failure_class (C-1 fix)"
        )
        assert "failure_class" not in sig.parameters, (
            "Old failure_class param must not exist on execute_sync"
        )

    def test_execute_async_has_true_failure_class(self):
        sig = inspect.signature(ExecutionService.execute)
        assert "true_failure_class" in sig.parameters
        assert "failure_class" not in sig.parameters


class TestTrueClassFlowsToOutcomeEngine:
    """OutcomeEngine must receive the true hidden class, not the diagnosed class."""

    def test_outcome_engine_receives_true_class_infra_outage_misdiagnosed_as_timeout(self):
        """
        True class = INFRA_OUTAGE.
        Raw error  = "bank response timed out" -> diagnosed TIMEOUT_TRANSIENT.
        OutcomeEngine SimEvent must carry INFRA_OUTAGE, not TIMEOUT_TRANSIENT.
        """
        gt = GroundTruth()
        engine = OutcomeEngine(ground_truth=gt, seed=99)
        captured = []
        orig = engine.simulate_outcome

        def spy(ev, action):
            captured.append(ev)
            return orig(ev, action)

        engine.simulate_outcome = spy

        pipeline = RevPilotPipeline(
            seed=99,
            execution_service=ExecutionService(outcome_engine=engine),
        )

        sim_ev = _make_sim_event(
            true_class=SimFailureClass.INFRA_OUTAGE,
            raw_error="bank response timed out",
        )
        result = pipeline.process_event(sim_ev)

        assert result.stage_reached == "completed"
        # Diagnosis should have predicted TIMEOUT_TRANSIENT (intentional mismatch)
        assert result.diagnosis.normalized_failure_class == FailureClass.TIMEOUT_TRANSIENT, (
            "Prerequisite: diagnosis should misclassify INFRA_OUTAGE as TIMEOUT_TRANSIENT"
        )
        if result.guardrail_verdict == GuardrailVerdict.approved:
            assert len(captured) == 1
            assert captured[0].normalised_failure_class == SimFailureClass.INFRA_OUTAGE, (
                f"OutcomeEngine received {captured[0].normalised_failure_class.value}, "
                f"expected INFRA_OUTAGE (the true class). C-1 regression detected."
            )

    def test_execute_sync_direct_uses_supplied_true_class(self):
        """Direct execute_sync call: SimEvent built with true_failure_class, not diagnosed."""
        engine = OutcomeEngine(ground_truth=GroundTruth(), seed=42)
        captured = []
        orig = engine.simulate_outcome

        def spy(ev, action):
            captured.append(ev)
            return orig(ev, action)

        engine.simulate_outcome = spy

        svc = ExecutionService(outcome_engine=engine)
        event = PaymentFailureEvent(
            payment_id="pay_direct", merchant_id="m",
            amount=1500.0, currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="timeout", attempt_number=1,
        )
        s = _strategy(event.event_id, "INFRA_OUTAGE+MID", SimAction.DELAYED_RETRY)
        g = _approved_guardrail(event.event_id, s.decision_id)

        svc.execute_sync(event=event, decision=s, guardrail=g,
                         true_failure_class=SimFailureClass.INFRA_OUTAGE)

        assert len(captured) == 1
        assert captured[0].normalised_failure_class == SimFailureClass.INFRA_OUTAGE


class TestStrategySeesOnlyDiagnosedClass:
    """Strategy context must reflect diagnosis output, not the true class."""

    def test_strategy_context_uses_diagnosed_class(self):
        pipeline = RevPilotPipeline(seed=42)
        sim_ev = _make_sim_event(
            true_class=SimFailureClass.INFRA_OUTAGE,
            raw_error="bank response timed out",
        )
        result = pipeline.process_event(sim_ev)
        assert result.strategy is not None
        assert "TIMEOUT_TRANSIENT" in result.strategy.context, (
            f"Strategy must use DIAGNOSED class TIMEOUT_TRANSIENT, got: {result.strategy.context}"
        )
        assert "INFRA_OUTAGE" not in result.strategy.context, (
            "True class INFRA_OUTAGE must NOT leak into strategy context"
        )

    def test_strategy_engine_view_excludes_true_class(self):
        sim_ev = _make_sim_event(SimFailureClass.INFRA_OUTAGE, "gateway down")
        view = sim_ev.strategy_engine_view()
        assert "normalised_failure_class" not in view
        assert "raw_gateway_error" in view


class TestMisdiagnosisChangesOutcomes:
    """Outcome probability must track true_failure_class, not diagnosed class."""

    def test_outcome_rate_differs_across_true_classes(self):
        """
        IMMEDIATE_RETRY: INFRA_OUTAGE P~0.15 vs TIMEOUT_TRANSIENT P~0.80.
        After fix, outcome rates over 500 trials must differ by >0.30.
        If rates are similar, true_failure_class is being ignored (C-1 regression).
        """
        N = 500
        action = SimAction.IMMEDIATE_RETRY
        p_infra = GROUND_TRUTH_P[SimFailureClass.INFRA_OUTAGE][ValueTier.MID][action]
        p_timeout = GROUND_TRUTH_P[SimFailureClass.TIMEOUT_TRANSIENT][ValueTier.MID][action]
        assert abs(p_infra - p_timeout) > 0.50, "Prerequisite: ground truth must differ"

        event = PaymentFailureEvent(
            payment_id="pay_rate", merchant_id="m",
            amount=1500.0, currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out", attempt_number=1,
        )
        s = _strategy(event.event_id, "TIMEOUT_TRANSIENT+MID", action)
        g = _approved_guardrail(event.event_id, s.decision_id)

        svc_a = ExecutionService(outcome_engine=OutcomeEngine(GroundTruth(), seed=1234))
        rate_a = sum(
            int(svc_a.execute_sync(event=event, decision=s, guardrail=g,
                                   true_failure_class=SimFailureClass.INFRA_OUTAGE
                                   ).status == OutcomeStatus.success)
            for _ in range(N)
        ) / N

        svc_b = ExecutionService(outcome_engine=OutcomeEngine(GroundTruth(), seed=1234))
        rate_b = sum(
            int(svc_b.execute_sync(event=event, decision=s, guardrail=g,
                                   true_failure_class=SimFailureClass.TIMEOUT_TRANSIENT
                                   ).status == OutcomeStatus.success)
            for _ in range(N)
        ) / N

        assert abs(rate_b - rate_a) > 0.30, (
            f"True class must control outcome probability. "
            f"INFRA_OUTAGE={rate_a:.3f}, TIMEOUT_TRANSIENT={rate_b:.3f}. "
            f"Similar rates indicate C-1 regression."
        )

    def test_correct_diagnosis_rate_matches_ground_truth(self):
        """When diagnosis is correct the observed rate must match the known ground-truth P."""
        N = 500
        action = SimAction.DELAYED_RETRY
        true_class = SimFailureClass.INFRA_OUTAGE
        gt_prob = GROUND_TRUTH_P[true_class][ValueTier.MID][action]  # ~0.72

        event = PaymentFailureEvent(
            payment_id="pay_correct", merchant_id="m",
            amount=1500.0, currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="psp unavailable", attempt_number=1,
        )
        s = _strategy(event.event_id, f"{true_class.value}+MID", action)
        g = _approved_guardrail(event.event_id, s.decision_id)
        svc = ExecutionService(outcome_engine=OutcomeEngine(GroundTruth(), seed=9999))

        observed = sum(
            int(svc.execute_sync(event=event, decision=s, guardrail=g,
                                  true_failure_class=true_class
                                  ).status == OutcomeStatus.success)
            for _ in range(N)
        ) / N
        margin = 3 * (gt_prob * (1 - gt_prob) / N) ** 0.5
        assert abs(observed - gt_prob) <= margin, (
            f"Observed {observed:.3f} not within 3sigma of gt {gt_prob:.3f} (margin {margin:.3f})"
        )


class TestPaymentFailureEventFallback:
    """Raw PaymentFailureEvent (no hidden class) must degrade gracefully."""

    def test_plain_pfe_processes_end_to_end(self):
        pipeline = RevPilotPipeline(seed=42)
        event = PaymentFailureEvent(
            payment_id="pay_pfe", merchant_id="m",
            amount=2000.0, currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out", attempt_number=1,
        )
        result = pipeline.process_event(event)
        assert result.stage_reached == "completed"
        assert result.diagnosis is not None
        assert result.outcome is not None

    def test_none_true_class_uses_unknown_fallback(self):
        """true_failure_class=None -> UNKNOWN sent to OutcomeEngine, no crash."""
        engine = OutcomeEngine(ground_truth=GroundTruth(), seed=42)
        captured = []
        orig = engine.simulate_outcome

        def spy(ev, action):
            captured.append(ev)
            return orig(ev, action)

        engine.simulate_outcome = spy
        svc = ExecutionService(outcome_engine=engine)

        event = PaymentFailureEvent(
            payment_id="pay_none", merchant_id="m",
            amount=1000.0, currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="timeout", attempt_number=1,
        )
        s = _strategy(event.event_id, "UNKNOWN+MID", SimAction.DELAYED_RETRY)
        g = _approved_guardrail(event.event_id, s.decision_id)

        result = svc.execute_sync(event=event, decision=s, guardrail=g, true_failure_class=None)
        assert result is not None
        assert result.status in {OutcomeStatus.success, OutcomeStatus.failure}
        assert len(captured) == 1
        assert captured[0].normalised_failure_class == SimFailureClass.UNKNOWN


class TestFullPipelineRegression:
    """Existing pipeline behaviour preserved after the fix."""

    def test_500_event_batch_completes(self):
        pipeline = RevPilotPipeline(seed=20260821)
        gen = EventGenerator(seed=20260821, n=500)
        events = gen.generate(n=500, seed=20260821)
        summary = pipeline.process_batch(events, batch_id="barrier_regression_500")
        assert summary.total_events == 500
        assert summary.processed_events == 500
        assert 0.0 <= summary.recovery_rate <= 1.0

    def test_all_failure_classes_complete_without_crash(self):
        """Each of the 9 failure classes processes cleanly after the fix."""
        pipeline = RevPilotPipeline(seed=7)
        cases = [
            (SimFailureClass.TIMEOUT_TRANSIENT,    "bank response timed out"),
            (SimFailureClass.HARD_FUNDS_ISSUE,     "insufficient funds in customer account"),
            (SimFailureClass.ISSUER_DECLINE,       "issuer declined transaction"),
            (SimFailureClass.AUTH_BLOCKED,         "3DS authentication failed OTP not entered"),
            (SimFailureClass.INFRA_OUTAGE,         "psp unavailable gateway down"),
            (SimFailureClass.DUPLICATE,            "duplicate reference order already processed"),
            (SimFailureClass.CUSTOMER_ABANDONMENT, "payment abandoned by user"),
            (SimFailureClass.FRAUD_SUSPECTED,      "fraud detected velocity spike"),
            (SimFailureClass.UNKNOWN,              "0x99_CORRUPT_PAYLOAD"),
        ]
        for true_class, raw_error in cases:
            sim_ev = _make_sim_event(true_class=true_class, raw_error=raw_error)
            result = pipeline.process_event(sim_ev)
            assert result.stage_reached == "completed", (
                f"Pipeline failed for {true_class.value}: {result.error_message}"
            )
            assert result.failure_class is not None
