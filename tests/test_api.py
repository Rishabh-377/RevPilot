"""
tests/test_api.py
=================

Tests for REST API endpoints, dynamic dashboard feeds, and C-3 invariant verification:
  - Root, health, ingestion, and recovery endpoints
  - Dynamic Control Room KPI loading (/api/v1/dashboard/overview)
  - Dynamic Benchmark comparison loading
  - Dynamic Non-stationary learning data loading (/api/v1/dashboard/learning)
  - Dynamic Exception registry loading (/api/v1/dashboard/exceptions) — no hardcoded fake values
  - Dynamic Chaos execution verification (/api/v1/dashboard/chaos/run)
  - Verification that changing underlying output files dynamically changes API responses
  - Verification that dashboard HTML contains zero hardcoded metric values
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.models.schemas import FailureClass

client = TestClient(app)


class TestAPIBasics:
    """Basic API health and routing tests."""

    def test_root_endpoint(self) -> None:
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["app"] == "RevPilot"
        assert data["version"] == "0.1.0"
        assert data["status"] == "running"

    def test_health_endpoint(self) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_events_endpoint_ingestion(self) -> None:
        response = client.post(
            "/api/v1/events",
            json={
                "payment_id": "pay_TEST_01",
                "merchant_id": "merch_TEST",
                "amount": 1000.0,
                "payment_method": "credit_card",
                "failure_code": "bank response timed out",
                "attempt_number": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["normalized_failure_class"] == "TIMEOUT_TRANSIENT"
        assert data["confidence"] > 0.5

    def test_recover_endpoint_execution(self) -> None:
        response = client.post(
            "/api/v1/recover",
            json={
                "payment_id": "pay_TEST_02",
                "merchant_id": "merch_TEST",
                "amount": 1200.0,
                "payment_method": "upi",
                "failure_code": "bank response timed out",
                "attempt_number": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["stage_reached"] == "completed"
        assert "selected_action" in data


class TestDashboardHtmlNoHardcodedMetrics:
    """Verify HTML contains no hardcoded benchmark KPI values (C-3 fix)."""

    def test_dashboard_html_contains_no_static_kpis(self) -> None:
        response = client.get("/dashboard")
        assert response.status_code == 200
        html = response.text

        # Verify element IDs for dynamic rendering exist
        assert 'id="kpi-total-events"' in html
        assert 'id="kpi-recovery-rate"' in html
        assert 'id="kpi-net-revenue"' in html
        assert 'id="comparison-table-body"' in html
        assert 'id="ns-phase-a-context"' in html
        assert 'id="ns-phase-b-delta"' in html

        # Verify initial placeholder '-' or dynamic loading text is present
        assert 'id="kpi-total-events" class="text-2xl font-bold font-mono text-white mt-1">—<' in html
        assert 'id="kpi-recovery-rate" class="text-2xl font-bold font-mono text-emerald-400 mt-1">—<' in html
        assert 'id="kpi-net-revenue" class="text-2xl font-bold font-mono text-emerald-400 mt-1">—<' in html


class TestDynamicDataLoading:
    """Verify that API responses originate dynamically from actual output files."""

    def test_dashboard_overview_reflects_output_files(self) -> None:
        response = client.get("/api/v1/dashboard/overview")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "online"
        assert "revpilot" in data
        assert "baseline" in data
        assert "comparison" in data

        if Path("output/revpilot_metrics.json").exists():
            with open("output/revpilot_metrics.json", "r", encoding="utf-8") as f:
                saved_metrics = json.load(f)
            # Ensure API matches the actual file on disk
            assert data["revpilot"]["financial"]["gross_recovered_revenue_inr"] == saved_metrics["financial"]["gross_recovered_revenue_inr"]

    def test_changing_benchmark_output_changes_api_response(self, tmp_path) -> None:
        """Mocking a change in output/revpilot_metrics.json changes the API output dynamically."""
        mock_custom_metrics = {
            "policy_name": "test_policy",
            "seed": 99999,
            "financial": {
                "successful_recoveries": 345,
                "recovery_rate": 0.69,
                "gross_recovered_revenue_inr": 999999.99,
                "action_cost_inr": 123.45,
                "net_recovered_revenue_inr": 999876.54,
            },
            "operational": {"events": 500, "processed": 500, "throughput_eps": 1000.0, "latency_ms": 0.5},
            "diagnosis": {"accuracy": 0.95, "unknown_rate": 0.05},
            "safety": {"blocked": 50, "human_review": 10, "unsafe_execution_count": 0},
        }

        with patch("backend.api.routes.Path.exists", return_value=True):
            with patch("builtins.open", unittest_mock_open_helper(mock_custom_metrics)):
                res = client.get("/api/v1/dashboard/overview")
                assert res.status_code == 200
                data = res.json()
                assert data["revpilot"]["financial"]["gross_recovered_revenue_inr"] == 999999.99
                assert data["revpilot"]["financial"]["recovery_rate"] == 0.69

    def test_exceptions_endpoint_dynamic_loading(self) -> None:
        """Verify /api/v1/dashboard/exceptions reads real records and does not return static fake samples."""
        mock_custom_exceptions = [
            {
                "exception_id": "EXC_REAL_999",
                "event_id": "evt_real_test",
                "timestamp": "2026-08-21T12:00:00Z",
                "component": "guardrail_engine",
                "exception_type": "VelocityLimitExceeded",
                "reason": "Card 24h retry cap reached.",
                "safe_fallback": "Execution blocked",
                "required_action": "None",
                "status": "HANDLED_SAFELY",
            }
        ]

        with patch("backend.api.routes.Path.exists", return_value=True):
            with patch("builtins.open", unittest_mock_open_helper(mock_custom_exceptions)):
                res = client.get("/api/v1/dashboard/exceptions")
                assert res.status_code == 200
                data = res.json()
                assert isinstance(data, list)
                assert len(data) == 1
                assert data[0]["exception_id"] == "EXC_REAL_999"
                # Hardcoded samples MUST NOT be present
                assert not any(x.get("exception_id") == "EXC_SAMPLE_01" for x in data)

    def test_chaos_execution_returns_real_scenario_results(self) -> None:
        """Verify chaos run endpoint returns actual scenario outputs with all 8 reporting fields."""
        response = client.post("/api/v1/dashboard/chaos/run")
        assert response.status_code == 200
        data = response.json()
        assert data["all_safe"] is True
        assert data["total_scenarios"] == 10
        assert data["scenarios_passed"] == 10
        for sc in data["scenarios"]:
            assert "scenario_id" in sc
            assert "injected_fault" in sc
            assert "pipeline_status" in sc
            assert "guardrail_decision" in sc
            assert "execution_called" in sc
            assert "financial_mutation" in sc
            assert "audit_recorded" in sc
            assert "safe" in sc

    def test_dashboard_overview_contains_definitions_and_synthetic_label(self) -> None:
        """Verify /dashboard/overview includes METRIC_DEFINITIONS and environment_type."""
        response = client.get("/api/v1/dashboard/overview")
        assert response.status_code == 200
        data = response.json()
        assert data["environment_type"] == "SYNTHETIC SIMULATION"
        assert "metric_definitions" in data
        assert "net_recovered_revenue_inr" in data["metric_definitions"]
        assert "recovery_rate" in data["metric_definitions"]
        assert "unsafe_executions" in data["metric_definitions"]

    def test_learning_empty_state_when_no_shift_file(self) -> None:
        """Verify /dashboard/learning returns None for non_stationary_shift if file not present."""
        with patch("backend.api.routes.Path.exists", return_value=False):
            response = client.get("/api/v1/dashboard/learning")
            assert response.status_code == 200
            data = response.json()
            assert data["non_stationary_shift"] is None
            assert "arms" in data

    def test_exceptions_returns_empty_list_when_no_exceptions(self) -> None:
        """Verify /dashboard/exceptions returns empty list if no real exceptions logged."""
        with patch("backend.api.routes.Path.exists", return_value=False):
            response = client.get("/api/v1/dashboard/exceptions")
            assert response.status_code == 200
            data = response.json()
            assert data == []


def unittest_mock_open_helper(return_data):
    from unittest.mock import mock_open

    return mock_open(read_data=json.dumps(return_data))


class TestJudgeModeApi:
    """Verify Judge Mode endpoints function correctly and trigger the real pipeline."""

    def test_judge_reset(self) -> None:
        response = client.post("/api/v1/judge/reset")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_judge_run_first(self) -> None:
        # Reset first to ensure deterministic start
        client.post("/api/v1/judge/reset")
        response = client.post("/api/v1/judge/run_first")
        assert response.status_code == 200
        data = response.json()
        
        assert "event" in data
        assert data["event"]["payment_id"] == "pay_judge_999"
        
        assert "pipeline_result" in data
        assert data["pipeline_result"]["stage_reached"] == "completed"
        
        assert "bandit_before" in data
        assert "bandit_after" in data
        assert "audit_trail" in data
        
        # Verify real learning happened (arm alpha/beta updated)
        selected = data["pipeline_result"]["selected_action"]
        assert selected is not None
        
        before_arm = data["bandit_before"][selected]
        after_arm = data["bandit_after"][selected]
        
        # Successful or failed outcome will increment either alpha or beta
        assert (after_arm["alpha"] + after_arm["beta"]) == (before_arm["alpha"] + before_arm["beta"] + 1)
        
        # Verify audit trail contains stage records
        stages = [a["stage"] for a in data["audit_trail"]]
        assert "diagnosis" in stages
        assert "strategy" in stages
        assert "guardrail" in stages
        assert "execution" in stages

    def test_judge_run_second(self) -> None:
        # Re-run after running first to test idempotency duplicate block
        client.post("/api/v1/judge/reset")
        client.post("/api/v1/judge/run_first")
        
        response = client.post("/api/v1/judge/run_second")
        assert response.status_code == 200
        data = response.json()
        
        assert "event" in data
        assert data["event"]["payment_id"] == "pay_judge_999"
        
        assert "pipeline_result" in data
        res = data["pipeline_result"]
        
        # Guardrail verdict must be blocked
        assert res["guardrail_verdict"] == "blocked"
        assert res["success"] is False
        assert res["amount_recovered"] == 0.0
        
        # Audit trail must show duplicate block
        stages = [a["stage"] for a in data["audit_trail"]]
        assert "guardrail" in stages
        
        # Final outcome stage status should be abandoned (blocked by guardrails)
        assert res["outcome"] is not None
        assert res["outcome"]["status"] == "abandoned"
        assert res["outcome"]["gateway_response_code"] == "GUARDRAIL_BLOCKED"
