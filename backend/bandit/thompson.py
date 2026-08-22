"""
Segmented Thompson Sampling Strategy Engine
============================================

The core decision-making component for RevPilot revenue recovery.

ARCHITECTURAL INVARIANTS:
  1. Statistical models optimize — LLMs do NOT select financial actions.
  2. Action selection is purely mathematical using Bayesian Expected Value (EV).
  3. Clearly separates sampled probabilities, posterior means, and EV scores.
  4. Context space = normalized_failure_class + value_tier.
  5. Prior rules only initialize priors; they do NOT hardcode the final decision.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Optional

from backend.bandit.state import ArmState, BanditState
from backend.models.schemas import (
    FailureClass,
    PaymentFailureEvent,
    RetryStrategy,
    StrategyDecision,
    ValueTier,
)
from backend.simulator.types import SimAction


# ---------------------------------------------------------------------------
# Action Economics Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionEconomics:
    """Cost and time discounting parameters for an action."""

    api_cost: float        # Direct gateway/API execution cost in INR
    friction_cost: float   # Customer friction penalty in INR
    time_discount: float   # Discount factor for delayed recovery (0.0 to 1.0)


DEFAULT_ACTION_ECONOMICS: dict[str, ActionEconomics] = {
    SimAction.IMMEDIATE_RETRY.value: ActionEconomics(api_cost=2.0, friction_cost=1.0, time_discount=1.00),
    SimAction.DELAYED_RETRY.value: ActionEconomics(api_cost=3.5, friction_cost=2.0, time_discount=0.98),
    SimAction.PAYMENT_LINK.value: ActionEconomics(api_cost=5.0, friction_cost=3.0, time_discount=0.92),
    SimAction.SWITCH_METHOD.value: ActionEconomics(api_cost=4.0, friction_cost=4.0, time_discount=0.97),
    SimAction.HUMAN_ESCALATION.value: ActionEconomics(api_cost=25.0, friction_cost=5.0, time_discount=0.75),
}

CANDIDATE_ACTIONS = [
    SimAction.IMMEDIATE_RETRY.value,
    SimAction.DELAYED_RETRY.value,
    SimAction.PAYMENT_LINK.value,
    SimAction.SWITCH_METHOD.value,
    SimAction.HUMAN_ESCALATION.value,
]


# ---------------------------------------------------------------------------
# Informed Priors (Configurable Initialization)
# ---------------------------------------------------------------------------

# Maps FailureClass -> Action -> (alpha_0, beta_0)
# Prior pseudo-counts (strength ~ 4-10 observations).
# These initialize the prior without hardcoding future decisions.

DEFAULT_INFORMED_PRIORS: dict[str, dict[str, tuple[float, float]]] = {
    fc.value: {
        action: (1.0, 1.0) for action in CANDIDATE_ACTIONS
    } for fc in FailureClass
}


# ---------------------------------------------------------------------------
# Thompson Sampling Strategy Engine
# ---------------------------------------------------------------------------


class ThompsonSamplingBandit:
    """Segmented Contextual Thompson Sampling Optimizer."""

    def __init__(
        self,
        state: Optional[BanditState] = None,
        economics: Optional[dict[str, ActionEconomics]] = None,
        informed_priors: Optional[dict[str, dict[str, tuple[float, float]]]] = None,
        candidate_actions: Optional[list[str]] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.state = state or BanditState()
        self.economics = economics or DEFAULT_ACTION_ECONOMICS
        self.priors = informed_priors or DEFAULT_INFORMED_PRIORS
        self.candidate_actions = candidate_actions or CANDIDATE_ACTIONS
        self._rng = random.Random(seed)
        self._initialize_priors()

    def _initialize_priors(self) -> None:
        """Populate initial prior distributions across contexts and tiers."""
        for fc in FailureClass:
            for vt in ["LOW", "MID", "HIGH"]:
                context = f"{fc.value}+{vt}"
                fc_priors = self.priors.get(fc.value, {})
                for action in self.candidate_actions:
                    default_a, default_b = fc_priors.get(action, (1.0, 1.0))
                    # Check if already present in state
                    if context not in self.state.arms or action not in self.state.arms[context]:
                        self.state.get_arm(
                            context=context,
                            action=action,
                            default_alpha=default_a,
                            default_beta=default_b,
                        )

    def _get_context_key(self, failure_class: str, value_tier: str) -> str:
        return f"{failure_class}+{value_tier}"

    def compute_ev(
        self,
        sampled_prob: float,
        transaction_amount: float,
        action: str,
    ) -> float:
        """Compute Expected Value (EV) in INR for a candidate action.

        EV (INR) = (sampled_prob * transaction_amount * time_discount) - api_cost_inr - friction_cost_inr
        where friction_cost_inr = friction_units * INR_PER_FRICTION_UNIT
        """
        econ = self.economics.get(action, ActionEconomics(api_cost=5.0, friction_cost=2.0, time_discount=0.95))
        
        # Unify non-monetary customer friction with monetary INR terms
        INR_PER_FRICTION_UNIT = 1.0  # Conversion rate: 1.0 INR per dimensionless friction unit
        friction_cost_inr = econ.friction_cost * INR_PER_FRICTION_UNIT
        
        ev = (sampled_prob * transaction_amount * econ.time_discount) - econ.api_cost - friction_cost_inr
        return ev

    def select_action(
        self,
        event_id: str,
        failure_class: str | FailureClass,
        value_tier: str | ValueTier,
        amount: float,
        diagnosis_id: Optional[str] = None,
    ) -> StrategyDecision:
        """Perform Thompson Sampling and select optimal recovery strategy.

        Steps:
          1. Sample probability theta ~ Beta(alpha, beta) for each arm
          2. Compute posterior means E[theta] = alpha / (alpha + beta)
          3. Calculate Expected Value (EV) for all candidate actions
          4. Rank actions by EV
          5. Identify winning action and check exploration flag
          6. Generate mathematical reasoning
        """
        fc_str = failure_class.value if isinstance(failure_class, FailureClass) else str(failure_class)
        vt_str = value_tier.value if isinstance(value_tier, ValueTier) else str(value_tier)
        context = self._get_context_key(fc_str, vt_str)

        sampled_probs: dict[str, float] = {}
        posterior_means: dict[str, float] = {}
        ev_scores: dict[str, float] = {}
        exploit_ev_scores: dict[str, float] = {}

        for action in self.candidate_actions:
            arm = self.state.get_arm(context, action)
            theta_sampled = arm.sample_probability(self._rng)
            mean_theta = arm.posterior_mean

            sampled_probs[action] = round(theta_sampled, 4)
            posterior_means[action] = round(mean_theta, 4)

            # Sampled EV (used by Thompson Sampling)
            ev = self.compute_ev(theta_sampled, amount, action)
            ev_scores[action] = round(ev, 2)

            # Exploit EV (what the posterior mean would choose)
            exploit_ev = self.compute_ev(mean_theta, amount, action)
            exploit_ev_scores[action] = round(exploit_ev, 2)

        # Rank candidates by sampled EV
        sorted_actions = sorted(self.candidate_actions, key=lambda a: ev_scores[a], reverse=True)
        selected_action = sorted_actions[0]
        selected_ev = ev_scores[selected_action]

        # Best exploit action (under posterior mean)
        exploit_action = max(self.candidate_actions, key=lambda a: exploit_ev_scores[a])
        exploration_flag = selected_action != exploit_action

        # Runner-up for explainability
        runner_up = sorted_actions[1] if len(sorted_actions) > 1 else None

        # Build mathematical reasoning
        win_prob = sampled_probs[selected_action]
        if runner_up is not None:
            runner_up_ev = ev_scores[runner_up]
            reasoning = (
                f"{selected_action} selected because sampled success probability {win_prob:.2f} "
                f"produces EV ₹{selected_ev:,.2f} versus ₹{runner_up_ev:,.2f} for {runner_up}."
            )
        else:
            reasoning = f"{selected_action} selected with sampled success probability {win_prob:.2f} (EV ₹{selected_ev:,.2f})."

        # Winning arm posterior confidence
        win_arm = self.state.get_arm(context, selected_action)
        confidence = round(min(1.0, max(0.0, win_arm.posterior_mean)), 4)

        self.state.total_decisions += 1

        return StrategyDecision(
            event_id=event_id,
            context=context,
            candidate_actions=self.candidate_actions,
            sampled_probabilities=sampled_probs,
            posterior_means=posterior_means,
            ev_scores=ev_scores,
            selected_action=selected_action,
            selected_ev=selected_ev,
            exploration_flag=exploration_flag,
            reasoning=reasoning,
            confidence=round(self.state.get_arm(context, selected_action).posterior_mean, 4),
            diagnosis_id=diagnosis_id,
        )

    def get_best_exploit_action(self, context: str, amount: float = 1000.0) -> tuple[str, float]:
        """Return the optimal greedy/exploit action and its EV under the posterior mean."""
        best_act = None
        best_ev = float("-inf")
        for action in self.candidate_actions:
            arm = self.state.get_arm(context, action)
            ev = self.compute_ev(arm.posterior_mean, amount, action)
            if ev > best_ev:
                best_ev = ev
                best_act = action
        return (best_act or self.candidate_actions[0], round(best_ev, 2))

    def select_arm(self, context: dict[str, Any]) -> StrategyDecision:
        """Legacy / standard interface for strategy selection."""
        event_id = str(context.get("event_id", "evt_unknown"))
        fc = context.get("failure_class", context.get("normalized_failure_class", FailureClass.UNKNOWN))
        vt = context.get("value_tier", ValueTier.MID)
        amount = float(context.get("amount", 1000.0))
        diag_id = context.get("diagnosis_id")

        return self.select_action(
            event_id=event_id,
            failure_class=fc,
            value_tier=vt,
            amount=amount,
            diagnosis_id=diag_id,
        )

    def observe_outcome(
        self,
        context: str,
        action: str | SimAction,
        success: bool,
        decay_factor: float = 1.0,
    ) -> None:
        """Statistical Bayesian updater: Beta(alpha + success, beta + failure)."""
        act_str = action.value if isinstance(action, SimAction) else str(action)
        arm = self.state.get_arm(context, act_str)
        arm.update(success=success, decay_factor=decay_factor)

    def update(self, decision_id: str, reward: float, context: Optional[str] = None, action: Optional[str] = None) -> None:
        """Update arm given reward (1.0 for success, 0.0 for failure)."""
        if context and action:
            self.observe_outcome(context=context, action=action, success=(reward > 0.5))
