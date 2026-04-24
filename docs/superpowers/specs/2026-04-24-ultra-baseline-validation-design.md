# Ultra High Conviction Baseline Validation Design

Date: 2026-04-24

## Summary

Run a fixed-window production validation for `ultra_high_conviction` before making any further rule changes. The objective is to produce a first baseline that separates three outcomes cleanly:

- freeze the current rule
- tune exactly one gate in the next iteration
- stop tuning and add better diagnostics first

This design is execution-only. It does not change signal rules, daemon behavior, alerts, schema, or backtest logic.

## Context

The repository already contains:

- the current `ultra_high_conviction` production rule
- a reproducible validation harness at `scripts/validate_ultra_signal_production.py`
- strategy documentation describing the rule shape and artifact contract

Recent work tightened the rule in three important ways:

- require top-24h cross-sectional leadership
- require stronger 7d and 30d context
- cap one-hour overextension to reduce chase risk

The next decision should not be made from intuition or isolated examples. It should be made from a fixed validation window, across both supported exchanges, with one stable decision rubric.

## Goals

- Validate that the ultra signal pipeline runs end-to-end against the live database-backed production feature flow.
- Produce one 7-day smoke result for Binance and Bybit to confirm command, data, and artifact health.
- Produce one 30-day baseline result for Binance and Bybit to support a freeze or next-step decision.
- Use fixed windows and a fixed upper bound so results are comparable and not contaminated by incomplete forward labels.
- Conclude with exactly one next action: freeze, single-gate tune, or diagnostics.

## Non-Goals

- Do not change any `ultra_high_conviction` thresholds in this phase.
- Do not add `per-gate drop count` diagnostics before the baseline is produced.
- Do not mix daemon health, stale-input recovery, Telegram delivery, or alert formatting into this validation pass.
- Do not widen the universe, alter exchange ingestion, or change database schemas.
- Do not interpret this run as the final research conclusion for the strategy.

## Execution Boundary

The work in this phase produces only three classes of outputs:

- four validation artifact directories
- a compact summary of four `summary.json` files
- one explicit decision statement for issue tracking

If the smoke run fails or both exchanges produce zero ultra signals in the 7-day window, the phase still ends with a decision. That decision becomes diagnostics, not ad hoc threshold changes.

## Fixed Windows

Use a shared upper bound of `2026-04-22T00:00:00Z`.

The 7-day smoke window is:

```text
2026-04-15T00:00:00Z -> 2026-04-22T00:00:00Z
```

The 30-day baseline window is:

```text
2026-03-23T00:00:00Z -> 2026-04-22T00:00:00Z
```

The upper bound is intentionally not the current day. The validation harness computes forward 24h labels, so the run needs enough future buffer to avoid partial labeling.

## Environment Assumptions

Execute from the repository root in the same environment that can already:

- run `acts`
- import from `src` via `PYTHONPATH=src`
- connect to the configured database successfully

Prepare these output roots before running validation:

```text
artifacts/autoresearch/ultra-baseline-7d
artifacts/autoresearch/ultra-baseline-30d
```

This design assumes the current harness output contract remains unchanged:

- `summary.json`
- `signals.csv`
- `metadata.json`
- `README.md`

## Execution Flow

Run the validation in this exact order:

1. Prepare the environment and output roots.
2. Run the 7-day smoke window for `binance`.
3. Run the 7-day smoke window for `bybit`.
4. Validate smoke outputs and decide whether baseline should proceed.
5. Run the 30-day baseline window for `binance`.
6. Run the 30-day baseline window for `bybit`.
7. Read all four `summary.json` files and print a compact comparison view.
8. Apply the fixed go/no-go rubric and record exactly one decision.

The order matters. The smoke run exists to separate pipeline breakage from rule tightness before spending time interpreting a 30-day result.

## Smoke Run Success Criteria

Each 7-day run passes the smoke gate when all of the following are true:

- the command exits successfully
- the terminal prints `output_dir=...`
- the output directory contains at least `summary.json` and `signals.csv`
- `summary.json` shows `hourly_rows > 0`
- `summary.json` shows `feature_rows > 0`

Smoke routing rules:

- if either exchange has `ultra_signal_count > 0`, proceed to the 30-day baseline
- if both exchanges have `ultra_signal_count = 0`, stop baseline interpretation and classify the outcome as diagnostics-first

The second rule is deliberate. Zero on both exchanges is more likely to indicate a rule-density or feature-chain problem than a trustworthy production baseline.

## Baseline Metrics To Compare

Collect these fields from every `summary.json`:

```text
exchange
from
to
ultra_signal_count
precision_1h
precision_4h
precision_24h
precision_before_dd8
avg_mfe_1h_pct
avg_mfe_24h_pct
avg_mae_24h_pct
```

These are sufficient for the first baseline decision because they cover:

- sample density
- short and medium forward hit rate
- path-dependent quality under drawdown
- upside and downside magnitude

## Decision Rubric

### Green: Freeze Current Rule

Prefer freezing the current rule when most of these conditions are satisfied on the 30-day combined result:

- total `ultra_signal_count` across Binance and Bybit is between `8` and `60`
- `precision_24h >= 0.50`
- `precision_before_dd8 >= 0.25`
- `avg_mfe_24h_pct >= 10.0`
- `avg_mae_24h_pct` is within the allowed drawdown bound

For `avg_mae_24h_pct`, interpret the bound according to the implementation convention:

- if MAE is stored as a negative drawdown, require `avg_mae_24h_pct >= -8.0`
- if MAE is stored as an absolute positive drawdown, require `avg_mae_24h_pct <= 8.0`

If this state is reached, the next step is observation of live output, not more threshold tightening.

### Yellow: Tune One Gate

Treat the result as tuneable but not broken when any of these conditions hold:

- total `ultra_signal_count` is between `3` and `7`
- `precision_24h` is between `0.35` and `0.50`
- `precision_before_dd8` is between `0.15` and `0.25`

The next iteration may change exactly one gate and must rerun the same 30-day window. No multi-variable tuning is allowed from a yellow result.

### Red: Diagnose Before Tuning

Treat the result as diagnostics-first when any of these conditions hold:

- total `ultra_signal_count <= 2`
- Binance and Bybit differ so sharply that the rule does not look stable across exchanges
- `precision_before_dd8 < 0.15`
- `summary.json` values look structurally abnormal
- `signals.csv` is effectively empty or inconsistent with the summary

Red means the next change should improve observability, not blindly loosen thresholds.

## Next-Step Priority If Tuning Is Needed

If the result lands in yellow, the next step depends on which failure shape dominates.

### Case A: Signal Count Too Low, Precision Still Acceptable

Relax the top-24h rank gate before touching `max_return_1h_pct`.

Reasoning:

- the one-hour cap is the explicit anti-chase protection added in the latest rule iteration
- relaxing cross-sectional rank is a cleaner first move than weakening overextension protection

### Case B: Signal Count Is Fine, But Chase Risk Or Drawdown Looks Weak

Tighten `max_return_1h_pct` first, for example from `40.0` toward `35.0`.

Reasoning:

- this directly targets late vertical entries
- it matches the current design intent better than broad trend-quality retuning

### Case C: 24h Precision Is Mediocre, But The Problem Does Not Look Like Chase Risk

Tighten trend-quality gates before touching the alert layer:

- `return_7d_percentile`
- `return_30d_percentile`
- breakout or confirmation requirements

Alerts are presentation and routing. They should not become the place where signal-definition problems are hidden.

## Diagnostics Design If Baseline Is Too Sparse

If the 30-day result is still too sparse to interpret, the next iteration should add `per-gate drop count` reporting to the validation harness.

The minimum useful diagnostic output is:

- raw feature-row count in the validation window
- count passing top-24h rank
- count passing 7d strength
- count passing 30d strength
- count passing the one-hour overextension cap
- final `ultra_high_conviction` count

This turns the next round from guesswork into a targeted rule-density diagnosis.

## Reporting Format

The issue update should use one compact template:

```md
Validation window:
- 7d: 2026-04-15T00:00:00Z -> 2026-04-22T00:00:00Z
- 30d: 2026-03-23T00:00:00Z -> 2026-04-22T00:00:00Z

7d smoke
- binance: signal_count=?, precision_24h=?, precision_before_dd8=?
- bybit: signal_count=?, precision_24h=?, precision_before_dd8=?

30d baseline
- binance: signal_count=?, precision_24h=?, precision_before_dd8=?, avg_mfe_24h_pct=?, avg_mae_24h_pct=?
- bybit: signal_count=?, precision_24h=?, precision_before_dd8=?, avg_mfe_24h_pct=?, avg_mae_24h_pct=?

Decision
- freeze current ultra rule / relax top-24h rank / tighten max_return_1h_pct / add gate-drop diagnostics
```

## Testing And Verification Boundary

This design does not require code changes, so there is no implementation test plan in this phase.

Verification for this phase is operational:

- the fixed validation commands complete successfully
- expected artifacts are written
- the summary fields are internally coherent
- the final decision follows the rubric without ad hoc reinterpretation

## Expected Follow-Up

After this design is approved and reviewed, the next planning step should produce an execution checklist only. It should not expand scope into broader strategy redesign unless the baseline forces that conclusion.
