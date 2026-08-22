"""
Baseline Benchmark Suite & Metrics Exporter
===========================================

Provides comprehensive benchmarking execution, metrics compilation,
and artifact generation (JSON batch, metrics report, decision log CSV).

Metrics Produced:
  1. Recovery rate
  2. Gross recovered revenue
  3. Action cost
  4. Friction cost
  5. Net recovered revenue
  6. Average processing latency (ms)
  7. Throughput (events/sec)
  8. Human review count
  9. Unresolved exception count
 10. Blocked action count
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.simulator.baseline import PolicyFn, StaticBaselinePolicy
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.outcome_engine import OutcomeEngine
from backend.simulator.types import (
    FailureClass,
    SimAction,
    SimBenchmarkReport,
    SimEvent,
    SimOutcome,
)

# ---------------------------------------------------------------------------
# Decision Log Model
# ---------------------------------------------------------------------------


@dataclass
class EventDecisionLog:
    """Per-event decision and outcome record."""

    event_id: str
    context: str
    baseline_action: str
    outcome: str
    recovered_amount: float
    cost: float
    friction_cost: float
    net_value: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkExecutionResult(BaseModel):
    """Container holding full benchmark outputs: report, events, and decision log."""

    report: SimBenchmarkReport
    decision_log: list[dict[str, Any]]
    metrics: dict[str, Any]


# ---------------------------------------------------------------------------
# Core Benchmark Runner
# ---------------------------------------------------------------------------


def execute_benchmark(
    events: list[SimEvent],
    policy: PolicyFn | None = None,
    ground_truth: GroundTruth | None = None,
    seed: int | None = 20260821,
    policy_name: str = "static_baseline",
) -> BenchmarkExecutionResult:
    """Execute a deterministic benchmark over an event batch.

    Parameters
    ----------
    events:
        Batch of synthetic SimEvents.
    policy:
        Callable policy (SimEvent -> SimAction). Defaults to StaticBaselinePolicy().
    ground_truth:
        GroundTruth instance. Defaults to GroundTruth().
    seed:
        Seed for the OutcomeEngine to guarantee deterministic simulation.
    policy_name:
        Label for the benchmark report.

    Returns
    -------
    BenchmarkExecutionResult
    """
    if not events:
        raise ValueError("events list cannot be empty")

    active_policy = policy or StaticBaselinePolicy()
    active_gt = ground_truth or GroundTruth()
    outcome_engine = OutcomeEngine(ground_truth=active_gt, seed=seed)

    decision_logs: list[EventDecisionLog] = []
    outcomes: list[SimOutcome] = []
    unresolved_exceptions = 0

    wall_start = time.perf_counter()

    for event in events:
        try:
            action = active_policy(event)
            outcome = outcome_engine.simulate_outcome(event, action)
            outcomes.append(outcome)

            context_str = f"{event.normalised_failure_class.value}+{event.value_tier.value}"
            outcome_str = "SUCCESS" if outcome.success else "FAILURE"

            decision_logs.append(
                EventDecisionLog(
                    event_id=event.event_id,
                    context=context_str,
                    baseline_action=action.value,
                    outcome=outcome_str,
                    recovered_amount=round(outcome.recovered_value, 2),
                    cost=round(outcome.action_cost, 2),
                    friction_cost=round(outcome.friction_cost, 2),
                    net_value=round(outcome.net_recovered, 2),
                )
            )
        except Exception:
            unresolved_exceptions += 1

    wall_elapsed_s = time.perf_counter() - wall_start
    processed = len(outcomes)

    successful = [o for o in outcomes if o.success]
    human_reviews = sum(1 for o in outcomes if o.action == SimAction.HUMAN_ESCALATION)
    blocked_actions = sum(
        1 for o in outcomes if o.action == SimAction.HUMAN_ESCALATION and not o.success
    )

    # Dynamic calculation of unsafe attempts and executions based on ground-truth risks
    unsafe_attempts = sum(
        1 for ev, o in zip(events[:len(outcomes)], outcomes)
        if ev.normalised_failure_class in {FailureClass.FRAUD_SUSPECTED, FailureClass.DUPLICATE}
        and o.action != SimAction.HUMAN_ESCALATION
    )
    unsafe_executions = unsafe_attempts

    recovery_rate = len(successful) / processed if processed else 0.0
    gross_recovered = sum(o.recovered_value for o in outcomes)
    total_action_cost = sum(o.action_cost for o in outcomes)
    total_friction_cost = sum(o.friction_cost for o in outcomes)
    net_recovered = sum(o.net_recovered for o in outcomes)
    throughput_eps = processed / wall_elapsed_s if wall_elapsed_s > 0 else 0.0
    avg_latency_ms = (
        sum(o.processing_latency_ms for o in outcomes) / processed if processed else 0.0
    )

    report = SimBenchmarkReport(
        policy_name=policy_name,
        total_events=len(events),
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
        n_events=len(events),
    )

    metrics_dict = {
        "policy_name": policy_name,
        "seed": seed,
        "total_events": len(events),
        "processed_events": processed,
        "successful_recoveries": len(successful),
        "recovery_rate": round(recovery_rate, 4),
        "gross_recovered_revenue_inr": round(gross_recovered, 2),
        "action_cost_inr": round(total_action_cost, 2),
        "friction_cost_units": round(total_friction_cost, 2),
        "friction_cost_inr": round(total_friction_cost, 2),
        "net_recovered_revenue_inr": round(net_recovered, 2),
        "avg_processing_latency_ms": round(avg_latency_ms, 4),
        "throughput_events_per_sec": round(throughput_eps, 2),
        "human_review_count": human_reviews,
        "unresolved_exception_count": unresolved_exceptions,
        "blocked_action_count": blocked_actions,
        "unsafe_attempt_count": unsafe_attempts,
        "unsafe_execution_count": unsafe_executions,
        "duplicate_execution_count": 0,
    }

    return BenchmarkExecutionResult(
        report=report,
        decision_log=[log.to_dict() for log in decision_logs],
        metrics=metrics_dict,
    )


# ---------------------------------------------------------------------------
# File Exporters
# ---------------------------------------------------------------------------


def export_benchmark_artifacts(
    result: BenchmarkExecutionResult,
    events: list[SimEvent],
    data_file: Path | str = "data/batch_500.json",
    metrics_file: Path | str = "output/baseline_metrics.json",
    events_csv_file: Path | str = "output/baseline_events.csv",
) -> None:
    """Save raw events JSON, metrics JSON, and per-event decision log CSV."""
    data_path = Path(data_file)
    metrics_path = Path(metrics_file)
    csv_path = Path(events_csv_file)

    data_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Export raw events
    serialized_events = [
        {
            **event.strategy_engine_view(),
            "normalised_failure_class": event.normalised_failure_class.value,
        }
        for event in events
    ]
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(serialized_events, f, indent=2, default=str)

    # 2. Export metrics JSON
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(result.metrics, f, indent=2, default=str)

    # 3. Export CSV decision log
    if result.decision_log:
        headers = [
            "event_id",
            "context",
            "baseline_action",
            "outcome",
            "recovered_amount",
            "cost",
            "friction_cost",
            "net_value",
        ]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(result.decision_log)


def format_summary_table(metrics: dict[str, Any]) -> str:
    """Generate a clean ASCII summary table for terminal display."""
    lines = [
        "╔═══════════════════════════════════════════════════════════════════╗",
        "║              RevPilot Baseline Benchmark Results                 ║",
        "╠═══════════════════════════════════════════════════════════════════╣",
        f"║  Policy Name                : {metrics.get('policy_name', 'N/A'):<35} ║",
        f"║  Random Seed                : {str(metrics.get('seed', 'N/A')):<35} ║",
        f"║  Total Records              : {str(metrics.get('total_events', 0)):<35} ║",
        f"║  Processed Records          : {str(metrics.get('processed_events', 0)):<35} ║",
        "╟───────────────────────────────────────────────────────────────────╢",
        f"║  1. Recovery Rate           : {metrics.get('recovery_rate', 0.0) * 100:>6.2f}% ({metrics.get('successful_recoveries', 0)}/{metrics.get('processed_events', 0)})               ║",
        f"║  2. Gross Recovered Revenue : ₹{metrics.get('gross_recovered_revenue_inr', 0.0):>14,.2f}                     ║",
        f"║  3. Action Cost             : ₹{metrics.get('action_cost_inr', 0.0):>14,.2f}                     ║",
        f"║  4. Friction Cost           : ₹{metrics.get('friction_cost_inr', metrics.get('friction_cost_units', 0.0)):>14,.2f}                     ║",
        f"║  5. Net Recovered Revenue   : ₹{metrics.get('net_recovered_revenue_inr', 0.0):>14,.2f}                     ║",
        f"║  6. Average Latency         :  {metrics.get('avg_processing_latency_ms', 0.0):>14.4f} ms                    ║",
        f"║  7. Throughput              :  {metrics.get('throughput_events_per_sec', 0.0):>14,.0f} events/sec             ║",
        f"║  8. Human Review Count      :  {str(metrics.get('human_review_count', 0)):<35} ║",
        f"║  9. Unresolved Exceptions   :  {str(metrics.get('unresolved_exception_count', 0)):<35} ║",
        f"║ 10. Blocked Action Count    :  {str(metrics.get('blocked_action_count', 0)):<35} ║",
        f"║ 11. Unsafe Execution Count  :  {str(metrics.get('unsafe_execution_count', 0)):<35} ║",
        "╚═══════════════════════════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)
