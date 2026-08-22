"""
Simulator Domain Types
======================

Enums and Pydantic models that live exclusively in the simulation layer.

These are SEPARATE from the strategy-engine contracts (PaymentFailureEvent,
RetryStrategy, etc.).  The simulator speaks its own vocabulary — raw gateway
errors, failure classes, and simulator-level actions — then the Strategy Engine
translates those signals into its own typed decisions.

No strategy-engine code should import from this module directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Simulator Enums
# ---------------------------------------------------------------------------


class FailureClass(str, Enum):
    """Normalised failure classes used inside the simulator.

    These are the hidden labels that the EventGenerator assigns based on
    raw gateway error strings.  The Strategy Engine never sees these labels
    directly — it only sees the raw error string.
    """

    TIMEOUT_TRANSIENT = "TIMEOUT_TRANSIENT"
    HARD_FUNDS_ISSUE = "HARD_FUNDS_ISSUE"
    ISSUER_DECLINE = "ISSUER_DECLINE"
    AUTH_BLOCKED = "AUTH_BLOCKED"
    INFRA_OUTAGE = "INFRA_OUTAGE"
    DUPLICATE = "DUPLICATE"
    CUSTOMER_ABANDONMENT = "CUSTOMER_ABANDONMENT"
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"
    UNKNOWN = "UNKNOWN"


class ValueTier(str, Enum):
    """Customer / transaction value tier.

    LOW  : amount < 500 INR
    MID  : 500 ≤ amount < 5 000 INR
    HIGH : amount ≥ 5 000 INR
    """

    LOW = "LOW"
    MID = "MID"
    HIGH = "HIGH"


class SimAction(str, Enum):
    """Recovery actions the simulator (and eventually the Strategy Engine) can take."""

    IMMEDIATE_RETRY = "IMMEDIATE_RETRY"
    DELAYED_RETRY = "DELAYED_RETRY"
    PAYMENT_LINK = "PAYMENT_LINK"
    SWITCH_METHOD = "SWITCH_METHOD"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"


class CustomerSegment(str, Enum):
    """Rough customer quality tier used to add realistic noise."""

    NEW = "NEW"
    REGULAR = "REGULAR"
    PREMIUM = "PREMIUM"
    AT_RISK = "AT_RISK"


class SimPaymentMethod(str, Enum):
    """Payment method as used in the simulator (raw gateway vocabulary)."""

    CARD = "CARD"
    UPI = "UPI"
    NETBANKING = "NETBANKING"
    WALLET = "WALLET"
    EMI = "EMI"


# ---------------------------------------------------------------------------
# Simulator Event
# ---------------------------------------------------------------------------


class SimEvent(BaseModel):
    """A synthetic payment-failure event produced by the EventGenerator.

    Fields visible to the Strategy Engine:
        event_id, transaction_id, customer_id, timestamp, amount, currency,
        payment_method, raw_gateway_error, previous_attempts,
        customer_segment, value_tier

    Fields hidden from the Strategy Engine (simulator-internal):
        _failure_class  — use SimEvent.failure_class property to access
    """

    event_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique simulator event ID",
    )
    transaction_id: str = Field(..., description="Gateway transaction reference")
    customer_id: str = Field(..., description="Opaque customer identifier")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the failure occurred",
    )
    amount: float = Field(..., gt=0, description="Transaction amount in INR")
    currency: str = Field(default="INR")
    payment_method: SimPaymentMethod = Field(..., description="Payment instrument")
    raw_gateway_error: str = Field(
        ..., description="Raw, messy gateway error string — not pre-classified"
    )
    previous_attempts: int = Field(
        default=0, ge=0, description="Number of prior recovery attempts for this payment"
    )
    customer_segment: CustomerSegment = Field(...)
    value_tier: ValueTier = Field(...)

    # ---- Hidden simulator context (NOT exposed to the Strategy Engine) ----
    # Stored with a leading underscore-like name via alias.
    # Strategy Engine code should never read normalised_failure_class.
    normalised_failure_class: FailureClass = Field(
        ...,
        description="SIMULATOR-ONLY: true failure class, invisible to strategy engine",
        exclude=True,   # excluded from model_dump() by default
    )

    def strategy_engine_view(self) -> dict:
        """Return only the fields the Strategy Engine is allowed to see.

        This enforces the information barrier between simulator and strategy.
        """
        return self.model_dump(
            exclude={"normalised_failure_class"},
            mode="python",
        )


# ---------------------------------------------------------------------------
# Simulator Outcome
# ---------------------------------------------------------------------------


class SimOutcome(BaseModel):
    """Result returned by the OutcomeEngine after applying an action to an event."""

    event_id: str = Field(..., description="Reference to the SimEvent")
    action: SimAction = Field(..., description="Action that was applied")
    success: bool = Field(..., description="Whether the payment was recovered")
    recovered_value: float = Field(
        default=0.0, ge=0, description="Amount recovered (0 on failure)"
    )
    action_cost: float = Field(
        default=0.0, ge=0, description="Direct cost of executing this action (INR)"
    )
    friction_cost: float = Field(
        default=0.0, ge=0, description="Customer experience cost (friction units)"
    )
    resolution_delay_s: float = Field(
        default=0.0, ge=0, description="Expected seconds until resolution"
    )
    net_recovered: float = Field(
        default=0.0, description="recovered_value − action_cost − friction_cost"
    )
    processing_latency_ms: float = Field(
        default=0.0, ge=0, description="Simulated processing time in milliseconds"
    )


# ---------------------------------------------------------------------------
# Benchmark Report
# ---------------------------------------------------------------------------


class SimBenchmarkReport(BaseModel):
    """Aggregated metrics from running a policy over a batch of SimEvents."""

    policy_name: str
    total_events: int = Field(..., ge=0)
    processed: int = Field(..., ge=0)
    successful_recoveries: int = Field(..., ge=0)
    recovery_rate: float = Field(..., ge=0.0, le=1.0)
    gross_recovered_revenue: float = Field(..., ge=0)
    total_action_cost: float = Field(..., ge=0)
    total_friction_cost: float = Field(..., ge=0)
    net_recovered_revenue: float  # can be negative if costs exceed recovery
    human_reviews: int = Field(..., ge=0, description="Events escalated to human review")
    blocked_actions: int = Field(
        ..., ge=0, description="Events where action was explicitly blocked / no-op"
    )
    unsafe_attempts: int = Field(
        default=0, ge=0, description="Automated recovery attempts on high-risk/fraud/duplicate transactions"
    )
    unsafe_executions: int = Field(
        default=0, ge=0, description="Automated recovery executions on high-risk/fraud/duplicate transactions"
    )
    duplicate_executions: int = Field(
        default=0, ge=0, description="Duplicate executions on the same payment"
    )
    unresolved_exceptions: int = Field(..., ge=0)
    throughput_eps: float = Field(..., ge=0, description="Events processed per second")
    avg_processing_latency_ms: float = Field(..., ge=0)
    seed: Optional[int] = Field(default=None, description="RNG seed used for reproducibility")
    n_events: int = Field(..., ge=0, description="Batch size that was benchmarked")
    run_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
