"""
Run Baseline Benchmark Script
=============================

CLI entrypoint to run the deterministic baseline benchmark, export artifacts,
and display summary metrics.

Usage:
    python -m scripts.run_baseline_benchmark --seed 20260821 --records 500
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.simulator.benchmark import (
    execute_benchmark,
    export_benchmark_artifacts,
    format_summary_table,
)
from backend.simulator.event_generator import EventGenerator


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run RevPilot baseline benchmark on synthetic payment failure events."
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260821,
        help="Random seed for reproducible event generation and simulation (default: 20260821)",
    )
    parser.add_argument(
        "--records",
        type=int,
        default=500,
        help="Number of synthetic failed-payment records to generate and evaluate (default: 500)",
    )
    parser.add_argument(
        "--data-file",
        type=str,
        default="data/batch_500.json",
        help="Target output path for raw event batch JSON (default: data/batch_500.json)",
    )
    parser.add_argument(
        "--metrics-file",
        type=str,
        default="output/baseline_metrics.json",
        help="Target output path for benchmark summary metrics JSON (default: output/baseline_metrics.json)",
    )
    parser.add_argument(
        "--events-csv",
        type=str,
        default="output/baseline_events.csv",
        help="Target output path for per-event decision log CSV (default: output/baseline_events.csv)",
    )

    args = parser.parse_args()

    # 1. Generate deterministic synthetic events
    generator = EventGenerator(seed=args.seed, n=args.records)
    events = generator.generate(n=args.records, seed=args.seed)

    # 2. Run baseline benchmark
    result = execute_benchmark(
        events=events,
        seed=args.seed,
        policy_name="static_baseline",
    )

    # 3. Export artifacts
    export_benchmark_artifacts(
        result=result,
        events=events,
        data_file=args.data_file,
        metrics_file=args.metrics_file,
        events_csv_file=args.events_csv,
    )

    # 4. Print clean summary table
    table_str = format_summary_table(result.metrics)
    print("\n" + table_str + "\n")
    print(f"Artifacts successfully generated:")
    print(f"  • Raw Events Batch : {args.data_file}")
    print(f"  • Summary Metrics  : {args.metrics_file}")
    print(f"  • Decision Log CSV : {args.events_csv}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
