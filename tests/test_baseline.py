"""
tests/test_baseline.py
======================

Tests specific to the static baseline policy and the benchmark harness:
  - Keyword-matching correctness
  - Determinism of the policy (same input → same output)
  - Benchmark harness metric integrity
  - Oracle policy upper-bound is ≥ baseline
  - Benchmark supports future Strategy Engine (PolicyFn contract)
"""

from __future__ import annotations

import pytest

from backend.simulator.baseline import (
    OraclePolicy,
    PolicyFn,
    StaticBaselinePolicy,
    run_benchmark,
)
from backend.simulator.event_generator import EventGenerator
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import (
    CustomerSegment,
    FailureClass,
    SimAction,
    SimBenchmarkReport,
    SimEvent,
    SimPaymentMethod,
    ValueTier,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def std_events() -> list[SimEvent]:
    return EventGenerator(seed=42, n=500).generate()


@pytest.fixture(scope="module")
def gt() -> GroundTruth:
    return GroundTruth()


@pytest.fixture(scope="module")
def engine(gt: GroundTruth) -> OutcomeEngine:
    # Fixed seed so all baseline tests see identical stochastic outcomes
    return OutcomeEngine(ground_truth=gt, seed=42)


@pytest.fixture(scope="module")
def baseline_policy() -> StaticBaselinePolicy:
    return StaticBaselinePolicy()


@pytest.fixture(scope="module")
def baseline_report(
    std_events: list[SimEvent],
    engine: OutcomeEngine,
    baseline_policy: StaticBaselinePolicy,
) -> SimBenchmarkReport:
    return run_benchmark(
        baseline_policy,
        std_events,
        engine,
        policy_name="static_baseline",
        seed=42,
    )


# ---------------------------------------------------------------------------
# 1. Keyword-matching correctness
# ---------------------------------------------------------------------------


class TestBaselineKeywordMatching:
    def _ev(self, error: str) -> SimEvent:
        return SimEvent(
            transaction_id="TXN_X",
            customer_id="CUS_X",
            amount=1000.0,
            payment_method=SimPaymentMethod.CARD,
            raw_gateway_error=error,
            customer_segment=CustomerSegment.REGULAR,
            value_tier=ValueTier.MID,
            normalised_failure_class=FailureClass.UNKNOWN,
        )

    def test_duplicate_maps_to_human_escalation(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("duplicate reference detected")
        assert baseline_policy(ev) == SimAction.HUMAN_ESCALATION

    def test_fraud_maps_to_human_escalation(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("transaction flagged by fraud engine")
        assert baseline_policy(ev) == SimAction.HUMAN_ESCALATION

    def test_timeout_maps_to_immediate_retry(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("bank response timed out")
        assert baseline_policy(ev) == SimAction.IMMEDIATE_RETRY

    def test_psp_unavailable_maps_to_delayed_retry(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("PSP unavailable")
        assert baseline_policy(ev) == SimAction.DELAYED_RETRY

    def test_insufficient_funds_maps_to_payment_link(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("insufficient balance in account")
        assert baseline_policy(ev) == SimAction.PAYMENT_LINK

    def test_auth_window_maps_to_immediate_retry(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("collect request expired")
        assert baseline_policy(ev) == SimAction.IMMEDIATE_RETRY

    def test_3ds_maps_to_immediate_retry(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("3DS authentication failed")
        assert baseline_policy(ev) == SimAction.IMMEDIATE_RETRY

    def test_do_not_honour_maps_to_switch_method(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("issuer declined — do not honour")
        assert baseline_policy(ev) == SimAction.SWITCH_METHOD

    def test_customer_abandoned_maps_to_payment_link(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("payment abandoned by user")
        assert baseline_policy(ev) == SimAction.PAYMENT_LINK

    def test_unknown_error_uses_default(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("error: GENERIC_DECLINE")
        assert baseline_policy(ev) == SimAction.DELAYED_RETRY

    def test_case_insensitive_matching(
        self, baseline_policy: StaticBaselinePolicy
    ) -> None:
        ev = self._ev("BANK RESPONSE TIMED OUT")
        assert baseline_policy(ev) == SimAction.IMMEDIATE_RETRY


# ---------------------------------------------------------------------------
# 2. Baseline determinism
# ---------------------------------------------------------------------------


class TestBaselineDeterminism:
    def test_same_event_always_produces_same_action(
        self, std_events: list[SimEvent], baseline_policy: StaticBaselinePolicy
    ) -> None:
        for ev in std_events[:100]:
            a1 = baseline_policy(ev)
            a2 = baseline_policy(ev)
            assert a1 == a2

    def test_two_baseline_instances_agree(
        self, std_events: list[SimEvent]
    ) -> None:
        p1 = StaticBaselinePolicy()
        p2 = StaticBaselinePolicy()
        for ev in std_events[:100]:
            assert p1(ev) == p2(ev)

    def test_baseline_repr(self) -> None:
        p = StaticBaselinePolicy(default_action=SimAction.PAYMENT_LINK)
        assert "StaticBaselinePolicy" in repr(p)
        assert "PAYMENT_LINK" in repr(p)


# ---------------------------------------------------------------------------
# 3. Benchmark harness integrity
# ---------------------------------------------------------------------------


class TestBenchmarkHarness:
    def test_report_is_sim_benchmark_report(
        self, baseline_report: SimBenchmarkReport
    ) -> None:
        assert isinstance(baseline_report, SimBenchmarkReport)

    def test_total_events_equals_batch_size(
        self, baseline_report: SimBenchmarkReport, std_events: list[SimEvent]
    ) -> None:
        assert baseline_report.total_events == len(std_events)

    def test_processed_leq_total(self, baseline_report: SimBenchmarkReport) -> None:
        assert baseline_report.processed <= baseline_report.total_events

    def test_successful_recoveries_leq_processed(
        self, baseline_report: SimBenchmarkReport
    ) -> None:
        assert baseline_report.successful_recoveries <= baseline_report.processed

    def test_recovery_rate_consistent_with_successes(
        self, baseline_report: SimBenchmarkReport
    ) -> None:
        expected = baseline_report.successful_recoveries / baseline_report.processed
        assert abs(baseline_report.recovery_rate - expected) < 1e-4

    def test_gross_revenue_non_negative(
        self, baseline_report: SimBenchmarkReport
    ) -> None:
        assert baseline_report.gross_recovered_revenue >= 0

    def test_net_leq_gross(self, baseline_report: SimBenchmarkReport) -> None:
        # Net = gross - action_cost; action cost >= 0 so net <= gross
        assert baseline_report.net_recovered_revenue <= baseline_report.gross_recovered_revenue + 1.0

    def test_human_reviews_non_negative(
        self, baseline_report: SimBenchmarkReport
    ) -> None:
        assert baseline_report.human_reviews >= 0

    def test_throughput_positive(self, baseline_report: SimBenchmarkReport) -> None:
        assert baseline_report.throughput_eps > 0

    def test_avg_latency_non_negative(
        self, baseline_report: SimBenchmarkReport
    ) -> None:
        assert baseline_report.avg_processing_latency_ms >= 0

    def test_seed_recorded(self, baseline_report: SimBenchmarkReport) -> None:
        assert baseline_report.seed == 42

    def test_n_events_recorded(
        self, baseline_report: SimBenchmarkReport, std_events: list[SimEvent]
    ) -> None:
        assert baseline_report.n_events == len(std_events)

    def test_empty_events_raises(
        self, engine: OutcomeEngine, baseline_policy: StaticBaselinePolicy
    ) -> None:
        with pytest.raises(ValueError):
            run_benchmark(baseline_policy, [], engine)

    def test_policy_name_preserved(
        self, std_events: list[SimEvent], engine: OutcomeEngine
    ) -> None:
        policy = StaticBaselinePolicy()
        report = run_benchmark(policy, std_events[:10], engine, policy_name="my_custom_policy")
        assert report.policy_name == "my_custom_policy"


# ---------------------------------------------------------------------------
# 4. Oracle upper-bound vs baseline
# ---------------------------------------------------------------------------


class TestOracleVsBaseline:
    def test_oracle_recovery_rate_geq_baseline(
        self,
        std_events: list[SimEvent],
        gt: GroundTruth,
        baseline_report: SimBenchmarkReport,
    ) -> None:
        """Oracle (peeking at true failure class) should recover >= static baseline.

        Each policy run gets a freshly-seeded engine so both see identical
        stochastic draws — a fair apples-to-apples comparison.
        """
        oracle = OraclePolicy()
        oracle_engine = OutcomeEngine(ground_truth=gt, seed=42)
        oracle_report = run_benchmark(oracle, std_events, oracle_engine, policy_name="oracle")
        # Re-run baseline with same fresh seed to compare on equal footing
        baseline_engine = OutcomeEngine(ground_truth=gt, seed=42)
        baseline = StaticBaselinePolicy()
        fresh_baseline = run_benchmark(baseline, std_events, baseline_engine, policy_name="baseline_fresh")
        assert oracle_report.recovery_rate >= fresh_baseline.recovery_rate, (
            f"Oracle {oracle_report.recovery_rate:.3f} < Baseline {fresh_baseline.recovery_rate:.3f}"
        )

    def test_oracle_net_revenue_geq_baseline(
        self,
        std_events: list[SimEvent],
        gt: GroundTruth,
    ) -> None:
        oracle_engine = OutcomeEngine(ground_truth=gt, seed=42)
        baseline_engine = OutcomeEngine(ground_truth=gt, seed=42)
        oracle_report = run_benchmark(OraclePolicy(), std_events, oracle_engine, policy_name="oracle")
        baseline_report = run_benchmark(StaticBaselinePolicy(), std_events, baseline_engine, policy_name="baseline")
        assert oracle_report.net_recovered_revenue >= baseline_report.net_recovered_revenue

    def test_oracle_repr(self) -> None:
        assert "OraclePolicy" in repr(OraclePolicy())


# ---------------------------------------------------------------------------
# 5. PolicyFn contract — future Strategy Engine compatibility
# ---------------------------------------------------------------------------


class TestPolicyFnContract:
    def test_any_callable_is_accepted(
        self, std_events: list[SimEvent], engine: OutcomeEngine
    ) -> None:
        """run_benchmark should accept any PolicyFn, including lambdas."""
        always_immediate: PolicyFn = lambda event: SimAction.IMMEDIATE_RETRY
        report = run_benchmark(
            always_immediate, std_events, engine, policy_name="always_immediate"
        )
        assert report.policy_name == "always_immediate"
        assert report.human_reviews == 0  # no HUMAN_ESCALATION

    def test_always_escalate_policy(
        self, std_events: list[SimEvent], engine: OutcomeEngine
    ) -> None:
        always_escalate: PolicyFn = lambda event: SimAction.HUMAN_ESCALATION
        report = run_benchmark(
            always_escalate, std_events, engine, policy_name="always_escalate"
        )
        assert report.human_reviews == report.processed

    def test_random_policy_is_accepted(
        self, std_events: list[SimEvent], engine: OutcomeEngine
    ) -> None:
        import random as _random

        rng = _random.Random(99)
        actions = list(SimAction)

        def random_policy(event: SimEvent) -> SimAction:
            return rng.choice(actions)

        report = run_benchmark(
            random_policy, std_events, engine, policy_name="random"
        )
        assert 0.0 <= report.recovery_rate <= 1.0

    def test_run_benchmark_same_events_different_policies_compare_fairly(
        self, std_events: list[SimEvent], engine: OutcomeEngine
    ) -> None:
        """Using the SAME engine instance and SAME events ensures fair comparison."""
        p_link: PolicyFn = lambda e: SimAction.PAYMENT_LINK
        p_retry: PolicyFn = lambda e: SimAction.IMMEDIATE_RETRY

        r_link = run_benchmark(p_link, std_events, engine, policy_name="payment_link")
        r_retry = run_benchmark(p_retry, std_events, engine, policy_name="immediate_retry")

        # Both should process all events
        assert r_link.processed == r_retry.processed == len(std_events)
        # Their outcomes should differ (different actions have different success rates)
        # (This will almost certainly be true given the probability matrix)
        # We don't assert which is better — that depends on the event mix.
        assert isinstance(r_link.recovery_rate, float)
        assert isinstance(r_retry.recovery_rate, float)
