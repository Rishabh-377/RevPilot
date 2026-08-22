"""
Bandit State & Arm Data Structures
==================================

Maintains Beta(alpha, beta) distributions, attempt counts, and outcomes
per (context, action) pair. Supports serialization and persistent state.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ArmState:
    """State tracking for a single (context, action) bandit arm."""

    alpha: float = 1.0
    beta: float = 1.0
    attempt_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    prior_alpha: float = 1.0
    prior_beta: float = 1.0

    @property
    def posterior_mean(self) -> float:
        """Bayesian expected value of theta: E[theta] = alpha / (alpha + beta)."""
        total = self.alpha + self.beta
        return self.alpha / total if total > 0 else 0.5

    @property
    def variance(self) -> float:
        """Variance of Beta distribution."""
        total = self.alpha + self.beta
        if total <= 0:
            return 0.0
        return (self.alpha * self.beta) / ((total ** 2) * (total + 1))

    def sample_probability(self, rng: random.Random | None = None) -> float:
        """Sample a success probability theta ~ Beta(alpha, beta)."""
        # random.Random.betavariate(alpha, beta)
        r = rng or random
        # Ensure parameters are strictly > 0
        a = max(1e-4, self.alpha)
        b = max(1e-4, self.beta)
        return float(r.betavariate(a, b))

    def update(self, success: bool, decay_factor: float = 1.0) -> None:
        """Update arm counts with outcome and optional time decay."""
        if decay_factor < 1.0:
            # Apply time decay towards initial prior
            self.alpha = (self.alpha - self.prior_alpha) * decay_factor + self.prior_alpha
            self.beta = (self.beta - self.prior_beta) * decay_factor + self.prior_beta

        if success:
            self.alpha += 1.0
            self.success_count += 1
        else:
            self.beta += 1.0
            self.failure_count += 1

        self.attempt_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "attempt_count": self.attempt_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "posterior_mean": round(self.posterior_mean, 4),
            "prior_alpha": round(self.prior_alpha, 4),
            "prior_beta": round(self.prior_beta, 4),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArmState:
        return cls(
            alpha=float(data.get("alpha", 1.0)),
            beta=float(data.get("beta", 1.0)),
            attempt_count=int(data.get("attempt_count", 0)),
            success_count=int(data.get("success_count", 0)),
            failure_count=int(data.get("failure_count", 0)),
            prior_alpha=float(data.get("prior_alpha", 1.0)),
            prior_beta=float(data.get("prior_beta", 1.0)),
        )


class BanditState:
    """Repository and persistence manager for segmented bandit arms."""

    def __init__(self, persistence_path: Path | str | None = None) -> None:
        self.persistence_path = Path(persistence_path) if persistence_path else None
        # Nested structure: self.arms[context][action] = ArmState
        self.arms: dict[str, dict[str, ArmState]] = {}
        self.total_decisions: int = 0

    def get_arm(self, context: str, action: str, default_alpha: float = 1.0, default_beta: float = 1.0) -> ArmState:
        """Get or initialize arm state for a given (context, action) pair."""
        if context not in self.arms:
            self.arms[context] = {}
        if action not in self.arms[context]:
            self.arms[context][action] = ArmState(
                alpha=default_alpha,
                beta=default_beta,
                prior_alpha=default_alpha,
                prior_beta=default_beta,
            )
        return self.arms[context][action]

    def set_arm(self, context: str, action: str, arm: ArmState) -> None:
        if context not in self.arms:
            self.arms[context] = {}
        self.arms[context][action] = arm

    def export_dict(self) -> dict[str, Any]:
        """Export all arms to nested dictionary."""
        return {
            "total_decisions": self.total_decisions,
            "contexts": {
                ctx: {act: arm.to_dict() for act, arm in act_dict.items()}
                for ctx, act_dict in self.arms.items()
            },
        }

    def load(self, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Load state from dictionary or persistence file."""
        source_data = data
        if source_data is None and self.persistence_path and self.persistence_path.exists():
            with open(self.persistence_path, encoding="utf-8") as f:
                source_data = json.load(f)

        if not source_data:
            return {}

        self.total_decisions = int(source_data.get("total_decisions", 0))
        contexts = source_data.get("contexts", {})
        for ctx, act_dict in contexts.items():
            self.arms[ctx] = {}
            for act, arm_data in act_dict.items():
                self.arms[ctx][act] = ArmState.from_dict(arm_data)

        return source_data

    def save(self, target_path: Path | str | None = None) -> None:
        """Save bandit state to JSON file."""
        dest = Path(target_path) if target_path else self.persistence_path
        if not dest:
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(self.export_dict(), f, indent=2)
