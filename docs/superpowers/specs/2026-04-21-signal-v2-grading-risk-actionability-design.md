# Signal v2 Grading, Risk, and Actionability Design

Date: 2026-04-21

## Context

The current altcoin trend system is a rule-based USDT perpetual scanner for Binance USD-M and Bybit linear markets. It ingests market data, writes feature snapshots, ranks markets, and sends Telegram alerts. It does not trade.

The current strategy has two candidate channels:

- `continuation_candidate`: the higher-quality main signal for confirmed trend continuation.
- `ignition_candidate`: the early breakout channel for RAVE, EDU, SUPER, and similar sudden explosive moves.

Recent backtests show the expected trade-off:

- `continuation`: 37 signals, 56.76% +10% precision.
- `ignition`: 113 signals, 48.67% +10% precision.
- `ignition_only`: 82 signals, 43.90% +10% precision.

The next problem is not basic coverage. The system can now catch early explosive moves. The v2 objective is to reduce ignition noise without losing RAVE-style early breakouts.

## Goals

- Preserve `continuation` as the main high-quality signal.
- Upgrade boolean candidates into graded signals.
- Keep RAVE-style explosive moves visible through a dedicated extreme channel.
- Separate trend strength from execution usefulness.
- Add risk flags and chase-risk scoring so extreme moves are not interpreted as low-risk entries.
- Add multi-timeframe volume impulse so 1h and 24h volume shocks are not underweighted.
- Make percentile rules stable for small allowlists by adding top-N rank checks.
- Add cross-exchange consensus as a confidence boost and later as an alert deduplication mechanism.
- Upgrade backtests to report grade-level quality, MFE, MAE, and path-dependent hit-before-drawdown labels.

## Non-Goals

- Do not add automated trading.
- Do not expand the allowlist as part of this change.
- Do not replace the existing `final_score` formula in the first implementation phase.
- Do not make `ignition` part of the legacy `trade_candidate` field.
- Do not create a symbol-level summary table in the first implementation phase.

## Compatibility Rules

The legacy fields remain available:

- `trade_candidate` remains equivalent to the continuation main strategy. It does not include ignition.
- `continuation_candidate` is derived from `continuation_grade IS NOT NULL`.
- `ignition_candidate` is derived from `ignition_grade IS NOT NULL`.
- `final_score` remains the trend radar score.
- `tier` remains the existing rank and transition-alert tier.

New v2 fields express signal type, priority, risk, and actionability.

## Data Model

Add these columns to `alt_signal.feature_snapshot` in an additive migration:

```text
volume_ratio_1h DOUBLE PRECISION
volume_impulse_score DOUBLE PRECISION
return_24h_rank INTEGER
return_7d_rank INTEGER
continuation_grade TEXT
ignition_grade TEXT
signal_priority INTEGER NOT NULL DEFAULT 0
risk_flags JSONB NOT NULL DEFAULT '[]'::jsonb
chase_risk_score DOUBLE PRECISION NOT NULL DEFAULT 0
actionability_score DOUBLE PRECISION NOT NULL DEFAULT 0
cross_exchange_confirmed BOOLEAN NOT NULL DEFAULT FALSE
```

The grade fields are nullable:

- `continuation_grade`: `NULL`, `B`, `A`
- `ignition_grade`: `NULL`, `B`, `A`, `EXTREME`

The first implementation phase should not split `derivatives_score` into separate database columns. The raw inputs already exist:

- `oi_delta_1h`
- `oi_delta_4h`
- `funding_zscore`
- `taker_buy_sell_ratio`

Risk flags can be computed from those values first. If backtests prove that separate derivatives confirmation and risk scores are useful, they can be promoted into schema fields later.

`rank_snapshot.payload` should include only summary fields, such as:

```json
{
  "trade_candidate": true,
  "continuation_candidate": true,
  "ignition_candidate": false,
  "continuation_grade": "A",
  "ignition_grade": null,
  "signal_priority": 3,
  "actionability_score": 82.5,
  "chase_risk_score": 20,
  "risk_flags": [],
  "cross_exchange_confirmed": true
}
```

## Data Flow

The snapshot pipeline becomes:

```text
market rows
  -> higher timeframe features
  -> component scores and final_score/tier
  -> percentile and rank assignment
  -> volume_impulse_score
  -> continuation_grade / ignition_grade
  -> risk_flags and chase_risk_score
  -> cross_exchange_confirmed
  -> actionability_score and signal_priority
  -> feature_snapshot and rank_snapshot payload
  -> alert event builder
```

This separates four concepts:

- Trend strength: `final_score`
- Signal type and grade: `continuation_grade`, `ignition_grade`
- Alert priority: `signal_priority`
- Execution usefulness: `actionability_score`

## Signal Grading

### Continuation

Continuation remains the main strategy. It should not be weakened by the addition of ignition.

Base conditions:

```text
no veto
return_1h_pct >= 6
return_4h_pct >= 10
return_24h_pct >= 12
volume_ratio_24h >= 5
is_top_24h(max_rank=3, min_percentile=0.94)
is_top_7d(max_rank=5, min_percentile=0.84)
quality_score >= 80
```

`continuation_grade = "A"` when the base conditions pass and:

```text
relative_strength_score >= 85
derivatives_score >= 45
volume_breakout_score >= 50 OR volume_impulse_score >= 50
```

`continuation_grade = "B"` when the base conditions pass but one or more A confirmations are missing.

If the base conditions fail, the grade is `NULL`.

### Ignition

Ignition is an early breakout radar. It should preserve coverage but classify quality and risk more clearly.

Evaluate in this order:

1. `EXTREME`
2. `A`
3. `B`
4. `NULL`

`ignition_grade = "EXTREME"` when:

```text
no veto
return_1h_pct >= 20
return_24h_pct >= 70
is_top_24h(max_rank=3, min_percentile=0.94)
relative_strength_score >= 90
quality_score >= 80
volume_ratio_24h >= 1.5 OR volume_impulse_score >= 35 OR volume_breakout_score >= 35
derivatives_score >= 25
```

The derivatives threshold is intentionally low for `EXTREME`, because open interest, funding, and taker metrics can lag the earliest phase of an explosive move.

`ignition_grade = "A"` when:

```text
no veto
return_1h_pct >= 10
return_24h_pct >= 35
is_top_24h(max_rank=3, min_percentile=0.94)
relative_strength_score >= 90
quality_score >= 85
volume_ratio_24h >= 2.2 OR volume_impulse_score >= 45 OR volume_breakout_score >= 45
derivatives_score >= 35
```

`ignition_grade = "B"` when:

```text
no veto
return_1h_pct >= 8
return_24h_pct >= 25
is_top_24h(max_rank=3, min_percentile=0.92)
relative_strength_score >= 85
quality_score >= 80
volume_ratio_24h >= 1.8 OR volume_impulse_score >= 45 OR volume_breakout_score >= 35
derivatives_score >= 30
```

If no ignition rule passes, the grade is `NULL`.

### Signal Priority

`signal_priority` is a coarse alert and sorting priority:

```text
3: continuation_A or ignition_EXTREME
2: continuation_B or ignition_A
1: ignition_B
0: no graded signal
```

Priority does not replace `actionability_score`. It only controls alert urgency and basic ordering.

### Tier Override

Tier override remains additive:

```text
ignition_B -> at least watchlist
ignition_A -> at least watchlist
ignition_EXTREME -> at least strong
continuation_A/B -> no forced strong unless final_score already qualifies
```

## Percentile and Rank Rules

The allowlist is small enough that percentile-only rules are unstable. The system should store both percentile and top-N rank:

```text
return_24h_rank
return_7d_rank
return_24h_percentile
return_7d_percentile
```

Use helper rules:

```text
is_top_24h(row, max_rank, min_percentile):
  return row.return_24h_rank <= max_rank
     OR row.return_24h_percentile >= min_percentile

is_top_7d(row, max_rank, min_percentile):
  return row.return_7d_rank <= max_rank
     OR row.return_7d_percentile >= min_percentile
```

Ranks are computed per exchange and snapshot. Rows with missing returns get `NULL` rank and rely on percentile only if available.

## Volume Impulse

Keep `volume_breakout_score` unchanged for `final_score` in the first phase. Add `volume_impulse_score` for ignition, risk explanation, and actionability.

`ratio_score(x, full_at)`:

```text
if x is missing or x <= 1: 0
else clamp(log(x) / log(full_at) * 100, 0, 100)
```

`volume_impulse_score`:

```text
0.40 * ratio_score(volume_ratio_1h, full_at=6)
+ 0.35 * ratio_score(volume_ratio_4h, full_at=5)
+ 0.25 * ratio_score(volume_ratio_24h, full_at=4)
+ 10 if breakout_20d
clamp 0..100
```

If `volume_ratio_1h` cannot be computed for a historical window, treat it as `1.0` so the score degrades conservatively.

## Risk Flags

Risk flags explain why a signal may be late, crowded, or less actionable. They do not hard-veto a candidate in v2 phase 1.

Generate these initial flags:

```text
EXTREME_MOVE:
  ignition_grade == EXTREME

CHASE_RISK:
  chase_risk_score >= 60

FUNDING_OVERHEAT:
  funding_zscore >= 2.5

PRICE_UP_OI_DOWN:
  oi_delta_1h < 0 and return_1h_pct >= 8

TAKER_CROWDING:
  taker_buy_sell_ratio >= 2.5

EXTENDED_1H:
  return_1h_pct >= 25

EXTENDED_24H:
  return_24h_pct >= 100
```

## Chase Risk

`chase_risk_score` is a 0-100 score:

```text
+20 if return_1h_pct >= 15
+20 if return_1h_pct >= 25
+20 if return_24h_pct >= 60
+20 if return_24h_pct >= 100
+10 if funding_zscore >= 2.0
+10 if taker_buy_sell_ratio >= 2.2
cap 100
```

Risk display buckets:

```text
LOW: 0-39
MEDIUM: 40-59
HIGH: 60-79
EXTREME: 80-100
```

## Actionability Score

`actionability_score` is used for opportunity ranking. It does not replace `final_score`.

Base:

```text
+35 continuation_A
+25 continuation_B
+25 ignition_A
+15 ignition_B
+20 ignition_EXTREME
```

Confirmation:

```text
+ min(15, relative_strength_score * 0.15)
+ min(15, volume_impulse_score * 0.15)
+ min(10, quality_score * 0.10)
+ 8 if cross_exchange_confirmed
```

Risk penalty:

```text
-25 if chase_risk_score >= 80
-15 if chase_risk_score >= 60
-8  if chase_risk_score >= 40
-10 if PRICE_UP_OI_DOWN
-10 if FUNDING_OVERHEAT and return_1h_pct >= 20
```

Clamp final actionability to `0..100`.

The system should support two ranking views:

```text
trend_rank:
  existing final_score ordering

opportunity_rank:
  actionability_score DESC,
  signal_priority DESC,
  final_score DESC
```

Do not add a new `opportunity_rank_snapshot` table in phase 1. Query `feature_snapshot` by `actionability_score` first.

## Cross-Exchange Confirmation

Phase 1 computes cross-exchange confirmation at runtime after all exchange-level feature rows have grades:

```text
triggered_exchange_count =
  count rows for symbol where continuation_grade or ignition_grade is not NULL

cross_exchange_confirmed =
  triggered_exchange_count >= 2
```

Set `cross_exchange_confirmed` on each row for the symbol. Add `+8` to actionability when true.

This is not a hard requirement. Some explosive moves can start on one exchange first.

Do not create `symbol_signal_summary` as a table in phase 1. Use runtime aggregation first.

## Alerts

Add four v2 alert event types:

```text
continuation_confirmed
ignition_detected
ignition_extreme
exhaustion_risk
```

Priority mapping:

```text
P1:
  continuation_A
  ignition_EXTREME

P2:
  continuation_B
  ignition_A
  exhaustion_risk

P3:
  ignition_B
```

Cooldown policy:

```text
P1: 1 hour
P2: 2 hours
P3: 4 hours
```

The current `alert_events` table should keep old alert types during migration:

```text
strong_trend
watchlist_enter
breakout_confirmed
risk_downgrade
explosive_move_early
continuation_confirmed
ignition_detected
ignition_extreme
exhaustion_risk
```

During migration, if one row triggers both a legacy transition alert and a v2 signal event, prefer the v2 event to avoid duplicate Telegram messages.

Phase 1 can remain exchange-level deduped by `asset_id + alert_type`.

Phase 2 should add symbol-level deduplication:

```text
same symbol + alert family + snapshot -> one alert
payload includes asset_ids and per-exchange grades
message shows Binance and Bybit signal details
```

Example extreme message:

```text
[IGNITION_EXTREME] RAVEUSDT
1h +32.3% | 24h +137.6%
RS 100 | Vol impulse 48 | Deriv 38.8
Cross-exchange: yes
Chase risk: HIGH
Actionability: Watch / wait pullback
```

Example continuation message:

```text
[CONTINUATION_A] ORDIUSDT
1h +7.1% | 4h +15.4% | 24h +28.2%
Vol 6.2x | RS 94 | Deriv 57
Signal quality: HIGH
```

## Backtesting

Keep the existing +10% 1h hit metric for continuity, but add grade-level and path-dependent metrics.

Forward labels:

```text
mfe_1h_pct
mfe_4h_pct
mfe_24h_pct
mae_1h_pct
mae_4h_pct
mae_24h_pct
hit_5pct_before_drawdown_5pct
hit_10pct_before_drawdown_8pct
time_to_hit_5pct_minutes
time_to_hit_10pct_minutes
```

Definitions:

- `mfe_*`: maximum favorable excursion after the signal, as a percentage of signal close.
- `mae_*`: maximum adverse excursion after the signal, reported as a positive drawdown percentage.
- `hit_10pct_before_drawdown_8pct`: true when price reaches +10% before reaching -8% in the evaluation window.
- `time_to_hit_*`: minutes from signal timestamp to first target hit; `NULL` when not hit.

Report groups:

```text
continuation_A
continuation_B
ignition_A
ignition_B
ignition_EXTREME
cross_exchange_confirmed
single_exchange_triggered
high_chase_risk
low_or_medium_chase_risk
```

Report fields:

```text
signal_count
hit_5pct_rate
hit_10pct_rate
hit_10pct_before_drawdown_8pct_rate
avg_mfe_1h_pct
avg_mfe_4h_pct
avg_mfe_24h_pct
avg_mae_1h_pct
avg_mae_4h_pct
avg_mae_24h_pct
median_time_to_hit_10pct_minutes
```

Implementation should be staged:

1. Use 1h bars to produce quick grade-level reports.
2. Add 1m path labels for accurate hit-before-drawdown and time-to-hit metrics.

Validation targets:

- `continuation_A` precision should be at least as good as current `continuation`.
- `ignition_A` precision should improve on current overall `ignition`.
- `ignition_B` should preserve early coverage and remain lower priority.
- `ignition_EXTREME` should catch RAVE-style moves and carry high chase-risk context.
- High chase-risk groups should show weaker hit-before-drawdown behavior than low/medium chase-risk groups.
- Cross-exchange confirmed groups should outperform single-exchange triggered groups.

## Implementation Phases

### Phase 1: Fields and Grading Core

Add schema fields and write the v2 signal model without changing alert behavior substantially.

Scope:

- Add migration `006_signal_v2_fields.sql`.
- Compute `volume_ratio_1h`.
- Compute `volume_impulse_score`.
- Compute `return_24h_rank` and `return_7d_rank`.
- Add `continuation_grade` and `ignition_grade`.
- Derive legacy booleans from grades.
- Add `risk_flags`, `chase_risk_score`, `actionability_score`, and `signal_priority`.
- Compute `cross_exchange_confirmed`.
- Add grade/actionability summary to rank payload.

Tests:

- `continuation_A` and `continuation_B` classification.
- `ignition_B`, `ignition_A`, and `ignition_EXTREME` classification.
- `EXTREME` accepts lower derivatives confirmation.
- Veto clears all grades.
- Rank helper handles small allowlists.
- `volume_impulse_score` handles missing `volume_ratio_1h`.
- Chase-risk flags include `EXTREME_MOVE` and `CHASE_RISK`.
- `trade_candidate` remains continuation-only.

### Phase 2: Alert Priority and Message Split

Add v2 alert types and messages.

Scope:

- Add alert SQL check constraint values.
- Build event-specific messages.
- Add priority-based cooldown.
- Prefer v2 events over duplicate legacy transition events.
- Keep legacy transition alerts until v2 has been observed in production.

Tests:

- P1, P2, and P3 cooldown behavior.
- Correct alert type per grade.
- `exhaustion_risk` triggers on high chase risk or crowded derivatives flags.
- Duplicate legacy and v2 alerts are suppressed in favor of v2.

### Phase 3: Symbol-Level Cross-Exchange Alerts

Add symbol-level alert aggregation without adding a table.

Scope:

- Build runtime `symbol_signal_summary`.
- Pick best exchange by actionability and priority.
- Include per-exchange grades in payload.
- Send one alert per symbol and alert family per snapshot.

Tests:

- Binance and Bybit duplicate signals produce one alert.
- Payload includes both exchanges.
- Single-exchange ignition still alerts.

### Phase 4: Backtest v2

Add grade-level grouped backtests and path-dependent labels.

Scope:

- Add v2 evaluator.
- Produce grouped reports by grade, chase-risk bucket, and cross-exchange status.
- Start with 1h bars.
- Add 1m path labels after the grouped report is working.

Tests:

- MFE and MAE calculations.
- Hit-before-drawdown ordering.
- Time-to-hit values.
- Grouped report counts match signal counts.

## Migration Strategy

Use additive migrations only. Existing historical rows can keep new nullable fields empty or defaulted.

Required migration:

```text
sql/006_signal_v2_fields.sql
src/altcoin_trend/migrations/006_signal_v2_fields.sql
```

All new columns should use `ADD COLUMN IF NOT EXISTS`.

The feature writer should populate all new fields for new snapshots. No historical backfill is required for phase 1.

## Documentation

Update `docs/strategy/current-strategy.md` after implementation to describe v2. Keep it operational and concise.

This design document remains the implementation reference:

```text
docs/superpowers/specs/2026-04-21-signal-v2-grading-risk-actionability-design.md
```

## Completion Criteria

- Existing tests pass.
- New tests cover each signal grade, risk flag, actionability score, rank helper, and schema migration.
- `trade_candidate` remains continuation-only.
- `final_score`, `actionability_score`, and `signal_priority` are documented as distinct concepts.
- Rank payload includes v2 summaries.
- V2 backtest can report by grade and compare against the current continuation/ignition baseline.
- Telegram alerts distinguish continuation, ignition, extreme ignition, and exhaustion risk.
