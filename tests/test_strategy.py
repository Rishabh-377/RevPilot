"""
tests/test_strategy.py
======================

Comprehensive unit tests for the RevPilot Strategy Engine (Segmented Thompson Sampling):
  1. Beta update correctness
  2. EV calculation correctness
  3. Action ranking correctness
  4. Reproducibility with seed
  5. Prior behavior and initialization
  6. Learning after outcomes
  7. Policy adaptation / shift after changing outcomes
  8. Clear separation of sampled probability, posterior mean, and EV
  9. Mathematical reasoning string verification
 10. Time-decay functionality
"""

from __future__ import annotations

import pytest

from backend.bandit.state import ArmState
from backend.bandit.thompson import (
    CANDIDATE_ACTIONS,
    ThompsonSamplingBandit,
)
from backend.models.schemas import FailureClass, StrategyDecision, ValueTier
from backend.simulator.types import SimAction


class TestBetaUpdateCorrectness:
    def test_single_success_update(self) -> None:
        arm = ArmState(alpha=2.0, beta=3.0, prior_alpha=2.0, prior_beta=3.0)
        arm.update(success=True)
        assert arm.alpha == 3.0
        assert arm.beta == 3.0
        assert arm.attempt_count == 1
        assert arm.success_count == 1
        assert arm.failure_count == 0
        assert arm.posterior_mean == 0.50

    def test_single_failure_update(self) -> None:
        arm = ArmState(alpha=2.0, beta=3.0, prior_alpha=2.0, prior_beta=3.0)
        arm.update(success=False)
        assert arm.alpha == 2.0
        assert arm.beta == 4.0
        assert arm.attempt_count == 1
        assert arm.success_count == 0
        assert arm.failure_count == 1
        assert arm.posterior_mean == pytest.approx(2.0 / 6.0)

    def test_multiple_sequential_updates(self) -> None:
        arm = ArmState(alpha=1.0, beta=1.0)
        outcomes = [True, True, False, True, False]
        for out in outcomes:
            arm.update(success=out)
        assert arm.alpha == 4.0  # 1.0 + 3
        assert arm.beta == 3.0   # 1.0 + 2
        assert arm.attempt_count == 5
        assert arm.success_count == 3
        assert arm.failure_count == 2
        assert arm.posterior_mean == pytest.approx(4.0 / 7.0)


class TestEVCalculationCorrectness:
    def test_manual_ev_formula(self) -> None:
        bandit = ThompsonSamplingBandit()
        # Action: IMMEDIATE_RETRY (api_cost=2.0, friction_cost=1.0, time_discount=1.0)
        # Amount = 1000.0, Sampled Prob = 0.80
        # EV = 0.80 * 1000.0 * 1.0 - 2.0 - 1.0 = 800.0 - 3.0 = 797.0
        ev = bandit.compute_ev(
            sampled_prob=0.80,
            transaction_amount=1000.0,
            action=SimAction.IMMEDIATE_RETRY.value,
        )
        assert ev == pytest.approx(797.0, abs=0.01)

    def test_ev_with_discount_and_higher_costs(self) -> None:
        bandit = ThompsonSamplingBandit()
        # Action: PAYMENT_LINK (api_cost=5.0, friction_cost=3.0, time_discount=0.92)
        # Amount = 2000.0, Sampled Prob = 0.50
        # EV = 0.50 * 2000.0 * 0.92 - 5.0 - 3.0 = 920.0 - 8.0 = 912.0
        ev = bandit.compute_ev(
            sampled_prob=0.50,
            transaction_amount=2000.0,
            action=SimAction.PAYMENT_LINK.value,
        )
        assert ev == pytest.approx(912.0, abs=0.01)

    def test_ev_small_transaction(self) -> None:
        bandit = ThompsonSamplingBandit()
        ev = bandit.compute_ev(
            sampled_prob=0.50,
            transaction_amount=5.0,
            action=SimAction.IMMEDIATE_RETRY.value,
        )
        assert ev == pytest.approx(-0.5, abs=0.01)

    def test_ev_large_transaction(self) -> None:
        bandit = ThompsonSamplingBandit()
        ev = bandit.compute_ev(
            sampled_prob=0.50,
            transaction_amount=100000.0,
            action=SimAction.IMMEDIATE_RETRY.value,
        )
        assert ev == pytest.approx(49997.0, abs=0.01)

    def test_ev_different_friction_and_api_costs(self) -> None:
        bandit = ThompsonSamplingBandit()
        ev = bandit.compute_ev(
            sampled_prob=0.80,
            transaction_amount=1000.0,
            action=SimAction.HUMAN_ESCALATION.value,
        )
        assert ev == pytest.approx(570.0, abs=0.01)

    def test_ev_negative_or_zero_ev(self) -> None:
        bandit = ThompsonSamplingBandit()
        ev = bandit.compute_ev(
            sampled_prob=0.0,
            transaction_amount=1000.0,
            action=SimAction.HUMAN_ESCALATION.value,
        )
        assert ev == pytest.approx(-30.0, abs=0.01)


class TestActionRankingAndSelection:
    def test_action_ranking_strictly_descending_by_ev(self) -> None:
        bandit = ThompsonSamplingBandit(seed=42)
        decision = bandit.select_action(
            event_id="evt_01",
            failure_class=FailureClass.TIMEOUT_TRANSIENT,
            value_tier=ValueTier.MID,
            amount=1500.0,
        )
        assert isinstance(decision, StrategyDecision)
        assert decision.selected_action in CANDIDATE_ACTIONS
        # Verify selected_action has the maximum EV
        for action, score in decision.ev_scores.items():
            assert decision.selected_ev >= score


class TestReproducibilityWithSeed:
    def test_identical_seed_produces_identical_decisions(self) -> None:
        b1 = ThompsonSamplingBandit(seed=100)
        b2 = ThompsonSamplingBandit(seed=100)

        d1 = b1.select_action("evt_1", FailureClass.HARD_FUNDS_ISSUE, ValueTier.MID, 2500.0)
        d2 = b2.select_action("evt_1", FailureClass.HARD_FUNDS_ISSUE, ValueTier.MID, 2500.0)

        assert d1.selected_action == d2.selected_action
        assert d1.sampled_probabilities == d2.sampled_probabilities
        assert d1.ev_scores == d2.ev_scores
        assert d1.selected_ev == d2.selected_ev
        assert d1.reasoning == d2.reasoning


class TestPriorBehavior:
    def test_priors_initialized_without_hardcoding(self) -> None:
        bandit = ThompsonSamplingBandit()
        # Verify initial prior means for TIMEOUT_TRANSIENT are 0.50 (uninformative Beta(1,1))
        arm_imm = bandit.state.get_arm("TIMEOUT_TRANSIENT+MID", SimAction.IMMEDIATE_RETRY.value)
        arm_esc = bandit.state.get_arm("TIMEOUT_TRANSIENT+MID", SimAction.HUMAN_ESCALATION.value)
        assert arm_imm.posterior_mean == pytest.approx(0.50, abs=0.01)
        assert arm_esc.posterior_mean == pytest.approx(0.50, abs=0.01)


class TestLearningAndPolicyShift:
    def test_learning_increases_arm_posterior(self) -> None:
        bandit = ThompsonSamplingBandit(seed=42)
        context = "INFRA_OUTAGE+MID"
        action = SimAction.DELAYED_RETRY.value

        arm_before = bandit.state.get_arm(context, action)
        mean_before = arm_before.posterior_mean

        # Observe 10 consecutive successes
        for _ in range(10):
            bandit.observe_outcome(context=context, action=action, success=True)

        arm_after = bandit.state.get_arm(context, action)
        assert arm_after.posterior_mean > mean_before
        assert arm_after.success_count == 10
        assert arm_after.attempt_count == 10

    def test_policy_shift_after_contrary_outcomes(self) -> None:
        """If the default winning arm consistently fails and an alternative succeeds, preference shifts."""
        bandit = ThompsonSamplingBandit(seed=77)
        context = "TIMEOUT_TRANSIENT+MID"
        initial_winner = SimAction.IMMEDIATE_RETRY.value
        alternative = SimAction.PAYMENT_LINK.value

        # Make the initial winner fail 30 times
        for _ in range(30):
            bandit.observe_outcome(context=context, action=initial_winner, success=False)

        # Make alternative succeed 30 times
        for _ in range(30):
            bandit.observe_outcome(context=context, action=alternative, success=True)

        arm_winner = bandit.state.get_arm(context, initial_winner)
        arm_alt = bandit.state.get_arm(context, alternative)

        assert arm_alt.posterior_mean > arm_winner.posterior_mean

        # Run multiple selections to verify alternative dominates decisions
        choices = [
            bandit.select_action(f"evt_{i}", FailureClass.TIMEOUT_TRANSIENT, ValueTier.MID, 2000.0).selected_action
            for i in range(20)
        ]
        assert choices.count(alternative) > choices.count(initial_winner)


class TestSeparationOfConcepts:
    def test_distinct_sampled_prob_posterior_mean_and_ev(self) -> None:
        bandit = ThompsonSamplingBandit(seed=42)
        decision = bandit.select_action("evt_99", FailureClass.HARD_FUNDS_ISSUE, ValueTier.HIGH, 10000.0)

        for action in CANDIDATE_ACTIONS:
            sampled_p = decision.sampled_probabilities[action]
            post_mean = decision.posterior_means[action]
            ev = decision.ev_scores[action]

            # Probabilities are in [0, 1]
            assert 0.0 <= sampled_p <= 1.0
            assert 0.0 <= post_mean <= 1.0

            # EV is in monetary scale (hundreds/thousands of INR)
            assert isinstance(ev, float)

            # Sampled probability should not be identical to EV (they represent different units)
            assert ev != sampled_p


class TestReasoningAndExplainability:
    def test_mathematical_reasoning_string_format(self) -> None:
        bandit = ThompsonSamplingBandit(seed=42)
        decision = bandit.select_action("evt_test", FailureClass.TIMEOUT_TRANSIENT, ValueTier.MID, 1500.0)

        assert decision.selected_action in decision.reasoning
        assert "selected because sampled success probability" in decision.reasoning
        assert "produces EV ₹" in decision.reasoning
        assert "versus ₹" in decision.reasoning


class TestTimeDecay:
    def test_time_decay_pulls_parameters_towards_prior(self) -> None:
        arm = ArmState(alpha=10.0, beta=2.0, prior_alpha=1.0, prior_beta=1.0)
        # Apply decay_factor = 0.5
        arm.update(success=True, decay_factor=0.5)
        # alpha_decayed = (10.0 - 1.0) * 0.5 + 1.0 = 4.5 + 1.0 = 5.5; + 1.0 (success) = 6.5
        # beta_decayed  = (2.0 - 1.0) * 0.5 + 1.0 = 0.5 + 1.0 = 1.5; + 0.0 = 1.5
        assert arm.alpha == pytest.approx(6.5)
        assert arm.beta == pytest.approx(1.5)


class TestPriorIndependence:
    def test_strategy_independent_of_ground_truth(self) -> None:
        # 1. Assert that bandit/thompson.py does not import ground_truth
        import inspect

        from backend.bandit import thompson
        source = inspect.getsource(thompson)
        assert "ground_truth" not in source
        assert "GroundTruth" not in source

        # 2. Prior initialization is independent of hidden reward probabilities
        bandit = ThompsonSamplingBandit()
        for arm_key, arm_dict in bandit.state.arms.items():
            for action, arm in arm_dict.items():
                assert arm.alpha == 1.0
                assert arm.beta == 1.0

        # 3. Verify that only historical observations can modify posterior state
        context = "TIMEOUT_TRANSIENT+MID"
        action = SimAction.IMMEDIATE_RETRY.value
        arm = bandit.state.get_arm(context, action)
        assert arm.alpha == 1.0
        assert arm.beta == 1.0

        bandit.observe_outcome(context, action, success=True)
        assert arm.alpha == 2.0
        assert arm.beta == 1.0
