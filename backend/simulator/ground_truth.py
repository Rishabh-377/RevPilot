"""
Ground Truth
============

Defines the hidden P(success | context, action) probability matrix used
exclusively by the OutcomeEngine to simulate realistic recovery outcomes.

ARCHITECTURAL INVARIANT
-----------------------
This module is SIMULATOR-ONLY.  The Strategy Engine must NEVER import from
here.  The information barrier is enforced in two ways:

  1. ``GroundTruth`` deliberately has no public method that returns the full
     matrix — only ``get_recovery_probability(event, action)`` which requires
     a real SimEvent (including the hidden ``normalised_failure_class``).

  2. Tests explicitly assert that the matrix is not accessible from the
     strategy-engine-facing view (``event.strategy_engine_view()``).

Context space (intentionally small)
------------------------------------
  normalised_failure_class  ×  value_tier   →  9 × 3 = 27 cells

Each cell holds a dict mapping SimAction → success_probability ∈ [0, 1].

Design goals
------------
  • Different optimal actions for different contexts.
  • Some actions are near-zero for some contexts (realistic).
  • Values are calibrated to produce measurable lift from a smart policy.
  • Supports simulated environmental drift via a ``drift_factor`` parameter.
"""

from __future__ import annotations

from backend.simulator.types import FailureClass, SimAction, SimEvent, ValueTier

# ---------------------------------------------------------------------------
# The Hidden Probability Matrix
# ---------------------------------------------------------------------------
# Structure: _P[FailureClass][ValueTier][SimAction] = base_probability
#
# Reading guide:
#   - TIMEOUT_TRANSIENT:  IMMEDIATE_RETRY is the clear winner; delays help less
#   - HARD_FUNDS_ISSUE:   PAYMENT_LINK and DELAYED_RETRY work; immediate retry
#                         almost never helps (customer still has no money)
#   - ISSUER_DECLINE:     SWITCH_METHOD is usually best; escalation helps HIGH tier
#   - AUTH_BLOCKED:       IMMEDIATE_RETRY gives a second chance; delay is also OK
#   - INFRA_OUTAGE:       DELAYED_RETRY wins; retrying immediately hits the same wall
#   - DUPLICATE:          Nothing automated helps; HUMAN_ESCALATION is the only path
#   - CUSTOMER_ABANDONMENT: PAYMENT_LINK re-engages; aggressive retry annoys
#   - FRAUD_SUSPECTED:    Only HUMAN_ESCALATION has any chance; everything else risky
#   - UNKNOWN:            Mixed; DELAYED_RETRY is a safe default

_P: dict[FailureClass, dict[ValueTier, dict[SimAction, float]]] = {
    FailureClass.TIMEOUT_TRANSIENT: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.78,
            SimAction.DELAYED_RETRY:    0.62,
            SimAction.PAYMENT_LINK:     0.35,
            SimAction.SWITCH_METHOD:    0.28,
            SimAction.HUMAN_ESCALATION: 0.10,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.80,
            SimAction.DELAYED_RETRY:    0.65,
            SimAction.PAYMENT_LINK:     0.38,
            SimAction.SWITCH_METHOD:    0.30,
            SimAction.HUMAN_ESCALATION: 0.12,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.75,
            SimAction.DELAYED_RETRY:    0.70,
            SimAction.PAYMENT_LINK:     0.42,
            SimAction.SWITCH_METHOD:    0.35,
            SimAction.HUMAN_ESCALATION: 0.18,
        },
    },
    FailureClass.HARD_FUNDS_ISSUE: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.04,
            SimAction.DELAYED_RETRY:    0.28,
            SimAction.PAYMENT_LINK:     0.42,
            SimAction.SWITCH_METHOD:    0.20,
            SimAction.HUMAN_ESCALATION: 0.08,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.03,
            SimAction.DELAYED_RETRY:    0.32,
            SimAction.PAYMENT_LINK:     0.48,
            SimAction.SWITCH_METHOD:    0.22,
            SimAction.HUMAN_ESCALATION: 0.10,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.02,
            SimAction.DELAYED_RETRY:    0.25,
            SimAction.PAYMENT_LINK:     0.55,
            SimAction.SWITCH_METHOD:    0.30,
            SimAction.HUMAN_ESCALATION: 0.20,
        },
    },
    FailureClass.ISSUER_DECLINE: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.12,
            SimAction.DELAYED_RETRY:    0.22,
            SimAction.PAYMENT_LINK:     0.30,
            SimAction.SWITCH_METHOD:    0.48,
            SimAction.HUMAN_ESCALATION: 0.15,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.10,
            SimAction.DELAYED_RETRY:    0.20,
            SimAction.PAYMENT_LINK:     0.32,
            SimAction.SWITCH_METHOD:    0.52,
            SimAction.HUMAN_ESCALATION: 0.18,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.08,
            SimAction.DELAYED_RETRY:    0.18,
            SimAction.PAYMENT_LINK:     0.35,
            SimAction.SWITCH_METHOD:    0.55,
            SimAction.HUMAN_ESCALATION: 0.30,
        },
    },
    FailureClass.AUTH_BLOCKED: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.55,
            SimAction.DELAYED_RETRY:    0.48,
            SimAction.PAYMENT_LINK:     0.40,
            SimAction.SWITCH_METHOD:    0.22,
            SimAction.HUMAN_ESCALATION: 0.08,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.58,
            SimAction.DELAYED_RETRY:    0.50,
            SimAction.PAYMENT_LINK:     0.42,
            SimAction.SWITCH_METHOD:    0.25,
            SimAction.HUMAN_ESCALATION: 0.10,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.52,
            SimAction.DELAYED_RETRY:    0.55,
            SimAction.PAYMENT_LINK:     0.45,
            SimAction.SWITCH_METHOD:    0.28,
            SimAction.HUMAN_ESCALATION: 0.20,
        },
    },
    FailureClass.INFRA_OUTAGE: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.18,
            SimAction.DELAYED_RETRY:    0.68,
            SimAction.PAYMENT_LINK:     0.30,
            SimAction.SWITCH_METHOD:    0.35,
            SimAction.HUMAN_ESCALATION: 0.12,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.15,
            SimAction.DELAYED_RETRY:    0.72,
            SimAction.PAYMENT_LINK:     0.32,
            SimAction.SWITCH_METHOD:    0.38,
            SimAction.HUMAN_ESCALATION: 0.14,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.12,
            SimAction.DELAYED_RETRY:    0.75,
            SimAction.PAYMENT_LINK:     0.35,
            SimAction.SWITCH_METHOD:    0.40,
            SimAction.HUMAN_ESCALATION: 0.20,
        },
    },
    FailureClass.DUPLICATE: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.02,
            SimAction.DELAYED_RETRY:    0.05,
            SimAction.PAYMENT_LINK:     0.08,
            SimAction.SWITCH_METHOD:    0.03,
            SimAction.HUMAN_ESCALATION: 0.60,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.02,
            SimAction.DELAYED_RETRY:    0.05,
            SimAction.PAYMENT_LINK:     0.08,
            SimAction.SWITCH_METHOD:    0.03,
            SimAction.HUMAN_ESCALATION: 0.65,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.01,
            SimAction.DELAYED_RETRY:    0.04,
            SimAction.PAYMENT_LINK:     0.07,
            SimAction.SWITCH_METHOD:    0.02,
            SimAction.HUMAN_ESCALATION: 0.70,
        },
    },
    FailureClass.CUSTOMER_ABANDONMENT: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.15,
            SimAction.DELAYED_RETRY:    0.28,
            SimAction.PAYMENT_LINK:     0.52,
            SimAction.SWITCH_METHOD:    0.20,
            SimAction.HUMAN_ESCALATION: 0.05,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.12,
            SimAction.DELAYED_RETRY:    0.30,
            SimAction.PAYMENT_LINK:     0.58,
            SimAction.SWITCH_METHOD:    0.22,
            SimAction.HUMAN_ESCALATION: 0.08,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.10,
            SimAction.DELAYED_RETRY:    0.28,
            SimAction.PAYMENT_LINK:     0.62,
            SimAction.SWITCH_METHOD:    0.25,
            SimAction.HUMAN_ESCALATION: 0.15,
        },
    },
    FailureClass.FRAUD_SUSPECTED: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.01,
            SimAction.DELAYED_RETRY:    0.02,
            SimAction.PAYMENT_LINK:     0.03,
            SimAction.SWITCH_METHOD:    0.02,
            SimAction.HUMAN_ESCALATION: 0.30,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.01,
            SimAction.DELAYED_RETRY:    0.02,
            SimAction.PAYMENT_LINK:     0.03,
            SimAction.SWITCH_METHOD:    0.02,
            SimAction.HUMAN_ESCALATION: 0.35,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.01,
            SimAction.DELAYED_RETRY:    0.01,
            SimAction.PAYMENT_LINK:     0.02,
            SimAction.SWITCH_METHOD:    0.01,
            SimAction.HUMAN_ESCALATION: 0.40,
        },
    },
    FailureClass.UNKNOWN: {
        ValueTier.LOW: {
            SimAction.IMMEDIATE_RETRY:  0.20,
            SimAction.DELAYED_RETRY:    0.32,
            SimAction.PAYMENT_LINK:     0.28,
            SimAction.SWITCH_METHOD:    0.18,
            SimAction.HUMAN_ESCALATION: 0.15,
        },
        ValueTier.MID: {
            SimAction.IMMEDIATE_RETRY:  0.22,
            SimAction.DELAYED_RETRY:    0.35,
            SimAction.PAYMENT_LINK:     0.30,
            SimAction.SWITCH_METHOD:    0.20,
            SimAction.HUMAN_ESCALATION: 0.18,
        },
        ValueTier.HIGH: {
            SimAction.IMMEDIATE_RETRY:  0.18,
            SimAction.DELAYED_RETRY:    0.38,
            SimAction.PAYMENT_LINK:     0.32,
            SimAction.SWITCH_METHOD:    0.22,
            SimAction.HUMAN_ESCALATION: 0.25,
        },
    },
}


# ---------------------------------------------------------------------------
# Action cost table: direct operational cost (INR) per action
# ---------------------------------------------------------------------------
_ACTION_COST: dict[SimAction, float] = {
    SimAction.IMMEDIATE_RETRY:  2.0,
    SimAction.DELAYED_RETRY:    3.5,
    SimAction.PAYMENT_LINK:     5.0,
    SimAction.SWITCH_METHOD:    4.0,
    SimAction.HUMAN_ESCALATION: 25.0,
}

# Friction cost (dimensionless units; higher = more customer friction)
_FRICTION_COST: dict[SimAction, float] = {
    SimAction.IMMEDIATE_RETRY:  1.0,
    SimAction.DELAYED_RETRY:    2.0,
    SimAction.PAYMENT_LINK:     3.0,
    SimAction.SWITCH_METHOD:    4.0,
    SimAction.HUMAN_ESCALATION: 5.0,
}

# Expected resolution delay in seconds
_RESOLUTION_DELAY_S: dict[SimAction, float] = {
    SimAction.IMMEDIATE_RETRY:  5.0,
    SimAction.DELAYED_RETRY:    300.0,
    SimAction.PAYMENT_LINK:     3_600.0,
    SimAction.SWITCH_METHOD:    60.0,
    SimAction.HUMAN_ESCALATION: 86_400.0,
}


# ---------------------------------------------------------------------------
# GroundTruth
# ---------------------------------------------------------------------------


class GroundTruth:
    """Hidden probability tables used *only* by the OutcomeEngine.

    Parameters
    ----------
    drift_factor:
        Multiplicative modifier applied to all probabilities.
        1.0 = no drift (normal conditions).
        < 1.0 = degraded environment (bank outage, festival traffic spike).
        > 1.0 = improved environment (bank promotions, weekday off-peak).
        Probabilities are clipped to [0.0, 1.0] after applying drift.
    """

    def __init__(
        self,
        drift_factor: float = 1.0,
        probability_overrides: dict[tuple[FailureClass, ValueTier, SimAction], float] | None = None,
    ) -> None:
        if drift_factor <= 0:
            raise ValueError(f"drift_factor must be > 0, got {drift_factor}")
        self.drift_factor = drift_factor
        self._overrides: dict[tuple[FailureClass, ValueTier, SimAction], float] = dict(
            probability_overrides or {}
        )

    # ------------------------------------------------------------------
    # Core query — used by OutcomeEngine
    # ------------------------------------------------------------------

    def get_recovery_probability(
        self,
        event: SimEvent,
        action: SimAction,
    ) -> float:
        """Return P(success | failure_class, value_tier, action).

        This method requires a full ``SimEvent`` (including the hidden
        ``normalised_failure_class``) — it cannot be called with only the
        strategy-engine-facing view of the event.

        Parameters
        ----------
        event:
            The SimEvent.  ``event.normalised_failure_class`` is read here.
        action:
            The recovery action being considered.

        Returns
        -------
        float in [0.0, 1.0]
        """
        key = (event.normalised_failure_class, event.value_tier, action)
        if key in self._overrides:
            base = self._overrides[key]
        else:
            base = _P[event.normalised_failure_class][event.value_tier][action]
        return float(min(1.0, max(0.0, base * self.drift_factor)))

    def set_override(
        self,
        failure_class: FailureClass,
        value_tier: ValueTier,
        action: SimAction,
        probability: float,
    ) -> None:
        """Set a targeted probability override for a specific (context, action) cell."""
        if not (0.0 <= probability <= 1.0):
            raise ValueError(f"probability must be in [0.0, 1.0], got {probability}")
        self._overrides[(failure_class, value_tier, action)] = probability

    def with_override(
        self,
        failure_class: FailureClass,
        value_tier: ValueTier,
        action: SimAction,
        probability: float,
    ) -> GroundTruth:
        """Return a new GroundTruth with the specified probability override."""
        new_gt = GroundTruth(
            drift_factor=self.drift_factor,
            probability_overrides=self._overrides.copy(),
        )
        new_gt.set_override(failure_class, value_tier, action, probability)
        return new_gt

    def get_action_cost(self, action: SimAction) -> float:
        """Return the direct INR cost of executing ``action``."""
        return _ACTION_COST[action]

    def get_friction_cost(self, action: SimAction) -> float:
        """Return the friction cost (dimensionless) for ``action``."""
        return _FRICTION_COST[action]

    def get_resolution_delay_s(self, action: SimAction) -> float:
        """Return expected resolution delay in seconds for ``action``."""
        return _RESOLUTION_DELAY_S[action]

    # ------------------------------------------------------------------
    # Optimal-policy helper (for oracle upper-bound benchmarks only)
    # ------------------------------------------------------------------

    def optimal_action(
        self,
        failure_class: FailureClass,
        value_tier: ValueTier,
    ) -> SimAction:
        """Return the action with the highest success probability for a context.

        This is the oracle policy — it is NOT available to the Strategy Engine.
        Used only in benchmarks to establish an upper bound.
        """
        cell = _P[failure_class][value_tier]
        return max(cell, key=lambda a: cell[a] * self.drift_factor)

    def context_matrix(self) -> dict[str, dict[str, dict[str, float]]]:
        """Return the full probability matrix (for testing and visualisation only).

        Returns a plain-dict copy so callers cannot mutate the master table.
        """
        return {
            fc.value: {
                vt.value: {a.value: prob for a, prob in actions.items()}
                for vt, actions in tiers.items()
            }
            for fc, tiers in _P.items()
        }

    # ------------------------------------------------------------------
    # Environmental drift
    # ------------------------------------------------------------------

    def with_drift(self, drift_factor: float) -> GroundTruth:
        """Return a new GroundTruth instance with a different drift factor."""
        return GroundTruth(drift_factor=drift_factor)
