"""
RevPilot End-to-End Recovery Pipeline
=====================================

Coordinates the full payment recovery lifecycle:
  Event
  → Schema Validation
  → Diagnosis (Root-cause classification)
  → Context Creation (FailureClass + ValueTier)
  → Strategy (Segmented Thompson Sampling EV Optimization)
  → Guardrail (Deterministic Financial Safety Gatekeeper)
  → Execution Adapter (Simulation-backed execution)
  → Outcome Processing
  → Statistical Update (Idempotent Bayesian Updates)
  → Reflection (Batch explanation & policy shift tracking)
  → Audit Trail (Structured audit record emitted at every stage)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from backend.agents.diagnosis import DiagnosisAgent
from backend.agents.reflection import OutcomeObservation, ReflectionAgent
from backend.bandit.thompson import ThompsonSamplingBandit
from backend.models.schemas import (
    AuditEvent,
    BatchReflectionRecord,
    DiagnosisResult,
    ExceptionRecord,
    FailureClass,
    GuardrailDecision,
    GuardrailVerdict,
    OutcomeResult,
    OutcomeStatus,
    PaymentFailureEvent,
    PaymentMethod,
    StrategyDecision,
    ValueTier,
)
from backend.services.audit import AuditService
from backend.services.execution import ExecutionService
from backend.services.guardrail import GuardrailEngine
from backend.simulator.types import SimAction, SimEvent


# ---------------------------------------------------------------------------
# Pipeline Result & Batch Summary Models
# ---------------------------------------------------------------------------


class EventPipelineResult(BaseModel):
    """Result of processing a single payment failure event through the full pipeline."""

    event_id: str
    event: Optional[PaymentFailureEvent] = None
    stage_reached: str
    success: bool
    status: str
    failure_class: Optional[FailureClass] = None
    context: Optional[str] = None
    selected_action: Optional[str] = None
    guardrail_verdict: Optional[GuardrailVerdict] = None
    amount_recovered: float = 0.0
    execution_cost: float = 0.0
    net_value: float = 0.0
    diagnosis: Optional[DiagnosisResult] = None
    strategy: Optional[StrategyDecision] = None
    guardrail: Optional[GuardrailDecision] = None
    outcome: Optional[OutcomeResult] = None
    audit_events: list[AuditEvent] = Field(default_factory=list)
    error_message: Optional[str] = None
    total_latency_ms: float = 0.0


class PipelineBatchSummary(BaseModel):
    """Aggregated results from processing a batch of payment failure events."""

    batch_id: str = Field(default_factory=lambda: f"batch_{uuid.uuid4().hex[:8]}")
    total_events: int = 0
    processed_events: int = 0
    successful_recoveries: int = 0
    blocked_count: int = 0
    escalated_count: int = 0
    failed_executions: int = 0
    unknown_diagnosis_count: int = 0
    exception_count: int = 0
    recovery_rate: float = 0.0
    gross_recovered_revenue: float = 0.0
    total_cost: float = 0.0
    net_recovered_revenue: float = 0.0
    avg_latency_ms: float = 0.0
    throughput_eps: float = 0.0
    reflection_summary: Optional[str] = None
    reflection_record: Optional[BatchReflectionRecord] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# RevPilot Pipeline Engine
# ---------------------------------------------------------------------------


class RevPilotPipeline:
    """The central orchestrator for the RevPilot recovery loop."""

    def __init__(
        self,
        diagnosis_agent: Optional[DiagnosisAgent] = None,
        bandit: Optional[ThompsonSamplingBandit] = None,
        guardrail_engine: Optional[GuardrailEngine] = None,
        execution_service: Optional[ExecutionService] = None,
        reflection_agent: Optional[ReflectionAgent] = None,
        audit_service: Optional[AuditService] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.diagnosis_agent = diagnosis_agent or DiagnosisAgent()
        self.bandit = bandit or ThompsonSamplingBandit(seed=seed)
        self.guardrail_engine = guardrail_engine or GuardrailEngine()
        self.execution_service = execution_service or ExecutionService(seed=seed)
        self.reflection_agent = reflection_agent or ReflectionAgent(bandit=self.bandit, persistence_path=None)
        self.audit_service = audit_service or AuditService()

    def process_event(
        self,
        event_data: PaymentFailureEvent | SimEvent | dict[str, Any],
    ) -> EventPipelineResult:
        """Process a single event through all 10 stages of the pipeline with audit logging."""
        t_pipeline_start = time.perf_counter()
        event_audits: list[AuditEvent] = []
        event_id = str(getattr(event_data, "event_id", uuid.uuid4()))

        # Helper to emit structured audit event at each stage
        def audit_stage(
            stage: str,
            input_ref: Optional[str],
            output_ref: Optional[str],
            decision: Optional[str],
            reason: Optional[str],
            status: str,
            start_time: float,
            details: Optional[dict[str, Any]] = None,
        ) -> AuditEvent:
            latency = (time.perf_counter() - start_time) * 1000.0
            ae = AuditEvent(
                event_id=event_id,
                stage=stage,
                input_reference=input_ref,
                output_reference=output_ref,
                decision=decision,
                reason=reason,
                latency_ms=round(latency, 4),
                status=status,
                details=details or {},
            )
            self.audit_service.log(ae)
            event_audits.append(ae)
            return ae

        # -------------------------------------------------------------
        # Stage 1: Schema Validation
        # -------------------------------------------------------------
        # INFORMATION BARRIER: The true failure class lives on the incoming
        # SimEvent.  We capture it here — before stripping to PaymentFailureEvent
        # — so that the simulator/outcome layer can later evaluate outcomes
        # against the actual ground-truth environment, not the model's prediction.
        # This variable must NEVER be passed to DiagnosisAgent or StrategyEngine.
        _true_failure_class: Optional[Any] = None  # stays None for raw PFE/dict inputs

        t_s1 = time.perf_counter()
        if isinstance(event_data, PaymentFailureEvent):
            event = event_data
            # No hidden ground truth available for raw PaymentFailureEvent inputs.
            # The simulator will fall back to FailureClass.UNKNOWN in execute_sync.
        elif isinstance(event_data, SimEvent):
            # Capture the hidden true class BEFORE stripping.
            _true_failure_class = event_data.normalised_failure_class
            event = PaymentFailureEvent(
                event_id=event_data.event_id,
                payment_id=event_data.transaction_id,
                merchant_id=event_data.customer_id,
                amount=event_data.amount,
                currency=event_data.currency,
                payment_method=PaymentMethod.upi,
                failure_code=event_data.raw_gateway_error,
                raw_gateway_error=event_data.raw_gateway_error,
                attempt_number=max(1, event_data.previous_attempts + 1),
            )
        elif isinstance(event_data, dict):
            try:
                event = PaymentFailureEvent(**event_data)
            except ValidationError as e:
                audit_stage(
                    stage="schema_validation",
                    input_ref="raw_dict",
                    output_ref=None,
                    decision="VALIDATION_FAILED",
                    reason=str(e),
                    status="failed",
                    start_time=t_s1,
                )
                return EventPipelineResult(
                    event_id=event_id,
                    stage_reached="schema_validation",
                    success=False,
                    status="validation_error",
                    error_message=str(e),
                    audit_events=event_audits,
                    total_latency_ms=(time.perf_counter() - t_pipeline_start) * 1000.0,
                )
        else:
            raise TypeError(f"Unsupported event type: {type(event_data)}")

        event_id = event.event_id
        audit_stage(
            stage="schema_validation",
            input_ref=event.payment_id,
            output_ref=event.event_id,
            decision="VALIDATED",
            reason=f"Valid PaymentFailureEvent (Amount: ₹{event.amount})",
            status="success",
            start_time=t_s1,
        )

        # -------------------------------------------------------------
        # Stage 2: Diagnosis
        # -------------------------------------------------------------
        t_s2 = time.perf_counter()
        raw_error = event.raw_gateway_error or event.failure_code or ""
        diagnosis = self.diagnosis_agent.diagnose_raw(
            raw_error=raw_error,
            event_id=event.event_id,
            metadata=event.metadata,
        )
        audit_stage(
            stage="diagnosis",
            input_ref=raw_error[:40],
            output_ref=diagnosis.diagnosis_id,
            decision=diagnosis.normalized_failure_class.value,
            reason=f"Source: {diagnosis.diagnosis_source} | Conf: {diagnosis.confidence:.2f} | {diagnosis.explanation[:60]}",
            status="success" if diagnosis.normalized_failure_class != FailureClass.UNKNOWN else "unknown",
            start_time=t_s2,
            details={
                "diagnosis_source": diagnosis.diagnosis_source,
                "model_provider": diagnosis.model_provider,
                "confidence": diagnosis.confidence,
                "retryability": diagnosis.retryability,
                "normalized_failure_class": diagnosis.normalized_failure_class.value,
                "fallback_reason": diagnosis.fallback_reason,
                "latency_ms": diagnosis.latency_ms,
                "engine": diagnosis.engine,
            },
        )



        # -------------------------------------------------------------
        # Stage 3: Context Creation
        # -------------------------------------------------------------
        t_s3 = time.perf_counter()
        amount = event.amount
        value_tier = ValueTier.LOW if amount < 500 else (ValueTier.MID if amount < 5000 else ValueTier.HIGH)
        context_str = f"{diagnosis.normalized_failure_class.value}+{value_tier.value}"
        audit_stage(
            stage="context_creation",
            input_ref=f"{diagnosis.normalized_failure_class.value}, ₹{amount}",
            output_ref=context_str,
            decision=context_str,
            reason=f"Context mapped to {context_str}",
            status="success",
            start_time=t_s3,
        )

        # -------------------------------------------------------------
        # Stage 4: Strategy Selection (Thompson Sampling)
        # -------------------------------------------------------------
        t_s4 = time.perf_counter()
        strategy = self.bandit.select_action(
            event_id=event.event_id,
            failure_class=diagnosis.normalized_failure_class,
            value_tier=value_tier,
            amount=event.amount,
            diagnosis_id=diagnosis.diagnosis_id,
        )
        audit_stage(
            stage="strategy",
            input_ref=context_str,
            output_ref=strategy.decision_id,
            decision=strategy.selected_action,
            reason=strategy.reasoning,
            status="success",
            start_time=t_s4,
            details={
                "selected_ev": strategy.selected_ev,
                "exploration_flag": strategy.exploration_flag,
                "confidence": strategy.confidence,
            },
        )

        # -------------------------------------------------------------
        # Stage 5: Guardrail Evaluation
        # -------------------------------------------------------------
        t_s5 = time.perf_counter()
        guardrail = self.guardrail_engine.evaluate(
            event=event,
            decision=strategy,
            diagnosis=diagnosis,
        )
        audit_stage(
            stage="guardrail",
            input_ref=strategy.decision_id,
            output_ref=guardrail.guardrail_id,
            decision=guardrail.verdict.value.upper(),
            reason=guardrail.reason,
            status=guardrail.verdict.value,
            start_time=t_s5,
            details={"triggered_rules": guardrail.rules_triggered},
        )

        # -------------------------------------------------------------
        # Stage 6: Execution Adapter
        # -------------------------------------------------------------
        # Pass the SIMULATOR'S true hidden failure class (_true_failure_class)
        # NOT the diagnosis agent's prediction.  The outcome engine must
        # evaluate success probability against the actual ground-truth
        # environment, otherwise misdiagnosis errors become invisible.
        t_s6 = time.perf_counter()
        outcome = self.execution_service.execute_sync(
            event=event,
            decision=strategy,
            guardrail=guardrail,
            true_failure_class=_true_failure_class,
        )
        audit_stage(
            stage="execution",
            input_ref=guardrail.guardrail_id,
            output_ref=outcome.outcome_id,
            decision=outcome.status.value.upper(),
            reason=f"Gateway response: {outcome.gateway_response_code}",
            status=outcome.status.value,
            start_time=t_s6,
            details={"amount_recovered": outcome.amount_recovered},
        )

        # -------------------------------------------------------------
        # Stage 7 & 8: Outcome Analysis & Statistical Update
        # -------------------------------------------------------------
        t_s7 = time.perf_counter()
        is_success = (outcome.status == OutcomeStatus.success)
        econ = self.bandit.economics.get(strategy.selected_action)
        action_cost = (econ.api_cost + econ.friction_cost) if econ else 5.0
        net_val = (outcome.amount_recovered - action_cost) if is_success else -action_cost

        # Only update statistical bandit on legitimately executed actions (approved path with executed outcome)
        executed_successfully_or_failed = (
            guardrail.verdict == GuardrailVerdict.approved
            and outcome.status in {OutcomeStatus.success, OutcomeStatus.failure}
            and outcome.gateway_response_code in {"200_OK_RECOVERED", "GATEWAY_DECLINE_RETRY_FAILED", "ERR_ADAPTER_NETWORK_TIMEOUT"}
        )

        if executed_successfully_or_failed:
            obs = OutcomeObservation(
                event_id=event.event_id,
                context=context_str,
                action=strategy.selected_action,
                success=is_success,
                recovered_value=outcome.amount_recovered,
                cost=action_cost,
                outcome_id=outcome.outcome_id,
            )
            self.reflection_agent.updater.process_observations([obs])
            audit_stage(
                stage="statistical_update",
                input_ref=outcome.outcome_id,
                output_ref=context_str,
                decision="UPDATED_BETA",
                reason=f"Observed success={is_success} on {strategy.selected_action}",
                status="success",
                start_time=t_s7,
            )
        else:
            audit_stage(
                stage="statistical_update",
                input_ref=outcome.outcome_id,
                output_ref=context_str,
                decision="SKIPPED",
                reason=f"No statistical update for non-executed outcome: {guardrail.verdict.value} / {outcome.gateway_response_code}",
                status="skipped",
                start_time=t_s7,
            )

        total_latency = (time.perf_counter() - t_pipeline_start) * 1000.0

        return EventPipelineResult(
            event_id=event.event_id,
            event=event,
            stage_reached="completed",
            success=is_success,
            status=outcome.status.value,
            failure_class=diagnosis.normalized_failure_class,
            context=context_str,
            selected_action=strategy.selected_action,
            guardrail_verdict=guardrail.verdict,
            amount_recovered=outcome.amount_recovered,
            execution_cost=action_cost if executed_successfully_or_failed else 0.0,
            net_value=net_val if executed_successfully_or_failed else 0.0,
            diagnosis=diagnosis,
            strategy=strategy,
            guardrail=guardrail,
            outcome=outcome,
            audit_events=event_audits,
            total_latency_ms=round(total_latency, 4),
        )

    def process_batch(
        self,
        events: list[PaymentFailureEvent | SimEvent | dict[str, Any]],
        batch_id: Optional[str] = None,
    ) -> PipelineBatchSummary:
        """Process a batch of events (e.g. 500 records) resiliently, generating batch reflection and summary."""
        bid = batch_id or f"batch_{uuid.uuid4().hex[:8]}"
        wall_start = time.perf_counter()

        results: list[EventPipelineResult] = []
        observations_for_reflection: list[OutcomeObservation] = []
        exceptions: list[ExceptionRecord] = []

        for i, raw_item in enumerate(events):
            try:
                res = self.process_event(raw_item)
                results.append(res)

                if res.guardrail_verdict == GuardrailVerdict.approved and res.outcome is not None:
                    observations_for_reflection.append(
                        OutcomeObservation(
                            event_id=res.event_id,
                            context=res.context or "UNKNOWN+MID",
                            action=res.selected_action,
                            success=res.success,
                            recovered_value=res.amount_recovered,
                            cost=res.execution_cost,
                            outcome_id=res.outcome.outcome_id,
                        )
                    )
            except Exception as e:
                # Resilient error recovery: record exception and continue processing batch
                ex_rec = ExceptionRecord(
                    event_id=str(getattr(raw_item, "event_id", f"item_{i}")),
                    component="pipeline_engine",
                    exception_type=type(e).__name__,
                    message=str(e),
                    severity="error",
                    handled=True,
                    fallback_action="skip_and_continue",
                )
                exceptions.append(ex_rec)

        wall_elapsed_s = time.perf_counter() - wall_start

        # Stage 9: Batch Reflection (if observations were executed)
        reflection_record: Optional[BatchReflectionRecord] = None
        if observations_for_reflection:
            reflection_record = self.reflection_agent.reflect_batch(
                observations=observations_for_reflection,
                batch_id=bid,
                apply_updates=False,
            )

        processed = len(results)
        successful = sum(1 for r in results if r.success)
        blocked = sum(1 for r in results if r.guardrail_verdict == GuardrailVerdict.blocked)
        escalated = sum(1 for r in results if r.guardrail_verdict == GuardrailVerdict.escalate)
        failed = sum(1 for r in results if r.status == "failure")
        unknown_diag = sum(1 for r in results if r.failure_class == FailureClass.UNKNOWN)

        gross_revenue = sum(r.amount_recovered for r in results)
        total_cost = sum(r.execution_cost for r in results)
        net_revenue = gross_revenue - total_cost

        rec_rate = (successful / processed) if processed > 0 else 0.0
        avg_lat = (sum(r.total_latency_ms for r in results) / processed) if processed > 0 else 0.0
        throughput = (processed / wall_elapsed_s) if wall_elapsed_s > 0 else 0.0

        ref_summary = (
            reflection_record.learning_summary
            if reflection_record
            else f"Batch {bid}: {processed} events processed, {successful} recovered."
        )

        return PipelineBatchSummary(
            batch_id=bid,
            total_events=len(events),
            processed_events=processed,
            successful_recoveries=successful,
            blocked_count=blocked,
            escalated_count=escalated,
            failed_executions=failed,
            unknown_diagnosis_count=unknown_diag,
            exception_count=len(exceptions),
            recovery_rate=round(rec_rate, 4),
            gross_recovered_revenue=round(gross_revenue, 2),
            total_cost=round(total_cost, 2),
            net_recovered_revenue=round(net_revenue, 2),
            avg_latency_ms=round(avg_lat, 4),
            throughput_eps=round(throughput, 2),
            reflection_summary=ref_summary,
            reflection_record=reflection_record,
        )
