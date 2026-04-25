# Validation Evidence Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repeatable evidence runner that generates audit-safe validation packages from real tests, real DB smoke, selector artifacts, and traceable comparison configs.

**Architecture:** Add a new orchestration script, `scripts/run_validation_evidence_package.py`, that calls existing pytest and validator commands instead of reimplementing validation semantics. Keep helper functions small and directly testable from `tests/test_run_validation_evidence_package.py`, with a thin `main()` that wires together package creation, command execution, DB-safe windows, selector artifact collection, comparison, manifest generation, and README generation.

**Tech Stack:** Python 3.12 standard library (`argparse`, `dataclasses`, `datetime`, `json`, `os`, `platform`, `shutil`, `subprocess`, `sys`, `tempfile`, `uuid`, `xml.etree.ElementTree`, `pathlib`), pandas/SQLAlchemy already present in the project only for DB timestamp lookup through existing `altcoin_trend.config.load_settings()` and `altcoin_trend.db.build_engine()`, pytest for tests.

---

## Plan Version

Evidence Package Implementation Plan v1.0. This is the task-by-task execution plan for `docs/superpowers/specs/2026-04-25-validation-evidence-package-design.md` v1.1. It replaces the earlier workflow-style description with TDD tasks, file ownership, validation commands, and commit boundaries.

## Scope Check

The approved spec is one subsystem: a P0 evidence-package runner. It does not require changes to signal rules, selector semantics, threshold policy, validator path-label logic, alerting, migrations, data backfill, or strategy docs. This plan therefore creates one runner script and one test file. Do not modify `scripts/validate_ultra_signal_production.py` unless implementation proves an existing CLI incompatibility blocks the runner; if that happens, keep the validator change limited to CLI plumbing and do not change validator semantics.

The runner must call the validator as an external command and must not duplicate selector, forward-label, coverage, or comparison policy logic. Prefer using a temporary empty `--output-root` and deterministic artifact discovery first; do not add a validator `--output-dir` unless the temporary-root approach cannot be made deterministic.

## File Structure

- Create: `scripts/run_validation_evidence_package.py`
  - Owns CLI parsing, package path creation, command execution, git/environment capture, DB-aware `end_at`, DB smoke classification, selector validation orchestration, artifact extraction, comparison orchestration, status calculation, manifest writing, and README writing.
  - Exposes pure helpers for tests. `main()` remains a thin orchestrator.
- Create: `tests/test_run_validation_evidence_package.py`
  - Imports the script by file path using `importlib.util.spec_from_file_location`, matching the current validator test pattern.
  - Uses monkeypatch/fake subprocess/fake DB helpers. No normal test depends on a real DB.
- Existing entrypoint called by runner: `scripts/validate_ultra_signal_production.py`
  - Runner must call it by subprocess with `--signal-family`, `--exchange`, `--window-days`, `--end-at`, and a temporary empty `--output-root`.
  - Runner must call its comparison mode when traceable comparison configs exist.
- Existing tests called by runner:
  - Targeted: `tests/test_validate_signal_semantics.py`, `tests/test_validate_ultra_signal_production.py`
  - Impacted: `tests/test_trade_backtest.py`, `tests/test_signal_v2.py`, `tests/test_validate_signal_semantics.py`, `tests/test_validate_ultra_signal_production.py`
  - DB smoke formal gate: `tests/test_validate_signal_db_smoke.py`

## Task Overview

- Task 1: Add evidence runner skeleton and collision-safe package directory.
- Task 2: Add command runner, log capture, git state, dirty diff, and environment capture.
- Task 3: Add exchange-scoped DB-aware `end_at` resolver and manual safety checks.
- Task 4: Add mandatory DB smoke classification and deterministic artifact discovery.
- Task 5: Extract selector summary fields and selector evidence statuses.
- Task 6: Run selector validation artifacts through the existing validator.
- Task 7: Add traceable comparison config discovery and comparison command construction.
- Task 8: Add layered status calculation and `EVIDENCE_PACKAGE.md` rendering.
- Task 9: Wire main orchestration and partial manifest writing.
- Task 10: Complete comparison execution, including stdout fallback persistence.
- Task 11: Optional real evidence smoke run and final manual inspection.

---

### Task 1: Runner Skeleton, CLI, Default Selectors, And Package Paths

**Files:**
- Create: `scripts/run_validation_evidence_package.py`
- Create: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Write failing tests for defaults, CLI, run id, and overwrite safety**

Add this test harness and tests to `tests/test_run_validation_evidence_package.py`:

```python
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_validation_evidence_package.py"
_SPEC = importlib.util.spec_from_file_location("run_validation_evidence_package", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_default_selectors_include_aggregate_and_grade_views():
    assert _MODULE.DEFAULT_SELECTORS == (
        "continuation",
        "continuation_A",
        "continuation_B",
        "ignition",
        "ignition_EXTREME",
        "ignition_A",
        "ignition_B",
        "reacceleration",
        "reacceleration_A",
        "reacceleration_B",
        "ultra_high_conviction",
    )


def test_parse_args_defaults_to_binance_and_30d():
    args = _MODULE.parse_args([])

    assert args.output_root == "artifacts/autoresearch/validation"
    assert args.window_days == 30
    assert args.exchange == "binance"
    assert args.selectors == ",".join(_MODULE.DEFAULT_SELECTORS)
    assert args.end_at is None
    assert args.comparison_root is None
    assert args.skip_tests is False
    assert args.allow_unsafe_end_at is False
    assert args.overwrite is False


def test_build_run_identity_uses_utc_date_time_and_git_sha7():
    now = datetime(2026, 4, 25, 10, 34, 22, tzinfo=timezone.utc)

    identity = _MODULE.build_run_identity(now=now, git_sha="52e5e9bbc5dd0fc0b3f6738df8bd965e482fb83e")

    assert identity == {
        "package_date": "2026-04-25",
        "run_id": "103422-52e5e9b",
        "git_sha7": "52e5e9b",
    }


def test_resolve_package_dir_refuses_existing_without_overwrite(tmp_path):
    existing = tmp_path / "2026-04-25" / "103422-52e5e9b"
    existing.mkdir(parents=True)

    with pytest.raises(FileExistsError, match="evidence package already exists"):
        _MODULE.resolve_package_dir(
            output_root=tmp_path,
            package_date="2026-04-25",
            run_id="103422-52e5e9b",
            overwrite=False,
        )


def test_resolve_package_dir_allows_existing_with_overwrite(tmp_path):
    existing = tmp_path / "2026-04-25" / "103422-52e5e9b"
    existing.mkdir(parents=True)

    resolved = _MODULE.resolve_package_dir(
        output_root=tmp_path,
        package_date="2026-04-25",
        run_id="103422-52e5e9b",
        overwrite=True,
    )

    assert resolved == existing
```

- [ ] **Step 2: Run the new tests and verify they fail because the script does not exist**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: collection fails with `FileNotFoundError` for `scripts/run_validation_evidence_package.py`.

- [ ] **Step 3: Create the runner skeleton with CLI and package helpers**

Create `scripts/run_validation_evidence_package.py` with this initial content:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_ROOT = "artifacts/autoresearch/validation"
DEFAULT_SELECTORS = (
    "continuation",
    "continuation_A",
    "continuation_B",
    "ignition",
    "ignition_EXTREME",
    "ignition_A",
    "ignition_B",
    "reacceleration",
    "reacceleration_A",
    "reacceleration_B",
    "ultra_high_conviction",
)


@dataclass(frozen=True)
class RunIdentity:
    package_date: str
    run_id: str
    git_sha7: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a validation evidence package.")
    parser.add_argument("--output-root", default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--end-at", dest="end_at")
    parser.add_argument("--selectors", default=",".join(DEFAULT_SELECTORS))
    parser.add_argument("--comparison-root")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--allow-unsafe-end-at", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def parse_selector_list(value: str) -> tuple[str, ...]:
    selectors = tuple(item.strip() for item in value.split(",") if item.strip())
    if not selectors:
        raise ValueError("at least one selector is required")
    return selectors


def build_run_identity(*, now: datetime, git_sha: str) -> dict[str, str]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(timezone.utc)
    git_sha7 = git_sha[:7] if git_sha else "unknown"
    return {
        "package_date": now_utc.strftime("%Y-%m-%d"),
        "run_id": f"{now_utc.strftime('%H%M%S')}-{git_sha7}",
        "git_sha7": git_sha7,
    }


def resolve_package_dir(
    *,
    output_root: Path,
    package_date: str,
    run_id: str,
    overwrite: bool,
) -> Path:
    package_dir = output_root / package_date / run_id
    if package_dir.exists() and not overwrite:
        raise FileExistsError(f"evidence package already exists: {package_dir}")
    return package_dir


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    parse_selector_list(args.selectors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the focused tests and verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit the runner skeleton**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): add evidence runner skeleton"
```

---

### Task 2: Command Logging, Git State, Dirty Diff, And Environment Capture

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for subprocess logs, dirty paths, and environment capture**

Append these tests to `tests/test_run_validation_evidence_package.py`:

```python
import subprocess


def test_run_command_writes_stdout_stderr_and_returns_record(tmp_path, monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = "warn\n"

    def fake_run(args, cwd, env, text, capture_output, check):
        assert args == ["pytest", "-q"]
        assert cwd == Path.cwd()
        assert env["EXAMPLE"] == "1"
        assert text is True
        assert capture_output is True
        assert check is False
        return FakeCompleted()

    monkeypatch.setattr(_MODULE.subprocess, "run", fake_run)

    record = _MODULE.run_command(
        name="targeted_tests",
        argv=["pytest", "-q"],
        package_dir=tmp_path,
        log_dir=tmp_path / "test_logs",
        cwd=Path.cwd(),
        env={"EXAMPLE": "1"},
    )

    assert record["name"] == "targeted_tests"
    assert record["exit_code"] == 0
    assert record["classification"] == "passed"
    assert Path(record["stdout_log"]).read_text(encoding="utf-8") == "ok\n"
    assert Path(record["stderr_log"]).read_text(encoding="utf-8") == "warn\n"


def test_run_command_marks_nonzero_as_failed(tmp_path, monkeypatch):
    class FakeCompleted:
        returncode = 2
        stdout = ""
        stderr = "failed\n"

    monkeypatch.setattr(_MODULE.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    record = _MODULE.run_command(
        name="impacted_tests",
        argv=["pytest", "-q"],
        package_dir=tmp_path,
        log_dir=tmp_path / "test_logs",
        cwd=Path.cwd(),
        env=None,
    )

    assert record["exit_code"] == 2
    assert record["classification"] == "failed"


def test_relevant_dirty_paths_are_limited_to_validation_relevant_areas():
    dirty = [
        "scripts/run_validation_evidence_package.py",
        "src/altcoin_trend/signals/v2.py",
        "tests/test_signal_v2.py",
        "docs/superpowers/specs/2026-04-25-validation-evidence-package-design.md",
        "README.md",
    ]

    assert _MODULE.relevant_dirty_paths(dirty) == [
        "scripts/run_validation_evidence_package.py",
        "src/altcoin_trend/signals/v2.py",
        "tests/test_signal_v2.py",
        "docs/superpowers/specs/2026-04-25-validation-evidence-package-design.md",
    ]


def test_dirty_worktree_policy_disables_threshold_claims_for_relevant_paths():
    assert _MODULE.dirty_worktree_policy([]) == "clean"
    assert _MODULE.dirty_worktree_policy(["scripts/run_validation_evidence_package.py"]) == "threshold_claims_disabled"


def test_collect_environment_contains_required_keys(monkeypatch):
    monkeypatch.setattr(_MODULE.platform, "platform", lambda: "Linux-test")

    environment = _MODULE.collect_environment(cwd=Path("/repo"))

    assert environment["platform"] == "Linux-test"
    assert environment["working_directory"] == "/repo"
    assert "python_version" in environment
```

- [ ] **Step 2: Run the focused tests and verify the new failures**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: failures mention missing `run_command`, `relevant_dirty_paths`, and `collect_environment`.

- [ ] **Step 3: Implement command logging and state helpers**

Update `scripts/run_validation_evidence_package.py` imports:

```python
import json
import os
import platform
import subprocess
import sys
from collections.abc import Mapping
```

Add these helpers below `resolve_package_dir()`:

```python
RELEVANT_DIRTY_PREFIXES = (
    "scripts/",
    "src/altcoin_trend/",
    "tests/",
    "docs/superpowers/specs/",
    "docs/superpowers/plans/",
)


def _iso_now() -> str:
    return utc_now().isoformat()


def run_command(
    *,
    name: str,
    argv: list[str],
    package_dir: Path,
    log_dir: Path,
    cwd: Path,
    env: Mapping[str, str] | None,
    junit_xml: Path | None = None,
) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = log_dir / f"{name}.stdout.log"
    stderr_log = log_dir / f"{name}.stderr.log"
    started_at = _iso_now()
    merged_env = os.environ.copy()
    if env:
        merged_env.update(dict(env))
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=merged_env,
        text=True,
        capture_output=True,
        check=False,
    )
    finished_at = _iso_now()
    stdout_log.write_text(completed.stdout or "", encoding="utf-8")
    stderr_log.write_text(completed.stderr or "", encoding="utf-8")
    return {
        "name": name,
        "argv": list(argv),
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": int(completed.returncode),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "junit_xml": str(junit_xml) if junit_xml is not None else None,
        "classification": "passed" if int(completed.returncode) == 0 else "failed",
    }


def relevant_dirty_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if path.startswith(RELEVANT_DIRTY_PREFIXES)]


def dirty_worktree_policy(relevant_paths: list[str]) -> str:
    return "threshold_claims_disabled" if relevant_paths else "clean"


def collect_environment(*, cwd: Path) -> dict[str, str]:
    environment = {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "working_directory": str(cwd),
    }
    for module_name in ("pandas", "sqlalchemy", "pytest"):
        try:
            module = __import__(module_name)
        except Exception:
            environment[f"{module_name}_version"] = "unavailable"
        else:
            environment[f"{module_name}_version"] = str(getattr(module, "__version__", "unknown"))
    return environment


def git_output(argv: list[str], *, cwd: Path) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def current_git_sha(*, cwd: Path) -> str:
    return git_output(["git", "rev-parse", "HEAD"], cwd=cwd) or "unknown"


def dirty_paths(*, cwd: Path) -> list[str]:
    output = git_output(["git", "status", "--short"], cwd=cwd)
    paths: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def archive_dirty_diff(*, cwd: Path, package_dir: Path, paths: list[str]) -> str | None:
    if not paths:
        return None
    completed = subprocess.run(
        ["git", "diff", "--", *paths],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    diff_path = package_dir / "dirty_diff.patch"
    diff_path.write_text(completed.stdout, encoding="utf-8")
    return str(diff_path)
```

- [ ] **Step 4: Run focused tests and fix only concrete failures**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit command and environment helpers**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): add evidence runner command logging"
```

---

### Task 3: DB-Scoped End Time And Manual End-Time Safety

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for scoped DB time and manual safety**

Append these tests to `tests/test_run_validation_evidence_package.py`:

```python
from datetime import timedelta


def test_floor_hour_and_parse_iso_datetime():
    parsed = _MODULE.parse_iso_datetime("2026-04-25T10:34:22Z")

    assert parsed.isoformat() == "2026-04-25T10:34:22+00:00"
    assert _MODULE.floor_hour(parsed).isoformat() == "2026-04-25T10:00:00+00:00"


def test_resolve_end_at_uses_scoped_market_and_wall_clock_boundaries():
    now = datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc)
    latest_market_ts = datetime(2026, 4, 25, 10, 55, tzinfo=timezone.utc)

    window = _MODULE.resolve_end_at(
        requested_end_at=None,
        latest_market_ts=latest_market_ts,
        now=now,
        allow_unsafe=False,
    )

    assert window["resolved_end_at"] == "2026-04-24T10:00:00+00:00"
    assert window["safe_end_at"] == "2026-04-24T10:00:00+00:00"
    assert window["end_at_policy"] == "db_aware_max_market_ts_minus_24h"
    assert window["end_at_safety_status"] == "safe"


def test_resolve_end_at_rejects_unsafe_manual_time_without_override():
    now = datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc)
    latest_market_ts = datetime(2026, 4, 25, 10, 55, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="manual --end-at is later than safe_end_at"):
        _MODULE.resolve_end_at(
            requested_end_at="2026-04-25T09:00:00Z",
            latest_market_ts=latest_market_ts,
            now=now,
            allow_unsafe=False,
        )


def test_resolve_end_at_allows_unsafe_manual_time_for_diagnostics():
    now = datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc)
    latest_market_ts = datetime(2026, 4, 25, 10, 55, tzinfo=timezone.utc)

    window = _MODULE.resolve_end_at(
        requested_end_at="2026-04-25T09:00:00Z",
        latest_market_ts=latest_market_ts,
        now=now,
        allow_unsafe=True,
    )

    assert window["resolved_end_at"] == "2026-04-25T09:00:00+00:00"
    assert window["safe_end_at"] == "2026-04-24T10:00:00+00:00"
    assert window["end_at_policy"] == "manual_override"
    assert window["end_at_safety_status"] == "unsafe"
```

- [ ] **Step 2: Run the tests and verify missing helper failures**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: failures mention missing `parse_iso_datetime`, `floor_hour`, and `resolve_end_at`.

- [ ] **Step 3: Implement datetime and DB timestamp helpers**

Update imports in `scripts/run_validation_evidence_package.py`:

```python
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine
```

Add these helpers below `collect_environment()`:

```python
def parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def floor_hour(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value_utc = value.astimezone(timezone.utc)
    return value_utc.replace(minute=0, second=0, microsecond=0)


def resolve_end_at(
    *,
    requested_end_at: str | None,
    latest_market_ts: datetime,
    now: datetime,
    allow_unsafe: bool,
) -> dict[str, str | None]:
    scoped_market_safe_end_at = floor_hour(latest_market_ts) - timedelta(hours=24)
    wall_clock_safe_end_at = floor_hour(now) - timedelta(hours=24)
    safe_end_at = min(scoped_market_safe_end_at, wall_clock_safe_end_at)
    if requested_end_at is None:
        resolved_end_at = safe_end_at
        return {
            "requested_end_at": None,
            "resolved_end_at": resolved_end_at.isoformat(),
            "safe_end_at": safe_end_at.isoformat(),
            "end_at_policy": "db_aware_max_market_ts_minus_24h",
            "end_at_safety_status": "safe",
        }
    requested = parse_iso_datetime(requested_end_at)
    safety_status = "safe" if requested <= safe_end_at else "unsafe"
    if safety_status == "unsafe" and not allow_unsafe:
        raise ValueError(
            f"manual --end-at is later than safe_end_at: requested={requested.isoformat()} safe={safe_end_at.isoformat()}"
        )
    return {
        "requested_end_at": requested.isoformat(),
        "resolved_end_at": requested.isoformat(),
        "safe_end_at": safe_end_at.isoformat(),
        "end_at_policy": "manual_override",
        "end_at_safety_status": safety_status,
    }


def query_latest_market_ts(*, exchange: str) -> datetime:
    settings = load_settings()
    engine = build_engine(settings)
    with engine.begin() as connection:
        latest = connection.execute(
            text("SELECT max(ts) FROM alt_core.market_1m WHERE exchange = :exchange"),
            {"exchange": exchange},
        ).scalar()
    if latest is None:
        raise RuntimeError(f"no market_1m rows found for exchange={exchange}")
    if isinstance(latest, datetime):
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return latest.astimezone(timezone.utc)
    return parse_iso_datetime(str(latest))
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit DB-aware end-time helpers**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): add evidence runner safe window"
```

---

### Task 4: DB Smoke Classification And Deterministic Artifact Discovery

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for JUnit classification and artifact discovery**

Append these tests to `tests/test_run_validation_evidence_package.py`:

```python
def test_classify_pytest_junit_marks_skipped_as_skipped(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="1"><testcase classname="x" name="y"><skipped /></testcase></testsuite>',
        encoding="utf-8",
    )

    result = _MODULE.classify_pytest_junit(junit)

    assert result == {
        "passed_count": 0,
        "skipped_count": 1,
        "failed_count": 0,
        "classification": "skipped",
    }


def test_classify_pytest_junit_marks_passed_when_no_skip_or_fail(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="x" name="y" /></testsuite>',
        encoding="utf-8",
    )

    result = _MODULE.classify_pytest_junit(junit)

    assert result["passed_count"] == 1
    assert result["skipped_count"] == 0
    assert result["failed_count"] == 0
    assert result["classification"] == "executed"


def test_discover_single_artifact_directory_requires_exactly_one_child(tmp_path):
    artifact = tmp_path / "generated"
    artifact.mkdir()
    (artifact / "summary.json").write_text("{}", encoding="utf-8")
    (artifact / "metadata.json").write_text("{}", encoding="utf-8")
    (artifact / "signals.csv").write_text("symbol\n", encoding="utf-8")
    (artifact / "README.md").write_text("# run\n", encoding="utf-8")

    assert _MODULE.discover_single_artifact_directory(tmp_path) == artifact


def test_discover_single_artifact_directory_fails_for_multiple_children(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()

    with pytest.raises(RuntimeError, match="expected exactly one artifact directory"):
        _MODULE.discover_single_artifact_directory(tmp_path)
```

- [ ] **Step 2: Run tests and verify helper failures**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `classify_pytest_junit` and `discover_single_artifact_directory`.

- [ ] **Step 3: Implement DB smoke JUnit parsing and artifact discovery**

Update imports:

```python
import shutil
import xml.etree.ElementTree as ET
```

Add constants near `DEFAULT_SELECTORS`:

```python
REQUIRED_ARTIFACT_FILES = ("summary.json", "metadata.json", "signals.csv", "README.md")
```

Add helpers:

```python
def classify_pytest_junit(junit_xml: Path) -> dict[str, int | str]:
    root = ET.fromstring(junit_xml.read_text(encoding="utf-8"))
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    failed = failures + errors
    passed = max(tests - failed - skipped, 0)
    if failed:
        classification = "failed"
    elif skipped:
        classification = "skipped"
    else:
        classification = "executed"
    return {
        "passed_count": passed,
        "skipped_count": skipped,
        "failed_count": failed,
        "classification": classification,
    }


def discover_single_artifact_directory(output_root: Path) -> Path:
    children = [path for path in output_root.iterdir() if path.is_dir()]
    if len(children) != 1:
        raise RuntimeError(f"expected exactly one artifact directory under {output_root}, found {len(children)}")
    artifact_dir = children[0]
    missing = [name for name in REQUIRED_ARTIFACT_FILES if not (artifact_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"artifact directory missing required files: {missing}")
    return artifact_dir


def place_artifact_directory(*, source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return destination
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit DB smoke and artifact discovery helpers**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): classify evidence smoke artifacts"
```

---

### Task 5: Selector Artifact Extraction And Selector Statuses

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for required field extraction and selector statuses**

Append these tests to `tests/test_run_validation_evidence_package.py`:

```python
import json


def test_extract_selector_artifact_reads_canonical_fields(tmp_path):
    artifact = tmp_path / "selector"
    artifact.mkdir()
    (artifact / "metadata.json").write_text(
        json.dumps(
            {
                "coverage_status": "trusted",
                "rule_version": "rule-1",
                "feature_preparation_version": "feature-1",
                "market_1m_timestamp_semantics": "minute_open_utc",
                "forward_scan_start_policy": "signal_available_at_inclusive",
            }
        ),
        encoding="utf-8",
    )
    (artifact / "summary.json").write_text(
        json.dumps(
            {
                "signal_count": 12,
                "primary_label_complete_count": 10,
                "incomplete_label_count": 2,
                "precision_before_dd8": 0.5,
                "avg_abs_mae_24h_pct": 6.0,
            }
        ),
        encoding="utf-8",
    )
    (artifact / "signals.csv").write_text("symbol\n", encoding="utf-8")
    (artifact / "README.md").write_text("# run\n", encoding="utf-8")

    extracted = _MODULE.extract_selector_artifact(selector="ignition", artifact_dir=artifact)

    assert extracted["artifact_status"] == "complete"
    assert extracted["coverage_status"] == "trusted"
    assert extracted["sample_status"] == "sample_observed"
    assert extracted["selector_evidence_status"] == "evidence_eligible"
    assert extracted["primary_label_complete_count"] == 10
    assert extracted["precision_before_dd8"] == 0.5


def test_extract_selector_artifact_marks_sample_limited(tmp_path):
    artifact = tmp_path / "selector"
    artifact.mkdir()
    (artifact / "metadata.json").write_text(
        json.dumps(
            {
                "coverage_status": "trusted",
                "rule_version": "rule-1",
                "feature_preparation_version": "feature-1",
                "market_1m_timestamp_semantics": "minute_open_utc",
                "forward_scan_start_policy": "signal_available_at_inclusive",
            }
        ),
        encoding="utf-8",
    )
    (artifact / "summary.json").write_text(
        json.dumps(
            {
                "signal_count": 3,
                "primary_label_complete_count": 3,
                "incomplete_label_count": 0,
                "precision_before_dd8": 0.0,
                "avg_abs_mae_24h_pct": 1.0,
            }
        ),
        encoding="utf-8",
    )
    (artifact / "signals.csv").write_text("symbol\n", encoding="utf-8")
    (artifact / "README.md").write_text("# run\n", encoding="utf-8")

    extracted = _MODULE.extract_selector_artifact(selector="ignition", artifact_dir=artifact)

    assert extracted["sample_status"] == "sample_limited"
    assert extracted["selector_evidence_status"] == "diagnostic_only"


def test_extract_selector_artifact_fails_for_missing_required_field(tmp_path):
    artifact = tmp_path / "selector"
    artifact.mkdir()
    (artifact / "metadata.json").write_text("{}", encoding="utf-8")
    (artifact / "summary.json").write_text("{}", encoding="utf-8")
    (artifact / "signals.csv").write_text("symbol\n", encoding="utf-8")
    (artifact / "README.md").write_text("# run\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required selector field"):
        _MODULE.extract_selector_artifact(selector="ignition", artifact_dir=artifact)
```

- [ ] **Step 2: Run tests and verify missing extractor failures**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `extract_selector_artifact`.

- [ ] **Step 3: Implement required field extraction and status helpers**

Add constants:

```python
METADATA_REQUIRED_FIELDS = (
    "coverage_status",
    "rule_version",
    "feature_preparation_version",
    "market_1m_timestamp_semantics",
    "forward_scan_start_policy",
)
SUMMARY_REQUIRED_FIELDS = (
    "signal_count",
    "primary_label_complete_count",
    "incomplete_label_count",
    "precision_before_dd8",
    "avg_abs_mae_24h_pct",
)
```

Add helpers:

```python
def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def numeric_value(value: Any, *, field: str) -> int | float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"invalid numeric required field {field}: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"invalid numeric required field {field}: {value!r}")
        return value
    raise ValueError(f"invalid numeric required field {field}: {value!r}")


def sample_status(primary_label_complete_count: int) -> str:
    if primary_label_complete_count == 0:
        return "no_signals"
    if primary_label_complete_count < 10:
        return "sample_limited"
    return "sample_observed"


def selector_evidence_status(*, artifact_status: str, coverage_status: str, sample_status_value: str) -> str:
    if artifact_status != "complete":
        return "gate_failed"
    if coverage_status == "trusted" and sample_status_value == "sample_observed":
        return "evidence_eligible"
    return "diagnostic_only"


def extract_selector_artifact(*, selector: str, artifact_dir: Path) -> dict[str, Any]:
    missing_files = [name for name in REQUIRED_ARTIFACT_FILES if not (artifact_dir / name).is_file()]
    if missing_files:
        raise ValueError(f"selector {selector} missing required artifact files: {missing_files}")
    metadata = read_json_object(artifact_dir / "metadata.json")
    summary = read_json_object(artifact_dir / "summary.json")
    extracted: dict[str, Any] = {
        "selector": selector,
        "artifact_dir": str(artifact_dir),
        "artifact_status": "complete",
        "field_conflicts": [],
    }
    for field in METADATA_REQUIRED_FIELDS:
        if field not in metadata:
            raise ValueError(f"missing required selector field {field} in metadata.json for {selector}")
        extracted[field] = metadata[field]
    for field in SUMMARY_REQUIRED_FIELDS:
        if field not in summary:
            raise ValueError(f"missing required selector field {field} in summary.json for {selector}")
        extracted[field] = numeric_value(summary[field], field=field)
        if field in metadata and metadata[field] != summary[field]:
            extracted["field_conflicts"].append(field)
    primary_count = int(extracted["primary_label_complete_count"])
    extracted["sample_status"] = sample_status(primary_count)
    extracted["selector_evidence_status"] = selector_evidence_status(
        artifact_status=str(extracted["artifact_status"]),
        coverage_status=str(extracted["coverage_status"]),
        sample_status_value=str(extracted["sample_status"]),
    )
    return extracted
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit selector artifact extraction**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): summarize selector evidence artifacts"
```

---

### Task 6: Selector Validation Subprocess Orchestration

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for validator command construction and artifact placement**

Append these tests:

```python
def test_build_selector_validator_command_passes_exchange_selector_window_and_temp_root(tmp_path):
    command = _MODULE.build_selector_validator_command(
        selector="ignition_A",
        exchange="binance",
        window_days=30,
        end_at="2026-04-24T10:00:00+00:00",
        output_root=tmp_path,
    )

    assert command == [
        ".venv/bin/python",
        "scripts/validate_ultra_signal_production.py",
        "--signal-family",
        "ignition_A",
        "--exchange",
        "binance",
        "--window-days",
        "30",
        "--end-at",
        "2026-04-24T10:00:00+00:00",
        "--output-root",
        str(tmp_path),
    ]


def test_run_selector_validation_moves_single_generated_artifact(tmp_path, monkeypatch):
    def fake_run_command(**kwargs):
        temp_root = Path(kwargs["argv"][-1])
        generated = temp_root / "generated-run"
        generated.mkdir(parents=True)
        (generated / "summary.json").write_text(
            json.dumps(
                {
                    "signal_count": 10,
                    "primary_label_complete_count": 10,
                    "incomplete_label_count": 0,
                    "precision_before_dd8": 0.5,
                    "avg_abs_mae_24h_pct": 5.0,
                }
            ),
            encoding="utf-8",
        )
        (generated / "metadata.json").write_text(
            json.dumps(
                {
                    "coverage_status": "trusted",
                    "rule_version": "rule-1",
                    "feature_preparation_version": "feature-1",
                    "market_1m_timestamp_semantics": "minute_open_utc",
                    "forward_scan_start_policy": "signal_available_at_inclusive",
                }
            ),
            encoding="utf-8",
        )
        (generated / "signals.csv").write_text("symbol\n", encoding="utf-8")
        (generated / "README.md").write_text("# run\n", encoding="utf-8")
        return {"name": kwargs["name"], "exit_code": 0, "classification": "passed"}

    monkeypatch.setattr(_MODULE, "run_command", fake_run_command)

    result = _MODULE.run_selector_validation(
        selector="ignition_A",
        exchange="binance",
        window_days=30,
        end_at="2026-04-24T10:00:00+00:00",
        package_dir=tmp_path,
        cwd=Path.cwd(),
    )

    assert result["selector"] == "ignition_A"
    assert result["artifact_status"] == "complete"
    assert (tmp_path / "selectors" / "ignition_A" / "30d" / "summary.json").is_file()
```

- [ ] **Step 2: Run tests and verify missing orchestration helpers**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `build_selector_validator_command` and `run_selector_validation`.

- [ ] **Step 3: Implement selector validation subprocess orchestration**

Update imports:

```python
import uuid
```

Add helpers:

```python
def build_selector_validator_command(
    *,
    selector: str,
    exchange: str,
    window_days: int,
    end_at: str,
    output_root: Path,
) -> list[str]:
    return [
        ".venv/bin/python",
        "scripts/validate_ultra_signal_production.py",
        "--signal-family",
        selector,
        "--exchange",
        exchange,
        "--window-days",
        str(window_days),
        "--end-at",
        end_at,
        "--output-root",
        str(output_root),
    ]


def run_selector_validation(
    *,
    selector: str,
    exchange: str,
    window_days: int,
    end_at: str,
    package_dir: Path,
    cwd: Path,
) -> dict[str, Any]:
    temp_root = package_dir / "tmp" / f"selector-{selector}-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
    command = build_selector_validator_command(
        selector=selector,
        exchange=exchange,
        window_days=window_days,
        end_at=end_at,
        output_root=temp_root,
    )
    command_record = run_command(
        name=f"selector_{selector}",
        argv=command,
        package_dir=package_dir,
        log_dir=package_dir / "test_logs",
        cwd=cwd,
        env=None,
    )
    if command_record["classification"] != "passed":
        raise RuntimeError(f"validator failed for selector={selector}")
    generated = discover_single_artifact_directory(temp_root)
    destination = package_dir / "selectors" / selector / "30d"
    placed = place_artifact_directory(source=generated, destination=destination)
    extracted = extract_selector_artifact(selector=selector, artifact_dir=placed)
    extracted["command"] = command_record
    return extracted
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit selector orchestration**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): run selector evidence artifacts"
```

---

### Task 7: Traceable Comparison Config Parsing And Comparison Execution

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for traceable config parsing and no-config status**

Append these tests:

```python
def test_load_traceable_comparison_configs_requires_existing_artifacts(tmp_path):
    summary = tmp_path / "baseline-summary.json"
    metadata = tmp_path / "baseline-metadata.json"
    candidate_summary = tmp_path / "candidate-summary.json"
    candidate_metadata = tmp_path / "candidate-metadata.json"
    for path in (summary, metadata, candidate_summary, candidate_metadata):
        path.write_text("{}", encoding="utf-8")
    config = tmp_path / "comparison.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selector": "ultra_high_conviction",
                "comparison_type": "threshold_change",
                "change_id": "change-1",
                "baseline": {"summary_path": str(summary), "metadata_path": str(metadata)},
                "candidate": {"summary_path": str(candidate_summary), "metadata_path": str(candidate_metadata)},
                "change_classification": "non_material",
                "created_from": "existing_artifacts",
                "created_at": "2026-04-25T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    configs = _MODULE.load_traceable_comparison_configs(tmp_path)

    assert len(configs) == 1
    assert configs[0]["selector"] == "ultra_high_conviction"
    assert configs[0]["change_id"] == "change-1"


def test_load_traceable_comparison_configs_rejects_filename_inference(tmp_path):
    (tmp_path / "baseline_30d_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "candidate_30d_summary.json").write_text("{}", encoding="utf-8")

    configs = _MODULE.load_traceable_comparison_configs(tmp_path)

    assert configs == []


def test_comparison_summary_for_missing_config_is_not_run():
    summary = _MODULE.comparison_not_run("missing_traceable_baseline_candidate_config")

    assert summary == {
        "comparison_status": "comparison_not_run",
        "reason": "missing_traceable_baseline_candidate_config",
        "threshold_decision_status": "no_decision",
    }
```

- [ ] **Step 2: Run tests and verify missing comparison helpers**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `load_traceable_comparison_configs` and `comparison_not_run`.

- [ ] **Step 3: Implement config loading, schema checks, and comparison summary**

Add helpers:

```python
def _resolve_config_path(config_path: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return config_path.parent / path


def _require_file(config_path: Path, value: str, *, field: str) -> str:
    resolved = _resolve_config_path(config_path, value)
    if not resolved.is_file():
        raise ValueError(f"comparison config {config_path} field {field} does not exist: {resolved}")
    return str(resolved)


def normalize_traceable_comparison_config(config_path: Path) -> dict[str, Any]:
    config = read_json_object(config_path)
    if config.get("schema_version") != 1:
        raise ValueError(f"comparison config {config_path} requires schema_version=1")
    if config.get("created_from") != "existing_artifacts":
        raise ValueError(f"comparison config {config_path} must use created_from=existing_artifacts")
    change_classification = config.get("change_classification")
    if change_classification not in {"material", "non_material"}:
        raise ValueError(f"comparison config {config_path} has invalid change_classification")
    baseline = config.get("baseline")
    candidate = config.get("candidate")
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError(f"comparison config {config_path} requires baseline and candidate objects")
    normalized = {
        "config_path": str(config_path),
        "selector": str(config["selector"]),
        "comparison_type": str(config.get("comparison_type", "threshold_change")),
        "change_id": str(config.get("change_id", config_path.stem)),
        "change_classification": change_classification,
        "baseline": {
            "summary_path": _require_file(config_path, str(baseline.get("summary_path", "")), field="baseline.summary_path"),
            "metadata_path": _require_file(config_path, str(baseline.get("metadata_path", "")), field="baseline.metadata_path"),
        },
        "candidate": {
            "summary_path": _require_file(config_path, str(candidate.get("summary_path", "")), field="candidate.summary_path"),
            "metadata_path": _require_file(config_path, str(candidate.get("metadata_path", "")), field="candidate.metadata_path"),
        },
        "ninety_day": None,
    }
    ninety_day = config.get("ninety_day")
    if isinstance(ninety_day, dict) and bool(ninety_day.get("required", False)):
        baseline_90d = ninety_day.get("baseline")
        candidate_90d = ninety_day.get("candidate")
        if not isinstance(baseline_90d, dict) or not isinstance(candidate_90d, dict):
            raise ValueError(f"comparison config {config_path} requires ninety_day baseline and candidate objects")
        normalized["ninety_day"] = {
            "required": True,
            "baseline": {
                "summary_path": _require_file(config_path, str(baseline_90d.get("summary_path", "")), field="ninety_day.baseline.summary_path"),
                "metadata_path": _require_file(config_path, str(baseline_90d.get("metadata_path", "")), field="ninety_day.baseline.metadata_path"),
            },
            "candidate": {
                "summary_path": _require_file(config_path, str(candidate_90d.get("summary_path", "")), field="ninety_day.candidate.summary_path"),
                "metadata_path": _require_file(config_path, str(candidate_90d.get("metadata_path", "")), field="ninety_day.candidate.metadata_path"),
            },
        }
    return normalized


def load_traceable_comparison_configs(root: Path | None) -> list[dict[str, Any]]:
    if root is None or not root.exists():
        return []
    configs = []
    for path in sorted(root.glob("*.json")):
        try:
            configs.append(normalize_traceable_comparison_config(path))
        except (KeyError, TypeError, ValueError):
            continue
    return configs


def comparison_not_run(reason: str) -> dict[str, str]:
    return {
        "comparison_status": "comparison_not_run",
        "reason": reason,
        "threshold_decision_status": "no_decision",
    }
```

- [ ] **Step 4: Add failing test for comparison command execution**

Append:

```python
def test_build_comparison_command_includes_90d_when_required(tmp_path):
    command = _MODULE.build_comparison_command(
        baseline_config=tmp_path / "baseline.json",
        candidate_config=tmp_path / "candidate.json",
        baseline_90d_config=tmp_path / "baseline-90d.json",
        candidate_90d_config=tmp_path / "candidate-90d.json",
        change_classification="material",
        output_root=tmp_path / "out",
    )

    assert "--compare-baseline-config" in command
    assert "--compare-candidate-config" in command
    assert "--compare-90d-baseline-config" in command
    assert "--compare-90d-candidate-config" in command
    assert "--require-90d" in command
    assert command[-2:] == ["--output-root", str(tmp_path / "out")]
```

- [ ] **Step 5: Implement comparison command construction**

Add:

```python
def build_comparison_command(
    *,
    baseline_config: Path,
    candidate_config: Path,
    baseline_90d_config: Path | None,
    candidate_90d_config: Path | None,
    change_classification: str,
    output_root: Path,
) -> list[str]:
    command = [
        ".venv/bin/python",
        "scripts/validate_ultra_signal_production.py",
        "--compare-baseline-config",
        str(baseline_config),
        "--compare-candidate-config",
        str(candidate_config),
        "--change-classification",
        change_classification,
    ]
    if baseline_90d_config is not None and candidate_90d_config is not None:
        command.extend(
            [
                "--compare-90d-baseline-config",
                str(baseline_90d_config),
                "--compare-90d-candidate-config",
                str(candidate_90d_config),
                "--require-90d",
            ]
        )
    command.extend(["--output-root", str(output_root)])
    return command
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 7: Commit comparison config support**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): load traceable comparison configs"
```

---

### Task 8: Manifest, Layered Status, And Evidence README

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing tests for status calculation and README wording**

Append:

```python
def test_calculate_status_downgrades_missing_comparison_to_diagnostics():
    status = _MODULE.calculate_package_status(
        commands=[{"classification": "passed"}],
        db_smoke={"classification": "executed"},
        selector_artifacts={"ignition": {"selector_evidence_status": "evidence_eligible"}},
        comparison={"comparison_status": "comparison_not_run"},
        tests_skipped_by_user=False,
        end_at_safety_status="safe",
        relevant_dirty_paths=[],
        dirty_diff_path=None,
    )

    assert status["gate_status"] == "passed"
    assert status["formal_evidence_gate_passed"] is True
    assert status["overall_status"] == "passed_with_diagnostics"
    assert status["threshold_decision_status"] == "no_decision"


def test_calculate_status_blocks_formal_gate_when_tests_skipped():
    status = _MODULE.calculate_package_status(
        commands=[],
        db_smoke={"classification": "executed"},
        selector_artifacts={"ignition": {"selector_evidence_status": "evidence_eligible"}},
        comparison={"comparison_status": "comparison_not_run"},
        tests_skipped_by_user=True,
        end_at_safety_status="safe",
        relevant_dirty_paths=[],
        dirty_diff_path=None,
    )

    assert status["gate_status"] == "failed"
    assert status["formal_evidence_gate_passed"] is False
    assert status["overall_status"] == "passed_with_diagnostics"


def test_build_evidence_readme_uses_fixed_no_comparison_wording(tmp_path):
    manifest = {
        "package_date": "2026-04-25",
        "run_id": "103422-52e5e9b",
        "exchange_universe": ["binance"],
        "resolved_end_at": "2026-04-24T10:00:00+00:00",
        "safe_end_at": "2026-04-24T10:00:00+00:00",
        "overall_status": "passed_with_diagnostics",
        "gate_status": "passed",
        "formal_evidence_gate_passed": True,
        "threshold_decision_status": "no_decision",
        "commands": [{"name": "targeted_tests", "classification": "passed", "exit_code": 0}],
        "db_smoke": {"classification": "executed", "passed_count": 1, "skipped_count": 0, "failed_count": 0},
        "selector_artifacts": {"ignition": {"selector_evidence_status": "evidence_eligible", "coverage_status": "trusted", "sample_status": "sample_observed", "primary_label_complete_count": 10}},
        "comparison": {"comparison_status": "comparison_not_run", "reason": "missing_traceable_baseline_candidate_config"},
        "relevant_dirty_paths": [],
        "end_at_safety_status": "safe",
    }

    text = _MODULE.build_evidence_readme(manifest)

    assert "## Gate Summary" in text
    assert "## Evidence Decision" in text
    assert "No threshold change is supported by this package because comparison was not run: missing_traceable_baseline_candidate_config." in text
```

- [ ] **Step 2: Run tests and verify missing status/README helpers**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `calculate_package_status` and `build_evidence_readme`.

- [ ] **Step 3: Implement status and README helpers**

Add:

```python
def calculate_package_status(
    *,
    commands: list[dict[str, Any]],
    db_smoke: dict[str, Any],
    selector_artifacts: dict[str, dict[str, Any]],
    comparison: dict[str, Any],
    tests_skipped_by_user: bool,
    end_at_safety_status: str,
    relevant_dirty_paths: list[str],
    dirty_diff_path: str | None,
) -> dict[str, Any]:
    command_failed = any(command.get("classification") != "passed" for command in commands)
    smoke_failed = db_smoke.get("classification") != "executed"
    selector_failed = any(item.get("selector_evidence_status") == "gate_failed" for item in selector_artifacts.values())
    unsafe_end = end_at_safety_status != "safe"
    dirty_blocks_gate = bool(relevant_dirty_paths) and dirty_diff_path is None
    gate_failed = command_failed or smoke_failed or selector_failed or tests_skipped_by_user or unsafe_end or dirty_blocks_gate
    diagnostic = (
        comparison.get("comparison_status") == "comparison_not_run"
        or any(item.get("selector_evidence_status") == "diagnostic_only" for item in selector_artifacts.values())
        or bool(relevant_dirty_paths)
        or tests_skipped_by_user
        or unsafe_end
    )
    if gate_failed and (command_failed or smoke_failed or selector_failed or dirty_blocks_gate):
        overall_status = "failed"
    elif diagnostic:
        overall_status = "passed_with_diagnostics"
    else:
        overall_status = "passed"
    comparison_status = comparison.get("comparison_status")
    threshold_decision_status = "no_decision"
    if not gate_failed and comparison_status == "evidence_backed" and not diagnostic:
        threshold_decision_status = "supported"
    elif comparison_status in {"not_supported", "experimental_only", "insufficient"}:
        threshold_decision_status = "not_supported"
    return {
        "gate_status": "failed" if gate_failed else "passed",
        "formal_evidence_gate_passed": not gate_failed,
        "overall_status": overall_status,
        "threshold_decision_status": threshold_decision_status,
    }


def build_evidence_decision(manifest: dict[str, Any]) -> str:
    comparison = manifest.get("comparison", {})
    comparison_status = comparison.get("comparison_status")
    if manifest.get("formal_evidence_gate_passed") is not True:
        return "This package is not a formal evidence gate for production threshold decisions."
    if comparison_status == "comparison_not_run":
        return f"No threshold change is supported by this package because comparison was not run: {comparison.get('reason')}."
    if comparison_status == "evidence_backed" and manifest.get("threshold_decision_status") == "supported":
        return (
            f"This package supports retaining candidate threshold change {comparison.get('change_id')} "
            f"because comparison artifact {comparison.get('comparison_path')} reports evidence_backed "
            "with trusted 30d evidence and trusted required 90d review."
        )
    return f"No threshold change is supported by this package because comparison result is {comparison_status}: {comparison.get('reason')}."


def build_evidence_readme(manifest: dict[str, Any]) -> str:
    command_lines = [
        f"- {command.get('name')}: {command.get('classification')} exit={command.get('exit_code')}"
        for command in manifest.get("commands", [])
    ]
    selector_lines = [
        (
            f"- {selector}: evidence={data.get('selector_evidence_status')}, "
            f"coverage={data.get('coverage_status')}, sample={data.get('sample_status')}, "
            f"primary_label_complete_count={data.get('primary_label_complete_count')}"
        )
        for selector, data in sorted(manifest.get("selector_artifacts", {}).items())
    ]
    caveats = []
    if manifest.get("exchange_universe") == ["binance"]:
        caveats.append("- This package validates binance only; other exchange evidence was not generated in this run.")
    if manifest.get("relevant_dirty_paths"):
        caveats.append("- Relevant dirty paths were present; production-ready threshold claims require human review of the archived diff.")
    if manifest.get("end_at_safety_status") != "safe":
        caveats.append("- The requested end_at was unsafe; this package is diagnostic-only.")
    if not caveats:
        caveats.append("- No package-level caveats recorded.")
    lines = [
        "# Validation Evidence Package",
        "",
        "## Gate Summary",
        "",
        f"- overall_status: {manifest.get('overall_status')}",
        f"- gate_status: {manifest.get('gate_status')}",
        f"- formal_evidence_gate_passed: {manifest.get('formal_evidence_gate_passed')}",
        f"- threshold_decision_status: {manifest.get('threshold_decision_status')}",
        "",
        "## Test Results",
        "",
        *command_lines,
        "",
        "## DB Smoke",
        "",
        f"- classification: {manifest.get('db_smoke', {}).get('classification')}",
        f"- passed_count: {manifest.get('db_smoke', {}).get('passed_count')}",
        f"- skipped_count: {manifest.get('db_smoke', {}).get('skipped_count')}",
        f"- failed_count: {manifest.get('db_smoke', {}).get('failed_count')}",
        "",
        "## Window",
        "",
        f"- resolved_end_at: {manifest.get('resolved_end_at')}",
        f"- safe_end_at: {manifest.get('safe_end_at')}",
        "",
        "## Selector Artifacts",
        "",
        *selector_lines,
        "",
        "## Comparison",
        "",
        f"- comparison_status: {manifest.get('comparison', {}).get('comparison_status')}",
        f"- reason: {manifest.get('comparison', {}).get('reason')}",
        "",
        "## Evidence Decision",
        "",
        build_evidence_decision(manifest),
        "",
        "## Caveats",
        "",
        *caveats,
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit manifest status and README helpers**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): build evidence package summary"
```

---

### Task 9: Main Orchestration And Partial Manifest Writing

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing integration-style test using fakes**

Append:

```python
def test_run_evidence_package_writes_manifest_and_readme(tmp_path, monkeypatch):
    monkeypatch.setattr(_MODULE, "current_git_sha", lambda cwd: "52e5e9bbc5dd0fc0b3f6738df8bd965e482fb83e")
    monkeypatch.setattr(_MODULE, "dirty_paths", lambda cwd: [])
    monkeypatch.setattr(_MODULE, "archive_dirty_diff", lambda cwd, package_dir, paths: None)
    monkeypatch.setattr(_MODULE, "query_latest_market_ts", lambda exchange: datetime(2026, 4, 25, 10, 55, tzinfo=timezone.utc))
    monkeypatch.setattr(_MODULE, "utc_now", lambda: datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc))
    monkeypatch.setattr(
        _MODULE,
        "run_command",
        lambda **kwargs: {
            "name": kwargs["name"],
            "argv": kwargs["argv"],
            "started_at": "2026-04-25T10:00:00+00:00",
            "finished_at": "2026-04-25T10:00:01+00:00",
            "exit_code": 0,
            "stdout_log": str(tmp_path / f"{kwargs['name']}.stdout.log"),
            "stderr_log": str(tmp_path / f"{kwargs['name']}.stderr.log"),
            "junit_xml": str(kwargs.get("junit_xml")) if kwargs.get("junit_xml") else None,
            "classification": "passed",
        },
    )
    monkeypatch.setattr(
        _MODULE,
        "classify_pytest_junit",
        lambda junit: {"passed_count": 1, "skipped_count": 0, "failed_count": 0, "classification": "executed"},
    )
    monkeypatch.setattr(
        _MODULE,
        "run_selector_validation",
        lambda **kwargs: {
            "selector": kwargs["selector"],
            "artifact_dir": str(tmp_path / kwargs["selector"]),
            "artifact_status": "complete",
            "coverage_status": "trusted",
            "sample_status": "sample_observed",
            "selector_evidence_status": "evidence_eligible",
            "primary_label_complete_count": 10,
            "signal_count": 10,
            "incomplete_label_count": 0,
            "precision_before_dd8": 0.5,
            "avg_abs_mae_24h_pct": 5.0,
            "rule_version": "rule-1",
            "feature_preparation_version": "feature-1",
            "market_1m_timestamp_semantics": "minute_open_utc",
            "forward_scan_start_policy": "signal_available_at_inclusive",
        },
    )

    exit_code = _MODULE.run_evidence_package(
        [
            "--output-root",
            str(tmp_path / "validation"),
            "--selectors",
            "ignition",
            "--exchange",
            "binance",
        ],
        cwd=Path.cwd(),
    )

    assert exit_code == 0
    package_dir = tmp_path / "validation" / "2026-04-25" / "121500-52e5e9b"
    manifest = json.loads((package_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["selector_artifacts"]["ignition"]["selector_evidence_status"] == "evidence_eligible"
    assert manifest["comparison"]["comparison_status"] == "comparison_not_run"
    assert manifest["dirty_worktree_policy"] == "clean"
    assert (package_dir / "EVIDENCE_PACKAGE.md").is_file()
```

Append this failure-path test in the same step:

```python
def test_run_evidence_package_writes_partial_manifest_on_db_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(_MODULE, "current_git_sha", lambda cwd: "52e5e9bbc5dd0fc0b3f6738df8bd965e482fb83e")
    monkeypatch.setattr(_MODULE, "dirty_paths", lambda cwd: [])
    monkeypatch.setattr(_MODULE, "archive_dirty_diff", lambda cwd, package_dir, paths: None)
    monkeypatch.setattr(_MODULE, "query_latest_market_ts", lambda exchange: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    monkeypatch.setattr(_MODULE, "utc_now", lambda: datetime(2026, 4, 25, 12, 15, tzinfo=timezone.utc))

    exit_code = _MODULE.run_evidence_package(
        ["--output-root", str(tmp_path / "validation"), "--selectors", "ignition"],
        cwd=Path.cwd(),
    )

    package_dir = tmp_path / "validation" / "2026-04-25" / "121500-52e5e9b"
    manifest = json.loads((package_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert exit_code == 1
    assert manifest["overall_status"] == "failed"
    assert manifest["formal_evidence_gate_passed"] is False
    assert "db unavailable" in manifest["error"]
    assert (package_dir / "EVIDENCE_PACKAGE.md").is_file()
```

- [ ] **Step 2: Run tests and verify missing orchestration function**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `run_evidence_package`.

- [ ] **Step 3: Implement orchestration and partial manifest writing**

Add:

```python
def write_manifest(package_dir: Path, manifest: dict[str, Any]) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def targeted_test_command() -> list[str]:
    return [
        ".venv/bin/pytest",
        "tests/test_validate_signal_semantics.py",
        "tests/test_validate_ultra_signal_production.py",
        "-q",
    ]


def impacted_test_command() -> list[str]:
    return [
        ".venv/bin/pytest",
        "tests/test_trade_backtest.py",
        "tests/test_signal_v2.py",
        "tests/test_validate_signal_semantics.py",
        "tests/test_validate_ultra_signal_production.py",
        "-q",
    ]


def db_smoke_command(junit_xml: Path) -> list[str]:
    return [
        ".venv/bin/pytest",
        "tests/test_validate_signal_db_smoke.py",
        "-q",
        "-rs",
        "--junitxml",
        str(junit_xml),
    ]


def run_evidence_package(argv: list[str] | None = None, *, cwd: Path | None = None) -> int:
    cwd = cwd or Path.cwd()
    args = parse_args(argv)
    selectors = parse_selector_list(args.selectors)
    git_sha = current_git_sha(cwd=cwd)
    now = utc_now()
    identity = build_run_identity(now=now, git_sha=git_sha)
    package_dir = resolve_package_dir(
        output_root=Path(args.output_root),
        package_date=identity["package_date"],
        run_id=identity["run_id"],
        overwrite=bool(args.overwrite),
    )
    if package_dir.exists() and args.overwrite:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=False)
    dirty = dirty_paths(cwd=cwd)
    relevant_dirty = relevant_dirty_paths(dirty)
    dirty_diff = archive_dirty_diff(cwd=cwd, package_dir=package_dir, paths=relevant_dirty)
    dirty_policy = dirty_worktree_policy(relevant_dirty)
    manifest: dict[str, Any] = {
        "package_date": identity["package_date"],
        "run_id": identity["run_id"],
        "package_dir": str(package_dir),
        "run_started_at": now.isoformat(),
        "run_finished_at": None,
        "git_sha": git_sha,
        "git_sha7": identity["git_sha7"],
        "worktree_dirty": bool(dirty),
        "dirty_paths": dirty,
        "relevant_dirty_paths": relevant_dirty,
        "dirty_diff_path": dirty_diff,
        "dirty_worktree_policy": dirty_policy,
        "exchange_universe": [args.exchange],
        "window_days": int(args.window_days),
        "selectors": list(selectors),
        "commands": [],
        "db_smoke": {},
        "selector_artifacts": {},
        "comparison": {},
        "environment": collect_environment(cwd=cwd),
    }
    try:
        latest_market_ts = query_latest_market_ts(exchange=args.exchange)
        window = resolve_end_at(
            requested_end_at=args.end_at,
            latest_market_ts=latest_market_ts,
            now=now,
            allow_unsafe=bool(args.allow_unsafe_end_at),
        )
        manifest.update(window)
        manifest["latest_market_1m_ts"] = latest_market_ts.isoformat()
        if not args.skip_tests:
            manifest["commands"].append(
                run_command(
                    name="targeted_tests",
                    argv=targeted_test_command(),
                    package_dir=package_dir,
                    log_dir=package_dir / "test_logs",
                    cwd=cwd,
                    env=None,
                )
            )
            manifest["commands"].append(
                run_command(
                    name="impacted_tests",
                    argv=impacted_test_command(),
                    package_dir=package_dir,
                    log_dir=package_dir / "test_logs",
                    cwd=cwd,
                    env=None,
                )
            )
        junit_xml = package_dir / "db_smoke" / "junit.xml"
        junit_xml.parent.mkdir(parents=True, exist_ok=True)
        db_command_record = run_command(
            name="db_smoke",
            argv=db_smoke_command(junit_xml),
            package_dir=package_dir,
            log_dir=package_dir / "db_smoke",
            cwd=cwd,
            env={"ACTS_RUN_DB_SMOKE": "1"},
            junit_xml=junit_xml,
        )
        smoke = classify_pytest_junit(junit_xml)
        smoke["command"] = db_command_record
        manifest["db_smoke"] = smoke
        for selector in selectors:
            manifest["selector_artifacts"][selector] = run_selector_validation(
                selector=selector,
                exchange=args.exchange,
                window_days=int(args.window_days),
                end_at=str(manifest["resolved_end_at"]),
                package_dir=package_dir,
                cwd=cwd,
            )
        comparison_root = Path(args.comparison_root) if args.comparison_root else None
        configs = load_traceable_comparison_configs(comparison_root)
        manifest["comparison"] = comparison_not_run("missing_traceable_baseline_candidate_config") if not configs else {"comparison_status": "insufficient", "reason": "comparison_config_present_before_comparison_support", "threshold_decision_status": "no_decision"}
        manifest.update(
            calculate_package_status(
                commands=list(manifest["commands"]),
                db_smoke=dict(manifest["db_smoke"]),
                selector_artifacts=dict(manifest["selector_artifacts"]),
                comparison=dict(manifest["comparison"]),
                tests_skipped_by_user=bool(args.skip_tests),
                end_at_safety_status=str(manifest["end_at_safety_status"]),
                relevant_dirty_paths=list(relevant_dirty),
                dirty_diff_path=dirty_diff,
            )
        )
        manifest["run_finished_at"] = utc_now().isoformat()
        write_manifest(package_dir, manifest)
        (package_dir / "EVIDENCE_PACKAGE.md").write_text(build_evidence_readme(manifest), encoding="utf-8")
        return 0 if manifest["overall_status"] != "failed" else 1
    except Exception as exc:
        manifest["error"] = str(exc)
        manifest["gate_status"] = "failed"
        manifest["formal_evidence_gate_passed"] = False
        manifest["threshold_decision_status"] = "no_decision"
        manifest["overall_status"] = "failed"
        manifest["run_finished_at"] = utc_now().isoformat()
        write_manifest(package_dir, manifest)
        (package_dir / "EVIDENCE_PACKAGE.md").write_text(build_evidence_readme(manifest), encoding="utf-8")
        return 1


def main(argv: list[str] | None = None) -> int:
    return run_evidence_package(argv, cwd=Path.cwd())
```

Remove the earlier skeleton `main()` that only parsed arguments.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all current runner tests pass.

- [ ] **Step 5: Commit main orchestration**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): orchestrate evidence package runs"
```

---

### Task 10: Complete Comparison Execution And Final Verification

**Files:**
- Modify: `scripts/run_validation_evidence_package.py`
- Modify: `tests/test_run_validation_evidence_package.py`

- [ ] **Step 1: Add failing test for successful comparison execution from traceable config**

Append:

```python
def test_run_comparison_config_writes_side_configs_and_result(tmp_path, monkeypatch):
    for name in ("baseline-summary.json", "baseline-metadata.json", "candidate-summary.json", "candidate-metadata.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    config = {
        "selector": "ultra_high_conviction",
        "change_id": "change-1",
        "change_classification": "non_material",
        "baseline": {"summary_path": str(tmp_path / "baseline-summary.json"), "metadata_path": str(tmp_path / "baseline-metadata.json")},
        "candidate": {"summary_path": str(tmp_path / "candidate-summary.json"), "metadata_path": str(tmp_path / "candidate-metadata.json")},
        "ninety_day": None,
    }

    def fake_run_command(**kwargs):
        output_root = Path(kwargs["argv"][-1])
        output_root.mkdir(parents=True, exist_ok=True)
        result_path = output_root / "result-comparison.json"
        result_path.write_text(json.dumps({"status": "evidence_backed", "reason": "metrics_pass"}), encoding="utf-8")
        readme_path = output_root / "result-comparison_README.md"
        readme_path.write_text("# comparison\n", encoding="utf-8")
        return {"name": kwargs["name"], "exit_code": 0, "classification": "passed"}

    monkeypatch.setattr(_MODULE, "run_command", fake_run_command)

    result = _MODULE.run_comparison_config(config=config, package_dir=tmp_path, cwd=Path.cwd())

    assert result["comparison_status"] == "evidence_backed"
    assert result["reason"] == "metrics_pass"
    assert result["change_id"] == "change-1"
    assert Path(result["comparison_path"]).is_file()


def test_run_comparison_config_persists_stdout_json_when_validator_writes_no_files(tmp_path, monkeypatch):
    for name in ("baseline-summary.json", "baseline-metadata.json", "candidate-summary.json", "candidate-metadata.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    config = {
        "selector": "ultra_high_conviction",
        "change_id": "change-1",
        "change_classification": "non_material",
        "baseline": {"summary_path": str(tmp_path / "baseline-summary.json"), "metadata_path": str(tmp_path / "baseline-metadata.json")},
        "candidate": {"summary_path": str(tmp_path / "candidate-summary.json"), "metadata_path": str(tmp_path / "candidate-metadata.json")},
        "ninety_day": None,
    }

    def fake_run_command(**kwargs):
        stdout_log = tmp_path / "comparison.stdout.log"
        stdout_log.write_text('{"status": "insufficient", "reason": "sample_limited"}\n', encoding="utf-8")
        return {
            "name": kwargs["name"],
            "exit_code": 0,
            "classification": "passed",
            "stdout_log": str(stdout_log),
            "stderr_log": str(tmp_path / "comparison.stderr.log"),
        }

    monkeypatch.setattr(_MODULE, "run_command", fake_run_command)

    result = _MODULE.run_comparison_config(config=config, package_dir=tmp_path, cwd=Path.cwd())

    assert result["comparison_status"] == "insufficient"
    assert result["reason"] == "sample_limited"
    assert json.loads(Path(result["comparison_path"]).read_text(encoding="utf-8"))["status"] == "insufficient"
    assert Path(result["comparison_readme_path"]).read_text(encoding="utf-8").startswith("# Signal Validation Comparison")
```

- [ ] **Step 2: Run tests and verify missing comparison executor**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: missing `run_comparison_config`.

- [ ] **Step 3: Implement comparison execution**

Add:

```python
def write_validator_comparison_side_config(path: Path, side: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "summary_path": side["summary_path"],
                "metadata_path": side["metadata_path"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def build_comparison_result_readme(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Signal Validation Comparison",
            "",
            f"- status: {result.get('status')}",
            f"- reason: {result.get('reason')}",
            "",
        ]
    )


def _comparison_json_from_stdout(stdout_log: Path) -> dict[str, Any]:
    for line in stdout_log.read_text(encoding="utf-8").splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "status" in parsed:
            return parsed
    raise RuntimeError(f"comparison stdout did not contain a JSON result: {stdout_log}")


def discover_comparison_result(output_root: Path, *, stdout_log: Path | None, comparison_dir: Path) -> tuple[Path, Path | None]:
    comparison_paths = sorted(output_root.glob("*-comparison.json"))
    if len(comparison_paths) > 1:
        raise RuntimeError(f"expected at most one comparison json under {output_root}, found {len(comparison_paths)}")
    if len(comparison_paths) == 1:
        comparison_path = comparison_paths[0]
        readme_path = comparison_path.with_name(comparison_path.stem + "_README.md")
        return comparison_path, readme_path if readme_path.is_file() else None
    if stdout_log is None:
        raise RuntimeError(f"expected comparison json under {output_root} or stdout JSON fallback")
    result = _comparison_json_from_stdout(stdout_log)
    comparison_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = comparison_dir / "comparison.json"
    readme_path = comparison_dir / "comparison_README.md"
    comparison_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    readme_path.write_text(build_comparison_result_readme(result), encoding="utf-8")
    return comparison_path, readme_path


def run_comparison_config(*, config: dict[str, Any], package_dir: Path, cwd: Path) -> dict[str, Any]:
    selector = str(config["selector"])
    comparison_dir = package_dir / "comparisons" / selector
    generated_config_dir = comparison_dir / "configs"
    output_root = comparison_dir / "output"
    baseline_config = write_validator_comparison_side_config(generated_config_dir / "baseline.json", config["baseline"])
    candidate_config = write_validator_comparison_side_config(generated_config_dir / "candidate.json", config["candidate"])
    baseline_90d_config = None
    candidate_90d_config = None
    ninety_day = config.get("ninety_day")
    if isinstance(ninety_day, dict) and ninety_day.get("required"):
        baseline_90d_config = write_validator_comparison_side_config(
            generated_config_dir / "baseline_90d.json",
            ninety_day["baseline"],
        )
        candidate_90d_config = write_validator_comparison_side_config(
            generated_config_dir / "candidate_90d.json",
            ninety_day["candidate"],
        )
    command = build_comparison_command(
        baseline_config=baseline_config,
        candidate_config=candidate_config,
        baseline_90d_config=baseline_90d_config,
        candidate_90d_config=candidate_90d_config,
        change_classification=str(config["change_classification"]),
        output_root=output_root,
    )
    record = run_command(
        name=f"comparison_{selector}",
        argv=command,
        package_dir=package_dir,
        log_dir=comparison_dir,
        cwd=cwd,
        env=None,
    )
    if record["classification"] != "passed":
        return {
            "comparison_status": "insufficient",
            "reason": "comparison_command_failed",
            "threshold_decision_status": "no_decision",
            "command": record,
            "change_id": config.get("change_id"),
        }
    comparison_path, readme_path = discover_comparison_result(
        output_root,
        stdout_log=Path(str(record.get("stdout_log"))) if record.get("stdout_log") else None,
        comparison_dir=comparison_dir,
    )
    result = read_json_object(comparison_path)
    status = str(result.get("status", "insufficient"))
    threshold_decision_status = "supported" if status == "evidence_backed" else "not_supported"
    return {
        "comparison_status": status,
        "reason": str(result.get("reason", "")),
        "threshold_decision_status": threshold_decision_status,
        "comparison_path": str(comparison_path),
        "comparison_readme_path": str(readme_path) if readme_path is not None else None,
        "change_id": config.get("change_id"),
        "selector": selector,
        "command": record,
    }
```

- [ ] **Step 4: Wire comparison execution into `run_evidence_package()`**

Replace:

```python
manifest["comparison"] = comparison_not_run("missing_traceable_baseline_candidate_config") if not configs else {"comparison_status": "insufficient", "reason": "comparison_config_present_before_comparison_support", "threshold_decision_status": "no_decision"}
```

with:

```python
if not configs:
    manifest["comparison"] = comparison_not_run("missing_traceable_baseline_candidate_config")
elif len(configs) == 1:
    manifest["comparison"] = run_comparison_config(config=configs[0], package_dir=package_dir, cwd=cwd)
else:
    manifest["comparison"] = {
        "comparison_status": "insufficient",
        "reason": "multiple_traceable_comparison_configs_not_supported_in_p0",
        "threshold_decision_status": "no_decision",
        "config_count": len(configs),
    }
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: all runner tests pass.

- [ ] **Step 6: Run targeted validation suites**

Run:

```bash
.venv/bin/pytest \
  tests/test_run_validation_evidence_package.py \
  tests/test_validate_signal_semantics.py \
  tests/test_validate_ultra_signal_production.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 7: Run impacted suite**

Run:

```bash
.venv/bin/pytest \
  tests/test_trade_backtest.py \
  tests/test_signal_v2.py \
  tests/test_run_validation_evidence_package.py \
  tests/test_validate_signal_semantics.py \
  tests/test_validate_ultra_signal_production.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 8: Check CLI help**

Run:

```bash
.venv/bin/python scripts/run_validation_evidence_package.py --help
```

Expected: help output includes `--exchange`, `--end-at`, `--selectors`, `--comparison-root`, `--skip-tests`, `--allow-unsafe-end-at`, and `--overwrite`.

- [ ] **Step 9: Commit final comparison execution**

Run:

```bash
git add scripts/run_validation_evidence_package.py tests/test_run_validation_evidence_package.py
git commit -m "feat(validation): execute traceable evidence comparisons"
```

---

### Task 11: Optional Real Evidence Smoke Run

**Files:**
- No source edits expected.
- Runtime artifacts under: `artifacts/autoresearch/validation/<YYYY-MM-DD>/<run_id>/`

- [ ] **Step 1: Run a diagnostic package with one selector first**

Run:

```bash
.venv/bin/python scripts/run_validation_evidence_package.py \
  --selectors ultra_high_conviction \
  --exchange binance
```

Expected: exits `0` when local DB and tests are available, or exits `1` with a partial `run_manifest.json` explaining the failing gate.

- [ ] **Step 2: Inspect the generated manifest**

Run:

```bash
find artifacts/autoresearch/validation -path '*/run_manifest.json' -printf '%T@ %p\n' | sort -n | tail -1
```

Expected: prints the newest `run_manifest.json` path.

- [ ] **Step 3: Inspect the newest README**

Run:

```bash
find artifacts/autoresearch/validation -path '*/EVIDENCE_PACKAGE.md' -printf '%T@ %p\n' | sort -n | tail -1
```

Expected: prints the newest `EVIDENCE_PACKAGE.md` path. Open it and confirm it has `## Gate Summary`, `## DB Smoke`, `## Selector Artifacts`, `## Evidence Decision`, and `## Caveats`.

- [ ] **Step 4: Do not commit runtime artifacts**

Run:

```bash
git status --short artifacts/autoresearch/validation
```

Expected: runtime artifact files are untracked or ignored. Do not add them to git unless the user explicitly asks to archive a specific package.

---

## Final Verification

- [ ] Run the runner test file:

```bash
.venv/bin/pytest tests/test_run_validation_evidence_package.py -q
```

Expected: pass.

- [ ] Run validation-focused tests:

```bash
.venv/bin/pytest \
  tests/test_run_validation_evidence_package.py \
  tests/test_validate_signal_semantics.py \
  tests/test_validate_ultra_signal_production.py \
  -q
```

Expected: pass.

- [ ] Run impacted suite:

```bash
.venv/bin/pytest \
  tests/test_trade_backtest.py \
  tests/test_signal_v2.py \
  tests/test_run_validation_evidence_package.py \
  tests/test_validate_signal_semantics.py \
  tests/test_validate_ultra_signal_production.py \
  -q
```

Expected: pass.

- [ ] Check CLI help:

```bash
.venv/bin/python scripts/run_validation_evidence_package.py --help
```

Expected: all spec-required flags are shown.

- [ ] Confirm git only contains source/test changes and no generated runtime package:

```bash
git status --short
```

Expected: source and tests changed as intended; no `artifacts/autoresearch/validation/...` files staged.

## Spec Coverage Self-Review

- Default selector set: Task 1 and Task 6.
- Collision-safe `<YYYY-MM-DD>/<run_id>` package path and overwrite rule: Task 1.
- Single-exchange scope and `--exchange`: Task 1, Task 3, Task 6.
- DB-scoped safe `end_at` and manual safety: Task 3.
- DB smoke pass/skip/fail detection: Task 4 and Task 9.
- Deterministic artifact discovery: Task 4 and Task 6.
- Canonical selector field extraction and sample status: Task 5.
- Traceable comparison schema and no filename inference: Task 7.
- Comparison execution, file result capture, and stdout JSON fallback persistence: Task 10.
- Layered package/selector/comparison/threshold status: Task 8.
- Dirty worktree handling, dirty-worktree policy, and diff archive: Task 2 and Task 9.
- Manifest, partial manifest failure path, and README output: Task 8 and Task 9.
- Skip-tests downgrade: Task 8 and Task 9.
- Normal tests stay DB-independent: all test tasks use monkeypatch/fakes; real DB is only in Task 11.
