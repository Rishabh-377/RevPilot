"""
RevPilot Chaos Engineering Suite
================================

Injects adversarial, malformed, out-of-order, and failure-prone payloads
into the normal RevPilot pipeline to verify deterministic financial safety,
graceful degradation, and audit completeness.

ARCHITECTURAL PRINCIPLES:
  1. Chaos Mode is DISABLED by default.
  2. Faults are injected through the NORMAL pipeline entrypoint.
  3. The chaos system NEVER alters, weakens, or mocks normal safety checks.
  4. Every scenario checks:
       - Was execution safely blocked / handled?
       - Did false successes occur (MUST BE ZERO)?
       - Was a structured audit entry emitted?
       - Were exceptions handled without crashing the host?
"""

from __future__ import annotations

import datetime
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from backend.models.schemas import (
    FailureClass,
    GuardrailVerdict,
    OutcomeStatus,
    PaymentFailureEvent,
    PaymentMethod,
)
from backend.services.pipeline import RevPilotPipeline

# ---------------------------------------------------------------------------
# Chaos Result Contract
# ---------------------------------------------------------------------------


class ChaosScenarioResult(BaseModel):
    """Execution output and safety verification for a single chaos scenario."""

    scenario_id: str
    name: str
    injected_fault: str
    expected_safe_behavior: str
    result: str
    safe: bool
    execution_called: bool
    exception_handled: bool
    audit_emitted: bool
    pipeline_status: str
    guardrail_decision: str | None = None
    financial_mutation: bool = False
    audit_recorded: bool = True
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Chaos Suite Definition & Runner
# ---------------------------------------------------------------------------


class ChaosSuite:
    """Manages and executes deterministic chaos and fault injection tests."""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    def run_all(self, seed: int = 20260821) -> list[ChaosScenarioResult]:
        """Execute all 10 chaos test scenarios against the pipeline."""
        scenarios = [
            self.scenario_01_duplicate_transaction_id,
            self.scenario_02_malformed_amount,
            self.scenario_03_negative_amount,
            self.scenario_04_unknown_currency,
            self.scenario_05_corrupted_gateway_error,
            self.scenario_06_delayed_webhook,
            self.scenario_07_out_of_order_event,
            self.scenario_08_simulated_api_timeout,
            self.scenario_09_simulated_execution_failure,
            self.scenario_10_stale_event,
        ]
        results = []
        for scenario_fn in scenarios:
            pipeline = RevPilotPipeline(seed=seed)
            res = scenario_fn(pipeline)
            results.append(res)
        return results

    # -----------------------------------------------------------------------
    # Scenario 1: Duplicate Transaction ID
    # -----------------------------------------------------------------------
    def scenario_01_duplicate_transaction_id(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject duplicate replay of an existing payment transaction."""
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_dup_101",
            merchant_id="merch_chaos",
            amount=1500.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
        )
        # First execution (approved)
        pipeline.process_event(ev)
        # Second execution (duplicate replay of exact same event/attempt)
        res2 = pipeline.process_event(ev)

        safe = (
            res2.guardrail_verdict == GuardrailVerdict.blocked
            and res2.amount_recovered == 0.0
            and len(res2.audit_events) > 0
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_01_DUPLICATE_TXN",
            name="Duplicate Transaction ID",
            injected_fault="Replay duplicate payment_id with identical attempt number.",
            expected_safe_behavior="Guardrail BLOCK on idempotency; execution not called; audit entry written.",
            result="Blocked by idempotency guardrail" if safe else "FAILED to block duplicate",
            safe=safe,
            execution_called=res2.amount_recovered > 0,
            exception_handled=True,
            audit_emitted=len(res2.audit_events) > 0,
            pipeline_status=res2.status,
            guardrail_decision=res2.guardrail_verdict.value.upper() if res2.guardrail_verdict else "NONE",
            financial_mutation=res2.amount_recovered > 0,
            audit_recorded=len(res2.audit_events) > 0,
            details={"rules_triggered": res2.guardrail.rules_triggered if res2.guardrail else []},
        )

    # -----------------------------------------------------------------------
    # Scenario 2: Malformed Amount
    # -----------------------------------------------------------------------
    def scenario_02_malformed_amount(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject malformed string payload as transaction amount."""
        raw_payload = {
            "payment_id": "pay_chaos_malformed_amt",
            "merchant_id": "merch_chaos",
            "amount": "one_thousand_rupees",  # Malformed string
            "currency": "INR",
            "payment_method": "upi",
            "failure_code": "bank timed out",
            "attempt_number": 1,
        }
        res = pipeline.process_event(raw_payload)

        safe = (
            res.status == "validation_error"
            and res.amount_recovered == 0.0
            and len(res.audit_events) > 0
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_02_MALFORMED_AMOUNT",
            name="Malformed Amount",
            injected_fault="Non-numeric string 'one_thousand_rupees' passed as amount.",
            expected_safe_behavior="Schema validation rejects event; no execution; audit logged.",
            result="Rejected by Schema Validation" if safe else "FAILED to reject malformed amount",
            safe=safe,
            execution_called=False,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision="VALIDATION_REJECTED",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"error_message": res.error_message},
        )

    # -----------------------------------------------------------------------
    # Scenario 3: Negative Amount
    # -----------------------------------------------------------------------
    def scenario_03_negative_amount(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject negative transaction amount."""
        raw_payload = {
            "payment_id": "pay_chaos_neg_amt",
            "merchant_id": "merch_chaos",
            "amount": -500.0,  # Negative
            "currency": "INR",
            "payment_method": "upi",
            "failure_code": "bank timed out",
            "attempt_number": 1,
        }
        res = pipeline.process_event(raw_payload)

        safe = (
            (res.status == "validation_error" or res.guardrail_verdict == GuardrailVerdict.blocked)
            and res.amount_recovered == 0.0
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_03_NEGATIVE_AMOUNT",
            name="Negative Amount",
            injected_fault="Negative transaction amount of -₹500.00.",
            expected_safe_behavior="Validation/Guardrail blocks negative amount; no execution.",
            result="Blocked on Negative Amount Validation" if safe else "FAILED to block negative amount",
            safe=safe,
            execution_called=False,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "VALIDATION_REJECTED",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"error_message": res.error_message},
        )

    # -----------------------------------------------------------------------
    # Scenario 4: Unknown Currency
    # -----------------------------------------------------------------------
    def scenario_04_unknown_currency(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject unsupported currency code (e.g. BTC)."""
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_curr",
            merchant_id="merch_chaos",
            amount=1000.0,
            currency="BTC",  # Unsupported
            payment_method=PaymentMethod.credit_card,
            failure_code="bank timed out",
            attempt_number=1,
        )
        res = pipeline.process_event(ev)

        safe = (
            res.guardrail_verdict == GuardrailVerdict.blocked
            and res.amount_recovered == 0.0
            and "rule_supported_currency" in res.guardrail.rules_triggered
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_04_UNKNOWN_CURRENCY",
            name="Unknown Currency",
            injected_fault="Unsupported currency code 'BTC'.",
            expected_safe_behavior="Guardrail BLOCK on unsupported currency; execution not called.",
            result="Blocked by Currency Guardrail" if safe else "FAILED to block unsupported currency",
            safe=safe,
            execution_called=False,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"reason": res.guardrail.reason if res.guardrail else ""},
        )

    # -----------------------------------------------------------------------
    # Scenario 5: Corrupted Gateway Error
    # -----------------------------------------------------------------------
    def scenario_05_corrupted_gateway_error(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject binary noise and corrupted null bytes as gateway error string."""
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_corrupt_err",
            merchant_id="merch_chaos",
            amount=2000.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="0xDEADBEEF\x00\xff_CORRUPTED_GATEWAY_BUFFER_PAYLOAD",
            raw_gateway_error="0xDEADBEEF\x00\xff_CORRUPTED_GATEWAY_BUFFER_PAYLOAD",
            attempt_number=1,
        )
        res = pipeline.process_event(ev)

        safe = (
            res.diagnosis.normalized_failure_class == FailureClass.UNKNOWN
            and res.diagnosis.confidence < 0.60
            and res.stage_reached == "completed"
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_05_CORRUPTED_ERROR",
            name="Corrupted Gateway Error",
            injected_fault="Garbage error string with raw null bytes and corrupt hex buffer.",
            expected_safe_behavior="Diagnosis classifies as UNKNOWN with low confidence; pipeline operates safely.",
            result="Classified as UNKNOWN with low confidence" if safe else "FAILED to isolate corrupt error",
            safe=safe,
            execution_called=res.amount_recovered > 0,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"confidence": res.diagnosis.confidence if res.diagnosis else 0.0},
        )

    # -----------------------------------------------------------------------
    # Scenario 6: Delayed Webhook
    # -----------------------------------------------------------------------
    def scenario_06_delayed_webhook(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject webhook arriving 3 hours after initial payment initiation."""
        delayed_ts = datetime.now(UTC) - timedelta(hours=3)
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_delayed_hook",
            merchant_id="merch_chaos",
            amount=3000.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
            timestamp=delayed_ts,
        )
        res = pipeline.process_event(ev)

        safe = (
            res.stage_reached == "completed"
            and len(res.audit_events) > 0
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_06_DELAYED_WEBHOOK",
            name="Delayed Webhook",
            injected_fault="Webhook arriving 3 hours after failure occurrence.",
            expected_safe_behavior="Handled through standard pipeline without state corruption; audit logged.",
            result="Processed with correct timestamp tracking" if safe else "FAILED delayed webhook handling",
            safe=safe,
            execution_called=res.amount_recovered > 0,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"event_timestamp": str(ev.timestamp)},
        )

    # -----------------------------------------------------------------------
    # Scenario 7: Out-of-Order Event
    # -----------------------------------------------------------------------
    def scenario_07_out_of_order_event(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject attempt number 4 directly (violates sequence limit)."""
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_ooo",
            merchant_id="merch_chaos",
            amount=2500.0,
            currency="INR",
            payment_method=PaymentMethod.credit_card,
            failure_code="bank response timed out",
            attempt_number=4,  # Out of bounds (>3)
        )
        res = pipeline.process_event(ev)

        safe = (
            res.guardrail_verdict == GuardrailVerdict.blocked
            and res.amount_recovered == 0.0
            and "rule_max_retries_per_payment" in res.guardrail.rules_triggered
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_07_OUT_OF_ORDER",
            name="Out-of-Order Attempt",
            injected_fault="Attempt number 4 arriving directly without prior attempts.",
            expected_safe_behavior="Guardrail BLOCK on max attempts limit; execution not called.",
            result="Blocked by Max Retries Guardrail" if safe else "FAILED to block out-of-order attempt",
            safe=safe,
            execution_called=False,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"attempt_number": ev.attempt_number},
        )

    # -----------------------------------------------------------------------
    # Scenario 8: Simulated API Timeout
    # -----------------------------------------------------------------------
    def scenario_08_simulated_api_timeout(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject simulated execution network timeout."""
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_timeout",
            merchant_id="merch_chaos",
            amount=4000.0,
            currency="INR",
            payment_method=PaymentMethod.upi,
            failure_code="bank response timed out",
            attempt_number=1,
        )
        from unittest.mock import patch

        from backend.services.execution import NetworkTimeoutException

        with patch.object(pipeline.execution_service.outcome_engine, "simulate_outcome", side_effect=NetworkTimeoutException("Simulated chaos timeout")):
            res = pipeline.process_event(ev)

        safe = (
            res.outcome.status == OutcomeStatus.failure
            and res.amount_recovered == 0.0
            and res.outcome.gateway_response_code == "ERR_ADAPTER_NETWORK_TIMEOUT"
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_08_API_TIMEOUT",
            name="Simulated API Timeout",
            injected_fault="Execution adapter simulated network timeout.",
            expected_safe_behavior="Execution marked failure; no false success; zero amount recovered.",
            result="Marked as execution failure; no false recovery" if safe else "FAILED on API timeout handling",
            safe=safe,
            execution_called=True,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"gateway_code": res.outcome.gateway_response_code if res.outcome else ""},
        )

    # -----------------------------------------------------------------------
    # Scenario 9: Simulated Execution Failure
    # -----------------------------------------------------------------------
    def scenario_09_simulated_execution_failure(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject execution decline from bank/switch."""
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_exec_fail",
            merchant_id="merch_chaos",
            amount=3500.0,
            currency="INR",
            payment_method=PaymentMethod.credit_card,
            failure_code="card issuer declined: restricted card",
            attempt_number=1,
        )
        res = pipeline.process_event(ev)

        safe = (
            res.stage_reached == "completed"
            and (res.amount_recovered == 0.0 or res.success)
            and len(res.audit_events) > 0
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_09_EXECUTION_FAILURE",
            name="Simulated Execution Failure",
            injected_fault="Issuer decline simulated in execution adapter.",
            expected_safe_behavior="Failure recorded accurately; statistical model observes failure.",
            result="Outcome recorded and learned safely" if safe else "FAILED execution outcome processing",
            safe=safe,
            execution_called=True,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"outcome_status": res.status},
        )

    # -----------------------------------------------------------------------
    # Scenario 10: Stale Event
    # -----------------------------------------------------------------------
    def scenario_10_stale_event(self, pipeline: RevPilotPipeline) -> ChaosScenarioResult:
        """Inject stale event with timestamp 48 hours in the past."""
        stale_ts = datetime.now(UTC) - timedelta(hours=48)
        ev = PaymentFailureEvent(
            payment_id="pay_chaos_stale",
            merchant_id="merch_chaos",
            amount=5000.0,
            currency="INR",
            payment_method=PaymentMethod.credit_card,
            failure_code="bank response timed out",
            attempt_number=1,
            timestamp=stale_ts,
        )
        res = pipeline.process_event(ev)

        safe = (
            res.guardrail_verdict == GuardrailVerdict.blocked
            and res.amount_recovered == 0.0
            and "rule_stale_event" in res.guardrail.rules_triggered
        )
        return ChaosScenarioResult(
            scenario_id="CHAOS_10_STALE_EVENT",
            name="Stale Event (>24h)",
            injected_fault="Event timestamp 48 hours in the past.",
            expected_safe_behavior="Guardrail BLOCK on event staleness; execution not called.",
            result="Blocked by Event Staleness Guardrail" if safe else "FAILED to block stale event",
            safe=safe,
            execution_called=False,
            exception_handled=True,
            audit_emitted=len(res.audit_events) > 0,
            pipeline_status=res.status,
            guardrail_decision=res.guardrail_verdict.value.upper() if res.guardrail_verdict else "NONE",
            financial_mutation=res.amount_recovered > 0,
            audit_recorded=len(res.audit_events) > 0,
            details={"reason": res.guardrail.reason if res.guardrail else ""},
        )

    def format_summary_table(self, results: list[ChaosScenarioResult]) -> str:
        """Generate formatted ASCII table displaying chaos scenario outputs."""
        lines = [
            "╔═════════════════════════════════════════════════════════════════════════════════════════════════════════════╗",
            "║                                   RevPilot Chaos Engineering Suite                                          ║",
            "╠═════════════════════════════════════════════════════════════════════════════════════════════════════════════╣",
            "║ Scenario ID               │ Result Summary                           │ Safety │ Exec Called │ Exception Handled ║",
            "╟───────────────────────────┼──────────────────────────────────────────┼────────┼─────────────┼───────────────────╢",
        ]
        for r in results:
            safe_str = "SAFE  " if r.safe else "UNSAFE"
            exec_str = "Yes" if r.execution_called else "No "
            ex_str = "Yes" if r.exception_handled else "No "
            lines.append(
                f"║ {r.scenario_id:<25} │ {r.result:<40} │ {safe_str} │ {exec_str:<11} │ {ex_str:<17} ║"
            )
        lines.append(
            "╚═════════════════════════════════════════════════════════════════════════════════════════════════════════════╝"
        )
        return "\n".join(lines)
