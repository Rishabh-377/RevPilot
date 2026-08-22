# RevPilot

**Autonomous Revenue Recovery Controller for Failed Payments**

> *AI interprets. Statistics optimize. Deterministic systems control money.*

---

## Overview

RevPilot is an autonomous revenue recovery controller that diagnoses payment failure events, optimizes multi-action recovery decisions via contextual Thompson Sampling, and executes recovery workflows strictly within deterministic financial guardrails.

Built on an explicit tripartite architecture, RevPilot ensures that machine learning and LLMs never have unilateral authority over financial mutations.

---

## 🏛️ Tripartite Architecture

```
+-------------------------------------------------------------------------------+
|                             REVPILOT ARCHITECTURE                             |
+-------------------------------------------------------------------------------+
|  1. INTERPRETATION LAYER (LLM - Gemini 2.5 Flash)                             |
|     * Semantic error code classification & root cause analysis               |
|     * Gated behind timeout (5.0s) and fallback to deterministic taxonomy      |
|     * ZERO financial authority (Cannot mutate funds or authorize execution)   |
+-------------------------------------------------------------------------------+
|  2. OPTIMIZATION LAYER (Thompson Sampling Multi-Armed Bandit)                 |
|     * 27 discrete contextual states (9 Failure Classes x 3 Value Tiers)       |
|     * Independent Beta(alpha, beta) posterior distributions per action arm    |
|     * Expected Value (EV) optimization accounting for API & friction costs     |
+-------------------------------------------------------------------------------+
|  3. AUTHORIZATION LAYER (Deterministic Fail-Closed Guardrails)                |
|     * The SOLE financial gatekeeper                                           |
|     * Atomic idempotency locks, velocity limits (max 3 retries/payment)       |
|     * Fraud / high-risk automatic abandonment (0 unsafe executions)           |
+-------------------------------------------------------------------------------+
```

---

## 🚀 Quick Start

### 1. Clone & Setup Environment
```bash
git clone <repo-url>
cd revpilot

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Create default configuration (runs self-contained in simulation mode)
cp .env.example .env
```

### 2. Run the Full Test Suite
```bash
pytest -v
```

### 3. Launch Dashboard & API
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
# or:
./scripts/dev.sh
```
Access the Control Room at: **`http://localhost:8000/dashboard`**

---

## 📊 Scientific Benchmark (Frozen Seed 20260821)

RevPilot is evaluated on a frozen 500-record synthetic payment failure dataset generated with fixed seed `20260821` against a static baseline policy:

| Metric | Static Baseline | RevPilot Adaptive | Status / Delta |
| :--- | :--- | :--- | :--- |
| **Events Processed** | 500 | 500 | Equal |
| **Diagnosis Accuracy** | 89.60% | 89.60% | Equal |
| **Recovery Rate** | 56.20% | 30.40% | Guardrail-Constrained |
| **Gross Recovered Revenue** | ₹35,96,572.30 | ₹15,52,825.49 | Reconciled |
| **Net Recovered Revenue** | ₹35,92,800.30 | ₹15,50,886.99 | Reconciled |
| **Unsafe Attempts Intercepted**| 24 (Executed Unsafely) | 62 (Intercepted) | Blocked by Guardrail |
| **Unsafe Executions** | **24** | **0** | **100% Contained (Invariant)** |

*Run the benchmark*:
```bash
python -m scripts.run_benchmark --records 500 --seed 20260821
```

---

## ⚡ Adversarial Resilience & Chaos Suite

RevPilot includes an automated 10-scenario Chaos Engineering suite testing duplicate replays, malformed amounts, negative amounts, foreign currency injections, API timeouts, and stale webhooks.

```bash
python -m scripts.run_chaos_suite
```
**Result**: 10/10 scenarios verified 100% financially safe with **0 unsafe executions**.

---

## 📚 Documentation

- [System Architecture](docs/ARCHITECTURE.md)
- [Security Model & Deterministic Guardrails](docs/SECURITY_MODEL.md)
- [Benchmark Methodology & Accounting](docs/BENCHMARK_METHODOLOGY.md)
- [Engineering Failure Journey](docs/FAILURE_JOURNEY.md)
- [Pre-Release Checklist](docs/RELEASE_CHECKLIST.md)

---

## ⚠️ Disclosure & Limitations

- **Default Simulation Mode**: Out-of-the-box, RevPilot operates on a high-fidelity synthetic payment simulation environment without requiring live banking credentials.
- **Optional Gemini Mode**: Live semantic LLM error diagnosis requires setting `REVPILOT_LLM_ENABLED=true` and supplying a `GEMINI_API_KEY`. (Deterministic regex failure classification remains 100% functional without LLM keys).
