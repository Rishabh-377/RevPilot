# Benchmark Methodology

## Purpose

Quantify RevPilot's recovery performance against naive baselines using simulated payment failure streams. Every claim of improvement must be backed by statistically significant benchmark results.

## KPIs

| KPI | Formula | Target | Unit |
|-----|---------|--------|------|
| Recovery Rate | `recovered / total_events` | > baseline + 10pp | fraction |
| Revenue Recovered | `Σ amount_recovered` | Maximize | INR |
| Time to Recovery | `avg(completed_at - timestamp)` | Minimize | ms |
| False Positive Rate | `retries_on_non_retryable / total_retries` | < 0.05 | fraction |
| Guardrail Block Rate | `blocked / total_decisions` | 0.05 – 0.20 | fraction |

## Baseline Strategies

| Baseline | Description | Expected Recovery Rate |
|----------|-------------|----------------------|
| `no_retry` | Never retry any failure | 0% |
| `immediate_retry` | Always retry once immediately | ~20-30% |
| `fixed_delay_retry` | Always retry once after 5 min | ~25-35% |
| `random_strategy` | Pick a random eligible strategy | ~25-35% |

RevPilot (Thompson Sampling) must demonstrate statistically significant improvement over `fixed_delay_retry`.

## Simulation Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `n_events` | 1000 | Number of failure events per run |
| `n_runs` | 10 | Number of independent runs |
| `failure_distribution` | Weighted by real-world frequency | How failures are sampled |
| `amount_range` | [100, 50000] INR | Transaction amount range |
| `time_horizon` | 24 hours simulated | Time window |

### Failure Distribution (Default)

| Failure Reason | Weight |
|---------------|--------|
| insufficient_funds | 0.25 |
| network_error | 0.15 |
| authentication_failed | 0.15 |
| bank_declined | 0.12 |
| limit_exceeded | 0.10 |
| issuer_unavailable | 0.08 |
| card_expired | 0.05 |
| invalid_card | 0.04 |
| fraud_suspected | 0.03 |
| duplicate_transaction | 0.03 |

## Statistical Significance

- Use **paired t-test** across `n_runs` to compare RevPilot vs each baseline
- Significance level: $\alpha = 0.05$
- Report: mean, std, 95% CI, p-value
- A result is considered significant only if $p < 0.05$

## BenchmarkResult Schema Usage

Each run produces a `BenchmarkResult` instance. Multiple runs are aggregated:

```python
results: list[BenchmarkResult] = run_benchmark(n_runs=10)
mean_recovery = mean([r.recovery_rate for r in results])
mean_revenue = mean([r.revenue_recovered for r in results])
```

## How to Run

```bash
# Run full benchmark suite
pytest tests/ -m benchmark -v

# Run specific baseline comparison
python -m scripts.benchmark --strategy thompson --baseline fixed_delay --n-events 1000 --n-runs 10
```

## Expected Output

```
╔══════════════════════════════════════════════════════════════╗
║                    RevPilot Benchmark Report                ║
╠══════════════════════════════════════════════════════════════╣
║ Strategy          │ Recovery │ Revenue    │ Avg Time │ p-val ║
║───────────────────┼──────────┼────────────┼──────────┼───────║
║ no_retry          │  0.0%    │ ₹0         │ N/A      │ —     ║
║ immediate_retry   │ 24.3%    │ ₹3,64,500  │ 120ms    │ —     ║
║ fixed_delay_retry │ 31.2%    │ ₹4,68,000  │ 305000ms │ —     ║
║ thompson_sampling │ 42.1%    │ ₹6,31,500  │ 245000ms │ 0.001 ║
╚══════════════════════════════════════════════════════════════╝
Improvement over best baseline: +10.9pp (p < 0.01)
```
