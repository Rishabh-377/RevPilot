# RevPilot Architecture & System Design

## 1. System Overview

RevPilot is an autonomous revenue recovery controller that diagnoses payment failure events, optimizes multi-action recovery decisions via contextual Thompson Sampling, and executes recovery workflows strictly within deterministic financial guardrails.

---

## 2. Core Tripartite Architectural Principle

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

## 3. End-to-End Control Pipeline

The execution flow consists of 7 discrete stages:

```
[INGEST] --> [DIAGNOSE] --> [STRATEGY] --> [GUARDRAIL] --> [EXECUTE] --> [OUTCOME] --> [AUDIT]
   |             |              |              |              |             |            |
Payment     Regex/LLM     Thompson        Fail-Closed     Simulated     Bayesian     Immutable
Failure     Taxonomy      Sampling        Rules Engine    Adapter       Posterior    Append-Only
Event       Mapping       EV Ranking      Authorization   Execution     Update       Log
```

### Stage Breakdown:
1. **INGEST**: Validates incoming payment event schema (strict positive INR amount, payment method, attempt counter).
2. **DIAGNOSE**: 2-tier classifier. Tier 1 matches deterministic regex taxonomy across 9 failure classes. Optional Tier 2 invokes Gemini for semantic reasoning with automatic timeout fallback.
3. **STRATEGY**: Samples posterior Beta distributions for candidate recovery actions to rank Expected Value:
   $$\text{EV} = p_{\text{sampled}} \times \text{Amount} - \text{API Cost} - \text{Friction Cost}$$
4. **GUARDRAIL**: Deterministic pre-execution authorization. Blocks duplicate attempts, velocity breaches, or suspected fraud.
5. **EXECUTE**: Executes authorized action against payment gateway adapter (or synthetic simulation engine).
6. **OUTCOME**: Records execution result (success/failure), calculates net financial yield.
7. **AUDIT**: Appends an immutable cryptographic HMAC-SHA256 audit entry to `output/audit_log.jsonl`.

---

## 4. Failure Taxonomy & Context Matrix

RevPilot partitions all payment failures into a 27-cell context state matrix:
- **9 Failure Classes**: `TIMEOUT_TRANSIENT`, `HARD_FUNDS_ISSUE`, `ISSUER_DECLINE`, `AUTH_BLOCKED`, `INFRA_OUTAGE`, `DUPLICATE`, `CUSTOMER_ABANDONMENT`, `FRAUD_SUSPECTED`, `UNKNOWN`.
- **3 Value Tiers**:
  - `LOW`: $\text{Amount} < ₹1,000$
  - `MID`: $₹1,000 \le \text{Amount} < ₹10,000$
  - `HIGH`: $\text{Amount} \ge ₹10,000$

---

## 5. Information Barrier Guarantee

The strategy optimizer has **zero access** to simulator ground-truth objects (`GroundTruth`, `SimOutcome`). All Bayesian updates are earned through observed empirical outcomes ($x \in \{0, 1\}$), preventing circular information leakage.
