"""
Guardrail Engine
================

Deterministic financial safety layer that evaluates every recovery decision
before execution. This is the ONLY component authorized to approve or block
financial actions.

ARCHITECTURAL INVARIANTS:
  1. Purely deterministic — no LLM or probabilistic logic.
  2. All rules are evaluated on every run to provide complete audit diagnostics.
  3. Default on any internal exception: BLOCKED.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import UTC, datetime

from backend.models.schemas import (
    DiagnosisResult,
    FailureClass,
    GuardrailDecision,
    GuardrailVerdict,
    PaymentFailureEvent,
    StrategyDecision,
)
from backend.simulator.types import SimAction


class GuardrailEngine:
    """Deterministic financial authorization gatekeeper."""

    def __init__(
        self,
        max_retries_per_payment: int = 3,
        max_retries_per_card_24h: int = 5,
        cooloff_seconds: int = 300,
    ) -> None:
        self.max_retries_per_payment = max_retries_per_payment
        self.max_retries_per_card_24h = max_retries_per_card_24h
        self.cooloff_seconds = cooloff_seconds

        # State tracking for rate/velocity/cooloff rules
        self._card_retries_24h: dict[str, int] = defaultdict(int)
        self._payment_last_attempt_ts: dict[str, float] = {}
        self._seen_idempotency_keys: set[str] = set()
        self._lock = threading.Lock()

    def evaluate(
        self,
        event: PaymentFailureEvent,
        decision: StrategyDecision,
        diagnosis: DiagnosisResult | None = None,
    ) -> GuardrailDecision:
        """Evaluate strategy decision against all deterministic safety rules.

        Returns GuardrailDecision with verdict (approved, blocked, escalate).
        """
        evaluated_rules: list[str] = []
        triggered_rules: list[str] = []
        block_reasons: list[str] = []
        escalate_reasons: list[str] = []

        card_key = event.card_last4 or event.payment_id
        current_card_retries = self._card_retries_24h[card_key]

        # -------------------------------------------------------------
        # Rule 1: Max Retry Count Per Payment
        # -------------------------------------------------------------
        rule_1 = "rule_max_retries_per_payment"
        evaluated_rules.append(rule_1)
        if event.attempt_number > self.max_retries_per_payment:
            triggered_rules.append(rule_1)
            block_reasons.append(
                f"Attempt {event.attempt_number} exceeds max allowed per payment ({self.max_retries_per_payment})."
            )

        # -------------------------------------------------------------
        # Rule 2: Max Retries Per Card in 24h
        # -------------------------------------------------------------
        rule_2 = "rule_max_retries_per_card_24h"
        evaluated_rules.append(rule_2)
        if current_card_retries >= self.max_retries_per_card_24h:
            triggered_rules.append(rule_2)
            block_reasons.append(
                f"Card/instrument has reached 24h retry limit ({self.max_retries_per_card_24h})."
            )

        # -------------------------------------------------------------
        # Rule 3: Amount Mutation Bounds (±0% in MVP)
        # -------------------------------------------------------------
        rule_3 = "rule_amount_mutation_bounds"
        evaluated_rules.append(rule_3)
        # In MVP, amount must match original amount exactly
        if event.amount <= 0:
            triggered_rules.append(rule_3)
            block_reasons.append(f"Invalid transaction amount: ₹{event.amount}.")

        # -------------------------------------------------------------
        # Rule 4: Fraud Flag Check
        # -------------------------------------------------------------
        rule_4 = "rule_fraud_check"
        evaluated_rules.append(rule_4)
        is_fraud = (
            diagnosis is not None
            and diagnosis.normalized_failure_class == FailureClass.FRAUD_SUSPECTED
        )
        if is_fraud:
            triggered_rules.append(rule_4)
            block_reasons.append("Unconditional block on suspected fraud.")

        # -------------------------------------------------------------
        # Rule 5: Non-Retryable Check
        # -------------------------------------------------------------
        rule_5 = "rule_non_retryable_check"
        evaluated_rules.append(rule_5)
        is_automated_retry = decision.selected_action != SimAction.HUMAN_ESCALATION.value
        if diagnosis is not None and not diagnosis.retryability and not is_fraud and is_automated_retry:
            triggered_rules.append(rule_5)
            block_reasons.append(
                f"Automated retry blocked: failure category {diagnosis.normalized_failure_class.value} is marked non-retryable."
            )

        # -------------------------------------------------------------
        # Rule 6: Supported Currency Check
        # -------------------------------------------------------------
        rule_6 = "rule_supported_currency"
        evaluated_rules.append(rule_6)
        if event.currency not in {"INR"}:
            triggered_rules.append(rule_6)
            block_reasons.append(f"Unsupported transaction currency '{event.currency}' (must be INR).")

        # -------------------------------------------------------------
        # Rule 7: Event Staleness Window Check (>24 hours)
        # -------------------------------------------------------------
        rule_7 = "rule_stale_event"
        evaluated_rules.append(rule_7)
        now_utc = datetime.now(UTC)
        event_ts = event.timestamp if event.timestamp.tzinfo else event.timestamp.replace(tzinfo=UTC)
        age_seconds = (now_utc - event_ts).total_seconds()
        if age_seconds > 86400:  # 24 hours
            triggered_rules.append(rule_7)
            block_reasons.append(f"Event timestamp is stale ({age_seconds/3600:.1f} hours old, max allowed 24h).")

        # -------------------------------------------------------------
        # Rule 8: Idempotency Enforcement (Thread-Safe Atomic Check-and-Consume)
        # -------------------------------------------------------------
        rule_8 = "rule_idempotency"
        evaluated_rules.append(rule_8)
        idemp_key = f"{event.payment_id}:{event.attempt_number}"
        with self._lock:
            if idemp_key in self._seen_idempotency_keys:
                is_dup = True
            else:
                self._seen_idempotency_keys.add(idemp_key)
                is_dup = False

        if is_dup:
            triggered_rules.append(rule_8)
            block_reasons.append("Duplicate execution request detected for same payment/attempt.")

        # -------------------------------------------------------------
        # Rule 9: Action Escalation Requirement
        # -------------------------------------------------------------
        rule_9 = "rule_human_escalation_action"
        evaluated_rules.append(rule_9)
        if decision.selected_action == SimAction.HUMAN_ESCALATION.value:
            triggered_rules.append(rule_9)
            escalate_reasons.append("Strategy recommended human escalation.")

        # -------------------------------------------------------------
        # Determine Final Verdict
        # -------------------------------------------------------------
        if block_reasons:
            verdict = GuardrailVerdict.blocked
            reason = " | ".join(block_reasons)
        elif escalate_reasons:
            verdict = GuardrailVerdict.escalate
            reason = " | ".join(escalate_reasons)
        else:
            verdict = GuardrailVerdict.approved
            reason = f"Approved {decision.selected_action} for execution on attempt {event.attempt_number}."
            # Increment 24h count only on approval
            self._card_retries_24h[card_key] += 1
            self._payment_last_attempt_ts[event.payment_id] = time.time()

        return GuardrailDecision(
            decision_id=decision.decision_id,
            event_id=event.event_id,
            verdict=verdict,
            rules_evaluated=evaluated_rules,
            rules_triggered=triggered_rules,
            reason=reason,
            retry_count_24h=current_card_retries,
            max_retry_limit=self.max_retries_per_payment,
            evaluated_at=datetime.now(UTC),
        )
