"""
Outcome Engine
==============

Given a SimEvent and a selected SimAction, the OutcomeEngine queries the
hidden GroundTruth probability table, samples a Bernoulli outcome, and
returns a fully-populated SimOutcome.

This is the only component that reads ``event.normalised_failure_class``
during normal operation — reinforcing the information barrier.

Reproducibility
---------------
Pass a fixed ``seed`` to ``OutcomeEngine`` to get deterministic outcomes
for the same (event, action) pairs across benchmark runs.
"""

from __future__ import annotations

import random
import time
from typing import Optional

from backend.simulator.ground_truth import GroundTruth
from backend.simulator.types import SimAction, SimEvent, SimOutcome


class OutcomeEngine:
    """Simulates payment recovery outcomes using the hidden GroundTruth.

    Parameters
    ----------
    ground_truth:
        A GroundTruth instance (optionally with drift applied).
    seed:
        RNG seed for reproducible outcome sampling.  ``None`` = non-deterministic.
    """

    def __init__(
        self,
        ground_truth: Optional[GroundTruth] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.ground_truth = ground_truth or GroundTruth()
        self._rng = random.Random(seed)

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def simulate_outcome(self, event: SimEvent, action: SimAction) -> SimOutcome:
        """Simulate the outcome of applying ``action`` to ``event``.

        Steps
        -----
        1. Query P(success | failure_class, value_tier, action) from GroundTruth.
        2. Bernoulli-sample success/failure.
        3. Apply action cost, friction cost, and resolution delay.
        4. Compute net recovered value.
        5. Return SimOutcome.

        Parameters
        ----------
        event:
            A SimEvent (must contain ``normalised_failure_class``).
        action:
            The recovery action to simulate.

        Returns
        -------
        SimOutcome
        """
        t_start = time.perf_counter()

        p_success = self.ground_truth.get_recovery_probability(event, action)
        success = self._rng.random() < p_success

        recovered_value = event.amount if success else 0.0
        action_cost = self.ground_truth.get_action_cost(action)
        friction_units = self.ground_truth.get_friction_cost(action)
        resolution_delay_s = self.ground_truth.get_resolution_delay_s(action)

        # Convert dimensionless customer friction units to INR to ensure unit compatibility
        INR_PER_FRICTION_UNIT = 1.0
        friction_cost_inr = friction_units * INR_PER_FRICTION_UNIT
        net = recovered_value - action_cost - friction_cost_inr

        latency_ms = (time.perf_counter() - t_start) * 1000.0

        return SimOutcome(
            event_id=event.event_id,
            action=action,
            success=success,
            recovered_value=recovered_value,
            action_cost=action_cost,
            friction_cost=friction_cost_inr,
            resolution_delay_s=resolution_delay_s,
            net_recovered=net,
            processing_latency_ms=latency_ms,
        )

    def simulate_batch(
        self,
        events: list[SimEvent],
        actions: list[SimAction],
    ) -> list[SimOutcome]:
        """Simulate outcomes for a parallel list of (event, action) pairs.

        Parameters
        ----------
        events:
            List of SimEvent instances.
        actions:
            Corresponding list of SimActions — must be the same length.

        Returns
        -------
        list[SimOutcome] in the same order as the input.
        """
        if len(events) != len(actions):
            raise ValueError(
                f"events and actions must have the same length "
                f"({len(events)} vs {len(actions)})"
            )
        return [self.simulate_outcome(e, a) for e, a in zip(events, actions)]
