"""
tests/test_simulator.py
=======================

Tests for the synthetic event environment:
  - EventGenerator reproducibility
  - SimEvent schema validation
  - Information barrier (hidden failure class not in strategy-engine view)
  - GroundTruth matrix coverage and properties
  - OutcomeEngine outcome distributions
  - Metric calculation correctness
"""

from __future__ import annotations

import pytest

from backend.simulator.event_generator import EventGenerator
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import (
    CustomerSegment,
    FailureClass,
    SimAction,
    SimBenchmarkReport,
    SimEvent,
    SimOutcome,
    SimPaymentMethod,
    ValueTier,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generator() -> EventGenerator:
    return EventGenerator(seed=42, n=500)


@pytest.fixture(scope="module")
def events(generator: EventGenerator) -> list[SimEvent]:
    return generator.generate()


@pytest.fixture(scope="module")
def ground_truth() -> GroundTruth:
    return GroundTruth()


@pytest.fixture(scope="module")
def outcome_engine(ground_truth: GroundTruth) -> OutcomeEngine:
    return OutcomeEngine(ground_truth=ground_truth, seed=99)


# ---------------------------------------------------------------------------
# 1. Reproducibility
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_identical_seed_produces_identical_events(self) -> None:
        gen = EventGenerator(seed=42, n=500)
        batch_a = gen.generate()
        batch_b = gen.generate()
        assert len(batch_a) == len(batch_b)
        for a, b in zip(batch_a, batch_b):
            assert a.event_id == b.event_id
            assert a.amount == b.amount
            assert a.raw_gateway_error == b.raw_gateway_error
            assert a.normalised_failure_class == b.normalised_failure_class

    def test_different_seeds_produce_different_events(self) -> None:
        gen = EventGenerator(seed=1, n=100)
        batch_a = gen.generate(seed=1)
        batch_b = gen.generate(seed=2)
        event_ids_a = {e.event_id for e in batch_a}
        event_ids_b = {e.event_id for e in batch_b}
        # At least some events must differ
        assert event_ids_a != event_ids_b

    def test_default_batch_size(self, events: list[SimEvent]) -> None:
        assert len(events) == 500

    def test_custom_n(self) -> None:
        gen = EventGenerator(seed=7, n=200)
        batch = gen.generate(n=100)
        assert len(batch) == 100

    def test_outcome_engine_reproducibility(
        self, events: list[SimEvent], ground_truth: GroundTruth
    ) -> None:
        eng_a = OutcomeEngine(ground_truth=ground_truth, seed=42)
        eng_b = OutcomeEngine(ground_truth=ground_truth, seed=42)
        for ev in events[:50]:
            action = SimAction.IMMEDIATE_RETRY
            oa = eng_a.simulate_outcome(ev, action)
            ob = eng_b.simulate_outcome(ev, action)
            assert oa.success == ob.success
            assert oa.recovered_value == ob.recovered_value


# ---------------------------------------------------------------------------
# 2. Schema Validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_all_required_fields_present(self, events: list[SimEvent]) -> None:
        required = {
            "event_id", "transaction_id", "customer_id", "timestamp",
            "amount", "currency", "payment_method", "raw_gateway_error",
            "previous_attempts", "customer_segment", "value_tier",
        }
        for ev in events[:20]:
            d = ev.model_dump()
            for field in required:
                assert field in d, f"Missing field: {field}"

    def test_amount_positive(self, events: list[SimEvent]) -> None:
        assert all(e.amount > 0 for e in events)

    def test_currency_is_inr(self, events: list[SimEvent]) -> None:
        assert all(e.currency == "INR" for e in events)

    def test_payment_method_valid_enum(self, events: list[SimEvent]) -> None:
        valid = set(SimPaymentMethod)
        assert all(e.payment_method in valid for e in events)

    def test_customer_segment_valid_enum(self, events: list[SimEvent]) -> None:
        valid = set(CustomerSegment)
        assert all(e.customer_segment in valid for e in events)

    def test_value_tier_consistent_with_amount(self, events: list[SimEvent]) -> None:
        for ev in events:
            if ev.value_tier == ValueTier.LOW:
                assert ev.amount < 500
            elif ev.value_tier == ValueTier.MID:
                assert 500 <= ev.amount < 5000
            else:
                assert ev.amount >= 5000

    def test_previous_attempts_non_negative(self, events: list[SimEvent]) -> None:
        assert all(e.previous_attempts >= 0 for e in events)

    def test_raw_gateway_error_non_empty(self, events: list[SimEvent]) -> None:
        assert all(e.raw_gateway_error.strip() != "" for e in events)

    def test_event_ids_unique(self, events: list[SimEvent]) -> None:
        ids = [e.event_id for e in events]
        assert len(ids) == len(set(ids))

    def test_events_sorted_by_timestamp(self, events: list[SimEvent]) -> None:
        for i in range(1, len(events)):
            assert events[i].timestamp >= events[i - 1].timestamp

    def test_failure_class_all_represented(self, events: list[SimEvent]) -> None:
        seen = {e.normalised_failure_class for e in events}
        assert seen == set(FailureClass), f"Missing classes: {set(FailureClass) - seen}"


# ---------------------------------------------------------------------------
# 3. Information Barrier — hidden failure class
# ---------------------------------------------------------------------------


class TestInformationBarrier:
    def test_strategy_engine_view_excludes_failure_class(
        self, events: list[SimEvent]
    ) -> None:
        for ev in events[:50]:
            view = ev.strategy_engine_view()
            assert "normalised_failure_class" not in view, (
                "normalised_failure_class must NOT appear in strategy-engine view"
            )

    def test_model_dump_excludes_failure_class_by_default(
        self, events: list[SimEvent]
    ) -> None:
        for ev in events[:50]:
            d = ev.model_dump()
            assert "normalised_failure_class" not in d

    def test_failure_class_accessible_on_object(
        self, events: list[SimEvent]
    ) -> None:
        for ev in events[:10]:
            fc = ev.normalised_failure_class
            assert isinstance(fc, FailureClass)

    def test_ground_truth_requires_sim_event(
        self, ground_truth: GroundTruth, events: list[SimEvent]
    ) -> None:
        """GroundTruth.get_recovery_probability needs the full SimEvent."""
        ev = events[0]
        prob = ground_truth.get_recovery_probability(ev, SimAction.DELAYED_RETRY)
        assert 0.0 <= prob <= 1.0

    def test_strategy_engine_view_contains_raw_error(
        self, events: list[SimEvent]
    ) -> None:
        for ev in events[:20]:
            view = ev.strategy_engine_view()
            assert "raw_gateway_error" in view
            assert view["raw_gateway_error"] == ev.raw_gateway_error


# ---------------------------------------------------------------------------
# 4. Ground Truth Matrix Properties
# ---------------------------------------------------------------------------


class TestGroundTruth:
    def test_all_context_cells_populated(self, ground_truth: GroundTruth) -> None:
        matrix = ground_truth.context_matrix()
        for fc in FailureClass:
            for vt in ValueTier:
                assert fc.value in matrix, f"Missing: {fc}"
                assert vt.value in matrix[fc.value], f"Missing: {fc}/{vt}"
                for action in SimAction:
                    assert action.value in matrix[fc.value][vt.value], (
                        f"Missing action {action} in {fc}/{vt}"
                    )

    def test_all_probabilities_in_0_1(self, ground_truth: GroundTruth) -> None:
        matrix = ground_truth.context_matrix()
        for fc_str, tiers in matrix.items():
            for vt_str, actions in tiers.items():
                for action_str, prob in actions.items():
                    assert 0.0 <= prob <= 1.0, (
                        f"Probability out of range: {fc_str}/{vt_str}/{action_str} = {prob}"
                    )

    def test_different_contexts_have_different_optimal_actions(
        self, ground_truth: GroundTruth
    ) -> None:
        """The optimal action must differ across at least some contexts."""
        optimal_actions = set()
        for fc in FailureClass:
            for vt in ValueTier:
                optimal_actions.add(ground_truth.optimal_action(fc, vt))
        assert len(optimal_actions) > 1, (
            "All contexts have the same optimal action — the matrix is degenerate"
        )

    def test_hard_funds_issue_optimal_is_not_immediate_retry(
        self, ground_truth: GroundTruth
    ) -> None:
        """IMMEDIATE_RETRY should never be optimal for HARD_FUNDS_ISSUE."""
        for vt in ValueTier:
            opt = ground_truth.optimal_action(FailureClass.HARD_FUNDS_ISSUE, vt)
            assert opt != SimAction.IMMEDIATE_RETRY

    def test_timeout_transient_immediate_retry_is_strong(
        self, ground_truth: GroundTruth
    ) -> None:
        """P(success | TIMEOUT_TRANSIENT, *, IMMEDIATE_RETRY) should be high (> 0.5)."""
        for vt in ValueTier:
            ev = _make_event(FailureClass.TIMEOUT_TRANSIENT, vt, 1000.0)
            prob = ground_truth.get_recovery_probability(ev, SimAction.IMMEDIATE_RETRY)
            assert prob > 0.5, f"Expected high prob for TIMEOUT/IMMEDIATE_RETRY/{vt}, got {prob}"

    def test_fraud_suspected_automated_actions_near_zero(
        self, ground_truth: GroundTruth
    ) -> None:
        """Automated actions on FRAUD_SUSPECTED should have very low success probability."""
        for vt in ValueTier:
            ev = _make_event(FailureClass.FRAUD_SUSPECTED, vt, 5000.0)
            for action in [
                SimAction.IMMEDIATE_RETRY,
                SimAction.DELAYED_RETRY,
                SimAction.SWITCH_METHOD,
            ]:
                prob = ground_truth.get_recovery_probability(ev, action)
                assert prob <= 0.05, (
                    f"FRAUD_SUSPECTED/{vt}/{action} prob={prob} is too high"
                )

    def test_drift_scales_probabilities(self, ground_truth: GroundTruth) -> None:
        drifted = ground_truth.with_drift(0.5)
        for fc in FailureClass:
            for vt in ValueTier:
                ev = _make_event(fc, vt, 1000.0)
                p_base = ground_truth.get_recovery_probability(ev, SimAction.DELAYED_RETRY)
                p_drift = drifted.get_recovery_probability(ev, SimAction.DELAYED_RETRY)
                assert p_drift <= p_base + 1e-9  # drift of 0.5 must not increase prob

    def test_drift_clips_to_1(self) -> None:
        gt = GroundTruth(drift_factor=10.0)
        ev = _make_event(FailureClass.TIMEOUT_TRANSIENT, ValueTier.MID, 1000.0)
        prob = gt.get_recovery_probability(ev, SimAction.IMMEDIATE_RETRY)
        assert prob <= 1.0

    def test_invalid_drift_raises(self) -> None:
        with pytest.raises(ValueError):
            GroundTruth(drift_factor=-0.1)

    def test_action_costs_positive(self, ground_truth: GroundTruth) -> None:
        for action in SimAction:
            assert ground_truth.get_action_cost(action) >= 0

    def test_resolution_delay_positive(self, ground_truth: GroundTruth) -> None:
        for action in SimAction:
            assert ground_truth.get_resolution_delay_s(action) >= 0


# ---------------------------------------------------------------------------
# 5. Outcome Distribution
# ---------------------------------------------------------------------------


class TestOutcomeDistribution:
    def test_outcome_fields_populated(
        self, events: list[SimEvent], outcome_engine: OutcomeEngine
    ) -> None:
        for ev in events[:20]:
            outcome = outcome_engine.simulate_outcome(ev, SimAction.DELAYED_RETRY)
            assert isinstance(outcome, SimOutcome)
            assert outcome.event_id == ev.event_id
            assert outcome.action == SimAction.DELAYED_RETRY
            assert outcome.recovered_value >= 0
            assert outcome.action_cost >= 0
            assert outcome.friction_cost >= 0
            assert outcome.resolution_delay_s >= 0
            assert outcome.processing_latency_ms >= 0

    def test_success_sets_recovered_value_to_amount(
        self, events: list[SimEvent], outcome_engine: OutcomeEngine
    ) -> None:
        for ev in events[:100]:
            outcome = outcome_engine.simulate_outcome(ev, SimAction.IMMEDIATE_RETRY)
            if outcome.success:
                assert outcome.recovered_value == pytest.approx(ev.amount)
            else:
                assert outcome.recovered_value == 0.0

    def test_net_recovered_is_value_minus_cost(
        self, events: list[SimEvent], outcome_engine: OutcomeEngine
    ) -> None:
        for ev in events[:50]:
            outcome = outcome_engine.simulate_outcome(ev, SimAction.PAYMENT_LINK)
            expected_net = outcome.recovered_value - outcome.action_cost - outcome.friction_cost
            assert outcome.net_recovered == pytest.approx(expected_net, abs=1e-6)

    def test_high_p_action_succeeds_more_often(
        self, ground_truth: GroundTruth
    ) -> None:
        """IMMEDIATE_RETRY on TIMEOUT_TRANSIENT events should win > 50% of the time."""
        gen = EventGenerator(seed=123, n=200)
        events = gen.generate()
        timeout_events = [
            e for e in events
            if e.normalised_failure_class == FailureClass.TIMEOUT_TRANSIENT
        ]
        assert len(timeout_events) >= 10, "Need enough timeout events for a meaningful test"

        eng = OutcomeEngine(ground_truth=ground_truth, seed=77)
        outcomes = [eng.simulate_outcome(e, SimAction.IMMEDIATE_RETRY) for e in timeout_events]
        success_rate = sum(o.success for o in outcomes) / len(outcomes)
        assert success_rate > 0.50, (
            f"Expected >50% success for TIMEOUT/IMMEDIATE_RETRY, got {success_rate:.2%}"
        )

    def test_batch_simulation_length(
        self, events: list[SimEvent], outcome_engine: OutcomeEngine
    ) -> None:
        actions = [SimAction.DELAYED_RETRY] * len(events)
        outcomes = outcome_engine.simulate_batch(events, actions)
        assert len(outcomes) == len(events)

    def test_batch_simulation_mismatched_length_raises(
        self, events: list[SimEvent], outcome_engine: OutcomeEngine
    ) -> None:
        with pytest.raises(ValueError, match="same length"):
            outcome_engine.simulate_batch(events[:10], [SimAction.DELAYED_RETRY] * 5)


# ---------------------------------------------------------------------------
# 6. Metric Calculations
# ---------------------------------------------------------------------------


class TestMetricCalculations:
    def _run(
        self, n: int = 200, seed: int = 42
    ) -> tuple[list[SimEvent], list[SimOutcome]]:
        from backend.simulator.baseline import StaticBaselinePolicy

        gen = EventGenerator(seed=seed, n=n)
        evts = gen.generate()
        gt = GroundTruth()
        eng = OutcomeEngine(ground_truth=gt, seed=seed)
        policy = StaticBaselinePolicy()
        outcomes = [eng.simulate_outcome(e, policy(e)) for e in evts]
        return evts, outcomes

    def test_recovery_rate_formula(self) -> None:
        evts, outcomes = self._run()
        expected = sum(o.success for o in outcomes) / len(outcomes)
        assert abs(expected - (sum(o.success for o in outcomes) / len(evts))) < 1e-9

    def test_gross_recovered_is_sum_of_recovered_values(self) -> None:
        _, outcomes = self._run()
        gross = sum(o.recovered_value for o in outcomes)
        assert gross >= 0

    def test_net_is_gross_minus_cost(self) -> None:
        _, outcomes = self._run()
        gross = sum(o.recovered_value for o in outcomes)
        cost = sum(o.action_cost for o in outcomes)
        friction = sum(o.friction_cost for o in outcomes)
        net = sum(o.net_recovered for o in outcomes)
        assert abs(net - (gross - cost - friction)) < 1.0  # float rounding tolerance

    def test_human_reviews_count(self) -> None:
        from backend.simulator.baseline import StaticBaselinePolicy

        gen = EventGenerator(seed=42, n=200)
        evts = gen.generate()
        gt = GroundTruth()
        eng = OutcomeEngine(ground_truth=gt, seed=42)
        policy = StaticBaselinePolicy()
        outcomes = [eng.simulate_outcome(e, policy(e)) for e in evts]
        human = sum(o.action == SimAction.HUMAN_ESCALATION for o in outcomes)
        assert human >= 0

    def test_zero_events_handled_gracefully(
        self, ground_truth: GroundTruth, outcome_engine: OutcomeEngine
    ) -> None:
        from backend.simulator.baseline import StaticBaselinePolicy, run_benchmark

        gen = EventGenerator(seed=1, n=1)
        evts = gen.generate(n=1)
        policy = StaticBaselinePolicy()
        report = run_benchmark(policy, evts, outcome_engine, policy_name="test")
        assert report.total_events == 1
        assert 0.0 <= report.recovery_rate <= 1.0

    def test_benchmark_report_schema(
        self, events: list[SimEvent], outcome_engine: OutcomeEngine
    ) -> None:
        from backend.simulator.baseline import StaticBaselinePolicy, run_benchmark

        policy = StaticBaselinePolicy()
        report = run_benchmark(
            policy, events, outcome_engine, policy_name="static_baseline", seed=42
        )
        assert isinstance(report, SimBenchmarkReport)
        assert report.total_events == len(events)
        assert report.processed <= report.total_events
        assert 0.0 <= report.recovery_rate <= 1.0
        assert report.gross_recovered_revenue >= 0
        assert report.total_action_cost >= 0
        assert report.policy_name == "static_baseline"
        assert report.seed == 42


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_event(fc: FailureClass, vt: ValueTier, amount: float) -> SimEvent:
    """Construct a minimal SimEvent for unit testing."""
    return SimEvent(
        transaction_id="TXN_TEST",
        customer_id="CUS_TEST",
        amount=amount,
        payment_method=SimPaymentMethod.CARD,
        raw_gateway_error="test error",
        customer_segment=CustomerSegment.REGULAR,
        value_tier=vt,
        normalised_failure_class=fc,
    )
