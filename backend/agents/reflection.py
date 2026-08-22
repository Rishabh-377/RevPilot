"""
Reflection Agent & Statistical Updater
======================================

Implements the learning interpretation and batch reflection layer.

ARCHITECTURAL PRINCIPLES:
  1. Reflection does NOT directly modify the bandit math.
  2. Core Loop:
       Outcome
       → Statistical Updater (Idempotent Bayesian Updates)
       → Updated Alpha / Beta
       → Reflection Agent explains the change & policy shifts
  3. Ensures duplicate outcomes cannot update the model twice.
  4. Generates mathematical learning statements and persists reflection records.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from backend.bandit.state import ArmState, BanditState
from backend.bandit.thompson import ThompsonSamplingBandit
from backend.models.schemas import (
    BatchReflectionRecord,
    ContextReflection,
    OutcomeResult,
)
from backend.simulator.types import SimAction, SimOutcome


# ---------------------------------------------------------------------------
# Outcome Observation Input
# ---------------------------------------------------------------------------


@dataclass
class OutcomeObservation:
    """Normalized observation of a completed recovery outcome."""

    event_id: str
    context: str
    action: str
    success: bool
    recovered_value: float = 0.0
    cost: float = 0.0
    outcome_id: Optional[str] = None

    @classmethod
    def from_sim_outcome(
        cls,
        sim_outcome: SimOutcome,
        context: str,
    ) -> OutcomeObservation:
        action_str = (
            sim_outcome.action.value
            if isinstance(sim_outcome.action, SimAction)
            else str(sim_outcome.action)
        )
        return cls(
            event_id=sim_outcome.event_id,
            context=context,
            action=action_str,
            success=sim_outcome.success,
            recovered_value=sim_outcome.recovered_value,
            cost=sim_outcome.action_cost + sim_outcome.friction_cost,
            outcome_id=f"sim_{sim_outcome.event_id}",
        )


# ---------------------------------------------------------------------------
# Idempotent Statistical Updater
# ---------------------------------------------------------------------------


class StatisticalUpdater:
    """Manages idempotent Bayesian updates to bandit arms.

    Guarantees duplicate outcomes (identified by event_id or outcome_id)
    cannot update arm counts or Alpha/Beta parameters twice.
    """

    def __init__(self, bandit: ThompsonSamplingBandit) -> None:
        self.bandit = bandit
        self._processed_ids: set[str] = set()

    def process_observations(
        self,
        observations: list[OutcomeObservation],
        decay_factor: float = 1.0,
    ) -> tuple[dict[str, dict[str, ArmState]], dict[str, dict[str, ArmState]], list[OutcomeObservation]]:
        """Apply Bayesian updates to bandit arms for new, non-duplicate observations.

        Returns
        -------
        tuple: (pre_snapshot, post_snapshot, applied_observations)
        """
        # 1. Deduplicate observations
        applied: list[OutcomeObservation] = []
        for obs in observations:
            unique_key = obs.outcome_id or obs.event_id
            if unique_key in self._processed_ids:
                continue
            self._processed_ids.add(unique_key)
            applied.append(obs)

        # 2. Snapshot arm states before update for affected contexts
        contexts = {obs.context for obs in applied}
        pre_snapshot: dict[str, dict[str, ArmState]] = {}
        for ctx in contexts:
            pre_snapshot[ctx] = {}
            for act in self.bandit.candidate_actions:
                arm = self.bandit.state.get_arm(ctx, act)
                pre_snapshot[ctx][act] = ArmState(
                    alpha=arm.alpha,
                    beta=arm.beta,
                    attempt_count=arm.attempt_count,
                    success_count=arm.success_count,
                    failure_count=arm.failure_count,
                    prior_alpha=arm.prior_alpha,
                    prior_beta=arm.prior_beta,
                )

        # 3. Apply Bayesian updates
        for obs in applied:
            self.bandit.observe_outcome(
                context=obs.context,
                action=obs.action,
                success=obs.success,
                decay_factor=decay_factor,
            )

        # 4. Snapshot arm states after update
        post_snapshot: dict[str, dict[str, ArmState]] = {}
        for ctx in contexts:
            post_snapshot[ctx] = {}
            for act in self.bandit.candidate_actions:
                arm = self.bandit.state.get_arm(ctx, act)
                post_snapshot[ctx][act] = ArmState(
                    alpha=arm.alpha,
                    beta=arm.beta,
                    attempt_count=arm.attempt_count,
                    success_count=arm.success_count,
                    failure_count=arm.failure_count,
                    prior_alpha=arm.prior_alpha,
                    prior_beta=arm.prior_beta,
                )

        return pre_snapshot, post_snapshot, applied

    def is_processed(self, identifier: str) -> bool:
        """Check if an event_id or outcome_id has already been processed."""
        return identifier in self._processed_ids


# ---------------------------------------------------------------------------
# Reflection Agent
# ---------------------------------------------------------------------------


class ReflectionAgent:
    """Analyzes batches of outcomes and explains Bayesian shifts and policy changes."""

    def __init__(
        self,
        bandit: Optional[ThompsonSamplingBandit] = None,
        persistence_path: Optional[Path | str] = "data/reflections.json",
    ) -> None:
        self.bandit = bandit or ThompsonSamplingBandit()
        self.updater = StatisticalUpdater(self.bandit)
        self.persistence_path = Path(persistence_path) if persistence_path else None
        self._history: list[BatchReflectionRecord] = []

    def reflect_batch(
        self,
        observations: list[OutcomeObservation],
        batch_id: Optional[str] = None,
        apply_updates: bool = True,
        decay_factor: float = 1.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> BatchReflectionRecord:
        """Execute reflection workflow on a completed outcome batch.

        Parameters
        ----------
        observations:
            List of completed outcome observations.
        batch_id:
            Optional identifier for this batch run.
        apply_updates:
            If True, statistical updater applies Bayesian Beta updates.
            If False, assumes updates were already applied streaming-style.
        """
        bid = batch_id or f"batch_{uuid.uuid4().hex[:10]}"

        if apply_updates:
            pre_snap, post_snap, applied = self.updater.process_observations(
                observations=observations, decay_factor=decay_factor
            )
        else:
            # Updates already applied during event execution
            applied = list(observations)
            contexts = {obs.context for obs in applied}
            pre_snap = {}
            post_snap = {}
            for ctx in contexts:
                post_snap[ctx] = {}
                pre_snap[ctx] = {}
                for act in self.bandit.candidate_actions:
                    arm = self.bandit.state.get_arm(ctx, act)
                    # Use current arm as post, and reconstruct approximate pre or prior
                    post_snap[ctx][act] = ArmState(
                        alpha=arm.alpha,
                        beta=arm.beta,
                        attempt_count=arm.attempt_count,
                        success_count=arm.success_count,
                        failure_count=arm.failure_count,
                        prior_alpha=arm.prior_alpha,
                        prior_beta=arm.prior_beta,
                    )
                    pre_snap[ctx][act] = ArmState(
                        alpha=arm.prior_alpha,
                        beta=arm.prior_beta,
                        attempt_count=0,
                        success_count=0,
                        failure_count=0,
                        prior_alpha=arm.prior_alpha,
                        prior_beta=arm.prior_beta,
                    )

        # Group observations by context
        context_groups: dict[str, list[OutcomeObservation]] = defaultdict(list)
        for obs in applied:
            context_groups[obs.context].append(obs)

        context_reflections: list[ContextReflection] = []

        for ctx, ctx_obs in context_groups.items():
            ref = self._analyze_context_segment(
                context=ctx,
                observations=ctx_obs,
                pre_arms=pre_snap.get(ctx, {}),
                post_arms=post_snap.get(ctx, {}),
            )
            context_reflections.append(ref)

        # Calculate overall batch metrics
        total_events = len(applied)
        total_successes = sum(1 for o in applied if o.success)
        total_failures = total_events - total_successes
        overall_rec_rate = total_successes / total_events if total_events else 0.0
        total_rev = sum(o.recovered_value for o in applied)
        total_cost = sum(o.cost for o in applied)
        total_net = total_rev - total_cost

        # Synthesize overall summary statement
        policy_shifts = [r for r in context_reflections if r.policy_changed]
        if policy_shifts:
            summary = (
                f"Batch {bid} processed {total_events} observations ({overall_rec_rate:.1%} recovery, "
                f"₹{total_net:,.2f} net value). Policy shifted in {len(policy_shifts)} context segment(s): "
                + "; ".join([f"{r.context}: {r.policy_before} -> {r.policy_after}" for r in policy_shifts])
            )
        else:
            summary = (
                f"Batch {bid} processed {total_events} observations across {len(context_groups)} context(s) "
                f"with {overall_rec_rate:.1%} recovery rate and ₹{total_net:,.2f} net value. "
                f"No optimal policy shifts observed."
            )

        record = BatchReflectionRecord(
            batch_id=bid,
            timestamp=datetime.now(UTC),
            total_events=total_events,
            total_successes=total_successes,
            total_failures=total_failures,
            overall_recovery_rate=round(overall_rec_rate, 4),
            total_recovered_revenue=round(total_rev, 2),
            total_cost=round(total_cost, 2),
            total_net_value=round(total_net, 2),
            context_reflections=context_reflections,
            learning_summary=summary,
            metadata=metadata or {},
        )

        self._history.append(record)
        self._persist_record(record)

        return record

    def _analyze_context_segment(
        self,
        context: str,
        observations: list[OutcomeObservation],
        pre_arms: dict[str, ArmState],
        post_arms: dict[str, ArmState],
    ) -> ContextReflection:
        """Analyze statistical and policy shifts for a single context segment."""
        total_obs = len(observations)
        successes = sum(1 for o in observations if o.success)
        failures = total_obs - successes
        rec_rate = successes / total_obs if total_obs else 0.0
        recovered_rev = sum(o.recovered_value for o in observations)
        cost = sum(o.cost for o in observations)
        net_val = recovered_rev - cost

        prev_stats: dict[str, dict[str, float]] = {}
        new_stats: dict[str, dict[str, float]] = {}
        changes_in_mean: dict[str, float] = {}

        # Default benchmark transaction amount for EV policy evaluation (mid-tier baseline ~2000 INR)
        eval_amount = 2000.0

        pre_evs: dict[str, float] = {}
        post_evs: dict[str, float] = {}

        for act in self.bandit.candidate_actions:
            pre_arm = pre_arms.get(act, ArmState())
            post_arm = post_arms.get(act, ArmState())

            prev_stats[act] = {
                "alpha": round(pre_arm.alpha, 3),
                "beta": round(pre_arm.beta, 3),
                "posterior_mean": round(pre_arm.posterior_mean, 4),
            }
            new_stats[act] = {
                "alpha": round(post_arm.alpha, 3),
                "beta": round(post_arm.beta, 3),
                "posterior_mean": round(post_arm.posterior_mean, 4),
            }
            delta_mean = round(post_arm.posterior_mean - pre_arm.posterior_mean, 4)
            changes_in_mean[act] = delta_mean

            pre_evs[act] = self.bandit.compute_ev(pre_arm.posterior_mean, eval_amount, act)
            post_evs[act] = self.bandit.compute_ev(post_arm.posterior_mean, eval_amount, act)

        # Determine optimal policy before vs after
        policy_before = max(self.bandit.candidate_actions, key=lambda a: pre_evs[a])
        policy_after = max(self.bandit.candidate_actions, key=lambda a: post_evs[a])
        policy_changed = policy_before != policy_after

        # Identify most active or impactful action in this batch
        action_counts = defaultdict(int)
        for o in observations:
            action_counts[o.action] += 1
        primary_action = max(action_counts.keys(), key=lambda a: action_counts[a]) if action_counts else policy_after

        pre_m = prev_stats[primary_action]["posterior_mean"]
        post_m = new_stats[primary_action]["posterior_mean"]

        # Generate learning statement
        direction = "increased" if post_m >= pre_m else "decreased"
        fmt_context = context.replace("+", "/")

        if policy_changed:
            statement = (
                f"Across {total_obs} observations in {fmt_context}, {primary_action} {direction} from "
                f"{pre_m*100:.0f}% to {post_m*100:.0f}% estimated success, making it the highest-EV recovery action "
                f"(shifting from {policy_before})."
            )
        else:
            statement = (
                f"Across {total_obs} observations in {fmt_context}, {primary_action} {direction} from "
                f"{pre_m*100:.0f}% to {post_m*100:.0f}% estimated success, "
                f"maintaining {policy_after} as the highest-EV recovery action."
            )

        return ContextReflection(
            context=context,
            total_observations=total_obs,
            successes=successes,
            failures=failures,
            recovery_rate=round(rec_rate, 4),
            recovered_revenue=round(recovered_rev, 2),
            cost=round(cost, 2),
            net_value=round(net_val, 2),
            previous_statistics=prev_stats,
            new_statistics=new_stats,
            changes_in_posterior_mean=changes_in_mean,
            policy_before=policy_before,
            policy_after=policy_after,
            policy_changed=policy_changed,
            learning_statement=statement,
        )

    def _persist_record(self, record: BatchReflectionRecord) -> None:
        """Append reflection record to JSON persistence storage."""
        if not self.persistence_path:
            return
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        records = []
        if self.persistence_path.exists():
            try:
                with open(self.persistence_path, "r", encoding="utf-8") as f:
                    records = json.load(f)
            except Exception:
                records = []
        records.append(record.model_dump(mode="json"))
        with open(self.persistence_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, default=str)

    def load_history(self) -> list[BatchReflectionRecord]:
        """Load stored reflection records from persistence."""
        if not self.persistence_path or not self.persistence_path.exists():
            return self._history
        try:
            with open(self.persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._history = [BatchReflectionRecord(**item) for item in data]
        except Exception:
            pass
        return self._history

    async def reflect(self, outcomes: list[OutcomeResult]) -> dict[str, Any]:
        """Legacy asynchronous stub for compatibility."""
        observations = [
            OutcomeObservation(
                event_id=o.event_id,
                context="UNKNOWN+MID",
                action=o.strategy_applied.value,
                success=(o.amount_recovered > 0),
                recovered_value=o.amount_recovered,
                cost=0.0,
                outcome_id=o.outcome_id,
            )
            for o in outcomes
        ]
        record = self.reflect_batch(observations)
        return record.model_dump(mode="json")
