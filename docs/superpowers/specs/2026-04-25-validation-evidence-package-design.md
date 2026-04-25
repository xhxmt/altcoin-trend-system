# Validation Evidence Package Design

Date: 2026-04-25
Version: v1.1

## Context

The validator has recently gained the core trust semantics needed for v2 signal validation: explicit signal timing, availability-based forward scans, coverage-aware summaries, prepared-frame integration tests, optional DB smoke coverage, and 30d/90d comparison policy checks. The next step is not another threshold adjustment. The next step is to produce a real, reproducible evidence package from the current validator and local market data.

This design covers a P0 evidence-package workflow only. It intentionally separates evidence generation from later P1 hardening work such as data-health metadata, no-lookahead regressions, and shadow rollout automation.

## Goal

Create a repeatable evidence runner that produces a local validation evidence package for the current v2 signal outputs. The package must show whether the validator, real DB path, selector artifacts, and optional comparison artifacts are strong enough to support threshold-change decisions.

The workflow must preserve the current validator's semantics. It orchestrates validation runs and summarizes artifacts; it does not change signal rules, threshold logic, forward-label definitions, or coverage policy.

## Scope

In scope:

- Generate a collision-safe local evidence package under `artifacts/autoresearch/validation/<YYYY-MM-DD>/<run_id>/`.
- Run the targeted validation test suite and impacted signal/backtest suite without relying on a real database.
- Run the real-DB smoke test as a separate required evidence gate.
- Compute a fixed DB-aware `end_at` scoped to the exchange being validated.
- Run 30-day validation artifacts for the default selector set.
- Preserve each selector's native validator artifacts: `summary.json`, `metadata.json`, `signals.csv`, and `README.md`.
- Run before/after comparison only when a traceable comparison config explicitly names baseline and candidate artifacts.
- Generate a machine-readable `run_manifest.json` and human-readable `EVIDENCE_PACKAGE.md`.

Out of scope:

- Threshold tuning.
- New signal families.
- Synthetic or inferred baseline/candidate config construction.
- Writing one-off validation results into `docs/strategy/current-strategy.md`.
- Data-health field expansion.
- No-lookahead regression tests.
- Shadow rollout scheduling.
- Full backfill quality repair.
- Multi-exchange validation orchestration.

## Default Selector Set

The default selector set is the current evidence selector set for production-facing output and aggregate validation views, not an open-ended promise to run every registry value forever. The P0 runner must run:

```text
continuation
continuation_A
continuation_B
ignition
ignition_EXTREME
ignition_A
ignition_B
reacceleration
reacceleration_A
reacceleration_B
ultra_high_conviction
```

Whole-family selectors such as `continuation`, `ignition`, and `reacceleration` are aggregate validation views. Grade selectors such as `continuation_A`, `ignition_EXTREME`, and `reacceleration_B` are the grade-specific output views. If a future registry-supported selector becomes a standalone output view, it must be added to this default set and to the runner tests.

## Exchange Scope

This P0 runner validates a single exchange by default:

```text
--exchange binance
```

The exchange value must be passed to every selector validation subprocess and recorded as:

```json
{
  "exchange_universe": ["binance"]
}
```

If the live system is broader than the selected exchange, `EVIDENCE_PACKAGE.md` must say so in `## Caveats`; for example, `This package validates binance only; bybit evidence was not generated in this run.`

If multi-exchange validation is added later, the DB-aware safe end time must be computed as the minimum safe end time across all selected exchanges:

```text
min(
  floor(now_utc, hour),
  min(floor(max_market_1m_ts_by_exchange), hour)
) - 24h
```

## Artifact Layout

The package path must be collision-safe. A formal run writes to:

```text
artifacts/autoresearch/validation/<YYYY-MM-DD>/<run_id>/
```

where:

```text
run_id = <UTC-HHMMSS>-<git_sha7>
```

Example:

```text
artifacts/autoresearch/validation/2026-04-25/103422-52e5e9b/
```

If the resolved package directory already exists, the runner must fail unless `--overwrite` is explicitly provided. The runner must never silently overwrite an existing evidence package.

The evidence package directory should use this structure:

```text
artifacts/autoresearch/validation/<YYYY-MM-DD>/<run_id>/
  EVIDENCE_PACKAGE.md
  run_manifest.json
  dirty_diff.patch
  test_logs/
  db_smoke/
  selectors/
    continuation/30d/
    continuation_A/30d/
    continuation_B/30d/
    ignition/30d/
    ignition_EXTREME/30d/
    ignition_A/30d/
    ignition_B/30d/
    reacceleration/30d/
    reacceleration_A/30d/
    reacceleration_B/30d/
    ultra_high_conviction/30d/
  comparisons/
  tmp/
```

`dirty_diff.patch` is present only when relevant dirty files are detected and the runner can archive their diff.

Each selector directory must contain the artifact files generated by `scripts/validate_ultra_signal_production.py`. Artifact discovery must be deterministic. The runner must not select artifacts by latest mtime from a non-empty shared output root.

Preferred artifact discovery:

1. If the validator supports a direct `--output-dir`, write directly to `selectors/<selector>/30d/`.
2. Otherwise, create an empty temporary output root under `tmp/selector-<selector>-<uuid>/`, invoke the validator once, and require exactly one generated artifact directory. Zero or multiple directories is a hard failure.

## Evidence Runner Interface

Add a thin orchestration entrypoint:

```bash
.venv/bin/python scripts/run_validation_evidence_package.py \
  --output-root artifacts/autoresearch/validation \
  --window-days 30 \
  --exchange binance
```

Supported options:

```text
--exchange <name>              Exchange to validate. Default: binance.
--end-at <iso timestamp>       Requested end_at override, still subject to safety checks.
--selectors <comma list>       Override the default selector set.
--comparison-root <path>       Search for traceable comparison configs.
--skip-tests                   Resume/debug mode only; not valid for a passing evidence gate.
--allow-unsafe-end-at          Allow diagnostic output for an unsafe manual end_at.
--overwrite                    Replace an existing package directory.
```

P0 supports one exchange per run. A future `--exchanges` option may be added later, but it is not part of this spec.

The runner is an orchestration layer only. It should use subprocess calls to existing commands where practical and should not duplicate the validator's label, coverage, selector, or comparison logic.

## Execution Flow

1. Resolve `package_date`, `run_id`, and `package_dir`.
2. Fail if `package_dir` already exists and `--overwrite` was not provided.
3. Create the package directory and initialize a partial `run_manifest.json`.
4. Record git SHA, dirty paths, relevant dirty paths, and environment versions.
5. Run the targeted validation tests:

   ```bash
   .venv/bin/pytest \
     tests/test_validate_signal_semantics.py \
     tests/test_validate_ultra_signal_production.py \
     -q
   ```

6. Run the impacted suite:

   ```bash
   .venv/bin/pytest \
     tests/test_trade_backtest.py \
     tests/test_signal_v2.py \
     tests/test_validate_signal_semantics.py \
     tests/test_validate_ultra_signal_production.py \
     -q
   ```

7. Query the latest local market timestamp scoped to the selected exchange:

   ```sql
   SELECT max(ts)
   FROM alt_core.market_1m
   WHERE exchange = :exchange
   ```

   If the scoped latest timestamp cannot be queried, the run is a hard failure unless both `--end-at` and `--allow-unsafe-end-at` are provided. In that override case the runner may produce diagnostic output, but `formal_evidence_gate_passed` must be false.

8. Resolve the package window:

   ```text
   scoped_market_safe_end_at = floor(max(market_1m.ts for exchange), hour) - 24h
   wall_clock_safe_end_at = floor(now_utc, hour) - 24h
   safe_end_at = min(scoped_market_safe_end_at, wall_clock_safe_end_at)
   resolved_end_at = safe_end_at
   start_at = resolved_end_at - window_days
   ```

9. If `--end-at` is provided:

   ```text
   requested_end_at = parsed --end-at
   resolved_end_at = requested_end_at
   ```

   The runner must still validate it against `safe_end_at`. If `requested_end_at > safe_end_at`, set `end_at_safety_status = "unsafe"`. The package cannot pass the formal evidence gate. The runner must fail unless `--allow-unsafe-end-at` is explicitly provided, in which case it may generate diagnostic output only.

   Manifest fields:

   ```json
   {
     "requested_end_at": "2026-04-24T00:00:00+00:00",
     "resolved_end_at": "2026-04-24T00:00:00+00:00",
     "safe_end_at": "2026-04-24T00:00:00+00:00",
     "end_at_policy": "manual_override",
     "end_at_safety_status": "safe"
   }
   ```

10. Run the real DB smoke test as a separate phase:

    ```bash
    ACTS_RUN_DB_SMOKE=1 .venv/bin/pytest \
      tests/test_validate_signal_db_smoke.py \
      -q -rs \
      --junitxml <package_dir>/db_smoke/junit.xml
    ```

    The runner must distinguish passed, skipped, and failed smoke outcomes. Pytest exit code `0` with one or more skipped DB smoke tests is a formal evidence gate failure.

11. For each selector, run the validator with the selected exchange, fixed `resolved_end_at`, and configured `window_days`.
12. Verify each selector artifact contains `summary.json`, `metadata.json`, `signals.csv`, and `README.md`.
13. Extract required selector fields using the canonical source table below.
14. If `--comparison-root` contains traceable comparison configs, run comparison mode for each config.
15. If no traceable comparison config exists, record:

    ```text
    comparison_not_run: missing_traceable_baseline_candidate_config
    ```

16. Write final `run_manifest.json` and `EVIDENCE_PACKAGE.md`.

Normal test phases intentionally exclude `tests/test_validate_signal_db_smoke.py`. The DB smoke test is expected to be environment-gated, so it is run only in the formal DB smoke phase where skip is treated as a gate failure.

## Traceable Comparison Config

A traceable comparison config must explicitly name baseline and candidate artifacts. The runner must not infer baseline/candidate pairs from filenames alone.

Minimum config schema:

```json
{
  "schema_version": 1,
  "selector": "ultra_high_conviction",
  "comparison_type": "threshold_change",
  "change_id": "example-change-id",
  "baseline": {
    "summary_path": ".../baseline_30d/summary.json",
    "metadata_path": ".../baseline_30d/metadata.json"
  },
  "candidate": {
    "summary_path": ".../candidate_30d/summary.json",
    "metadata_path": ".../candidate_30d/metadata.json"
  },
  "ninety_day": {
    "required": true,
    "baseline": {
      "summary_path": ".../baseline_90d/summary.json",
      "metadata_path": ".../baseline_90d/metadata.json"
    },
    "candidate": {
      "summary_path": ".../candidate_90d/summary.json",
      "metadata_path": ".../candidate_90d/metadata.json"
    }
  },
  "change_classification": "material",
  "created_from": "existing_artifacts",
  "created_at": "2026-04-25T00:00:00+00:00"
}
```

Rules:

- `baseline.summary_path`, `baseline.metadata_path`, `candidate.summary_path`, and `candidate.metadata_path` are required.
- `change_classification` must be `material` or `non_material`.
- If `ninety_day.required` is true, all 90-day paths are required.
- `created_from` must be `existing_artifacts`; other values are not traceable in this P0 workflow.
- Missing, malformed, or ambiguous configs produce `comparison_not_run` with a concrete reason.
- A config may apply to one selector only. Cross-selector comparison configs are out of scope.

The runner may translate a traceable config into the per-side config files expected by `scripts/validate_ultra_signal_production.py` comparison mode. This is allowed because the baseline and candidate artifacts remain explicitly named by the traceable config.

If the validator comparison mode writes comparison files, the runner stores or references those files under `comparisons/<selector>/`. If the validator only emits JSON to stdout, the runner must capture stdout, parse the comparison result, write `comparison.json`, and generate `comparison_README.md` from the parsed result.

## Required Selector Field Sources

The runner must extract required fields from canonical files:

| Field | Canonical source |
|---|---|
| `coverage_status` | `metadata.json` |
| `rule_version` | `metadata.json` |
| `feature_preparation_version` | `metadata.json` |
| `market_1m_timestamp_semantics` | `metadata.json` |
| `forward_scan_start_policy` | `metadata.json` |
| `signal_count` | `summary.json` |
| `primary_label_complete_count` | `summary.json` |
| `incomplete_label_count` | `summary.json` |
| `precision_before_dd8` | `summary.json` |
| `avg_abs_mae_24h_pct` | `summary.json` |

Missing required fields are hard failures for that selector. If a field appears in both files and conflicts with the canonical source, the canonical source wins for reporting, but the conflict must be recorded in the selector artifact summary. Null numeric metrics are hard failures; the validator should emit numeric zero rates for zero-signal artifacts.

## Run Manifest

`run_manifest.json` is the machine-readable audit entrypoint. It must include at least:

```json
{
  "package_date": "2026-04-25",
  "run_id": "103422-52e5e9b",
  "package_dir": "artifacts/autoresearch/validation/2026-04-25/103422-52e5e9b",
  "run_started_at": "2026-04-25T10:34:22+00:00",
  "run_finished_at": "2026-04-25T10:50:00+00:00",
  "git_sha": "52e5e9bbc5dd0fc0b3f6738df8bd965e482fb83e",
  "git_sha7": "52e5e9b",
  "worktree_dirty": true,
  "dirty_paths": [],
  "relevant_dirty_paths": [],
  "dirty_diff_path": null,
  "exchange_universe": ["binance"],
  "window_days": 30,
  "requested_end_at": null,
  "resolved_end_at": "2026-04-24T00:00:00+00:00",
  "safe_end_at": "2026-04-24T00:00:00+00:00",
  "latest_market_1m_ts": "2026-04-25T00:00:00+00:00",
  "end_at_policy": "db_aware_max_market_ts_minus_24h",
  "end_at_safety_status": "safe",
  "selectors": [],
  "commands": [],
  "db_smoke": {},
  "selector_artifacts": {},
  "comparison": {},
  "environment": {},
  "gate_status": "passed",
  "formal_evidence_gate_passed": true,
  "threshold_decision_status": "no_decision",
  "overall_status": "passed_with_diagnostics"
}
```

Command records must use this minimum shape:

```json
{
  "name": "targeted_tests",
  "argv": [".venv/bin/pytest", "tests/test_validate_signal_semantics.py", "-q"],
  "started_at": "2026-04-25T10:34:22+00:00",
  "finished_at": "2026-04-25T10:35:00+00:00",
  "exit_code": 0,
  "stdout_log": "test_logs/targeted.stdout.log",
  "stderr_log": "test_logs/targeted.stderr.log",
  "junit_xml": null,
  "classification": "passed"
}
```

The environment block should record at least Python version, pandas version, SQLAlchemy version, pytest version, platform, and working directory.

## Dirty Worktree Policy

The runner must record dirty file paths. Relevant dirty paths are paths under:

```text
scripts/
src/altcoin_trend/
tests/
docs/superpowers/specs/
docs/superpowers/plans/
```

If relevant dirty paths exist:

- Record `relevant_dirty_paths`.
- Try to archive the relevant diff to `dirty_diff.patch`.
- `overall_status` may be `passed_with_diagnostics` at best.
- `threshold_decision_status` must be `no_decision`.
- The README must state that production-ready threshold claims are not allowed from this package without separate human acceptance of the dirty diff.

If relevant dirty paths exist and the diff cannot be archived, `formal_evidence_gate_passed` must be false.

## Evidence Package README

`EVIDENCE_PACKAGE.md` is the human-readable review entrypoint. It must use these sections:

```text
# Validation Evidence Package

## Gate Summary
## Test Results
## DB Smoke
## Window
## Selector Artifacts
## Comparison
## Evidence Decision
## Caveats
```

The selector table must distinguish artifact availability, coverage quality, sample status, and threshold-decision eligibility. A selector with complete files but `coverage_status != trusted` is a valid diagnostic artifact, not threshold evidence.

The comparison section must state whether comparison ran. If it did not run, the reason must be explicit and must not be phrased as support for any threshold change.

The Evidence Decision section must use one of these explicit forms:

```text
No threshold change is supported by this package because comparison was not run: <reason>.
```

```text
No threshold change is supported by this package because comparison result is <status>: <reason>.
```

```text
This package supports retaining candidate threshold change <change_id> because comparison artifact <path> reports evidence_backed with trusted 30d evidence and trusted required 90d review.
```

If `--skip-tests` was used, if `end_at_safety_status != "safe"`, or if relevant dirty paths exist, the Evidence Decision must say the package is not a formal evidence gate for production threshold decisions.

## Layered Status Model

Status must be layered. Do not use selector or comparison statuses as the package-level status.

Package-level:

```text
overall_status:
- passed
- passed_with_diagnostics
- failed

gate_status:
- passed
- failed

formal_evidence_gate_passed:
- true
- false
```

Selector-level:

```text
artifact_status:
- complete
- missing
- invalid

sample_status:
- sample_observed
- sample_limited
- no_signals

selector_evidence_status:
- evidence_eligible
- diagnostic_only
- gate_failed
```

Selector-level `evidence_eligible` requires complete artifacts, `coverage_status == trusted`, and `sample_status == sample_observed`. It does not by itself support a threshold change. Threshold evidence must still come from comparison artifacts.

Sample status is computed from `primary_label_complete_count`:

```text
no_signals = primary_label_complete_count == 0
sample_limited = 0 < primary_label_complete_count < 10
sample_observed = primary_label_complete_count >= 10
```

This selector-level sample status is not a substitute for comparison sample policy. Before/after threshold evidence must still satisfy the validator comparison policy, including the required baseline and candidate sample floors.

Comparison-level:

```text
comparison_status:
- evidence_backed
- not_supported
- experimental_only
- insufficient
- comparison_not_run
```

Threshold-decision level:

```text
threshold_decision_status:
- supported
- not_supported
- no_decision
```

`threshold_decision_status = "supported"` is allowed only when `formal_evidence_gate_passed == true`, comparison status is `evidence_backed`, and no dirty-worktree or unsafe-end-time caveat blocks production threshold decisions.

If comparison is not run, `overall_status` may be `passed_with_diagnostics` but not `passed`, and `threshold_decision_status` must be `no_decision`.

## Skip-Tests Policy

When `--skip-tests` is used:

- The runner may exit 0 if artifact generation succeeds.
- `overall_status` must be `passed_with_diagnostics` or `failed`, never `passed`.
- `formal_evidence_gate_passed` must be false.
- `threshold_decision_status` must be `no_decision`.
- `EVIDENCE_PACKAGE.md` must state that the package is not a formal evidence gate.

## Error Handling

Hard failures should exit non-zero but still write a partial manifest when possible. Hard failures include:

- Targeted tests fail.
- Impacted tests fail.
- DB latest timestamp cannot be queried unless both `--end-at` and `--allow-unsafe-end-at` were provided for diagnostic-only output.
- Manual `--end-at` is unsafe and `--allow-unsafe-end-at` was not provided.
- `ACTS_RUN_DB_SMOKE=1` skips or fails.
- A validator subprocess exits non-zero.
- Deterministic artifact discovery returns zero or multiple generated artifact directories.
- A selector artifact is missing `summary.json`, `metadata.json`, `signals.csv`, or `README.md`.
- Required fields cannot be parsed from a selector artifact.
- A numeric required metric is null or non-finite.

Diagnostic-only conditions should not necessarily fail the runner, but they must prevent evidence-backed conclusions:

- `coverage_status != trusted`.
- `sample_status != sample_observed`.
- Comparison config is missing.
- Comparison result is `not_supported`, `experimental_only`, or `insufficient`.
- `--skip-tests` was used.
- Manual `--end-at` required `--allow-unsafe-end-at`.
- Relevant dirty paths exist.

Evidence-backed threshold claims are allowed only when comparison artifacts themselves report trusted 30-day evidence and any required trusted 90-day review. The runner must not infer this from raw selector artifacts alone.

## Testing Strategy

Normal automated tests must not depend on a real database. Add runner tests with monkeypatched subprocess and DB-query helpers to cover:

- Default selector expansion, including `continuation_A`, `continuation_B`, and `ignition_EXTREME`.
- Single-exchange option handling and exchange propagation to validator subprocesses.
- Scoped DB-aware `end_at` calculation.
- Manual `--end-at` safety checks.
- Unsafe manual end time behavior with and without `--allow-unsafe-end-at`.
- Collision-safe run directory generation.
- Existing package directory failure unless `--overwrite` is provided.
- Test command logging.
- DB smoke JUnit or output parsing, including skip-as-failure behavior for the evidence gate.
- Deterministic selector artifact discovery.
- Required-file checks.
- Required metric extraction from canonical files.
- Conflict recording when non-canonical files disagree.
- Traceable comparison config parsing.
- Missing, malformed, and ambiguous comparison config handling.
- Comparison JSON capture when validator emits JSON to stdout.
- Layered status calculation.
- Dirty path detection and dirty diff archiving.
- `--skip-tests` status downgrade.
- Partial manifest writing on hard failure.
- `EVIDENCE_PACKAGE.md` section generation and fixed Evidence Decision wording.

The existing `ACTS_RUN_DB_SMOKE=1` test remains environment-gated. The runner treats it as mandatory during a formal evidence package run, while ordinary pytest remains DB-independent.

## Acceptance Criteria

- A formal run creates `artifacts/autoresearch/validation/<YYYY-MM-DD>/<run_id>/`.
- The runner fails rather than silently overwriting an existing package directory unless `--overwrite` is provided.
- The package contains `EVIDENCE_PACKAGE.md` and `run_manifest.json`.
- The package records `package_date`, `run_id`, `package_dir`, git SHA, dirty paths, and exchange universe.
- Test commands, exit codes, stdout/stderr log paths, and classifications are recorded.
- The DB-aware `safe_end_at`, `resolved_end_at`, end-at safety status, and scoped latest DB timestamp are recorded.
- `ACTS_RUN_DB_SMOKE=1` is run with skip detection and classified as executed, skipped, or failed.
- All eleven default selectors produce 30-day artifacts.
- Each selector artifact exposes required coverage, sample, and metric fields in the top-level package summary.
- Required fields are extracted from canonical files and missing/null required metrics are classified correctly.
- Missing comparison config is recorded as `comparison_not_run: missing_traceable_baseline_candidate_config`.
- Comparison is run only from traceable config files and never synthesized from filenames alone.
- The final README separates package, selector, comparison, and threshold-decision statuses.
- If comparison is not run, `overall_status` is at most `passed_with_diagnostics` and `threshold_decision_status` is `no_decision`.
- No threshold change is described as production-ready unless comparison artifacts provide trusted 30-day evidence and any required trusted 90-day review, the formal evidence gate passed, the end time is safe, and dirty-worktree caveats do not block the decision.

## Follow-Up Work

After the P0 evidence package is working, open separate specs or plans for:

- Data-health metadata such as latest benchmark timestamps, forward coverage rates, and material gap summaries.
- No-lookahead regression coverage for prepared features and selector decisions.
- Seven-day shadow validation rollout.
- Batch forward-row fetching for performance once trust semantics are stable.
- Multi-exchange evidence package orchestration.
