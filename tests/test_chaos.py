"""
tests/test_chaos.py
===================

Unit tests for all 10 RevPilot Chaos Engineering scenarios:
  1. Duplicate Transaction ID
  2. Malformed Amount
  3. Negative Amount
  4. Unknown Currency
  5. Corrupted Gateway Error
  6. Delayed Webhook
  7. Out-of-Order Event
  8. Simulated API Timeout
  9. Simulated Execution Failure
 10. Stale Event (>24h)
"""

from __future__ import annotations

import pytest

from backend.models.schemas import OutcomeStatus
from backend.services.chaos import ChaosScenarioResult, ChaosSuite
from backend.services.pipeline import RevPilotPipeline


@pytest.fixture
def chaos_suite() -> ChaosSuite:
    return ChaosSuite(enabled=True)


class TestChaosScenarios:
    def test_chaos_disabled_by_default(self) -> None:
        default_suite = ChaosSuite()
        assert default_suite.enabled is False

    def test_scenario_01_duplicate_transaction(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_01_duplicate_transaction_id(pipeline)
        assert isinstance(res, ChaosScenarioResult)
        assert res.safe is True
        assert res.pipeline_status == "abandoned"
        assert res.audit_emitted is True

    def test_scenario_02_malformed_amount(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_02_malformed_amount(pipeline)
        assert res.safe is True
        assert res.pipeline_status == "validation_error"
        assert res.exception_handled is True

    def test_scenario_03_negative_amount(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_03_negative_amount(pipeline)
        assert res.safe is True
        assert res.execution_called is False

    def test_scenario_04_unknown_currency(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_04_unknown_currency(pipeline)
        assert res.safe is True
        assert res.pipeline_status == "abandoned"

    def test_scenario_05_corrupted_gateway_error(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_05_corrupted_gateway_error(pipeline)
        assert res.safe is True
        assert res.details.get("confidence", 1.0) < 0.60

    def test_scenario_06_delayed_webhook(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_06_delayed_webhook(pipeline)
        assert res.safe is True
        assert res.audit_emitted is True

    def test_scenario_07_out_of_order_event(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_07_out_of_order_event(pipeline)
        assert res.safe is True
        assert res.pipeline_status == "abandoned"

    def test_scenario_08_simulated_api_timeout(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_08_simulated_api_timeout(pipeline)
        assert res.safe is True
        assert res.pipeline_status == OutcomeStatus.failure.value

    def test_scenario_09_simulated_execution_failure(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_09_simulated_execution_failure(pipeline)
        assert res.safe is True
        assert res.audit_emitted is True

    def test_scenario_10_stale_event(self, chaos_suite: ChaosSuite) -> None:
        pipeline = RevPilotPipeline(seed=42)
        res = chaos_suite.scenario_10_stale_event(pipeline)
        assert res.safe is True
        assert res.pipeline_status == "abandoned"

    def test_run_all_scenarios_pass_cleanly(self, chaos_suite: ChaosSuite) -> None:
        results = chaos_suite.run_all(seed=20260821)
        assert len(results) == 10
        for r in results:
            assert r.safe is True, f"Scenario {r.scenario_id} violated safety: {r.result}"
            assert r.audit_emitted is True
            assert r.exception_handled is True
