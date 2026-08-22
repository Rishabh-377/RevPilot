"""
tests/test_reflection.py
========================

Unit tests for ReflectionAgent & StatisticalUpdater:
  1. Batch metric calculations (successes, failures, recovery rate, revenue, cost, net value)
  2. Tracking of posterior mean shifts (pre vs post update)
  3. Policy change detection (shifts in highest EV optimal action)
  4. Learning statement generation with mathematical explanation
  5. Idempotency & duplicate outcome protection (cannot update model twice)
  6. Persistence & roundtrip loading of reflection records
  7. Multi-context batch analysis
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest

from backend.agents.reflection import (
    OutcomeObservation,
    ReflectionAgent,
    StatisticalUpdater,
)
from backend.bandit.thompson import ThompsonSamplingBandit
from backend.models.schemas import BatchReflectionRecord, ContextReflection
from backend.simulator.types import SimAction


@pytest.fixture
def fresh_bandit() -> ThompsonSamplingBandit:
    return ThompsonSamplingBandit(seed=42)


@pytest.fixture
def reflection_agent(fresh_bandit: ThompsonSamplingBandit) -> ReflectionAgent:
    return ReflectionAgent(bandit=fresh_bandit, persistence_path=None)


class TestBatchMetricCalculations:
    def test_batch_metrics_accuracy(self, reflection_agent: ReflectionAgent) -> None:
        context = "TIMEOUT_TRANSIENT+HIGH"
        observations = [
            OutcomeObservation(
                event_id=f"evt_{i}",
                context=context,
                action=SimAction.PAYMENT_LINK.value,
                success=(i < 6),  # 6 successes, 4 failures out of 10
                recovered_value=5000.0 if i < 6 else 0.0,
                cost=10.0,
            )
            for i in range(10)
        ]

        record = reflection_agent.reflect_batch(observations, batch_id="batch_test_01")
        assert isinstance(record, BatchReflectionRecord)
        assert record.total_events == 10
        assert record.total_successes == 6
        assert record.total_failures == 4
        assert record.overall_recovery_rate == 0.60
        assert record.total_recovered_revenue == 30000.0  # 6 * 5000
        assert record.total_cost == 100.0                # 10 * 10
        assert record.total_net_value == 29900.0          # 30000 - 100


class TestPosteriorMeanShiftTracking:
    def test_posterior_mean_shift_recorded(self, reflection_agent: ReflectionAgent) -> None:
        context = "INFRA_OUTAGE+MID"
        action = SimAction.DELAYED_RETRY.value

        arm_before = reflection_agent.bandit.state.get_arm(context, action)
        mean_before = arm_before.posterior_mean

        observations = [
            OutcomeObservation(
                event_id=f"evt_infra_{i}",
                context=context,
                action=action,
                success=True,
                recovered_value=2000.0,
                cost=5.0,
            )
            for i in range(15)
        ]

        record = reflection_agent.reflect_batch(observations, batch_id="batch_infra")
        ctx_ref = record.context_reflections[0]

        delta = ctx_ref.changes_in_posterior_mean[action]
        post_mean = ctx_ref.new_statistics[action]["posterior_mean"]
        prev_mean = ctx_ref.previous_statistics[action]["posterior_mean"]

        assert delta > 0.0
        assert post_mean == pytest.approx(prev_mean + delta, abs=1e-4)


class TestPolicyChangeDetection:
    def test_policy_change_detected_and_explained(self, reflection_agent: ReflectionAgent) -> None:
        context = "TIMEOUT_TRANSIENT+HIGH"
        # Initially IMMEDIATE_RETRY is the winner for TIMEOUT_TRANSIENT
        # We send massive success for PAYMENT_LINK and failures for IMMEDIATE_RETRY
        observations = []
        for i in range(25):
            observations.append(
                OutcomeObservation(
                    event_id=f"evt_paylink_{i}",
                    context=context,
                    action=SimAction.PAYMENT_LINK.value,
                    success=True,
                    recovered_value=10000.0,
                    cost=8.0,
                )
            )
        for i in range(25):
            observations.append(
                OutcomeObservation(
                    event_id=f"evt_imm_{i}",
                    context=context,
                    action=SimAction.IMMEDIATE_RETRY.value,
                    success=False,
                    recovered_value=0.0,
                    cost=3.0,
                )
            )

        record = reflection_agent.reflect_batch(observations, batch_id="batch_shift")
        ctx_ref = record.context_reflections[0]

        assert ctx_ref.policy_changed is True
        assert ctx_ref.policy_after == SimAction.PAYMENT_LINK.value
        assert "making it the highest-EV recovery action" in ctx_ref.learning_statement


class TestLearningStatementFormat:
    def test_learning_statement_contains_expected_tokens(self, reflection_agent: ReflectionAgent) -> None:
        context = "HARD_FUNDS_ISSUE+LOW"
        observations = [
            OutcomeObservation(
                event_id="evt_funds_01",
                context=context,
                action=SimAction.PAYMENT_LINK.value,
                success=True,
                recovered_value=300.0,
                cost=5.0,
            )
        ]
        record = reflection_agent.reflect_batch(observations, batch_id="batch_stmt")
        ctx_ref = record.context_reflections[0]

        statement = ctx_ref.learning_statement
        assert "Across 1 observations in HARD_FUNDS_ISSUE/LOW" in statement
        assert "PAYMENT_LINK" in statement
        assert "estimated success" in statement


class TestIdempotencyAndDuplicateProtection:
    def test_duplicate_events_ignored_by_updater(self, fresh_bandit: ThompsonSamplingBandit) -> None:
        updater = StatisticalUpdater(fresh_bandit)
        context = "TIMEOUT_TRANSIENT+MID"
        action = SimAction.IMMEDIATE_RETRY.value

        arm = fresh_bandit.state.get_arm(context, action)
        alpha_init = arm.alpha
        beta_init = arm.beta

        obs1 = OutcomeObservation(event_id="evt_dup_100", context=context, action=action, success=True)
        obs2 = OutcomeObservation(event_id="evt_dup_100", context=context, action=action, success=True)  # Duplicate!

        _, _, applied = updater.process_observations([obs1, obs2])

        assert len(applied) == 1
        assert arm.alpha == alpha_init + 1.0
        assert arm.attempt_count == 1

        # Process same ID again in a second call
        _, _, applied2 = updater.process_observations([obs1])
        assert len(applied2) == 0
        assert arm.alpha == alpha_init + 1.0  # Still unchanged


class TestReflectionPersistence:
    def test_save_and_load_history(self, fresh_bandit: ThompsonSamplingBandit) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "reflections.json"
            agent = ReflectionAgent(bandit=fresh_bandit, persistence_path=file_path)

            obs = [
                OutcomeObservation(
                    event_id="evt_persist_01",
                    context="AUTH_BLOCKED+MID",
                    action=SimAction.IMMEDIATE_RETRY.value,
                    success=True,
                    recovered_value=1200.0,
                    cost=3.0,
                )
            ]

            record = agent.reflect_batch(obs, batch_id="batch_persist_test")
            assert file_path.exists()

            # Load via a new agent instance
            loaded_agent = ReflectionAgent(bandit=fresh_bandit, persistence_path=file_path)
            history = loaded_agent.load_history()

            assert len(history) == 1
            assert history[0].batch_id == "batch_persist_test"
            assert history[0].total_recovered_revenue == 1200.0
            assert len(history[0].context_reflections) == 1
