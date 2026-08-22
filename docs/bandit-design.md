# Bandit Design

## Why Thompson Sampling?

| Algorithm | Exploration | Simplicity | Contextual | Bayesian | Chosen |
|-----------|-----------|-----------|-----------|---------|--------|
| ε-greedy | Fixed ε | ✅ Simple | ❌ | ❌ | ❌ |
| UCB1 | Deterministic | ✅ Simple | ❌ | ❌ | ❌ |
| Thompson Sampling | Probabilistic | ✅ Simple | ✅ Extensible | ✅ | ✅ |
| LinUCB | Deterministic | ❌ Complex | ✅ | ❌ | ❌ |
| Neural Bandit | Learned | ❌ Complex | ✅ | ❌ | ❌ |

**Thompson Sampling wins** because:
1. Natural exploration via posterior sampling — no hyperparameter tuning for ε
2. Bayesian updates are computationally trivial (Beta distribution conjugacy)
3. Converges to optimal arm while maintaining exploration
4. Easy to extend to contextual bandits
5. Interpretable — we can inspect the posterior distributions directly
6. One-week feasibility — much simpler than RL approaches

## Mathematical Formulation

### Arms

Each retry strategy is an arm $i \in \{1, \ldots, K\}$:

| Arm | Strategy | Description |
|-----|----------|-------------|
| 1 | `immediate_retry` | Retry immediately with same parameters |
| 2 | `delayed_retry` | Retry after a configurable delay |
| 3 | `amount_split` | Split into smaller sub-transactions |
| 4 | `card_switch` | Prompt for alternate payment method |
| 5 | `downgrade_retry` | Retry with reduced auth requirements |
| 6 | `time_shift` | Schedule retry at optimal time |
| 7 | `no_action` | Do not retry |

### Prior

Each arm starts with a Beta prior:

$$\theta_i \sim \text{Beta}(\alpha_i^{(0)}, \beta_i^{(0)})$$

Default: $\alpha_i^{(0)} = 1, \beta_i^{(0)} = 1$ (uniform prior).

### Selection

At each decision step $t$:

1. Sample $\hat{\theta}_i^{(t)} \sim \text{Beta}(\alpha_i^{(t)}, \beta_i^{(t)})$ for each arm $i$
2. Select arm $k = \arg\max_i \hat{\theta}_i^{(t)}$

### Posterior Update

After observing reward $r \in \{0, 1\}$ for arm $k$:

$$\alpha_k^{(t+1)} = \alpha_k^{(t)} + r$$
$$\beta_k^{(t+1)} = \beta_k^{(t)} + (1 - r)$$

where $r = 1$ if the retry succeeded (amount recovered) and $r = 0$ otherwise.

### Expected Value

The expected success probability for arm $i$ at time $t$:

$$\mathbb{E}[\theta_i^{(t)}] = \frac{\alpha_i^{(t)}}{\alpha_i^{(t)} + \beta_i^{(t)}}$$

## Contextual Features

The bandit conditions arm selection on these context features:

| Feature | Type | Values | Source |
|---------|------|--------|--------|
| `failure_reason` | Categorical | 10 categories | DiagnosisResult |
| `amount_bucket` | Categorical | low (<500), medium (500-5000), high (>5000) | PaymentFailureEvent |
| `time_of_day` | Categorical | morning, afternoon, evening, night | PaymentFailureEvent.timestamp |
| `merchant_category` | Categorical | From merchant metadata | PaymentFailureEvent.metadata |
| `retry_count` | Integer | 1-5 | PaymentFailureEvent.attempt_number |
| `payment_method` | Categorical | 5 methods | PaymentFailureEvent |

**MVP approach**: Maintain separate Beta distributions per `failure_reason`. Other features are logged but not used for conditioning in v1.

## Cold Start Strategy

1. **Initialize with taxonomy priors**: Use `base_recovery_rate` from the failure taxonomy to set informed priors:

$$\alpha_i^{(0)} = \text{base\_rate} \times N_{\text{pseudo}}$$
$$\beta_i^{(0)} = (1 - \text{base\_rate}) \times N_{\text{pseudo}}$$

where $N_{\text{pseudo}} = 10$ (equivalent to 10 pseudo-observations).

2. **Forced exploration**: For the first $N_{\text{warmup}} = 50$ events per failure reason, cycle through all eligible strategies.

3. **Fallback**: If bandit state is corrupted or missing, fall back to taxonomy default strategies.

## Exploration vs Exploitation

Thompson Sampling automatically balances exploration and exploitation:
- Arms with high uncertainty (wide posteriors) get explored more
- Arms with strong evidence (narrow posteriors) get exploited more
- The `exploration` flag in `StrategyDecision` records whether the sampled arm differed from the arm with highest mean

## State Persistence

```json
{
  "arms": {
    "insufficient_funds": {
      "immediate_retry": {"alpha": 5.0, "beta": 12.0},
      "delayed_retry": {"alpha": 18.0, "beta": 7.0},
      "amount_split": {"alpha": 8.0, "beta": 9.0},
      "time_shift": {"alpha": 12.0, "beta": 6.0}
    }
  },
  "total_decisions": 142,
  "last_updated": "2026-08-21T00:00:00Z"
}
```

Persisted to SQLite via `BanditState`. Loaded on startup, saved after every batch of updates.
