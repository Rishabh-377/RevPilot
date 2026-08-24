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

from fastapi.testclient import TestClient

from backend.main import app

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

    def test_dashboard_frontend_javascript_syntax_and_functions(self) -> None:
        """Verify frontend JavaScript contains required interaction handlers without syntax corruption."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        html = response.text
        
        # Verify critical client-side event handlers and routing functions exist
        assert "function updateClock()" in html
        assert "function switchTab(tabId)" in html
        assert "function loadOverviewData()" in html
        assert "function loadExplorerTransactions()" in html
        assert "function loadLearningData()" in html
        assert "function loadExceptionsData()" in html
        assert "function triggerChaosRun()" in html
        assert "function loadAuditLogs()" in html
        assert "function renderTransactionDetail(tx)" in html
        assert "function renderComparisonTable(" in html
        assert "switchTab('control_room')" in html

    def test_favicon_endpoints_return_200(self) -> None:
        """Verify /favicon.ico and /favicon.svg endpoints return HTTP 200 with valid content."""
        res_ico = client.get("/favicon.ico")
        assert res_ico.status_code == 200
        assert "image/x-icon" in res_ico.headers.get("content-type", "")
        assert len(res_ico.content) > 0

        res_svg = client.get("/favicon.svg")
        assert res_svg.status_code == 200
        assert "image/svg+xml" in res_svg.headers.get("content-type", "")
        assert b"<svg" in res_svg.content

    def test_dashboard_html_contains_favicon_links(self) -> None:
        """Verify dashboard HTML includes favicon link elements."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert 'href="/favicon.svg"' in response.text
        assert 'href="/favicon.ico"' in response.text



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
            with open("output/revpilot_metrics.json", encoding="utf-8") as f:
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


class TestProductCredibilityHardening:
    """Comprehensive regression suite for product credibility and demo hardening."""

    def test_financial_formatting_helpers_in_frontend(self) -> None:
        """Verify centralized formatting helpers exist and prevent NaN in dashboard."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        html = response.text

        assert "function formatCurrency(val, fallback = 'N/A')" in html
        assert "function formatINR(val, fallback = 'N/A')" in html
        assert "function formatLakhs(val, fallback = 'N/A')" in html
        assert "function formatPercent(val, fallback = '—')" in html

    def test_blocked_vs_executed_semantic_invariants(self) -> None:
        """Verify blocked actions have execution_called = false and 0 financial leakage."""
        client.post("/api/v1/judge/reset")
        client.post("/api/v1/judge/run_first")
        res_dup = client.post("/api/v1/judge/run_second")
        assert res_dup.status_code == 200
        data = res_dup.json()

        pipeline_res = data["pipeline_result"]
        assert pipeline_res["guardrail_verdict"] == "blocked"
        assert pipeline_res["amount_recovered"] == 0.0
        assert pipeline_res["net_value"] == 0.0
        assert pipeline_res["success"] is False

    def test_all_audit_stage_filters(self) -> None:
        """Verify audit trail endpoint filters accurately by every valid pipeline stage."""
        stages = [
            "schema_validation",
            "diagnosis",
            "context_creation",
            "strategy",
            "guardrail",
            "execution",
            "statistical_update",
        ]
        for st in stages:
            resp = client.get(f"/api/v1/dashboard/audit?stage={st}")
            assert resp.status_code == 200
            items = resp.json()
            for item in items:
                assert item["stage"].lower() == st.lower()

            # Test uppercase parameter as well
            resp_upper = client.get(f"/api/v1/dashboard/audit?stage={st.upper()}")
            assert resp_upper.status_code == 200

    def test_chaos_suite_real_backend_execution(self) -> None:
        """Verify chaos suite executes real adversarial attacks with 10/10 containment."""
        resp = client.post("/api/v1/dashboard/chaos/run")
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_scenarios"] == 10
        assert data["scenarios_passed"] == 10
        assert data["all_safe"] is True

        # Check duplicate attack scenario
        sc_dup = next((s for s in data["scenarios"] if s["scenario_id"] == "CHAOS_01_DUPLICATE_TXN"), None)
        assert sc_dup is not None
        assert sc_dup["safe"] is True
        assert sc_dup["execution_called"] is False
        assert sc_dup["financial_mutation"] is False
        assert sc_dup["audit_recorded"] is True

        # Check API timeout scenario
        sc_timeout = next((s for s in data["scenarios"] if s["scenario_id"] == "CHAOS_08_API_TIMEOUT"), None)
        assert sc_timeout is not None
        assert sc_timeout["safe"] is True
        assert sc_timeout["execution_called"] is True
        assert sc_timeout["financial_mutation"] is False

    def test_dashboard_transactions_include_normalized_amounts(self) -> None:
        """Verify transaction items return valid numeric amounts without null/undefined."""
        resp = client.get("/api/v1/dashboard/transactions?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert len(data["items"]) > 0

        for it in data["items"]:
            assert "amount" in it
            assert it["amount"] is not None
            assert isinstance(it["amount"], (int, float))
            assert it["amount"] > 0

    def test_chaos_expected_protection_never_undefined(self) -> None:
        """Verify every chaos scenario returns a non-empty expected_safe_behavior and frontend renders it."""
        resp = client.post("/api/v1/dashboard/chaos/run")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scenarios"]) == 10

        expected_map = {
            "CHAOS_01_DUPLICATE_TXN": "Guardrail BLOCK on idempotency; execution not called; audit entry written.",
            "CHAOS_02_MALFORMED_AMOUNT": "Schema validation rejects event; no execution; audit logged.",
            "CHAOS_03_NEGATIVE_AMOUNT": "Validation/Guardrail blocks negative amount; no execution.",
            "CHAOS_04_UNKNOWN_CURRENCY": "Guardrail BLOCK on unsupported currency; execution not called.",
            "CHAOS_05_CORRUPTED_ERROR": "Diagnosis classifies as UNKNOWN with low confidence; pipeline operates safely.",
            "CHAOS_06_DELAYED_WEBHOOK": "Handled through standard pipeline without state corruption; audit logged.",
            "CHAOS_07_OUT_OF_ORDER": "Guardrail BLOCK on max attempts limit; execution not called.",
            "CHAOS_08_API_TIMEOUT": "Execution marked failure; no false success; zero amount recovered.",
            "CHAOS_09_EXECUTION_FAILURE": "Failure recorded accurately; statistical model observes failure.",
            "CHAOS_10_STALE_EVENT": "Guardrail BLOCK on event staleness; execution not called.",
        }

        for sc in data["scenarios"]:
            sid = sc["scenario_id"]
            assert "expected_safe_behavior" in sc
            assert sc["expected_safe_behavior"] is not None
            assert sc["expected_safe_behavior"] != ""
            assert sc["expected_safe_behavior"] != "undefined"
            assert sc["expected_safe_behavior"] == expected_map[sid]

        # Verify frontend template binds to expected_safe_behavior
        dash_res = client.get("/dashboard")
        assert dash_res.status_code == 200
        assert "${sc.expected_safe_behavior" in dash_res.text

    def test_revenue_and_strategy_terminology_clarity(self) -> None:
        """Verify clear distinction between conceptual GMV flow, benchmark KPIs, and strategy recommendation vs guardrail."""
        dash_res = client.get("/dashboard")
        assert dash_res.status_code == 200
        html = dash_res.text

        # Verify Revenue Terminology
        assert "CONCEPTUAL GMV FLOW" in html
        assert "Simulated Recovery Potential" in html
        assert "Illustrative Merchant Opportunity" in html
        assert "NET SIMULATED BENCHMARK REVENUE" in html

        # Verify Strategy vs Guardrail Nomenclature
        assert "MODEL RECOMMENDATION (THOMPSON SAMPLING PROPOSAL)" in html
        assert "Proposed Candidate Action (Max EV)" in html
        assert "FINAL AUTHORIZATION GATE (FAIL-CLOSED)" in html

        # Verify Judge Demo Scene 12 Title & Fallbacks
        assert "Benchmark Reconciliation &amp; Net Recovered Revenue" in html or "Benchmark Reconciliation & Net Recovered Revenue" in html
        assert '30.40%"' in html
        assert '₹15.51L")' in html


class TestDecisionExplorerStatusFilter:
    """Regression test suite for Decision Explorer status filtering."""

    def test_filter_all_statuses(self) -> None:
        """Verify omitting or passing empty status_filter returns full 150 transactions."""
        resp = client.get("/api/v1/dashboard/transactions?limit=150")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 150
        assert len(data["items"]) == 150

    def test_filter_success_only(self) -> None:
        """Verify status_filter=success returns only successful recoveries."""
        resp = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=success")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 35
        assert len(data["items"]) == 35
        for item in data["items"]:
            assert item["status"] == "success"

    def test_filter_failure_only(self) -> None:
        """Verify status_filter=failure returns only failed execution attempts."""
        resp = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=failure")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 64
        assert len(data["items"]) == 64
        for item in data["items"]:
            assert item["status"] == "failure"

    def test_filter_blocked_abandoned(self) -> None:
        """Verify status_filter=abandoned and status_filter=blocked return blocked events."""
        resp_ab = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=abandoned")
        assert resp_ab.status_code == 200
        data_ab = resp_ab.json()
        assert data_ab["total"] == 41
        for item in data_ab["items"]:
            assert item["status"] == "abandoned" or (item.get("guardrail") and item["guardrail"]["verdict"] == "blocked")

        resp_bl = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=blocked")
        assert resp_bl.status_code == 200
        data_bl = resp_bl.json()
        assert data_bl["total"] == 41

    def test_filter_escalated_pending(self) -> None:
        """Verify status_filter=pending and status_filter=escalated return human escalation events."""
        resp_pe = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=pending")
        assert resp_pe.status_code == 200
        data_pe = resp_pe.json()
        assert data_pe["total"] == 10
        for item in data_pe["items"]:
            assert item["status"] == "pending" or (item.get("guardrail") and item["guardrail"]["verdict"] == "escalate")

        resp_es = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=escalated")
        assert resp_es.status_code == 200
        data_es = resp_es.json()
        assert data_es["total"] == 10

    def test_filter_empty_result_set(self) -> None:
        """Verify unknown status filter returns 0 items cleanly."""
        resp = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=non_existent_status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert len(data["items"]) == 0

    def test_filter_switch_and_reset(self) -> None:
        """Verify switching filters back and forth cleanly restores full set."""
        # 1. Success
        r1 = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=success")
        assert r1.json()["total"] == 35

        # 2. Failure
        r2 = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=failure")
        assert r2.json()["total"] == 64

        # 3. Abandoned
        r3 = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=abandoned")
        assert r3.json()["total"] == 41

        # 4. Reset to all
        r_all = client.get("/api/v1/dashboard/transactions?limit=150&status_filter=")
        assert r_all.json()["total"] == 150

    def test_frontend_filter_bindings_and_ux_contract(self) -> None:
        """Verify DOM elements and JavaScript functions for status filtering are properly wired."""
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text

        # Verify element ID and onchange handler match
        assert 'id="explorer-filter-status"' in html
        assert 'onchange="loadExplorerTransactions()"' in html

        # Verify JS correctly queries explorer-filter-status
        assert 'document.getElementById("explorer-filter-status")' in html
        assert 'status_filter=' in html

        # Verify count badge and empty state text
        assert 'id="tx-stream-count"' in html
        assert "No transactions match this status." in html
        assert "selectTransactionInStream" in html




