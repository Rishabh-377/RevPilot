"""
tests/test_benchmark.py
=======================

Comprehensive tests for benchmark execution, determinism, artifact exports,
and metric calculations:
  - Deterministic execution given the same seed
  - Correctness and presence of all 10 target benchmark metrics
  - Per-event decision log schema (event_id, context, baseline_action, outcome, recovered_amount, cost, net_value)
  - Raw event batch serialization / deserialization
  - Artifact export integrity (data/batch_500.json, output/baseline_metrics.json, output/baseline_events.csv)
  - Summary table formatting
"""

from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

import pytest

from backend.simulator.baseline import StaticBaselinePolicy
from backend.simulator.benchmark import (
    BenchmarkExecutionResult,
    EventDecisionLog,
    execute_benchmark,
    export_benchmark_artifacts,
    format_summary_table,
)
from backend.simulator.event_generator import EventGenerator
from backend.simulator.ground_truth import GroundTruth
from backend.simulator.types import SimAction, SimEvent


@pytest.fixture(scope="module")
def fixed_seed_events() -> list[SimEvent]:
    gen = EventGenerator(seed=20260821, n=500)
    return gen.generate(n=500, seed=20260821)


@pytest.fixture(scope="module")
def benchmark_result(fixed_seed_events: list[SimEvent]) -> BenchmarkExecutionResult:
    return execute_benchmark(
        events=fixed_seed_events,
        seed=20260821,
        policy_name="static_baseline",
    )


class TestBenchmarkExecution:
    def test_deterministic_output_under_same_seed(
        self, fixed_seed_events: list[SimEvent]
    ) -> None:
        """Running the benchmark twice with identical seed must produce identical recovery & financial metrics."""
        res1 = execute_benchmark(events=fixed_seed_events, seed=20260821)
        res2 = execute_benchmark(events=fixed_seed_events, seed=20260821)

        # Compare all deterministic business metrics (excluding non-deterministic wall-clock timings)
        deterministic_keys = [
            "policy_name",
            "seed",
            "total_events",
            "processed_events",
            "successful_recoveries",
            "recovery_rate",
            "gross_recovered_revenue_inr",
            "action_cost_inr",
            "friction_cost_units",
            "net_recovered_revenue_inr",
            "human_review_count",
            "unresolved_exception_count",
            "blocked_action_count",
        ]
        for key in deterministic_keys:
            assert res1.metrics[key] == res2.metrics[key], f"Mismatch in {key}"

        # Per-event decision logs must be 100% identical
        assert len(res1.decision_log) == len(res2.decision_log)
        assert res1.decision_log == res2.decision_log

    def test_ten_required_metrics_present(
        self, benchmark_result: BenchmarkExecutionResult
    ) -> None:
        """Assert all 10 target metrics are computed and present."""
        m = benchmark_result.metrics
        required_keys = [
            "recovery_rate",
            "gross_recovered_revenue_inr",
            "action_cost_inr",
            "friction_cost_units",
            "net_recovered_revenue_inr",
            "avg_processing_latency_ms",
            "throughput_events_per_sec",
            "human_review_count",
            "unresolved_exception_count",
            "blocked_action_count",
        ]
        for key in required_keys:
            assert key in m, f"Metric {key} missing from benchmark metrics"

    def test_metric_values_sanity(
        self, benchmark_result: BenchmarkExecutionResult
    ) -> None:
        m = benchmark_result.metrics
        assert m["total_events"] == 500
        assert m["processed_events"] == 500
        assert 0.0 <= m["recovery_rate"] <= 1.0
        assert m["gross_recovered_revenue_inr"] >= 0.0
        assert m["action_cost_inr"] >= 0.0
        assert m["friction_cost_units"] >= 0.0
        assert m["net_recovered_revenue_inr"] <= m["gross_recovered_revenue_inr"]
        assert m["avg_processing_latency_ms"] >= 0.0
        assert m["throughput_events_per_sec"] > 0.0
        assert m["human_review_count"] >= 0
        assert m["unresolved_exception_count"] == 0
        assert m["blocked_action_count"] >= 0

    def test_decision_log_count_and_columns(
        self, benchmark_result: BenchmarkExecutionResult
    ) -> None:
        log = benchmark_result.decision_log
        assert len(log) == 500
        required_cols = {
            "event_id",
            "context",
            "baseline_action",
            "outcome",
            "recovered_amount",
            "cost",
            "friction_cost",
            "net_value",
        }
        for entry in log[:20]:
            assert set(entry.keys()) == required_cols
            assert entry["outcome"] in {"SUCCESS", "FAILURE"}
            assert entry["recovered_amount"] >= 0.0
            assert entry["cost"] >= 0.0
            assert entry["friction_cost"] >= 0.0
            assert entry["net_value"] == pytest.approx(
                entry["recovered_amount"] - entry["cost"] - entry["friction_cost"], abs=0.01
            )
            assert "+" in entry["context"]

    def test_summary_table_formatting(
        self, benchmark_result: BenchmarkExecutionResult
    ) -> None:
        table = format_summary_table(benchmark_result.metrics)
        assert "RevPilot Baseline Benchmark Results" in table
        assert "Recovery Rate" in table
        assert "Gross Recovered Revenue" in table
        assert "Net Recovered Revenue" in table


class TestArtifactExports:
    def test_export_and_file_verification(
        self,
        fixed_seed_events: list[SimEvent],
        benchmark_result: BenchmarkExecutionResult,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            data_file = tmp_path / "data" / "batch_500.json"
            metrics_file = tmp_path / "output" / "baseline_metrics.json"
            csv_file = tmp_path / "output" / "baseline_events.csv"

            export_benchmark_artifacts(
                result=benchmark_result,
                events=fixed_seed_events,
                data_file=data_file,
                metrics_file=metrics_file,
                events_csv_file=csv_file,
            )

            assert data_file.exists()
            assert metrics_file.exists()
            assert csv_file.exists()

            # Verify JSON events batch
            with open(data_file, "r", encoding="utf-8") as f:
                loaded_events = json.load(f)
            assert len(loaded_events) == 500
            assert loaded_events[0]["event_id"] == fixed_seed_events[0].event_id

            # Verify JSON metrics
            with open(metrics_file, "r", encoding="utf-8") as f:
                loaded_metrics = json.load(f)
            assert loaded_metrics["total_events"] == 500
            assert loaded_metrics["recovery_rate"] == benchmark_result.metrics["recovery_rate"]

            # Verify CSV decision log
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 500
            assert set(rows[0].keys()) == {
                "event_id",
                "context",
                "baseline_action",
                "outcome",
                "recovered_amount",
                "cost",
                "friction_cost",
                "net_value",
            }
            assert rows[0]["event_id"] == fixed_seed_events[0].event_id
