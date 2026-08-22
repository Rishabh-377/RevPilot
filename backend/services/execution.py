"""
Execution Service & Adapter
===========================

Executes approved recovery actions via simulator adapter.
Enforces mandatory guardrail approval before performing any financial action.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from backend.models.schemas import (
    GuardrailDecision,
    GuardrailVerdict,
    OutcomeResult,
    OutcomeStatus,
    PaymentFailureEvent,
    RetryStrategy,
    StrategyDecision,
)
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import (
    CustomerSegment,
    FailureClass,
    SimAction,
    SimEvent,
    SimPaymentMethod,
    ValueTier,
)


class NetworkTimeoutException(Exception):
    """Custom exception raised by test doubles to simulate runtime adapter timeouts."""
    pass


class ExecutionService:
    """Execution adapter simulating financial recovery attempts.
    
    SECURITY INVARIANT:
      Execution can happen ONLY when:
        1. All inputs are valid (event, decision, guardrail)
        2. Entity binding strictly matches (guardrail.event_id == event.event_id,
           guardrail.decision_id == decision.decision_id, decision.event_id == event.event_id)
        3. Action is valid and supported (no silent conversion to DELAYED_RETRY)
        4. Guardrail verdict is explicitly APPROVED (GuardrailVerdict.approved)
        5. Idempotency check passes (if enabled)
      Any failure to meet ALL conditions results in immediate, fail-closed abortion without financial execution.
    """

    def __init__(
        self,
        outcome_engine: OutcomeEngine | None = None,
        seed: int | None = None,
        enforce_idempotency: bool = False,
    ) -> None:
        self.ground_truth = GroundTruth()
        self.outcome_engine = outcome_engine or OutcomeEngine(ground_truth=self.ground_truth, seed=seed)
        self.enforce_idempotency = enforce_idempotency
        self._executed_keys: set[str] = set()
        self._lock = threading.Lock()

    async def execute(
        self,
        event: PaymentFailureEvent,
        decision: StrategyDecision,
        guardrail: GuardrailDecision,
        true_failure_class: FailureClass | None = None,
    ) -> OutcomeResult:
        """Execute recovery action strictly if Guardrail approved.

        Parameters
        ----------
        true_failure_class:
            The SIMULATOR's hidden ground-truth failure class, extracted from
            the original SimEvent before schema stripping.  This is the class
            that must be used when querying GroundTruth for outcome simulation.
            It must NEVER be the diagnosis agent's prediction — that would
            create a self-fulfilling simulator where diagnosis errors are never
            penalised by the ground truth.
        """
        return self.execute_sync(
            event=event,
            decision=decision,
            guardrail=guardrail,
            true_failure_class=true_failure_class,
        )

    def execute_sync(
        self,
        event: PaymentFailureEvent,
        decision: StrategyDecision,
        guardrail: GuardrailDecision,
        true_failure_class: FailureClass | None = None,
    ) -> OutcomeResult:
        """Synchronous execution implementation with fail-closed authorization.

        Parameters
        ----------
        true_failure_class:
            The SIMULATOR's hidden ground-truth failure class, extracted from
            the original SimEvent before schema stripping.  Must NOT be the
            diagnosis agent's prediction.  When None (e.g. input was a raw
            PaymentFailureEvent, not a SimEvent), defaults to FailureClass.UNKNOWN
            so the simulator degrades gracefully rather than silently using a
            wrong class.
        """
        t_start = time.perf_counter()

        # -------------------------------------------------------------
        # Step 0: Input Object & Type Validation (Fail-Closed)
        # -------------------------------------------------------------
        if event is None or not getattr(event, "event_id", None):
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id="UNKNOWN",
                decision_id=getattr(decision, "decision_id", "UNKNOWN") if decision else "UNKNOWN",
                strategy_applied=RetryStrategy.no_action,
                status=OutcomeStatus.abandoned,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="INVALID_EVENT",
                completed_at=datetime.now(UTC),
            )

        if decision is None or not getattr(decision, "decision_id", None):
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id="UNKNOWN",
                strategy_applied=RetryStrategy.no_action,
                status=OutcomeStatus.abandoned,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="INVALID_DECISION",
                completed_at=datetime.now(UTC),
            )

        if guardrail is None or not hasattr(guardrail, "verdict"):
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id=decision.decision_id,
                strategy_applied=RetryStrategy.no_action,
                status=OutcomeStatus.abandoned,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="GUARDRAIL_MISSING_OR_MALFORMED",
                completed_at=datetime.now(UTC),
            )

        # -------------------------------------------------------------
        # Step 1: Entity Binding Verification (Fail-Closed)
        # -------------------------------------------------------------
        # The approved GuardrailDecision must strictly belong to the exact transaction
        # and decision being executed.
        guardrail_event_id = getattr(guardrail, "event_id", None)
        guardrail_decision_id = getattr(guardrail, "decision_id", None)
        decision_event_id = getattr(decision, "event_id", None)

        if (
            guardrail_event_id != event.event_id
            or guardrail_decision_id != decision.decision_id
            or decision_event_id != event.event_id
        ):
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id=decision.decision_id,
                strategy_applied=RetryStrategy.no_action,
                status=OutcomeStatus.abandoned,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="ENTITY_BINDING_MISMATCH",
                completed_at=datetime.now(UTC),
            )

        # -------------------------------------------------------------
        # Step 2: Action Validation (No Silent Defaulting)
        # -------------------------------------------------------------
        raw_action = getattr(decision, "selected_action", None)
        if not raw_action or not isinstance(raw_action, str) or not raw_action.strip():
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id=decision.decision_id,
                strategy_applied=RetryStrategy.no_action,
                status=OutcomeStatus.abandoned,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="INVALID_ACTION_EMPTY",
                completed_at=datetime.now(UTC),
            )

        # Parse action strictly against SimAction enum; NEVER silently default unknown actions
        try:
            action_enum = SimAction(raw_action.upper().strip())
        except (ValueError, KeyError, AttributeError):
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id=decision.decision_id,
                strategy_applied=RetryStrategy.no_action,
                status=OutcomeStatus.abandoned,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="INVALID_ACTION_UNRECOGNIZED",
                completed_at=datetime.now(UTC),
            )

        # Map to RetryStrategy
        try:
            strat_enum = RetryStrategy(action_enum.value.lower())
        except ValueError:
            try:
                strat_enum = RetryStrategy(raw_action.lower())
            except ValueError:
                strat_enum = RetryStrategy.no_action

        # -------------------------------------------------------------
        # Step 3: Strict Fail-Closed Verdict Authorization
        # -------------------------------------------------------------
        # ONLY GuardrailVerdict.approved may proceed to execution.
        # Any other state (blocked, escalate, unknown, malformed) MUST NOT execute.
        if guardrail.verdict != GuardrailVerdict.approved:
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            if guardrail.verdict == GuardrailVerdict.blocked:
                return OutcomeResult(
                    event_id=event.event_id,
                    decision_id=decision.decision_id,
                    strategy_applied=strat_enum,
                    status=OutcomeStatus.abandoned,
                    amount_recovered=0.0,
                    latency_ms=latency_ms,
                    gateway_response_code="GUARDRAIL_BLOCKED",
                    completed_at=datetime.now(UTC),
                )
            elif guardrail.verdict == GuardrailVerdict.escalate:
                return OutcomeResult(
                    event_id=event.event_id,
                    decision_id=decision.decision_id,
                    strategy_applied=strat_enum,
                    status=OutcomeStatus.pending,
                    amount_recovered=0.0,
                    latency_ms=latency_ms,
                    gateway_response_code="ESCALATED_HUMAN_REVIEW",
                    completed_at=datetime.now(UTC),
                )
            else:
                return OutcomeResult(
                    event_id=event.event_id,
                    decision_id=decision.decision_id,
                    strategy_applied=strat_enum,
                    status=OutcomeStatus.abandoned,
                    amount_recovered=0.0,
                    latency_ms=latency_ms,
                    gateway_response_code="GUARDRAIL_VERDICT_UNAUTHORIZED",
                    completed_at=datetime.now(UTC),
                )

        # Non-executable action handling (Human escalation cannot be financially executed)
        if action_enum == SimAction.HUMAN_ESCALATION:
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id=decision.decision_id,
                strategy_applied=strat_enum,
                status=OutcomeStatus.pending,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="ESCALATED_HUMAN_REVIEW",
                completed_at=datetime.now(UTC),
            )

        # -------------------------------------------------------------
        # Step 4: Idempotency Enforcement (Defense-in-Depth - Atomic check-and-consume)
        # -------------------------------------------------------------
        if self.enforce_idempotency:
            idemp_key = f"{event.payment_id}:{decision.decision_id}:{guardrail.guardrail_id}"
            with self._lock:
                if idemp_key in self._executed_keys:
                    is_dup = True
                else:
                    self._executed_keys.add(idemp_key)
                    is_dup = False

            if is_dup:
                latency_ms = int((time.perf_counter() - t_start) * 1000)
                return OutcomeResult(
                    event_id=event.event_id,
                    decision_id=decision.decision_id,
                    strategy_applied=strat_enum,
                    status=OutcomeStatus.abandoned,
                    amount_recovered=0.0,
                    latency_ms=latency_ms,
                    gateway_response_code="DUPLICATE_EXECUTION_BLOCKED",
                    completed_at=datetime.now(UTC),
                )

        # -------------------------------------------------------------
        # Step 5: Simulate Execution via OutcomeEngine (Thread-Safe & Fail-Closed)
        # -------------------------------------------------------------
        # IMPORTANT: Use true_failure_class (simulator ground truth) — NOT the
        # diagnosis agent's prediction.  Using the diagnosed class would cause
        # the simulator to evaluate outcomes against the model's guess rather
        # than the actual hidden environment state, making diagnosis errors
        # invisible and benchmark results meaningless.
        fc = true_failure_class or FailureClass.UNKNOWN
        amount = event.amount
        vt = ValueTier.LOW if amount < 500 else (ValueTier.MID if amount < 5000 else ValueTier.HIGH)

        sim_event = SimEvent(
            event_id=event.event_id,
            transaction_id=event.payment_id,
            customer_id=event.merchant_id,
            amount=amount,
            currency=event.currency,
            payment_method=SimPaymentMethod.CARD,
            raw_gateway_error=event.failure_code,
            previous_attempts=event.attempt_number,
            customer_segment=CustomerSegment.REGULAR,
            value_tier=vt,
            normalised_failure_class=fc,  # ← true ground-truth class, not diagnosis
        )

        try:
            sim_outcome = self.outcome_engine.simulate_outcome(sim_event, action_enum)
        except NetworkTimeoutException:
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            return OutcomeResult(
                event_id=event.event_id,
                decision_id=decision.decision_id,
                strategy_applied=strat_enum,
                status=OutcomeStatus.failure,
                amount_recovered=0.0,
                latency_ms=latency_ms,
                gateway_response_code="ERR_ADAPTER_NETWORK_TIMEOUT",
                completed_at=datetime.now(UTC),
            )

        latency_ms = int((time.perf_counter() - t_start) * 1000)

        status = OutcomeStatus.success if sim_outcome.success else OutcomeStatus.failure
        recovered_amount = event.amount if sim_outcome.success else 0.0
        resp_code = "200_OK_RECOVERED" if sim_outcome.success else "GATEWAY_DECLINE_RETRY_FAILED"

        return OutcomeResult(
            event_id=event.event_id,
            decision_id=decision.decision_id,
            strategy_applied=strat_enum,
            status=status,
            amount_recovered=recovered_amount,
            latency_ms=latency_ms,
            gateway_response_code=resp_code,
            completed_at=datetime.now(UTC),
        )
