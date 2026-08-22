# RevPilot Security Model & Deterministic Guardrails

## 1. Security Philosophy

RevPilot operates on a strict **Fail-Closed Financial Authorization** model. In financial revenue recovery, optimistic execution leads to double-charging customers, merchant liability, and regulatory penalties. If any condition is ambiguous, malformed, or unauthorized, execution immediately halts with ₹0.00 financial mutation.

---

## 2. Invariants & Control Boundaries

| Boundary | Enforcement Mechanism | Security Invariant |
| :--- | :--- | :--- |
| **LLM Authorization** | Architectural Isolation | **NOT PERMITTED**. LLMs cannot call execution adapters or authorize funds. |
| **Direct Execution** | Pre-execution Gating | **NOT PERMITTED**. No action executes without explicit Guardrail approval. |
| **Amount Mutation** | Pydantic Schema Validation | **NOT PERMITTED**. Amounts are immutable float values strictly validated $> 0$. |
| **Guardrail Bypass** | Pipeline Orchestrator | **NOT PERMITTED**. The pipeline cannot skip the guardrail verification stage. |
| **Ground Truth Isolation** | Information Barrier | **NOT ACCESSIBLE**. Strategy optimizer only sees observed empirical outcomes. |
| **Double-Charge Protection**| Atomic Idempotency Store | **ZERO DOUBLE CHARGES**. Duplicate transactions are rejected at the gate. |

---

## 3. Deterministic Guardrail Rules

Every recovery action must satisfy all 6 deterministic guardrail predicates:

1. **Idempotency Verification**: Checks payment ID against the atomic in-memory `IdempotencyStore`. If payment ID was previously executed, verdict is `BLOCKED`.
2. **Velocity Limits**:
   - Max 3 retries per individual payment ID.
   - Max 5 retries per card fingerprint per 24 hours.
3. **Cool-Off Window**: Enforces a minimum 300-second backoff between consecutive retries on the same payment instrument.
4. **Risk Gating**: Transactions classified with `RiskLevel.HIGH` or `FailureClass.FRAUD_SUSPECTED` are immediately abandoned.
5. **Currency Gating**: Only `INR` transactions are permitted. Non-INR currencies are rejected with HTTP 422.
6. **Action Validity**: Recovery action must be a member of the typed `RetryStrategy` enum.

---

## 4. Adversarial Resilience & Chaos Suite

RevPilot contains a dedicated 10-scenario Chaos Engineering suite (`scripts/run_chaos_suite.py`) testing edge cases:

1. `CHAOS_01_DUPLICATE_TXN`: Duplicate payment replay $\rightarrow$ **BLOCKED** by Idempotency Guardrail.
2. `CHAOS_02_MALFORMED_AMOUNT`: String amount in payload $\rightarrow$ **REJECTED** by Schema Validator.
3. `CHAOS_03_NEGATIVE_AMOUNT`: Negative amount ($-\text{₹}500$) $\rightarrow$ **BLOCKED** on Negative Amount Validation.
4. `CHAOS_04_UNKNOWN_CURRENCY`: Foreign currency (`USD`) $\rightarrow$ **BLOCKED** by Currency Guardrail.
5. `CHAOS_05_CORRUPTED_ERROR`: Corrupt binary string error $\rightarrow$ **CLASSIFIED** as UNKNOWN with safe fallback.
6. `CHAOS_06_DELAYED_WEBHOOK`: Out-of-window webhook $\rightarrow$ **PROCESSED** with exact timestamp tracking.
7. `CHAOS_07_OUT_OF_ORDER`: Attempt count $>3$ $\rightarrow$ **BLOCKED** by Max Retries Guardrail.
8. `CHAOS_08_API_TIMEOUT`: Gateway connection timeout $\rightarrow$ **RECORDED** as execution failure; no false recovery.
9. `CHAOS_09_EXECUTION_FAILURE`: Downstream 500 error $\rightarrow$ **LEARNED** safely without double mutation.
10. `CHAOS_10_STALE_EVENT`: Event timestamp $>24\text{h}$ old $\rightarrow$ **BLOCKED** by Event Staleness Guardrail.

**Result**: 10/10 scenarios verified 100% financially safe with **0 unsafe executions**.
