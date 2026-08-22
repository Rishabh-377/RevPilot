"""
tests/test_non_stationary.py
============================

Automated tests verifying non-stationary simulator capabilities and
autonomous Strategy Engine adaptation under hidden environmental shifts:
  1. Simulator Ground Truth overrides and contextual degradation
  2. Information barrier enforcement during regime changes
  3. Posterior mean adaptation in response to observed binary outcomes
  4. Autonomous policy shifts without code changes
  5. Reflection agent detection and explanation of policy transitions
  6. Contextual generalization across multiple failure classes
"""

from __future__ import annotations

from backend.models.schemas import FailureClass, ValueTier
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.non_stationary import (
    EnvironmentShiftConfig,
    NonStationaryBenchmarkReport,
    run_non_stationary_experiment,
)
from backend.simulator.types import CustomerSegment, SimAction, SimEvent, SimPaymentMethod


class TestNonStationarySimulator:
    def test_simulator_supports_ground_truth_overrides(self) -> None:
        gt_base = GroundTruth()
        ev = SimEvent(
            event_id="test_ev",
            transaction_id="txn_01",
            customer_id="cust_01",
            amount=2000.0,
            currency="INR",
            payment_method=SimPaymentMethod.UPI,
            customer_segment=CustomerSegment.REGULAR,
            raw_gateway_error="bank timeout",
            previous_attempts=0,
            value_tier=ValueTier.MID,
            normalised_failure_class=FailureClass.TIMEOUT_TRANSIENT,
        )

        p_base = gt_base.get_recovery_probability(ev, SimAction.IMMEDIATE_RETRY)
        assert p_base == 0.80

        gt_shifted = gt_base.with_override(
            failure_class=FailureClass.TIMEOUT_TRANSIENT,
            value_tier=ValueTier.MID,
            action=SimAction.IMMEDIATE_RETRY,
            probability=0.10,
        )
        p_shifted = gt_shifted.get_recovery_probability(ev, SimAction.IMMEDIATE_RETRY)
        assert p_shifted == 0.10

        # Unchanged actions remain unaffected
        p_delayed = gt_shifted.get_recovery_probability(ev, SimAction.DELAYED_RETRY)
        assert p_delayed == 0.65

    def test_information_barrier_in_non_stationary_mode(self) -> None:
        ev = SimEvent(
            event_id="test_barrier_ev",
            transaction_id="txn_02",
            customer_id="cust_02",
            amount=1500.0,
            currency="INR",
            payment_method=SimPaymentMethod.UPI,
            customer_segment=CustomerSegment.REGULAR,
            raw_gateway_error="bank timeout",
            previous_attempts=0,
            value_tier=ValueTier.MID,
            normalised_failure_class=FailureClass.TIMEOUT_TRANSIENT,
        )
        se_view = ev.strategy_engine_view()
        assert "normalised_failure_class" not in se_view
        assert "ground_truth" not in se_view


class TestNonStationaryPolicyAdaptation:
    def test_posterior_estimates_adapt_to_environmental_shift(self) -> None:
        report = run_non_stationary_experiment(
            phase_a_records=120,
            phase_b_records=120,
            seed=20260821,
        )
        assert isinstance(report, NonStationaryBenchmarkReport)
        assert report.adaptation_verified is True
        assert report.posterior_delta < 0  # Posterior for degraded action decreased

    def test_policy_adapts_without_code_changes(self) -> None:
        report = run_non_stationary_experiment(
            phase_a_records=150,
            phase_b_records=150,
            seed=20260821,
        )
        # Phase A: IMMEDIATE_RETRY is preferred
        assert report.policy_before.preferred_action == SimAction.IMMEDIATE_RETRY.value
        assert report.policy_before.posterior_mean > 0.70

        # Phase B: Switches autonomously to DELAYED_RETRY
        assert report.policy_after.preferred_action == SimAction.DELAYED_RETRY.value
        assert report.policy_shifted is True

    def test_reflection_agent_explains_non_stationary_shift(self) -> None:
        report = run_non_stationary_experiment(
            phase_a_records=100,
            phase_b_records=100,
            seed=20260821,
        )
        assert "TIMEOUT_TRANSIENT+MID" in report.learning_statement
        assert "IMMEDIATE_RETRY" in report.learning_statement
        assert "DELAYED_RETRY" in report.learning_statement

    def test_contextual_generalization_on_auth_blocked(self) -> None:
        shift = EnvironmentShiftConfig(
            target_failure_class=FailureClass.AUTH_BLOCKED,
            target_value_tier=ValueTier.HIGH,
            action_degraded=SimAction.IMMEDIATE_RETRY,
            prob_before=0.52,
            prob_after=0.08,
            description="Issuer 3DS service degradation.",
        )
        report = run_non_stationary_experiment(
            phase_a_records=120,
            phase_b_records=120,
            seed=20260821,
            shift_config=shift,
        )
        assert report.target_context == "AUTH_BLOCKED+HIGH"
        assert report.adaptation_verified is True
        assert report.posterior_delta < 0
