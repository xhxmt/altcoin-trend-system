# Signal Validation Trust Design

Date: 2026-04-25
Version: v1.1

## Context

The altcoin trend system already has a working collection -> feature -> signal -> alert -> validation loop, but recent signal work has moved quickly across `signal-v2`, `reacceleration`, and `ultra_high_conviction`. The highest current risk is not whether the daemon can produce rows. The risk is whether backtest labels, signal timestamps, and rule comparisons are stable enough to support trust in alerts and threshold changes.

This design focuses on validation trust. It intentionally does not redesign data collection, deployment, alert transport, or formal artifact schemas.

## Goal

Evolve `scripts/validate_ultra_signal_production.py` from an ultra-specific validator into a generic v2 signal validation entrypoint while keeping the current file and existing ultra workflow compatible.

The validator must make signal timing explicit, use a consistent forward-label definition across v2 families, report sensitivity around the primary label, and provide enough before/after evidence to justify small threshold corrections when they are clearly supported.

## Scope

In scope:

- Validate all current v2 signal families: `continuation`, `ignition`, `reacceleration`, and `ultra_high_conviction`.
- Keep the current script as the primary entrypoint instead of adding a new formal CLI command.
- Normalize hourly validation timestamps to exchange K-line open time, using Binance open-time semantics when exchange behavior differs.
- Use hourly close as the primary entry price for forward labels.
- Keep `+10% before -8% drawdown` as the primary path-quality metric.
- Add sensitivity reporting for target returns `+5%`, `+10%`, `+15%` against drawdown thresholds `-5%`, `-8%`, `-12%`.
- Allow small threshold corrections only when fixed-window validation supports them.
- Define a minimum internal artifact contract for reproducible validation and before/after comparisons.

Out of scope:

- A new `acts validate-signals` CLI.
- A formal artifact schema contract.
- A full rule-search platform.
- Data backfill quality checks, API rate limiting, deployment packaging, or Prometheus-style monitoring.

## Validation Semantics Contract

This validator assumes `alt_core.market_1m.ts` is canonical UTC minute-open time. The validator must record this as:

```json
{
  "market_1m_timestamp_semantics": "minute_open_utc"
}
```

If the stored market timestamp semantics are ever uncertain, validation results must be marked insufficient rather than treated as trusted evidence.

The validator is long-signal only in this iteration.

For each selected hourly signal:

- `signal_ts` is the UTC hourly bucket start.
- `signal_available_at = signal_ts + 1 hour`.
- `entry_ts = signal_available_at`.
- `entry_price = close` of the validated hourly bar.
- `entry_policy = "hour_close_proxy"`.

The signal-forming hour covers 1m rows where:

```text
signal_ts <= market_1m.ts < signal_available_at
```

The forward window for horizon `H` covers 1m rows where:

```text
signal_available_at <= market_1m.ts < signal_available_at + H
```

No row with `market_1m.ts < signal_available_at` may be used for forward labels. With minute-open timestamps, the first eligible forward row for a `10:00:00Z` hourly signal is the `11:00:00Z` 1m bar.

`entry_policy = "hour_close_proxy"` means the hourly close is treated as an executable proxy price at signal availability. This keeps the primary metric stable with the current design. When 1m open data is available, the validator must also report `next_minute_open_entry_price` and `next_minute_open_entry_return_delta_pct` as diagnostics. Those diagnostics do not replace the primary metric in this iteration.

For target/drawdown path labels:

- A target is hit when `1m high >= entry_price * (1 + target_pct)`.
- A drawdown is hit when `1m low <= entry_price * (1 - drawdown_pct_abs)`.
- If target and drawdown are crossed in the same 1m bar, classify drawdown as first for the primary metric, record `path_order = "ambiguous_same_bar"`, and include `ambiguous_same_bar_count` in summary output.

## Architecture

The existing validation script should be organized around four internal units.

1. Hourly aggregation

Aggregate `alt_core.market_1m` into hourly bars with `ts` set to the hour bucket start. The bucket start is the exchange K-line open time. Binance open-time semantics are the reference behavior; Bybit-derived data must be normalized to the same meaning if its raw timestamp semantics differ.

2. Feature and signal selection

Reuse the existing feature preparation path and centralize signal-family parsing in an internal registry. Unsupported families, missing required columns, and unsupported grade selectors must fail clearly.

The registry must describe each family with explicit selector columns:

| Family | Required selector columns | Whole-family selector | Grade selectors | Emit gate flow |
|---|---|---|---|---|
| `continuation` | `continuation_grade` | `continuation_grade IS NOT NULL` | `continuation_A`, `continuation_B` | no |
| `ignition` | `ignition_grade` | `ignition_grade IS NOT NULL` | `ignition_EXTREME`, `ignition_A`, `ignition_B` | no |
| `reacceleration` | `reacceleration_grade` | `reacceleration_grade IS NOT NULL` | `reacceleration_A`, `reacceleration_B` | no |
| `ultra_high_conviction` | `ultra_high_conviction` | `ultra_high_conviction IS TRUE` | none | yes |

Each selected row must also carry the common reporting columns `asset_id`, `exchange`, `symbol`, `ts`, `close`, `return_1h_pct`, `return_4h_pct`, `return_24h_pct`, `return_7d_pct`, `return_30d_pct`, `quality_score`, `chase_risk_score`, and `risk_flags`. A missing selector column is a hard validation error. A missing optional reporting column is written as null and listed in metadata under `missing_optional_columns`.

Selectors must support whole-family requests such as `ignition` and grade-specific requests such as `ignition_A` or `reacceleration_B` where the family supports grades.

Feature preparation must be label-blind and time-local:

- Features for a signal row may only use bars with timestamp less than or equal to the validated hourly close.
- Cross-sectional ranks must be computed only among rows available at the same `signal_ts`.
- No forward label, future return, or post-signal data may be used in signal selection.

3. Forward label engine

The forward label engine implements the validation semantics contract above. It must expose enough fields for tests and artifacts to prove that rows from the signal-forming hour are excluded.

4. Comparison runner

The default validation window is 30 days and must end at least 24 hours before run time:

```text
default_end = floor(now_utc, "hour") - 24 hours
default_start = default_end - 30 days
```

Any material rule-threshold change must also run a 90-day check with the same end timestamp. If the 90-day window has material data gaps, stale data, missing benchmark inputs, or insufficient signal count, the result must be marked as insufficient to justify the rule change.

## Metrics

The primary decision metric remains `precision_before_dd8`:

```text
precision_before_dd8 =
  count(primary-label-complete signals where +10% target is reached before -8% drawdown within 24h)
  / count(primary-label-complete signals)
```

Fixed-horizon hit metrics must use explicit names:

```text
hit10_1h_rate =
  count(1h-complete signals with MFE over 1h >= +10%)
  / count(1h-complete signals)

hit10_4h_rate =
  count(4h-complete signals with MFE over 4h >= +10%)
  / count(4h-complete signals)

hit10_24h_rate =
  count(24h-complete signals with MFE over 24h >= +10%)
  / count(24h-complete signals)
```

The legacy `precision_1h`, `precision_4h`, and `precision_24h` names may remain in artifacts for compatibility, but README output and new tests should prefer `hit10_1h_rate`, `hit10_4h_rate`, and `hit10_24h_rate`.

Risk metrics:

```text
avg_mfe_24h_pct = average maximum favorable excursion over the 24h forward window.
avg_mae_24h_pct = average maximum adverse excursion over the 24h forward window, stored as a negative percentage.
avg_abs_mae_24h_pct = average absolute adverse excursion; lower is better.
median_time_to_hit_10pct_minutes = median time among complete signals that hit +10%.
```

Reports should include:

- Signal count.
- `hit10_1h_rate`, `hit10_4h_rate`, and `hit10_24h_rate`.
- `precision_before_dd8`.
- `avg_mfe_24h_pct`.
- `avg_mae_24h_pct`.
- `avg_abs_mae_24h_pct`.
- `median_time_to_hit_10pct_minutes`.

The validator must report a sensitivity matrix for target-return and drawdown combinations:

```text
targets:   +5%, +10%, +15%
drawdowns: -5%, -8%, -12%
```

Each matrix cell must include `eligible_count`, `hit_count`, `incomplete_count`, and `precision`. The matrix is diagnostic. It must not replace the primary `+10% before -8%` metric.

## Coverage Rules

A signal is label-complete for a horizon only when:

- Forward rows cover the full interval from `signal_available_at` to `signal_available_at + horizon`.
- Missing 1m rows within the horizon do not exceed the configured tolerance.
- Default tolerance is 0 missing minutes for 1h and 4h horizons, and 2 missing minutes for the 24h horizon.

A validation window is trusted only when:

- At least 95% of selected signals are label-complete for the primary 24h horizon.
- Benchmark symbols required for feature preparation are present and fresh for the exchange window.
- `window_end` is at least 24h before validation run time.

Incomplete labels must not be silently counted as failed signals. They must be excluded from the denominator for the affected horizon and reported through incomplete counts.

## Threshold Change Policy

Threshold edits are allowed only when they are small and evidence-backed.

A before/after comparison is evidence-backed only when baseline and candidate validation use identical:

- `window_start`.
- `window_end`.
- Exchange universe.
- Symbol allowlist and blocklist.
- Feature preparation version.
- Signal family selector.
- Timestamp semantics.
- Entry policy.
- Primary label definition.
- Coverage rules.

A threshold change is material if any of the following is true:

- It changes any gate threshold by more than 10% relative or 5 percentage points absolute.
- It changes the 30-day selected signal count by more than 20%.
- It adds or removes a required gate.
- It changes primary signal family membership.
- It changes timestamp, entry, or forward-label semantics.

Non-material threshold corrections require fixed 30-day before/after evidence and documentation. Material changes require both fixed 30-day evidence and a trusted 90-day review.

For a threshold change to be retained as evidence-backed:

- 30-day `precision_before_dd8` must improve or stay equal.
- `avg_abs_mae_24h_pct` must decrease, or `avg_mae_24h_pct` must move toward zero.
- Signal count must not decrease by more than 20% on the fixed 30-day window unless the change is explicitly marked experimental.
- Baseline and candidate sample counts must meet the minimum sample requirement.
- Trusted 90-day review is required for material changes.
- Incomplete or stale 90-day data cannot be used as positive evidence.
- Strategy documentation or validation README output must state the evidence behind the change.

Minimum sample requirement:

- Evidence-backed 30-day comparisons require `baseline_count >= 20` and `candidate_count >= 10`.
- If sample counts are below those limits, the result is sample-limited and cannot by itself justify a retained production threshold change.
- Sample-limited changes may only be kept as experimental and must be labeled experimental in `README.md` and `metadata.json`.

Changes that only improve `hit10_24h_rate`, or only look better on a tiny sample without path-quality improvement, should not be kept as production rules.

## Outputs

The script continues to write the existing artifact set:

- `summary.json`
- `signals.csv`
- `metadata.json`
- `README.md`

This design does not define a public artifact schema, but the validator must maintain a minimum internal artifact contract for reproducible validation and before/after comparisons. Field names should remain stable where practical, and old ultra validation usage should continue to work, but future additions are allowed without a schema version bump.

`metadata.json` must include at least these fields:

| Field | Required value or format |
|---|---|
| `validator_version` | `v1.1` |
| `git_sha` | Current repository commit SHA or `dirty:<sha>` when the worktree affects validation code |
| `run_started_at` | UTC ISO-8601 timestamp |
| `window_start` | UTC ISO-8601 timestamp |
| `window_end` | UTC ISO-8601 timestamp |
| `exchange_universe` | JSON array of exchange names |
| `symbol_allowlist` | JSON array of symbols, empty array for full-market mode |
| `symbol_blocklist` | JSON array of blocked symbols |
| `family` | One of `continuation`, `ignition`, `reacceleration`, `ultra_high_conviction` |
| `selector` | Concrete selector string such as `ignition_A` or `ultra_high_conviction` |
| `rule_version` | Stable rule-version string for the evaluated candidate |
| `feature_preparation_version` | Stable version string or git SHA for the feature computation path |
| `entry_policy` | `hour_close_proxy` |
| `market_1m_timestamp_semantics` | `minute_open_utc` |
| `timestamp_semantics` | `hour_bucket_start_utc` |
| `forward_scan_start_policy` | `signal_available_at_inclusive` |
| `primary_label` | `+10_before_-8` |
| `horizon_hours` | `24` |
| `primary_label_complete_count` | Non-negative integer |
| `incomplete_label_count` | Non-negative integer |
| `coverage_status` | `trusted`, `insufficient_forward_coverage`, `insufficient_signal_count`, `stale_data`, `benchmark_missing`, or `material_gaps` |
| `missing_optional_columns` | JSON array of optional reporting columns absent from the evaluated frame |

For before/after runs, metadata must also record `comparison_window_start`, `comparison_window_end`, `baseline_rule_version`, `candidate_rule_version`, `baseline_git_sha`, and `candidate_git_sha` with concrete values.

`signals.csv` must include at least:

```text
exchange
symbol
signal_family
signal_grade
signal_ts
signal_available_at
entry_ts
entry_price
entry_policy
label_complete_24h
hit_10_before_dd8
mfe_24h_pct
mae_24h_pct
abs_mae_24h_pct
time_to_hit_10pct_minutes
path_order
```

The sensitivity matrix in `summary.json` must include denominator and incomplete counts for each target/drawdown cell.

The 90-day review status must use a specific reason code:

```text
trusted
insufficient_forward_coverage
insufficient_signal_count
stale_data
benchmark_missing
material_gaps
```

The ultra family must keep `gate_flow` diagnostics. Other families do not need gate-flow in this iteration.

## Script Interface

The implementation stays in `scripts/validate_ultra_signal_production.py`. The script must expose generic validation without introducing a new `acts` command.

Expected usage shape:

```bash
python scripts/validate_ultra_signal_production.py \
  --signal-family ultra_high_conviction \
  --window-days 30 \
  --end-at 2026-04-24T00:00:00Z \
  --output-root artifacts/autoresearch
```

Comparison usage shape:

```bash
python scripts/validate_ultra_signal_production.py \
  --signal-family reacceleration \
  --window-days 30 \
  --end-at 2026-04-24T00:00:00Z \
  --compare-baseline-config baseline.json \
  --compare-candidate-config candidate.json \
  --require-90d
```

The canonical new flags are `--signal-family`, `--window-days`, `--end-at`, `--compare-baseline-config`, `--compare-candidate-config`, and `--require-90d`. Existing flags may remain as compatibility aliases, but these canonical flags must be documented in the script help and covered by tests.

## Error Handling

The validator must fail clearly when a requested signal family is unsupported, the validation window is invalid, or required columns are absent after feature preparation.

If a validation window lacks enough forward data to compute the requested horizon, the output must mark the affected labels or window as incomplete rather than silently treating missing forward data as a failed signal.

If the optional 90-day validation is requested but data coverage is insufficient, the result must say that the 90-day check is not strong enough to justify threshold changes and include the specific status reason.

## Testing

Testing is split into three levels.

Unit tests:

- Hour bucket start semantics.
- `signal_ts` versus `signal_available_at`.
- Forward-label boundary behavior.
- Primary `+10% before -8%` label.
- Same-bar target/drawdown ambiguity using conservative drawdown-first behavior.
- Sensitivity matrix calculations.
- Signal-family and grade-specific selectors.
- MAE directionality: `avg_mae_24h_pct` moves toward zero when adverse excursion improves, and `avg_abs_mae_24h_pct` decreases.
- Coverage denominators and incomplete-label exclusion.

Fixture integration tests:

- Construct a small 1m dataset and run the validation path end to end.
- Cover at least one selected row for `continuation`, `ignition`, `reacceleration`, and `ultra_high_conviction`.
- Verify summary metrics and output rows use the agreed timestamp semantics.
- Prove that rows from the signal-forming hour are excluded from forward labels.
- Verify before/after comparison refuses or marks insufficient any run where baseline and candidate windows differ.

Optional real DB smoke test:

- Runs only when local PostgreSQL and `alt_core.market_1m` are available.
- Confirms the script can generate artifacts from real stored market data.
- Must not make normal test runs fail when the database is absent.

## Acceptance Criteria

- Existing ultra validation behavior remains usable through the current script path.
- All current v2 families can be validated by the same script.
- Hourly signal timestamps use bucket-start/open-time semantics.
- Forward labels scan only after the signal is available.
- The default validation window ends at least 24 hours before run time so 24h labels can be complete.
- The primary path label and sensitivity matrix have automated test coverage.
- Same-bar target/drawdown ambiguity has an explicit tested policy.
- `metadata.json` records entry policy, timestamp semantics, window start/end, family selector, rule version, and coverage status.
- Before/after comparison refuses or marks insufficient any run where baseline and candidate windows differ.
- `avg_mae_24h_pct` directionality is unambiguous in code, README output, and tests.
- Any retained threshold change has fixed-window before/after evidence.
- Optional DB smoke testing is environment-gated and does not break ordinary test runs.
