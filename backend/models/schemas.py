"""
RevPilot Pydantic Contracts
===========================

Every important boundary between components uses typed schemas.
These contracts define the data flowing through the core recovery loop:

    Payment Failure → Diagnosis → Strategy → Guardrail → Execution → Outcome → Audit
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FailureReason(str, Enum):
    """Categorization of why a payment failed."""

    insufficient_funds = "insufficient_funds"
    card_expired = "card_expired"
    bank_declined = "bank_declined"
    network_error = "network_error"
    fraud_suspected = "fraud_suspected"
    authentication_failed = "authentication_failed"
    limit_exceeded = "limit_exceeded"
    invalid_card = "invalid_card"
    issuer_unavailable = "issuer_unavailable"
    duplicate_transaction = "duplicate_transaction"


class FailureClass(str, Enum):
    """Normalized failure taxonomy for diagnosis."""

    TIMEOUT_TRANSIENT = "TIMEOUT_TRANSIENT"
    HARD_FUNDS_ISSUE = "HARD_FUNDS_ISSUE"
    ISSUER_DECLINE = "ISSUER_DECLINE"
    AUTH_BLOCKED = "AUTH_BLOCKED"
    INFRA_OUTAGE = "INFRA_OUTAGE"
    DUPLICATE = "DUPLICATE"
    CUSTOMER_ABANDONMENT = "CUSTOMER_ABANDONMENT"
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"
    UNKNOWN = "UNKNOWN"


class RiskLevel(str, Enum):
    """Risk level assessed during diagnosis."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ValueTier(str, Enum):
    """Transaction value tier."""

    LOW = "LOW"
    MID = "MID"
    HIGH = "HIGH"


class RetryStrategy(str, Enum):
    """Recovery strategies that the bandit optimizer can select."""

    immediate_retry = "immediate_retry"
    delayed_retry = "delayed_retry"
    amount_split = "amount_split"
    card_switch = "card_switch"
    downgrade_retry = "downgrade_retry"
    time_shift = "time_shift"
    no_action = "no_action"
    payment_link = "payment_link"
    switch_method = "switch_method"
    human_escalation = "human_escalation"


class PaymentMethod(str, Enum):
    """Payment instrument type."""

    credit_card = "credit_card"
    debit_card = "debit_card"
    upi = "upi"
    net_banking = "net_banking"
    wallet = "wallet"


class GuardrailVerdict(str, Enum):
    """Outcome of deterministic guardrail evaluation."""

    approved = "approved"
    blocked = "blocked"
    escalate = "escalate"


class OutcomeStatus(str, Enum):
    """Status of a recovery attempt."""

    success = "success"
    failure = "failure"
    pending = "pending"
    abandoned = "abandoned"


class AuditAction(str, Enum):
    """Actions recorded in the audit trail."""

    retry_initiated = "retry_initiated"
    retry_succeeded = "retry_succeeded"
    retry_failed = "retry_failed"
    guardrail_blocked = "guardrail_blocked"
    strategy_selected = "strategy_selected"
    diagnosis_completed = "diagnosis_completed"
    escalation_triggered = "escalation_triggered"


# ---------------------------------------------------------------------------
# Core Contracts
# ---------------------------------------------------------------------------


class PaymentFailureEvent(BaseModel):
    """Input event representing a failed payment that enters the recovery pipeline."""

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this failure event",
    )
    payment_id: str = Field(..., description="Razorpay payment ID (e.g. pay_XXXXXX)")
    merchant_id: str = Field(..., description="Merchant identifier")
    amount: float = Field(..., gt=0, description="Transaction amount in minor currency units")
    currency: str = Field(default="INR", description="ISO 4217 currency code")
    payment_method: PaymentMethod = Field(..., description="Payment instrument used")
    failure_reason: FailureReason = Field(
        default=FailureReason.bank_declined, description="Classified failure reason"
    )
    failure_code: str = Field(..., description="Raw error code from payment gateway")
    raw_gateway_error: Optional[str] = Field(
        default=None, description="Optional raw error string if different from failure_code"
    )
    card_last4: Optional[str] = Field(default=None, description="Last 4 digits of card number")
    card_network: Optional[str] = Field(
        default=None, description="Card network (visa, mastercard, rupay, etc.)"
    )
    bank_code: Optional[str] = Field(default=None, description="Issuing bank IFSC or code")
    attempt_number: int = Field(..., ge=1, description="Which attempt this is (1 = first try)")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the failure occurred"
    )
    metadata: dict = Field(default_factory=dict, description="Arbitrary additional context")


class DiagnosisResult(BaseModel):
    """Output from the Diagnosis Agent — LLM-assisted root cause analysis."""

    diagnosis_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique diagnosis identifier",
    )
    event_id: str = Field(..., description="Reference to the originating PaymentFailureEvent")
    normalized_failure_class: FailureClass = Field(
        default=FailureClass.UNKNOWN,
        description="Normalized failure class in internal taxonomy",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score of the diagnosis (0-1)"
    )
    retryability: bool = Field(
        default=True, description="Whether this failure is worth retrying"
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW, description="Assessed financial/fraud risk level"
    )
    evidence: list[str] = Field(
        default_factory=list, description="Extracted keywords, tokens, or raw patterns"
    )
    explanation: str = Field(
        default="", description="Human-readable root cause explanation"
    )
    diagnosis_source: str = Field(
        default="DETERMINISTIC_RULE",
        description="Source of diagnosis: 'LLM' | 'DETERMINISTIC_FALLBACK' | 'DETERMINISTIC_RULE'",
    )
    model_provider: Optional[str] = Field(
        default=None, description="Model and provider identifier if LLM was used"
    )
    fallback_reason: Optional[str] = Field(
        default=None, description="Reason for fallback if LLM failed"
    )
    latency_ms: float = Field(
        default=0.0, description="Latency of diagnosis operation in milliseconds"
    )
    engine: str = Field(
        default="deterministic_rule",
        description="Diagnosis engine used (e.g. 'llm_gemini', 'deterministic_fallback', 'deterministic_rule')",
    )

    # Backwards-compatibility fields & aliases
    failure_category: Optional[FailureReason] = Field(
        default=None, description="Legacy FailureReason mapping"
    )
    is_retryable: Optional[bool] = Field(
        default=None, description="Legacy alias for retryability"
    )
    reasoning: Optional[str] = Field(
        default=None, description="Legacy alias for explanation"
    )
    context_signals: dict = Field(
        default_factory=dict,
        description="Contextual signals (time_of_day, merchant_risk_score, etc.)",
    )
    diagnosed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When diagnosis was produced"
    )


    def model_post_init(self, __context: Any) -> None:
        if self.is_retryable is not None and "retryability" not in self.model_fields_set:
            self.retryability = self.is_retryable
        elif self.is_retryable is None:
            self.is_retryable = self.retryability

        if self.reasoning is not None and "explanation" not in self.model_fields_set:
            self.explanation = self.reasoning
        elif self.reasoning is None:
            self.reasoning = self.explanation


class StrategyDecision(BaseModel):
    """Output from the Segmented Thompson Sampling Bandit — recovery strategy selection."""

    decision_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique decision identifier",
    )
    event_id: str = Field(..., description="Reference to the originating payment/failure event")
    context: str = Field(
        default="", description="Context key (normalized_failure_class + value_tier)"
    )
    candidate_actions: list[str] = Field(
        default_factory=list, description="List of candidate actions evaluated"
    )
    sampled_probabilities: dict[str, float] = Field(
        default_factory=dict, description="Action -> sampled success probability theta ~ Beta(alpha, beta)"
    )
    posterior_means: dict[str, float] = Field(
        default_factory=dict, description="Action -> posterior mean alpha / (alpha + beta)"
    )
    ev_scores: dict[str, float] = Field(
        default_factory=dict, description="Action -> Expected Value in INR after costs & discounting"
    )
    selected_action: str = Field(
        default="", description="The winning action selected by the bandit"
    )
    selected_ev: float = Field(
        default=0.0, description="Expected value of the selected winning action (INR)"
    )
    exploration_flag: bool = Field(
        default=False,
        description="True if chosen action differed from the highest posterior mean / exploit action",
    )
    reasoning: str = Field(
        default="", description="Mathematical explanation for why this action was selected"
    )
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Confidence in the winning action / posterior certainty (0-1)"
    )

    # Backwards-compatibility aliases
    diagnosis_id: Optional[str] = Field(
        default=None, description="Legacy diagnosis ID reference"
    )
    selected_strategy: Optional[RetryStrategy] = Field(
        default=None, description="Legacy alias for selected_action"
    )
    exploration: Optional[bool] = Field(
        default=None, description="Legacy alias for exploration_flag"
    )
    arm_probabilities: Optional[dict[str, float]] = Field(
        default=None, description="Legacy alias for sampled_probabilities"
    )
    context_used: dict = Field(
        default_factory=dict, description="Legacy dictionary for context signals"
    )
    decided_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the decision was made"
    )

    def model_post_init(self, __context: Any) -> None:
        if self.selected_strategy is not None and not self.selected_action:
            self.selected_action = (
                self.selected_strategy.value
                if isinstance(self.selected_strategy, RetryStrategy)
                else str(self.selected_strategy)
            )
        elif self.selected_action and self.selected_strategy is None:
            try:
                self.selected_strategy = RetryStrategy(self.selected_action.lower())
            except ValueError:
                # Check if it maps to SimAction (uppercase)
                from backend.simulator.types import SimAction
                try:
                    sim_enum = SimAction(self.selected_action.upper())
                    self.selected_strategy = RetryStrategy(sim_enum.value.lower())
                except ValueError:
                    self.selected_strategy = RetryStrategy.no_action

        if self.exploration is not None and "exploration_flag" not in self.model_fields_set:
            self.exploration_flag = self.exploration
        elif self.exploration is None:
            self.exploration = self.exploration_flag

        if self.arm_probabilities is not None and not self.sampled_probabilities:
            self.sampled_probabilities = self.arm_probabilities
        elif self.sampled_probabilities and self.arm_probabilities is None:
            self.arm_probabilities = self.sampled_probabilities

        if self.context_used and not self.context:
            self.context = str(self.context_used)

    @model_validator(mode="after")
    def validate_selected_action(self) -> StrategyDecision:
        if self.selected_action:
            try:
                RetryStrategy(self.selected_action.lower())
            except ValueError:
                from backend.simulator.types import SimAction
                try:
                    SimAction(self.selected_action.upper())
                except ValueError:
                    raise ValueError(f"Invalid selected_action: {self.selected_action}")
        return self


class GuardrailDecision(BaseModel):
    """Output from the Guardrail Engine — deterministic safety check.

    This is the ONLY component that authorizes or blocks a financial action.
    No LLM is involved in this decision.
    """

    guardrail_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique guardrail evaluation identifier",
    )
    decision_id: str = Field(..., description="Reference to the StrategyDecision being evaluated")
    event_id: str = Field(..., description="Reference to the originating PaymentFailureEvent")
    verdict: GuardrailVerdict = Field(..., description="Approve, block, or escalate")
    rules_evaluated: list[str] = Field(..., description="Names of all rules that were evaluated")
    rules_triggered: list[str] = Field(
        default_factory=list, description="Names of rules that triggered a block or escalation"
    )
    reason: str = Field(..., description="Human-readable reason for the verdict")
    retry_count_24h: int = Field(
        ..., description="Number of retries for this card/instrument in the last 24 hours"
    )
    max_retry_limit: int = Field(
        ..., description="Maximum retries allowed per the current configuration"
    )
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the guardrail evaluation ran"
    )


class OutcomeResult(BaseModel):
    """Result after executing the recovery action."""

    outcome_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique outcome identifier",
    )
    event_id: str = Field(..., description="Reference to the originating PaymentFailureEvent")
    decision_id: str = Field(..., description="Reference to the StrategyDecision that was executed")
    strategy_applied: RetryStrategy = Field(..., description="The strategy that was actually run")
    status: OutcomeStatus = Field(..., description="Result of the recovery attempt")
    amount_recovered: float = Field(
        default=0.0, ge=0, description="Amount successfully recovered"
    )
    latency_ms: int = Field(default=0, ge=0, description="End-to-end latency in milliseconds")
    gateway_response_code: Optional[str] = Field(
        default=None, description="Response code from the payment gateway"
    )
    completed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the outcome was recorded"
    )


class AuditEvent(BaseModel):
    """Immutable audit log entry. Append-only — emitted at every pipeline stage."""

    audit_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique audit entry identifier",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this audit event occurred",
    )
    event_id: str = Field(..., description="Reference to the originating payment event")
    stage: str = Field(
        default="pipeline",
        description="Pipeline stage (e.g. schema_validation, diagnosis, context_creation, strategy, guardrail, execution, outcome, statistical_update, reflection)",
    )
    input_reference: Optional[str] = Field(
        default=None, description="Identifier or summary of the stage input"
    )
    output_reference: Optional[str] = Field(
        default=None, description="Identifier or summary of the stage output"
    )
    decision: Optional[str] = Field(
        default=None, description="Decision made at this stage (e.g. APPROVED, BLOCKED, STRATEGY_SELECTED)"
    )
    reason: Optional[str] = Field(
        default=None, description="Rationale or explanation for the decision"
    )
    latency_ms: float = Field(
        default=0.0, ge=0.0, description="Stage processing latency in milliseconds"
    )
    status: str = Field(
        default="success",
        description="Execution status at this stage (success, blocked, escalated, failed, skipped)",
    )

    # Backwards-compatibility fields & aliases
    action: Optional[AuditAction] = Field(
        default=None, description="Legacy action enum category"
    )
    actor: str = Field(
        default="pipeline",
        description="System component that produced this entry",
    )
    details: dict = Field(default_factory=dict, description="Structured details payload")
    idempotency_key: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Ensures this audit event is not duplicated",
    )
    parent_audit_id: Optional[str] = Field(
        default=None, description="Links to a parent audit entry for event chaining"
    )
    created_at: Optional[datetime] = Field(
        default=None, description="Legacy alias for timestamp"
    )

    def model_post_init(self, __context: Any) -> None:
        if self.created_at is not None and "timestamp" not in self.model_fields_set:
            self.timestamp = self.created_at
        elif self.created_at is None:
            self.created_at = self.timestamp
        self.idempotency_key = f"{self.event_id}:{self.stage}"


class BenchmarkResult(BaseModel):
    """Output from benchmark/simulation runs comparing strategies."""

    benchmark_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique benchmark run identifier",
    )
    strategy_name: str = Field(..., description="Name of the strategy being benchmarked")
    total_events: int = Field(..., ge=0, description="Number of events in the benchmark run")
    recovery_rate: float = Field(
        ..., ge=0, le=1, description="Fraction of events successfully recovered"
    )
    revenue_recovered: float = Field(
        ..., ge=0, description="Total revenue recovered in the run"
    )
    avg_time_to_recovery_ms: float = Field(
        ..., ge=0, description="Average time from failure to recovery in ms"
    )
    false_positive_rate: float = Field(
        ..., ge=0, le=1, description="Rate of unnecessary retries on non-retryable failures"
    )
    guardrail_block_rate: float = Field(
        ..., ge=0, le=1, description="Fraction of decisions blocked by guardrails"
    )
    baseline_recovery_rate: float = Field(
        ..., ge=0, le=1, description="Recovery rate of the baseline strategy"
    )
    improvement_over_baseline: float = Field(
        ..., description="Percentage improvement over baseline (can be negative)"
    )
    run_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the benchmark was executed"
    )
    parameters: dict = Field(
        default_factory=dict, description="Configuration parameters used in this run"
    )


class ExceptionRecord(BaseModel):
    """Records system exceptions for graceful failure tracking and post-mortems."""

    exception_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique exception record identifier",
    )
    event_id: Optional[str] = Field(
        default=None, description="Payment event ID if the exception is event-related"
    )
    component: str = Field(..., description="System component where the exception occurred")
    exception_type: str = Field(..., description="Exception class name")
    message: str = Field(..., description="Human-readable error message")
    stack_trace: Optional[str] = Field(default=None, description="Full stack trace if available")
    severity: str = Field(
        default="error", description="Severity level: error, warning, or critical"
    )
    handled: bool = Field(default=True, description="Whether the exception was handled gracefully")
    fallback_action: Optional[str] = Field(
        default=None, description="What fallback action was taken, if any"
    )
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the exception occurred"
    )


class ContextReflection(BaseModel):
    """Segment-level reflection analysis comparing bandit beliefs before and after a batch."""

    context: str = Field(..., description="Context segment (e.g. TIMEOUT_TRANSIENT+HIGH)")
    total_observations: int = Field(..., ge=0)
    successes: int = Field(..., ge=0)
    failures: int = Field(..., ge=0)
    recovery_rate: float = Field(..., ge=0.0, le=1.0)
    recovered_revenue: float = Field(..., ge=0.0)
    cost: float = Field(..., ge=0.0)
    net_value: float = Field(...)
    previous_statistics: dict[str, dict[str, float]] = Field(
        default_factory=dict, description="Action -> {alpha, beta, posterior_mean} before batch"
    )
    new_statistics: dict[str, dict[str, float]] = Field(
        default_factory=dict, description="Action -> {alpha, beta, posterior_mean} after batch"
    )
    changes_in_posterior_mean: dict[str, float] = Field(
        default_factory=dict, description="Action -> delta in posterior mean"
    )
    policy_before: str = Field(..., description="Optimal exploit action before batch")
    policy_after: str = Field(..., description="Optimal exploit action after batch")
    policy_changed: bool = Field(..., description="Whether optimal exploit action changed")
    learning_statement: str = Field(..., description="Human-readable mathematical learning insight")


class BatchReflectionRecord(BaseModel):
    """Persisted record of an outcome batch reflection analysis."""

    batch_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique batch identifier",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this reflection analysis was generated",
    )
    total_events: int = Field(..., ge=0)
    total_successes: int = Field(..., ge=0)
    total_failures: int = Field(..., ge=0)
    overall_recovery_rate: float = Field(..., ge=0.0, le=1.0)
    total_recovered_revenue: float = Field(..., ge=0.0)
    total_cost: float = Field(..., ge=0.0)
    total_net_value: float = Field(...)
    context_reflections: list[ContextReflection] = Field(default_factory=list)
    learning_summary: str = Field(..., description="High-level synthesized learning statement")
    metadata: dict[str, Any] = Field(default_factory=dict)
