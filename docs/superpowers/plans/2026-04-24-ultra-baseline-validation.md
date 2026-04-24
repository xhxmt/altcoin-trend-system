# Ultra High Conviction Baseline Validation Implementation Plan

> **Execution mode:** This plan is for operational validation only. Do not change signal rules, add diagnostics, or patch production code while executing it. Use checkbox (`- [ ]`) tracking and record evidence as you go.

**Goal:** Produce the first reproducible `ultra_high_conviction` production baseline using fixed 7-day and 30-day windows across Binance and Bybit, then end with one explicit decision: freeze, tune one gate next, or add diagnostics first.

**Architecture:** Reuse the existing validation harness at `scripts/validate_ultra_signal_production.py` without modification. Treat the workflow as four runs plus one summary pass. Separate pipeline health from signal-quality interpretation by requiring a 7-day smoke gate before the 30-day baseline.

**Tech Stack:** Existing project Python environment, `PYTHONPATH=src`, live configured database, shell, JSON artifact inspection.

---

## Spec Reference

Execute the approved design:

`docs/superpowers/specs/2026-04-24-ultra-baseline-validation-design.md`

This plan assumes:

- the current repo already contains `scripts/validate_ultra_signal_production.py`
- the operator is in an environment that can already run `acts`
- database connectivity is already available in that environment

No code changes are part of this plan.

## Fixed Inputs

- Shared upper bound: `2026-04-22T00:00:00Z`
- 7-day smoke window: `2026-04-15T00:00:00Z -> 2026-04-22T00:00:00Z`
- 30-day baseline window: `2026-03-23T00:00:00Z -> 2026-04-22T00:00:00Z`
- Exchanges: `binance`, `bybit`

## Output Targets

- `artifacts/autoresearch/ultra-baseline-7d`
- `artifacts/autoresearch/ultra-baseline-30d`
- one issue-ready summary block
- one final decision statement

## Task 1: Prepare The Execution Environment

- [ ] **Step 1: Move to repo root**

Run:

```bash
cd /path/to/altcoin-trend-system
```

- [ ] **Step 2: Export the import path**

Run:

```bash
export PYTHONPATH=src
```

- [ ] **Step 3: Create output roots**

Run:

```bash
mkdir -p artifacts/autoresearch/ultra-baseline-7d
mkdir -p artifacts/autoresearch/ultra-baseline-30d
```

- [ ] **Step 4: Confirm execution assumptions**

Before running the validator, verify that this shell is the same one that can already:

- run `acts`
- connect to the configured database
- import project modules from `src`

If any of these assumptions are false, stop here and repair the environment before collecting baseline results.

## Task 2: Run 7-Day Smoke Validation

- [ ] **Step 1: Run Binance 7-day smoke**

Run:

```bash
python scripts/validate_ultra_signal_production.py \
  --from 2026-04-15T00:00:00Z \
  --to 2026-04-22T00:00:00Z \
  --exchange binance \
  --output-root artifacts/autoresearch/ultra-baseline-7d
```

Expected:

- command exits successfully
- terminal prints `output_dir=...`

- [ ] **Step 2: Run Bybit 7-day smoke**

Run:

```bash
python scripts/validate_ultra_signal_production.py \
  --from 2026-04-15T00:00:00Z \
  --to 2026-04-22T00:00:00Z \
  --exchange bybit \
  --output-root artifacts/autoresearch/ultra-baseline-7d
```

Expected:

- command exits successfully
- terminal prints `output_dir=...`

- [ ] **Step 3: Validate smoke artifacts**

For each exchange output directory, confirm:

- `summary.json` exists
- `signals.csv` exists
- `summary.json.hourly_rows > 0`
- `summary.json.feature_rows > 0`

If any of these fail, classify the run as pipeline-health failure and stop interpretation. Do not tune thresholds.

- [ ] **Step 4: Apply smoke routing rule**

Read both smoke `summary.json` files and inspect `ultra_signal_count`.

Routing:

- if either exchange has `ultra_signal_count > 0`, proceed to the 30-day baseline
- if both exchanges have `ultra_signal_count = 0`, stop and record `add gate-drop diagnostics`

Do not continue to threshold tuning from a double-zero smoke result.

## Task 3: Run 30-Day Baseline Validation

- [ ] **Step 1: Run Binance 30-day baseline**

Run:

```bash
python scripts/validate_ultra_signal_production.py \
  --from 2026-03-23T00:00:00Z \
  --to 2026-04-22T00:00:00Z \
  --exchange binance \
  --output-root artifacts/autoresearch/ultra-baseline-30d
```

- [ ] **Step 2: Run Bybit 30-day baseline**

Run:

```bash
python scripts/validate_ultra_signal_production.py \
  --from 2026-03-23T00:00:00Z \
  --to 2026-04-22T00:00:00Z \
  --exchange bybit \
  --output-root artifacts/autoresearch/ultra-baseline-30d
```

- [ ] **Step 3: Validate baseline artifacts**

For both 30-day output directories, confirm:

- `summary.json` exists
- `signals.csv` exists
- `metadata.json` exists
- `README.md` exists

If any artifact is missing, treat the baseline as invalid and route to diagnostics-first.

## Task 4: Summarize The Four Runs

- [ ] **Step 1: Print a compact summary view**

Run:

```bash
python - <<'PY'
import json
from pathlib import Path

roots = [
    Path("artifacts/autoresearch/ultra-baseline-7d"),
    Path("artifacts/autoresearch/ultra-baseline-30d"),
]

cols = [
    "exchange",
    "from",
    "to",
    "ultra_signal_count",
    "precision_1h",
    "precision_4h",
    "precision_24h",
    "precision_before_dd8",
    "avg_mfe_1h_pct",
    "avg_mfe_24h_pct",
    "avg_mae_24h_pct",
]

for root in roots:
    print(f"\\n=== {root} ===")
    for path in sorted(root.glob("*/summary.json")):
        data = json.loads(path.read_text())
        print(path.parent.name)
        for col in cols:
            print(f"  {col}: {data.get(col)}")
        print()
PY
```

- [ ] **Step 2: Capture the four key summaries**

Record for:

- 7d `binance`
- 7d `bybit`
- 30d `binance`
- 30d `bybit`

Use only the values present in `summary.json`. Do not estimate or round by hand beyond what the file already contains.

## Task 5: Make The Decision Using The Fixed Rubric

- [ ] **Step 1: Evaluate green criteria**

Freeze the current rule when the 30-day combined result mostly satisfies:

- total `ultra_signal_count` between `8` and `60`
- `precision_24h >= 0.50`
- `precision_before_dd8 >= 0.25`
- `avg_mfe_24h_pct >= 10.0`
- `avg_mae_24h_pct` within the allowed drawdown bound

Interpret MAE exactly once:

- negative convention: require `avg_mae_24h_pct >= -8.0`
- positive convention: require `avg_mae_24h_pct <= 8.0`

- [ ] **Step 2: Evaluate yellow criteria**

If green is not met, classify as tune-one-gate when any of the following applies:

- total `ultra_signal_count` between `3` and `7`
- `precision_24h` between `0.35` and `0.50`
- `precision_before_dd8` between `0.15` and `0.25`

Yellow does not authorize multiple simultaneous threshold changes.

- [ ] **Step 3: Evaluate red criteria**

Classify as diagnostics-first when any of the following applies:

- total `ultra_signal_count <= 2`
- Binance and Bybit diverge abnormally
- `precision_before_dd8 < 0.15`
- summary values look structurally abnormal
- `signals.csv` is effectively empty or inconsistent with summary counts

- [ ] **Step 4: Resolve to one explicit next action**

Allowed decision outputs:

- `freeze current ultra rule`
- `relax top-24h rank`
- `tighten max_return_1h_pct`
- `add gate-drop diagnostics`

Pick exactly one. Do not combine actions in the first baseline conclusion.

## Task 6: Apply The Post-Baseline Priority Rule

- [ ] **Step 1: If signal count is too low but precision is still acceptable**

Choose:

```text
relax top-24h rank
```

Do not touch `max_return_1h_pct` first.

- [ ] **Step 2: If signal count is acceptable but chase risk or drawdown looks weak**

Choose:

```text
tighten max_return_1h_pct
```

- [ ] **Step 3: If results are too sparse to interpret**

Choose:

```text
add gate-drop diagnostics
```

The next implementation change should then report:

- raw feature-row count
- count passing top-24h rank
- count passing 7d strength
- count passing 30d strength
- count passing one-hour overextension cap
- final `ultra_high_conviction` count

## Task 7: Publish The Result In The Standard Format

- [ ] **Step 1: Prepare the issue-ready block**

Use this template:

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

- [ ] **Step 2: Keep the conclusion narrow**

The final report should contain:

- the fixed windows
- the four summary lines
- one decision

Do not append speculative redesign ideas to this first baseline report.

## Verification Checklist

- [ ] Commands exited successfully where required
- [ ] Smoke artifacts exist and are non-empty
- [ ] Baseline artifacts exist and are complete
- [ ] Four summaries were collected from file outputs
- [ ] Decision was made from the fixed rubric
- [ ] Final report used the standard issue format

## Failure Handling

- If environment setup fails, stop before baseline and repair the environment.
- If smoke artifact creation fails, stop and treat it as pipeline-health work.
- If both smoke runs produce zero ultra signals, stop and route to diagnostics.
- If 30-day artifacts are incomplete, treat the run as invalid and route to diagnostics.
- If the result is ambiguous, prefer `add gate-drop diagnostics` over ad hoc threshold changes.
