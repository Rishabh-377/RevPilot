"""
Non-Stationary Simulator & Experiment Runner
============================================

Extends the RevPilot simulator to model non-stationary payment environments:
  Phase A: Normal gateway reliability.
  Phase B: An environmental shift causes a specific gateway/context to degrade.

The Strategy Engine is NEVER informed of the hidden probability shift.
It observes only the outcomes and must adapt its Bayesian posterior and
policy autonomously.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.reflection import OutcomeObservation, ReflectionAgent
from backend.bandit.thompson import ThompsonSamplingBandit
from backend.models.schemas import FailureClass, ValueTier
from backend.services.execution import ExecutionService
from backend.services.guardrail import GuardrailEngine
from backend.services.pipeline import RevPilotPipeline
from backend.simulator.event_generator import EventGenerator
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import CustomerSegment, SimAction, SimEvent, SimPaymentMethod


# ---------------------------------------------------------------------------
# Non-Stationary Data Models
# ---------------------------------------------------------------------------


class PolicySnapshot(BaseModel):
    """Snapshot of policy arm estimates and preferred action for a context."""

    context: str
    preferred_action: str
    posterior_mean: float
    alpha: float
    beta: float
    expected_value: float


class EnvironmentShiftConfig(BaseModel):
    """Specification of the hidden ground-truth environment shift."""

    target_failure_class: FailureClass = FailureClass.TIMEOUT_TRANSIENT
    target_value_tier: ValueTier = ValueTier.MID
    action_degraded: SimAction = SimAction.IMMEDIATE_RETRY
    prob_before: float = 0.80
    prob_after: float = 0.12
    description: str = (
        "Downstream gateway queue congestion causes immediate retries to fail, "
        "while delayed retries remain highly effective."
    )


class NonStationaryBenchmarkReport(BaseModel):
    """Full report of the non-stationary adaptation benchmark."""

    benchmark_id: str
    target_context: str
    phase_a_records: int
    phase_b_records: int
    policy_before: PolicySnapshot
    environment_shift: EnvironmentShiftConfig
    policy_after: PolicySnapshot
    adaptation_verified: bool
    policy_shifted: bool
    posterior_delta: float
    learning_statement: str


# ---------------------------------------------------------------------------
# Non-Stationary Experiment Runner
# ---------------------------------------------------------------------------


def run_non_stationary_experiment(
    phase_a_records: int = 150,
    phase_b_records: int = 150,
    seed: int = 20260821,
    shift_config: Optional[EnvironmentShiftConfig] = None,
) -> NonStationaryBenchmarkReport:
    """Execute a chronological non-stationary experiment over Phase A and Phase B."""
    shift = shift_config or EnvironmentShiftConfig()
    target_ctx_str = f"{shift.target_failure_class.value}+{shift.target_value_tier.value}"

    # Determine representative transaction amount and error string
    if shift.target_value_tier == ValueTier.LOW:
        amount = 300.0
    elif shift.target_value_tier == ValueTier.MID:
        amount = 1500.0
    else:
        amount = 8000.0

    raw_error_map = {
        FailureClass.TIMEOUT_TRANSIENT: "bank response timed out after 30000ms",
        FailureClass.AUTH_BLOCKED: "3ds authentication failed / otp rejected",
        FailureClass.ISSUER_DECLINE: "card issuer declined transaction",
        FailureClass.INFRA_OUTAGE: "gateway 503 service unavailable",
        FailureClass.HARD_FUNDS_ISSUE: "insufficient balance in customer account",
        FailureClass.CUSTOMER_ABANDONMENT: "customer closed payment window",
    }
    raw_err = raw_error_map.get(shift.target_failure_class, "bank response timed out")

    # 1. Initialize normal Ground Truth (Phase A)
    gt_normal = GroundTruth()
    outcome_engine_a = OutcomeEngine(ground_truth=gt_normal, seed=seed)
    pipeline = RevPilotPipeline(seed=seed)
    # Inject Phase A outcome engine
    pipeline.execution_service.outcome_engine = outcome_engine_a

    # Generate Phase A events specifically targeting the context
    events_a: list[SimEvent] = []
    for i in range(phase_a_records):
        events_a.append(
            SimEvent(
                event_id=f"phase_a_ev_{i}",
                transaction_id=f"txn_a_{i}",
                customer_id=f"cust_a_{i % 20}",
                amount=amount,
                currency="INR",
                payment_method=SimPaymentMethod.UPI,
                customer_segment=CustomerSegment.REGULAR,
                raw_gateway_error=raw_err,
                previous_attempts=0,
                value_tier=shift.target_value_tier,
                normalised_failure_class=shift.target_failure_class,
            )
        )

    # Execute Phase A chronologically
    for ev in events_a:
        pipeline.process_event(ev)

    # Snapshot Policy Before (End of Phase A)
    best_act_a, best_ev_a = pipeline.bandit.get_best_exploit_action(target_ctx_str, amount=amount)
    arm_a = pipeline.bandit.state.get_arm(target_ctx_str, best_act_a)
    policy_before = PolicySnapshot(
        context=target_ctx_str,
        preferred_action=best_act_a,
        posterior_mean=round(arm_a.posterior_mean, 4),
        alpha=round(arm_a.alpha, 2),
        beta=round(arm_a.beta, 2),
        expected_value=round(best_ev_a, 2),
    )

    # 2. Transition to Phase B (Degraded Ground Truth)
    # The pipeline and bandit ARE NOT reset and ARE NOT notified of this shift
    gt_degraded = gt_normal.with_override(
        failure_class=shift.target_failure_class,
        value_tier=shift.target_value_tier,
        action=shift.action_degraded,
        probability=shift.prob_after,
    )
    outcome_engine_b = OutcomeEngine(ground_truth=gt_degraded, seed=seed + 100)
    pipeline.execution_service.outcome_engine = outcome_engine_b

    # Generate Phase B events (same failure pattern, degraded gateway reality)
    events_b: list[SimEvent] = []
    for j in range(phase_b_records):
        events_b.append(
            SimEvent(
                event_id=f"phase_b_ev_{j}",
                transaction_id=f"txn_b_{j}",
                customer_id=f"cust_b_{j % 20}",
                amount=amount,
                currency="INR",
                payment_method=SimPaymentMethod.UPI,
                customer_segment=CustomerSegment.REGULAR,
                raw_gateway_error=raw_err,
                previous_attempts=0,
                value_tier=shift.target_value_tier,
                normalised_failure_class=shift.target_failure_class,
            )
        )

    observations_b: list[OutcomeObservation] = []
    # Execute Phase B chronologically
    for ev in events_b:
        res = pipeline.process_event(ev)
        if res.guardrail_verdict.value == "approved" and res.outcome is not None:
            observations_b.append(
                OutcomeObservation(
                    event_id=res.event_id,
                    context=target_ctx_str,
                    action=res.selected_action,
                    success=res.success,
                    recovered_value=res.amount_recovered,
                    cost=res.execution_cost,
                    outcome_id=res.outcome.outcome_id,
                )
            )

    # Snapshot Policy After (End of Phase B)
    best_act_b, best_ev_b = pipeline.bandit.get_best_exploit_action(target_ctx_str, amount=amount)
    arm_b = pipeline.bandit.state.get_arm(target_ctx_str, best_act_b)
    arm_degraded = pipeline.bandit.state.get_arm(target_ctx_str, shift.action_degraded.value)

    policy_after = PolicySnapshot(
        context=target_ctx_str,
        preferred_action=best_act_b,
        posterior_mean=round(arm_b.posterior_mean, 4),
        alpha=round(arm_b.alpha, 2),
        beta=round(arm_b.beta, 2),
        expected_value=round(best_ev_b, 2),
    )

    # Reflection Agent Explanation
    ref_record = pipeline.reflection_agent.reflect_batch(
        observations=observations_b,
        batch_id="phase_b_adaptation",
        apply_updates=False,
    )

    policy_shifted = (policy_before.preferred_action != policy_after.preferred_action)
    posterior_delta = arm_degraded.posterior_mean - policy_before.posterior_mean
    adaptation_verified = (
        arm_degraded.posterior_mean < policy_before.posterior_mean or policy_shifted
    )

    statement = (
        f"In context {target_ctx_str}, environment shifted P({shift.action_degraded.value}) from "
        f"{shift.prob_before:.2f} to {shift.prob_after:.2f}. Strategy engine observed outcomes, "
        f"driving posterior mean from {policy_before.posterior_mean:.2f} down to {arm_degraded.posterior_mean:.2f}. "
        f"Optimal policy adapted from {policy_before.preferred_action} to {policy_after.preferred_action}."
    )

    return NonStationaryBenchmarkReport(
        benchmark_id="non_stationary_adaptation_bench",
        target_context=target_ctx_str,
        phase_a_records=phase_a_records,
        phase_b_records=phase_b_records,
        policy_before=policy_before,
        environment_shift=shift,
        policy_after=policy_after,
        adaptation_verified=adaptation_verified,
        policy_shifted=policy_shifted,
        posterior_delta=round(posterior_delta, 4),
        learning_statement=statement,
    )
