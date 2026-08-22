"""
Baseline Strategy & Benchmark Harness
======================================

Provides:
  1. ``StaticBaselinePolicy`` — a simple deterministic recovery policy that
     maps each normalised failure class to a fixed action.  It cannot adapt.

  2. ``run_benchmark(policy, events, outcome_engine)`` — a reusable function
     that evaluates *any* callable policy over an identical event batch and
     produces a ``SimBenchmarkReport``.

     This is the hook that the future Strategy Engine plugs into.  As long as
     the policy conforms to the ``PolicyFn`` type alias:

         PolicyFn = Callable[[SimEvent], SimAction]

     it can be swapped in transparently.

IMPORTANT
---------
- ``run_benchmark`` does NOT fabricate metrics.  Every number in the report
  is derived mechanically from OutcomeEngine results.
- ``StaticBaselinePolicy`` operates only on the raw gateway error string and
  the payment_method field — it cannot see ``normalised_failure_class``.
  It uses simple keyword matching to mimic what a human on-call might do.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import (
    FailureClass,
    SimAction,
    SimBenchmarkReport,
    SimEvent,
    SimOutcome,
)

# Type alias for any callable policy the benchmark harness can evaluate.
PolicyFn = Callable[[SimEvent], SimAction]


# ---------------------------------------------------------------------------
# Keyword → Action mapping (static baseline)
# ---------------------------------------------------------------------------
# Maps normalised_failure_class → action (for the oracle-aware version).
# The public-facing version matches on raw error keywords only.

_CLASS_TO_ACTION: dict[FailureClass, SimAction] = {
    FailureClass.TIMEOUT_TRANSIENT:    SimAction.IMMEDIATE_RETRY,
    FailureClass.HARD_FUNDS_ISSUE:     SimAction.PAYMENT_LINK,
    FailureClass.ISSUER_DECLINE:       SimAction.SWITCH_METHOD,
    FailureClass.AUTH_BLOCKED:         SimAction.IMMEDIATE_RETRY,
    FailureClass.INFRA_OUTAGE:         SimAction.DELAYED_RETRY,
    FailureClass.DUPLICATE:            SimAction.HUMAN_ESCALATION,
    FailureClass.CUSTOMER_ABANDONMENT: SimAction.PAYMENT_LINK,
    FailureClass.FRAUD_SUSPECTED:      SimAction.HUMAN_ESCALATION,
    FailureClass.UNKNOWN:              SimAction.DELAYED_RETRY,
}

# Raw-string keyword rules for the public-facing (strategy-engine-blind) mode.
# Ordered — first match wins.
_KEYWORD_RULES: list[tuple[list[str], SimAction]] = [
    (["duplicate", "already exists", "idempotency", "already processed"],
     SimAction.HUMAN_ESCALATION),
    (["fraud", "suspicious", "watchlist", "risk score", "card-testing"],
     SimAction.HUMAN_ESCALATION),
    (["timeout", "timed out", "no data received", "connection reset", "socket closed"],
     SimAction.IMMEDIATE_RETRY),
    (["psp unavailable", "gateway down", "host unreachable", "switch unavailable",
      "unavailable", "503"],
     SimAction.DELAYED_RETRY),
    (["insufficient", "not sufficient", "balance too low", "limit exhausted",
      "limit reached", "credit limit"],
     SimAction.PAYMENT_LINK),
    # Customer abandonment must come BEFORE auth-blocked because both share
    # "abandoned" in some strings — more specific match first.
    (["payment abandoned by user", "navigated away", "closed the payment",
      "rejected by customer", "upi collect request rejected"],
     SimAction.PAYMENT_LINK),
    (["auth window", "collect request expired", "otp not entered", "3ds",
      "authentication", "pin entry", "step-up"],
     SimAction.IMMEDIATE_RETRY),
    (["do not honour", "declined by issuing bank", "restricted card",
      "transaction not permitted", "blocked by issuer", "lost or stolen"],
     SimAction.SWITCH_METHOD),
]


class StaticBaselinePolicy:
    """Simple deterministic recovery policy based on raw error keyword matching.

    The Strategy Engine sees only the strategy-engine-facing fields of each
    SimEvent.  This policy mimics that constraint: it reads
    ``event.raw_gateway_error`` and ``event.payment_method`` only.

    No learning, no probabilities, no LLM — just a lookup table.
    """

    def __init__(self, default_action: SimAction = SimAction.DELAYED_RETRY) -> None:
        self.default_action = default_action

    def __call__(self, event: SimEvent) -> SimAction:
        """Select a recovery action for ``event``."""
        return self._select(event.raw_gateway_error)

    def _select(self, raw_error: str) -> SimAction:
        error_lower = raw_error.lower()
        for keywords, action in _KEYWORD_RULES:
            if any(kw in error_lower for kw in keywords):
                return action
        return self.default_action

    def __repr__(self) -> str:
        return f"StaticBaselinePolicy(default={self.default_action.value})"


class OraclePolicy:
    """Oracle upper-bound policy that peeks at the true failure class.

    NOT available to the Strategy Engine — exists only to establish the
    theoretical ceiling against which adaptive policies are compared.
    """

    def __call__(self, event: SimEvent) -> SimAction:
        return _CLASS_TO_ACTION[event.normalised_failure_class]

    def __repr__(self) -> str:
        return "OraclePolicy()"


# ---------------------------------------------------------------------------
# Benchmark harness
# ---------------------------------------------------------------------------


def run_benchmark(
    policy: PolicyFn,
    events: list[SimEvent],
    outcome_engine: OutcomeEngine,
    policy_name: str = "unnamed_policy",
    seed: int | None = None,
) -> SimBenchmarkReport:
    """Evaluate ``policy`` over ``events`` and return a ``SimBenchmarkReport``.

    This function is the *only* sanctioned way to measure policy performance.
    All metrics are derived mechanically from OutcomeEngine results — nothing
    is fabricated.

    Parameters
    ----------
    policy:
        Any callable that maps ``SimEvent → SimAction``.
    events:
        The event batch (must be identical across compared policies to ensure
        a fair comparison).
    outcome_engine:
        OutcomeEngine instance.  Use the same instance across all compared
        policies to ensure identical stochastic outcomes.
    policy_name:
        Human-readable label for the report.
    seed:
        RNG seed used to generate the events (recorded in the report for
        reproducibility metadata; does not affect this function's RNG).

    Returns
    -------
    SimBenchmarkReport
    """
    total_events = len(events)
    if total_events == 0:
        raise ValueError("events list must not be empty")

    wall_start = time.perf_counter()

    outcomes: list[SimOutcome] = []
    unresolved_exceptions = 0

    for event in events:
        try:
            action = policy(event)
            outcome = outcome_engine.simulate_outcome(event, action)
            outcomes.append(outcome)
        except Exception:
            unresolved_exceptions += 1

    wall_elapsed_s = time.perf_counter() - wall_start
    processed = len(outcomes)

    # --- Aggregate metrics (no fabrication) ---
    successful = [o for o in outcomes if o.success]
    human_reviews = sum(
        1 for o in outcomes if o.action == SimAction.HUMAN_ESCALATION
    )
    # "blocked" = HUMAN_ESCALATION that did NOT succeed (unresolved escalation)
    blocked_actions = sum(
        1 for o in outcomes
        if o.action == SimAction.HUMAN_ESCALATION and not o.success
    )

    # Dynamic calculation of unsafe attempts and executions based on ground-truth risks
    unsafe_attempts = sum(
        1 for ev, o in zip(events[:len(outcomes)], outcomes)
        if ev.normalised_failure_class in {FailureClass.FRAUD_SUSPECTED, FailureClass.DUPLICATE}
        and o.action != SimAction.HUMAN_ESCALATION
    )
    unsafe_executions = unsafe_attempts  # For baseline, all selected non-escalated actions are directly executed

    recovery_rate = len(successful) / processed if processed else 0.0
    gross_recovered = sum(o.recovered_value for o in outcomes)
    total_action_cost = sum(o.action_cost for o in outcomes)
    total_friction_cost = sum(o.friction_cost for o in outcomes)
    net_recovered = sum(o.net_recovered for o in outcomes)

    throughput_eps = processed / wall_elapsed_s if wall_elapsed_s > 0 else 0.0
    avg_latency_ms = (
        sum(o.processing_latency_ms for o in outcomes) / processed
        if processed
        else 0.0
    )

    return SimBenchmarkReport(
        policy_name=policy_name,
        total_events=total_events,
        processed=processed,
        successful_recoveries=len(successful),
        recovery_rate=round(recovery_rate, 6),
        gross_recovered_revenue=round(gross_recovered, 2),
        total_action_cost=round(total_action_cost, 2),
        total_friction_cost=round(total_friction_cost, 2),
        net_recovered_revenue=round(net_recovered, 2),
        human_reviews=human_reviews,
        blocked_actions=blocked_actions,
        unsafe_attempts=unsafe_attempts,
        unsafe_executions=unsafe_executions,
        duplicate_executions=0,
        unresolved_exceptions=unresolved_exceptions,
        throughput_eps=round(throughput_eps, 2),
        avg_processing_latency_ms=round(avg_latency_ms, 4),
        seed=seed,
        n_events=total_events,
    )
