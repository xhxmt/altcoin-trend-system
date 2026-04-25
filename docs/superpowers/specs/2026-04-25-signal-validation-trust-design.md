# Signal Validation Trust Design

Date: 2026-04-25

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

Out of scope:

- A new `acts validate-signals` CLI.
- A formal artifact schema contract.
- A full rule-search platform.
- Data backfill quality checks, API rate limiting, deployment packaging, or Prometheus-style monitoring.

## Architecture

The existing validation script should be organized around four internal units.

1. Hourly aggregation

Aggregate `alt_core.market_1m` into hourly bars with `ts` set to the hour bucket start. The bucket start is the exchange K-line open time. Binance open-time semantics are the reference behavior; Bybit-derived data must be normalized to the same meaning if its raw timestamp semantics differ.

2. Feature and signal selection

Reuse the existing feature preparation path and the current signal-family selection logic. The validator should support whole-family selectors such as `ignition` and grade-specific selectors such as `ignition_A` or `reacceleration_B` when those are already supported by the script.

3. Forward label engine

The primary entry price is the validated hourly bar close. The validator records both `signal_ts` and `signal_available_at`:

- `signal_ts`: hour bucket start, for example `2026-04-22T10:00:00Z`.
- `signal_available_at`: hour bucket close/availability time, for example `2026-04-22T11:00:00Z`.

Forward scanning starts after the signal is available, not from the beginning of the hour that produced the signal. The boundary must be explicit and tested so labels cannot accidentally use minutes from the signal-forming hour.

4. Comparison runner

The default validation window is 30 days. Any material rule-threshold change must also run a 90-day check. If the 90-day window has material data gaps or cannot be trusted, the result must be marked as insufficient to justify the rule change.

## Metrics

The primary decision metric remains `precision_before_dd8`, meaning the signal hits `+10%` before an `-8%` drawdown. Reports should continue to include:

- Signal count.
- `precision_1h`, `precision_4h`, and `precision_24h`.
- `precision_before_dd8`.
- `avg_mfe_24h_pct`.
- `avg_mae_24h_pct`.
- `median_time_to_hit_10pct_minutes`.

The validator should also report a sensitivity matrix for target-return and drawdown combinations:

```text
targets:   +5%, +10%, +15%
drawdowns: -5%, -8%, -12%
```

This sensitivity matrix is diagnostic. It should not replace the primary `+10% before -8%` metric.

## Threshold Change Policy

Threshold edits are allowed only when they are small and evidence-backed.

For a threshold change to stay in the implementation:

- There must be a before/after comparison on the fixed 30-day window.
- Material changes must also have a 90-day review.
- `precision_before_dd8` must improve or stay equal while other path-risk metrics improve.
- `avg_mae_24h_pct` must decrease; lower adverse excursion is better.
- Signal count must not decrease by more than 20% on the fixed 30-day window, unless the previous sample count is below 5 and the README explicitly labels the result as sample-limited.
- If the 90-day data is incomplete or stale, it cannot be used as positive evidence for the change.
- Strategy documentation or validation README output must state the evidence behind the change.

Changes that only improve `precision_24h`, or only look better on a tiny sample without path-quality improvement, should not be kept.

## Outputs

The script continues to write the existing artifact set:

- `summary.json`
- `signals.csv`
- `metadata.json`
- `README.md`

This design does not make those files a strict public schema contract. Field names should remain stable where practical, and old ultra validation usage should continue to work, but future additions are allowed without a schema version bump.

The ultra family should keep `gate_flow` diagnostics. Other families do not need gate-flow in this iteration.

## Error Handling

The validator should fail clearly when a requested signal family is unsupported, the validation window is invalid, or required columns are absent after feature preparation.

If a validation window lacks enough forward data to compute the requested horizon, the output should mark the affected labels or window as incomplete rather than silently treating missing forward data as a failed signal.

If the optional 90-day validation is requested but data coverage is insufficient, the result should say that the 90-day check is not strong enough to justify threshold changes.

## Testing

Testing is split into three levels.

Unit tests:

- Hour bucket start semantics.
- `signal_ts` versus `signal_available_at`.
- Forward-label boundary behavior.
- Primary `+10% before -8%` label.
- Sensitivity matrix calculations.
- Signal-family and grade-specific selectors.

Fixture integration tests:

- Construct a small 1m dataset and run the validation path end to end.
- Cover at least one selected row for `continuation`, `ignition`, `reacceleration`, and `ultra_high_conviction`.
- Verify summary metrics and output rows use the agreed timestamp semantics.

Optional real DB smoke test:

- Runs only when local PostgreSQL and `alt_core.market_1m` are available.
- Confirms the script can generate artifacts from real stored market data.
- Must not make normal test runs fail when the database is absent.

## Acceptance Criteria

- Existing ultra validation behavior remains usable through the current script path.
- All current v2 families can be validated by the same script.
- Hourly signal timestamps use bucket-start/open-time semantics.
- Forward labels scan only after the signal is available.
- The primary path label and sensitivity matrix have automated test coverage.
- Any retained threshold change has fixed-window before/after evidence.
- Optional DB smoke testing is environment-gated and does not break ordinary test runs.
