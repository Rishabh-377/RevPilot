"""
Non-Stationary Benchmark Runner
===============================

CLI script to run the two-phase non-stationary payment environment benchmark.
Demonstrates autonomous policy adaptation under hidden environmental shifts
without code changes.

Usage:
    python -m scripts.run_non_stationary_benchmark --records 150 --seed 20260821
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.simulator.non_stationary import (
    NonStationaryBenchmarkReport,
    run_non_stationary_experiment,
)


def format_non_stationary_table(report: NonStationaryBenchmarkReport) -> str:
    shift = report.environment_shift
    p_before = report.policy_before
    p_after = report.policy_after

    lines = [
        "╔═══════════════════════════════════════════════════════════════════════════════════════╗",
        "║                   RevPilot Non-Stationary Environment Adaptation                      ║",
        "╠═══════════════════════════════════════════════════════════════════════════════════════╣",
        f"║  Target Context: {report.target_context:<18} Phase A: {report.phase_a_records} events  Phase B: {report.phase_b_records} events  ║",
        "╠═══════════════════════════════════════════════════════════════════════════════════════╣",
        "║  Phase / Metric           │ Action / Parameter    │ Probability / Stats   │ EV (₹1,500)   ║",
        "╟───────────────────────────┼───────────────────────┼───────────────────────┼───────────────╢",
        "║  1. PHASE A (Normal)      │                       │                       │               ║",
        f"║     • Ground Truth P(Win) │ {shift.action_degraded.value:<21} │ P = {shift.prob_before:<17.2f} │ Ground Truth  ║",
        f"║     • Learned Posterior   │ {p_before.preferred_action:<21} │ μ = {p_before.posterior_mean:<6.4f} (α={p_before.alpha:4.1f},β={p_before.beta:4.1f})│ ₹{p_before.expected_value:>11,.2f}  ║",
        f"║     • Preferred Policy    │ {p_before.preferred_action:<21} │ OPTIMAL EXPLOIT       │ HIGHEST EV    ║",
        "╟───────────────────────────┼───────────────────────┼───────────────────────┼───────────────╢",
        "║  2. HIDDEN SHIFT (Phase B)│                       │                       │               ║",
        f"║     • Shift Description   │ Gateway queue drop    │ {shift.action_degraded.value} {shift.prob_before:.2f} -> {shift.prob_after:.2f} │ Hidden Reality║",
        "║     • Strategy Awareness  │ ZERO (Information Bar)│ Observes binary 0/1   │ No Hint Given ║",
        "╟───────────────────────────┼───────────────────────┼───────────────────────┼───────────────╢",
        "║  3. PHASE B (Adapted)     │                       │                       │               ║",
        f"║     • Posterior Shift     │ {shift.action_degraded.value:<21} │ Δμ = {report.posterior_delta:<16.4f} │ Posterior Drop║",
        f"║     • Adapted Policy      │ {p_after.preferred_action:<21} │ μ = {p_after.posterior_mean:<6.4f} (α={p_after.alpha:4.1f},β={p_after.beta:4.1f})│ ₹{p_after.expected_value:>11,.2f}  ║",
        f"║     • Policy Change       │ {p_before.preferred_action} -> {p_after.preferred_action:<10} │ AUTONOMOUS SHIFT      │ No Code Change║",
        "╚═══════════════════════════════════════════════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run RevPilot Non-Stationary Adaptation Benchmark."
    )
    parser.add_argument(
        "--records",
        type=int,
        default=150,
        help="Number of events per phase (default: 150)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260821,
        help="Random seed for reproducible simulation (default: 20260821)",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default="output/non_stationary_shift.json",
        help="Target output JSON path (default: output/non_stationary_shift.json)",
    )

    args = parser.parse_args()

    print(f"\n[1/2] Executing Non-Stationary Experiment (Phase A: {args.records} events, Phase B: {args.records} events)...")
    report = run_non_stationary_experiment(
        phase_a_records=args.records,
        phase_b_records=args.records,
        seed=args.seed,
    )

    out_path = Path(args.output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report.model_dump(mode="json"), f, indent=2)

    table_str = format_non_stationary_table(report)
    print("\n" + table_str + "\n")

    print(f"Learning Statement:\n  {report.learning_statement}\n")
    print(f"Artifact successfully saved: {out_path}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
