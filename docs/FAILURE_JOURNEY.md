# RevPilot Engineering Failure Journey

## 1. Why We Document Failures

Building an autonomous financial controller requires zero tolerance for hidden regressions, unhandled edge cases, or false claims of intelligence. In fintech payment recovery, an unhandled failure or an unsafe optimistic execution leads directly to duplicate debits, customer churn, and regulatory violation.

RevPilot was developed not through cosmetic feature assembly, but through adversarial code review, hostile security evaluation, and empirical failure hardening. Every critical invariant in RevPilot was tested to destruction and reinforced. This document records the genuine engineering failures discovered and resolved during development.

---

## 2. Critical Failures Found & Resolved (C1–C5)

### C-1: Simulator Circularity & Information Leakage
- **What broke**: Early iterations of the decision engine had access to the simulator ground-truth object directly or relied on normalized labels before diagnosis.
- **Why it was dangerous**: If the strategy engine peeks at the simulator's hidden true success probability matrix, its high recovery rate is an illusion caused by information leakage rather than real learning.
- **Detection**: Hostile audit identified simulator objects being passed across the decision boundary.
- **Root Cause**: Lack of an explicit architectural barrier between the simulation environment and the controller.
- **Fix**: Created an explicit `SimEvent` vs `PaymentFailureEvent` boundary. The controller only receives raw error strings (`raw_gateway_error`), timestamp, amount, and payment method. The strategy engine's Thompson Sampling maintains its own Beta distributions initialized with weakly informative priors ($\alpha=1.0, \beta=1.0$).
- **Regression Test**: `tests/test_information_barrier.py::test_strategy_engine_view_excludes_failure_class`, `tests/test_strategy.py::test_strategy_independent_of_ground_truth`.
- **Final Status**: **VERIFIED FROZEN**.

### C-2: Missing / Un-isolated LLM Failure Modes
- **What broke**: Unhandled timeout and network errors when making semantic LLM calls for error classification.
- **Why it was dangerous**: If LLM API latency spikes or fails, payment recovery crashes, leaving transactions stranded.
- **Detection**: Fault injection during external LLM mock tests.
- **Root Cause**: Direct synchronous LLM invocation without deterministic regex fallback.
- **Fix**: Implemented a 2-tier diagnosis architecture. Tier 1 uses high-speed deterministic regex rules covering all 9 failure classes with 100% availability. Tier 2 uses Gemini 2.5 Flash with strict timeout (5.0s) and fallback to deterministic taxonomy if LLM is unavailable or low-confidence.
- **Regression Test**: `tests/test_diagnosis.py`, `tests/test_pipeline.py::test_path4_diagnosis_unknown_safely_handled`.
- **Final Status**: **VERIFIED FROZEN**.

### C-3: Fabricated / Static Dashboard Metrics
- **What broke**: Frontend HTML initially contained placeholder metric values that could be mistaken for actual live results if API was down.
- **Why it was dangerous**: Violated the core fintech principle of end-to-end telemetry traceability (UI → API → Backend Engine → Audit Log).
- **Detection**: Static code scan on dashboard HTML templates.
- **Root Cause**: Hardcoded initial prototype placeholder values.
- **Fix**: Removed all hardcoded KPI metrics from HTML. Initialized all elements with neutral placeholders (`—`). All metric values are loaded dynamically from `/api/v1/dashboard/overview`, `/api/v1/dashboard/learning`, and `/api/v1/dashboard/transactions` which read actual batch results and database state.
- **Regression Test**: `tests/test_api.py::TestDashboardHtmlNoHardcodedMetrics::test_dashboard_html_contains_no_static_kpis`.
- **Final Status**: **RESOLVED & VERIFIED**.

### C-4: Unsafe Execution Gate Behavior
- **What broke**: Incomplete guardrail checks allowed retry attempts on duplicate or fraudulent transactions.
- **Why it was dangerous**: Caused double-charging customers or automated retries on stolen cards.
- **Detection**: Adversarial chaos test scenarios `CHAOS_01_DUPLICATE_TXN` and `CHAOS_09_FRAUD_SUSPECTED`.
- **Root Cause**: Missing pre-execution authorization check.
- **Fix**: Implemented deterministic fail-closed guardrail engine. Guardrails unconditionally enforce:
  1. Idempotency store check (locks payment ID before execution).
  2. Max 3 retries per payment, max 5 per card per 24h.
  3. Strict risk gating (FRAUD_SUSPECTED and DUPLICATE are immediately abandoned with zero automated execution).
  4. Positive INR currency and amount validation.
- **Regression Test**: `tests/test_execution_fail_closed.py`, `tests/test_guardrails.py`, `tests/test_chaos.py`.
- **Final Status**: **VERIFIED FROZEN**.

### C-5: Unfair Benchmark Accounting
- **What broke**: Baseline static rules and RevPilot were evaluated under asymmetric cost or evaluation rules.
- **Why it was dangerous**: Rendered financial lift claims statistically invalid.
- **Detection**: Benchmark fairness audit and variance decomposition.
- **Root Cause**: Discrepancies in how API costs and friction costs were deducted across comparative runs.
- **Fix**: Unified the benchmark execution framework (`backend/simulator/benchmark.py`). Both static baseline and RevPilot process the identical 500-event synthetic dataset (seed `20260821`) through the same `OutcomeEngine` with identical fee structures (Immediate Retry: ₹2.00, Delayed Retry: ₹3.50, Payment Link: ₹1.00, Switch Method: ₹5.00, Customer Friction units).
- **Regression Test**: `tests/test_benchmark_fairness.py::test_benchmark_input_symmetry`, `test_identical_fee_accounting`.
- **Final Status**: **FAIR & FROZEN**.

---

## 3. High-Severity Failures Resolved (H-1 to H-12)

| Issue ID | Problem Description | Root Cause | Resolution | Test Proof |
| :--- | :--- | :--- | :--- | :--- |
| **H-1** | Idempotency TOCTOU concurrency race | Separate check and record calls | Atomic check-and-consume in `IdempotencyStore` | `test_pipeline.py::test_audit_idempotency` |
| **H-2** | Ground-truth derived Bayesian priors | Priors initialized to match simulator hidden probabilities | Replaced with uninformative/weakly informative Beta(1,1) priors | `test_strategy.py::test_prior_independence` |
| **H-3** | Non-deterministic audit keys | UUID random generation in audit logs | Deterministic HMAC-SHA256 idempotency keys from `event_id + stage + timestamp` | `test_pipeline.py::test_audit_idempotency_deduplication` |
| **H-4** | Test execution backdoor in production code | `force_execution_error` parameter in execution adapter | Removed test backdoors from production paths; isolated to test fixtures | `test_execution_fail_closed.py` |
| **H-5** | Invalid recovery action fallback | Unrecognized action string defaulted to DELAYED_RETRY | Invalid actions immediately fail-closed and transition to `ABANDONED` | `test_pipeline.py::test_invalid_action_rejected` |
| **H-6** | Unit mismatch in EV calculation | Dimensionless friction cost subtracted directly from INR revenue | Standardized friction cost conversion into INR penalty term | `test_strategy.py::test_manual_ev_formula` |
| **H-7** | Duplicate event double-updating Bayesian posterior | Bandit updated on duplicate replay | Bayesian state updater checks idempotency key before performing Beta distribution increment | `test_reflection.py::test_duplicate_events_ignored_by_updater` |
| **H-8** | Non-stationary policy stagnation | Policy failed to shift when gateway degraded | Added exponential time decay / variance tracking to allow prompt adaptation | `test_non_stationary.py::test_posterior_estimates_adapt_to_environmental_shift` |
| **H-9** | Silent exception swallowing in batch runner | Try/except caught all exceptions without logging | Explicit `exceptions.json` registry created to track and isolate every handled anomaly | `test_pipeline.py::test_error_resilience` |
| **H-10** | Chaos cross-scenario state contamination | Scenarios shared pipeline instance | Each chaos scenario executes in an isolated sandbox with clean state | `test_chaos.py::test_all_10_chaos_scenarios_pass` |

---

## 4. Benchmark Evolution & Fairness

- **Original Benchmark Problem**: Disparate evaluation loops gave RevPilot an unfair advantage by discounting retries differently than the static baseline.
- **Fairness Correction**: Both policies now execute against the frozen 500-record dataset with seed `20260821`.
- **Frozen Benchmark Results**:
  - **Static Baseline**: 38.60% Recovery Rate, Gross ₹25.68L, Net ₹18.52L, Unsafe Executions: 2.
  - **RevPilot Adaptive**: **53.80% Recovery Rate**, Gross ₹38.15L, **Net ₹27.03L**, **Unsafe Executions: 0**.
  - **Net Financial Lift**: **+₹8.51 Lakhs (+45.96% lift)** with **100% deterministic safety invariant**.

---

## 5. Testing Evolution

- Total Test Suite: **316 passing tests** across 17 test modules.
- **Categories**:
  - Unit Tests: 142
  - Integration Tests: 84
  - API Smoke & Contract Tests: 38
  - Financial Safety & Idempotency Tests: 24
  - Chaos & Adversarial Tests: 18
  - Portability & Clean Startup Tests: 10

---

## 6. Engineering Lessons

1. **Fail Closed, Never Fall Through**: In financial software, an unknown state must always halt or escalate safely—never guess or optimistically proceed.
2. **Strict Architectural Separation**: LLMs provide semantic root-cause interpretation, Thompson Sampling provides expected value optimization, and deterministic Guardrails provide financial authorization.
3. **Priors Must Be Ground-Truth Independent**: Statistical algorithms must earn their posterior distributions through observed outcomes rather than hardcoded cheating.
4. **Idempotency Must Be Atomic**: Any gap between checking an event and recording its execution creates double-charge vulnerabilities under concurrency.

---

## 7. Remaining Known Limitations

- **Synthetic Simulation Mode**: While RevPilot includes production-ready contracts for Razorpay webhook ingestion and API adapters, the default out-of-the-box mode runs on high-fidelity synthetic simulation.
- **LLM Rate Limits**: In production with live Gemini API keys, high TPS environments require asynchronous queue batching (e.g. Celery / Redis) for Tier 2 semantic diagnosis.
