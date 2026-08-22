# Chaos Testing Design

## Purpose

Validate that RevPilot's guardrails hold under adversarial and edge-case conditions. The system must **fail safely** — defaulting to blocking retries rather than executing unsafe actions.

## Design Principle

> If the chaos test can make RevPilot execute an unauthorized financial action, the guardrails have failed.

## Chaos Scenarios

| # | Scenario | Injection Method | Expected Behavior | Severity |
|---|----------|-----------------|-------------------|----------|
| 1 | Spike in failures | Simulator burst | Velocity limit triggers, blocks excess | High |
| 2 | Bandit converges to bad arm | Force bad posterior | Guardrail blocks non-retryable, recovery rate drops gracefully | High |
| 3 | Repeated fraud flags | All events = fraud_suspected | 100% block rate, zero retries | Critical |
| 4 | Clock skew | Offset timestamps | Cool-off period still enforced | Medium |
| 5 | Database corruption | Corrupt bandit state | Falls back to taxonomy defaults | High |
| 6 | LLM hallucination | Diagnosis returns wrong category | Guardrail catches non-retryable misclassification | Critical |
| 7 | Amount mutation attempt | Strategy tries to change amount | Amount bounds rule blocks | Critical |
| 8 | Idempotency violation | Duplicate decision IDs | Idempotency rule blocks | High |

## Scenario Details

### 1. Failure Spike
- **Inject**: Generate 100 failures in 1 second for a single merchant
- **Expected**: Velocity limit (10/min) blocks after first 10
- **Pass criteria**: No more than 10 retries executed, remaining 90 blocked
- **Audit**: All blocks logged with rule = `velocity_limit`

### 2. Bad Arm Convergence
- **Inject**: Set bandit state so `immediate_retry` has α=100, β=1 for `fraud_suspected`
- **Expected**: Bandit selects `immediate_retry`, but guardrail blocks because fraud is non-retryable
- **Pass criteria**: 0 retries executed, all blocked by Rule 7 or Rule 8
- **Learning**: Bandit receives reward=0, posterior corrects over time

### 3. All Fraud
- **Inject**: Generate 1000 events all with `failure_reason=fraud_suspected`
- **Expected**: 100% guardrail block rate, 0 retries executed
- **Pass criteria**: `guardrail_block_rate == 1.0`, `recovery_rate == 0.0`

### 4. Clock Skew
- **Inject**: Set event timestamps 10 minutes in the future or past
- **Expected**: Cool-off period logic handles gracefully (uses server time, not event time)
- **Pass criteria**: Cool-off period still enforced correctly

### 5. State Corruption
- **Inject**: Corrupt the bandit state JSON (invalid keys, negative alpha/beta, missing arms)
- **Expected**: `BanditState.load()` detects corruption, falls back to default priors
- **Pass criteria**: No crash, `ExceptionRecord` logged, system continues with fresh priors

### 6. LLM Hallucination
- **Inject**: DiagnosisAgent returns `is_retryable=True` for a `fraud_suspected` event
- **Expected**: Guardrail engine independently checks taxonomy and blocks
- **Pass criteria**: Retry blocked despite diagnosis saying retryable
- **Key insight**: Guardrails do NOT trust diagnosis — they verify independently

### 7. Amount Mutation
- **Inject**: Create a StrategyDecision with `amount_split` strategy where split amounts sum to more than original
- **Expected**: Amount bounds rule blocks (MVP: ±0% tolerance)
- **Pass criteria**: Blocked, `ExceptionRecord` logged

### 8. Idempotency Violation
- **Inject**: Submit the exact same (payment_id, strategy, attempt_number) twice
- **Expected**: Second attempt blocked by idempotency rule
- **Pass criteria**: First attempt proceeds, second blocked

## Integration with pytest

```python
@pytest.mark.chaos
class TestChaosScenarios:
    def test_failure_spike(self): ...
    def test_bad_arm_convergence(self): ...
    def test_all_fraud_blocked(self): ...
    def test_clock_skew_handling(self): ...
    def test_state_corruption_recovery(self): ...
    def test_llm_hallucination_caught(self): ...
    def test_amount_mutation_blocked(self): ...
    def test_idempotency_enforced(self): ...
```

Run chaos tests:
```bash
pytest tests/ -m chaos -v
```

## Pass/Fail Criteria

| Criterion | Requirement |
|-----------|------------|
| No unauthorized retry | Zero retries on non-retryable failures |
| No amount mutation | Zero retries with modified amounts |
| No duplicate execution | Zero duplicate retries |
| Safe default on error | System blocks (not approves) on internal errors |
| State recovery | System continues operating after state corruption |
| Audit completeness | Every block is logged with reason |
