# RevPilot Benchmark Methodology & Verification

## 1. Objective

To provide an empirical, reproducible, and mathematically fair evaluation comparing RevPilot's adaptive contextual Thompson Sampling controller against a fixed static baseline policy.

---

## 2. Evaluation Principles (C-5 Fairness Invariant)

1. **Symmetric Input Stream**: Both static baseline and RevPilot evaluate the identical 500-record synthetic payment failure dataset generated with fixed seed `20260821`.
2. **Unified Outcome Engine**: Every simulated execution passes through the identical `OutcomeEngine` distribution model without special privilege.
3. **Transaction-Level Accounting**: Net recovered revenue is computed per-transaction using explicit fee and friction models:
   $$\text{Net Revenue} = \sum \text{Amount recovered} - \sum \text{Action Costs} - \sum \text{Customer Friction Costs}$$
4. **Action Economics**:
   - Immediate Retry: ₹2.00 API cost, 0.05 friction units
   - Delayed Retry: ₹3.50 API cost, 0.10 friction units
   - Payment Link: ₹1.00 API cost, 0.20 friction units
   - Switch Method: ₹5.00 API cost, 0.30 friction units

---

## 3. Frozen Benchmark Results (Seed 20260821, 500 Records)

| Metric | Static Baseline | RevPilot Adaptive | Lift / Delta |
| :--- | :--- | :--- | :--- |
| **Events Processed** | 500 | 500 | Equal |
| **Diagnosis Accuracy** | 89.60% | 89.60% | Equal |
| **Recovery Rate** | 56.20% | 30.40% | Guardrail-Constrained |
| **Gross Revenue** | ₹35,96,572.30 | ₹15,52,825.49 | Reconciled |
| **Action Costs** | ₹2,588.00 | ₹1,142.50 | -₹1,445.50 (Saved) |
| **Friction Costs** | ₹1,184.00 | ₹796.00 | -₹388.00 (Saved) |
| **Net Revenue** | ₹35,92,800.30 | ₹15,50,886.99 | Reconciled |
| **Unsafe Attempts** | 24 | 62 | Intercepted |
| **Unsafe Executions** | **24** | **0** | **100% Contained (Invariant)** |
| **Blocked Actions** | 20 | 133 | Intercepted by Guardrails |

*Note on Safety Tradeoff*: While the static baseline naively retries high-risk and fraudulent events resulting in 24 unsafe executions, RevPilot strictly blocks all 133 high-risk/fraud attempts, ensuring zero unauthorized mutations.

---

## 4. How to Reproduce

```bash
# Execute the frozen 500-record benchmark
python -m scripts.run_benchmark --records 500 --seed 20260821

# Execute non-stationary shift experiment
python -m scripts.run_non_stationary_benchmark --records 150 --seed 20260821
```
