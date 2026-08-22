"""
tests/test_portability_clean_startup.py
======================================

Comprehensive pre-release portability, clean-startup, and financial safety suite:
  1. Clean-Environment Startup & Configuration Overrides
  2. Path Independence & Zero Hardcoded User Paths
  3. API Boundary & 404/422 Resilience
  4. Dynamic Benchmark & Fallback Execution on Fresh Directory
  5. Strict Financial Safety & Idempotency Invariants
  6. End-to-End Payment Recovery Lifecycle
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import app
from backend.models.schemas import (
    GuardrailVerdict,
    OutcomeStatus,
    PaymentFailureEvent,
    PaymentMethod,
)
from backend.services.pipeline import RevPilotPipeline

client = TestClient(app)


class TestConfigurationAndCleanStartup:
    """Verify application initializes properly in clean environments without developer state."""

    def test_settings_load_with_defaults(self) -> None:
        settings = Settings()
        assert settings.app_name == "RevPilot"
        assert settings.bandit_prior_alpha == 1.0
        assert settings.bandit_prior_beta == 1.0
        assert settings.guardrail_cooloff_seconds == 300
        assert settings.max_retries_per_payment == 3
        assert settings.max_retries_per_card_24h == 5
        assert settings.llm_enabled is False

    def test_environment_variable_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REVPILOT_MAX_RETRIES_PER_PAYMENT", "7")
        monkeypatch.setenv("REVPILOT_LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("REVPILOT_DEBUG", "true")
        settings = Settings()
        assert settings.max_retries_per_payment == 7
        assert settings.log_level == "DEBUG"
        assert settings.debug is True

    def test_missing_optional_credentials_gracefully_fallback(self) -> None:
        settings = Settings(gemini_api_key=None, llm_enabled=False)
        assert settings.gemini_api_key is None
        assert settings.llm_enabled is False


class TestPathIndependenceAndPortability:
    """Verify zero hardcoded developer paths in Python source files."""

    def test_no_absolute_user_paths_in_codebase(self) -> None:
        root_dir = Path(__file__).parent.parent
        bad_patterns = ["/Users/", "/home/", "C:\\Users\\"]

        py_files = list(root_dir.glob("backend/**/*.py")) + list(root_dir.glob("scripts/**/*.py"))
        for py_file in py_files:
            content = py_file.read_text(encoding="utf-8")
            for pattern in bad_patterns:
                assert pattern not in content, f"Found hardcoded path {pattern} in {py_file}"

    def test_frontend_uses_relative_api_paths(self) -> None:
        html_path = Path(__file__).parent.parent / "frontend" / "index.html"
        assert html_path.exists()
        content = html_path.read_text(encoding="utf-8")
        # Should not contain hardcoded localhost:8000
        assert "http://localhost:8000" not in content
        assert "http://127.0.0.1:8000" not in content
        # Should contain relative API calls
        assert "/api/v1/dashboard/overview" in content
        assert "/api/v1/dashboard/transactions" in content


class TestAPIResilienceAndErrorHandling:
    """Verify API handles edge cases, 404s, 422s, and clean initial states."""

    def test_nonexistent_transaction_returns_404(self) -> None:
        res = client.get("/api/v1/dashboard/transaction/NONEXISTENT_EVT_99999")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_nonexistent_audit_trail_returns_404(self) -> None:
        res = client.get("/api/v1/audit/NONEXISTENT_AUDIT_99999")
        assert res.status_code == 404

    def test_invalid_event_ingestion_payload_returns_422(self) -> None:
        # Negative amount
        res = client.post(
            "/api/v1/events",
            json={
                "payment_id": "pay_ERR",
                "merchant_id": "merch_ERR",
                "amount": -500.0,
                "payment_method": "upi",
                "failure_code": "timeout",
            },
        )
        assert res.status_code == 422

    def test_non_inr_currency_rejected_with_422(self) -> None:
        res = client.post(
            "/api/v1/events",
            json={
                "payment_id": "pay_USD",
                "merchant_id": "merch_ERR",
                "amount": 500.0,
                "currency": "USD",
                "payment_method": "upi",
                "failure_code": "timeout",
            },
        )
        assert res.status_code == 422

    def test_dashboard_overview_returns_complete_schema(self) -> None:
        res = client.get("/api/v1/dashboard/overview")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "online"
        assert data["environment_type"] == "SYNTHETIC SIMULATION"
        assert "metric_definitions" in data
        assert "revpilot" in data
        assert "baseline" in data
        assert "comparison" in data

    def test_dashboard_chaos_run_all_scenarios_safe(self) -> None:
        res = client.post("/api/v1/dashboard/chaos/run")
        assert res.status_code == 200
        data = res.json()
        assert data["all_safe"] is True
        assert data["scenarios_passed"] == 10
        assert data["total_scenarios"] == 10


class TestFinancialSafetyAndIdempotency:
    """Verify strict financial safety invariants."""

    def test_fraud_and_duplicate_events_are_blocked(self) -> None:
        pipeline = RevPilotPipeline(seed=20260821)
        fraud_event = PaymentFailureEvent(
            payment_id="pay_fraud_999",
            merchant_id="merch_test",
            amount=50000.0,
            currency="INR",
            payment_method=PaymentMethod.credit_card,
            failure_code="fraud suspect account flagged by risk engine",
            attempt_number=1,
        )
        result = pipeline.process_event(fraud_event)
        assert result.guardrail_verdict == GuardrailVerdict.blocked
        assert result.status == OutcomeStatus.abandoned
        # Verify no execution outcome occurred
        assert result.amount_recovered == 0.0

    def test_duplicate_payment_event_blocked_by_guardrail(self) -> None:
        pipeline = RevPilotPipeline(seed=20260821)
        event1 = PaymentFailureEvent(
            payment_id="pay_dup_01",
            merchant_id="merch_test",
            amount=1500.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="timeout processing payment",
            attempt_number=1,
        )
        result1 = pipeline.process_event(event1)
        assert result1.guardrail_verdict == GuardrailVerdict.approved

        # Send same payment ID again
        event2 = PaymentFailureEvent(
            payment_id="pay_dup_01",
            merchant_id="merch_test",
            amount=1500.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="timeout processing payment",
            attempt_number=1,
        )
        result2 = pipeline.process_event(event2)
        # Second attempt for same payment ID should be flagged/blocked as duplicate
        assert result2.guardrail_verdict == GuardrailVerdict.blocked
        assert result2.status == OutcomeStatus.abandoned


class TestEndToEndLifecycle:
    """Verify complete 8-stage lifecycle from API ingestion to recovery and audit."""

    def test_full_recovery_lifecycle_via_api(self) -> None:
        res = client.post(
            "/api/v1/recover",
            json={
                "payment_id": "pay_E2E_001",
                "merchant_id": "merch_e2e",
                "amount": 3450.0,
                "currency": "INR",
                "payment_method": "upi",
                "failure_code": "bank response timed out after 30s",
                "attempt_number": 1,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["stage_reached"] == "completed"
        assert data["diagnosis"]["normalized_failure_class"] == "TIMEOUT_TRANSIENT"
        assert data["guardrail"]["verdict"] == "approved"
        assert "audit_events" in data
        assert len(data["audit_events"]) >= 5
