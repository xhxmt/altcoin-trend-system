#!/usr/bin/env python3

import argparse
import difflib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from altcoin_trend.config import load_settings
from altcoin_trend.db import build_engine


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
REQUIRED_ARTIFACT_FILES = ("summary.json", "metadata.json", "signals.csv", "README.md")
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
COUNT_REQUIRED_FIELDS = (
    "signal_count",
    "primary_label_complete_count",
    "incomplete_label_count",
)
SAFE_SELECTOR_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


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


RELEVANT_DIRTY_PREFIXES = (
    "scripts/",
    "src/altcoin_trend/",
    "tests/",
    "docs/superpowers/specs/",
    "docs/superpowers/plans/",
)


class DirtyPathList(list[str]):
    def __init__(self, paths: list[str], *, rename_pairs: list[tuple[str, str]] | None = None) -> None:
        super().__init__(paths)
        self.rename_pairs = tuple(rename_pairs or [])


class SelectorValidationError(RuntimeError):
    def __init__(
        self,
        *,
        selector: str,
        command: dict[str, Any] | None,
        reason: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.selector = selector
        self.command = command
        self.reason = reason


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


def build_selector_validator_command(
    *,
    selector: str,
    exchange: str,
    window_days: int,
    end_at: str,
    output_root: Path,
) -> list[str]:
    validate_selector_name(selector)
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


def validate_selector_name(selector: str) -> str:
    if not SAFE_SELECTOR_NAME_RE.fullmatch(selector):
        raise ValueError(f"unsafe selector name: {selector!r}")
    return selector


def run_selector_validation(
    *,
    selector: str,
    exchange: str,
    window_days: int,
    end_at: str,
    package_dir: Path,
    cwd: Path,
) -> dict[str, Any]:
    selector_component = validate_selector_name(selector)
    temp_root = package_dir / "tmp" / f"selector-{selector_component}-{uuid.uuid4().hex}"
    temp_root.mkdir(parents=True, exist_ok=False)
    command = build_selector_validator_command(
        selector=selector,
        exchange=exchange,
        window_days=window_days,
        end_at=end_at,
        output_root=temp_root,
    )
    command_record = run_command(
        name=f"selector_{selector_component}",
        argv=command,
        package_dir=package_dir,
        log_dir=package_dir / "test_logs",
        cwd=cwd,
        env=None,
    )
    if command_record["classification"] != "passed":
        raise SelectorValidationError(
            selector=selector,
            command=command_record,
            reason="validator_failed",
            message=f"validator failed for selector={selector}",
        )
    try:
        generated = discover_single_artifact_directory(temp_root)
        destination = package_dir / "selectors" / selector_component / "30d"
        placed = place_artifact_directory(generated, destination)
        extracted = extract_selector_artifact(selector=selector, artifact_dir=placed)
    except Exception as exc:
        raise SelectorValidationError(
            selector=selector,
            command=command_record,
            reason="artifact_processing_failed",
            message=f"artifact processing failed for selector={selector}: {exc}",
        ) from exc
    extracted["command"] = command_record
    return extracted


def classify_pytest_junit(junit_xml: Path) -> dict[str, int | str]:
    root = ET.fromstring(junit_xml.read_text(encoding="utf-8"))
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
    failed = failures + errors
    if tests == 0:
        failed = 1
    passed = max(tests - failed - skipped, 0)
    if tests == 0:
        classification = "failed"
    elif failed:
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


def place_artifact_directory(source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return destination


def read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


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
            "summary_path": _require_file(
                config_path,
                str(baseline.get("summary_path", "")),
                field="baseline.summary_path",
            ),
            "metadata_path": _require_file(
                config_path,
                str(baseline.get("metadata_path", "")),
                field="baseline.metadata_path",
            ),
        },
        "candidate": {
            "summary_path": _require_file(
                config_path,
                str(candidate.get("summary_path", "")),
                field="candidate.summary_path",
            ),
            "metadata_path": _require_file(
                config_path,
                str(candidate.get("metadata_path", "")),
                field="candidate.metadata_path",
            ),
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
                "summary_path": _require_file(
                    config_path,
                    str(baseline_90d.get("summary_path", "")),
                    field="ninety_day.baseline.summary_path",
                ),
                "metadata_path": _require_file(
                    config_path,
                    str(baseline_90d.get("metadata_path", "")),
                    field="ninety_day.baseline.metadata_path",
                ),
            },
            "candidate": {
                "summary_path": _require_file(
                    config_path,
                    str(candidate_90d.get("summary_path", "")),
                    field="ninety_day.candidate.summary_path",
                ),
                "metadata_path": _require_file(
                    config_path,
                    str(candidate_90d.get("metadata_path", "")),
                    field="ninety_day.candidate.metadata_path",
                ),
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


def count_value(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"invalid count required field {field}: {value!r}")
    return value


def finite_number_value(value: Any, *, field: str) -> int | float:
    numeric = numeric_value(value, field=field)
    if not math.isfinite(float(numeric)):
        raise ValueError(f"invalid {field}: {value!r}")
    return numeric


def precision_value(value: Any, *, field: str) -> int | float:
    numeric = finite_number_value(value, field=field)
    if numeric < 0 or numeric > 1:
        raise ValueError(f"invalid {field}: {value!r}")
    return numeric


def non_negative_metric_value(value: Any, *, field: str) -> int | float:
    numeric = finite_number_value(value, field=field)
    if numeric < 0:
        raise ValueError(f"invalid {field}: {value!r}")
    return numeric


def validate_selector_counts(
    *,
    signal_count: int,
    primary_label_complete_count: int,
    incomplete_label_count: int,
) -> None:
    if (
        primary_label_complete_count > signal_count
        or incomplete_label_count > signal_count
        or primary_label_complete_count + incomplete_label_count > signal_count
    ):
        raise ValueError(
            "inconsistent selector counts: "
            f"signal_count={signal_count}, "
            f"primary_label_complete_count={primary_label_complete_count}, "
            f"incomplete_label_count={incomplete_label_count}"
        )


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
    field_conflicts = [
        field
        for field in (*METADATA_REQUIRED_FIELDS, *SUMMARY_REQUIRED_FIELDS)
        if field in metadata and field in summary and metadata[field] != summary[field]
    ]
    extracted: dict[str, Any] = {
        "selector": selector,
        "artifact_dir": str(artifact_dir),
        "artifact_status": "complete",
        "field_conflicts": field_conflicts,
    }
    for field in METADATA_REQUIRED_FIELDS:
        if field not in metadata:
            raise ValueError(f"missing required selector field {field} in metadata.json for {selector}")
        extracted[field] = metadata[field]
    for field in SUMMARY_REQUIRED_FIELDS:
        if field not in summary:
            raise ValueError(f"missing required selector field {field} in summary.json for {selector}")
        if field in COUNT_REQUIRED_FIELDS:
            extracted[field] = count_value(summary[field], field=field)
        elif field == "precision_before_dd8":
            extracted[field] = precision_value(summary[field], field=field)
        elif field == "avg_abs_mae_24h_pct":
            extracted[field] = non_negative_metric_value(summary[field], field=field)
        else:
            extracted[field] = numeric_value(summary[field], field=field)
    signal_count = extracted["signal_count"]
    primary_count = extracted["primary_label_complete_count"]
    incomplete_count = extracted["incomplete_label_count"]
    validate_selector_counts(
        signal_count=signal_count,
        primary_label_complete_count=primary_count,
        incomplete_label_count=incomplete_count,
    )
    extracted["sample_status"] = sample_status(primary_count)
    extracted["selector_evidence_status"] = selector_evidence_status(
        artifact_status=str(extracted["artifact_status"]),
        coverage_status=str(extracted["coverage_status"]),
        sample_status_value=str(extracted["sample_status"]),
    )
    return extracted


def _is_relevant_dirty_path(path: str) -> bool:
    return path.startswith(RELEVANT_DIRTY_PREFIXES)


def relevant_dirty_paths(paths: list[str]) -> list[str]:
    relevant: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if path not in seen:
            relevant.append(path)
            seen.add(path)

    for path in paths:
        if _is_relevant_dirty_path(path):
            add(path)
    for source, destination in getattr(paths, "rename_pairs", ()):
        if _is_relevant_dirty_path(source) or _is_relevant_dirty_path(destination):
            add(source)
            add(destination)
    return relevant


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
            f"manual --end-at is later than safe_end_at: requested={requested.isoformat()} "
            f"safe={safe_end_at.isoformat()}"
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


def git_output(argv: list[str], *, cwd: Path, allow_failure: bool = False) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        if allow_failure:
            return ""
        detail = (completed.stderr or completed.stdout or "").strip()
        message = f"git command failed ({' '.join(argv)})"
        if detail:
            message = f"{message}: {detail}"
        raise RuntimeError(message)
    return completed.stdout.rstrip("\n")


def current_git_sha(*, cwd: Path) -> str:
    return git_output(["git", "rev-parse", "HEAD"], cwd=cwd, allow_failure=True).strip() or "unknown"


def dirty_paths(*, cwd: Path) -> DirtyPathList:
    output = git_output(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=cwd)
    entries = [entry for entry in output.split("\0") if entry]
    paths: list[str] = []
    rename_pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        if len(entry) < 4:
            index += 1
            continue
        status = entry[:2]
        path = entry[3:]
        if "R" in status or "C" in status:
            if index + 1 >= len(entries):
                paths.append(path)
                index += 1
                continue
            source = entries[index + 1]
            paths.extend([source, path])
            rename_pairs.append((source, path))
            index += 2
            continue
        paths.append(path)
        index += 1
    return DirtyPathList(paths, rename_pairs=rename_pairs)


def _git_diff_output(argv: list[str], *, cwd: Path) -> str | None:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout


def _untracked_paths(*, cwd: Path, paths: list[str]) -> list[str] | None:
    completed = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", *paths],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return sorted(path for path in completed.stdout.split("\0") if path)


def _archive_untracked_path(*, cwd: Path, path: str) -> str | None:
    file_path = cwd / path
    header = f"# Untracked file: {path}\n"
    if not file_path.is_file():
        return None
    try:
        raw_content = file_path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw_content:
        return None
    try:
        content = raw_content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if content == "":
        return (
            f"{header}"
            f"diff --git a/{path} b/{path}\n"
            "new file mode 100644\n"
            "index 0000000..e69de29\n"
        )
    return header + "".join(
        difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{path}",
        )
    )


def archive_dirty_diff(*, cwd: Path, package_dir: Path, paths: list[str]) -> str | None:
    if not paths:
        return None
    tracked_diff = _git_diff_output(["git", "diff", "HEAD", "--binary", "--", *paths], cwd=cwd)
    untracked = _untracked_paths(cwd=cwd, paths=paths)
    if tracked_diff is None or untracked is None:
        return None
    chunks = [tracked_diff] if tracked_diff.strip() else []
    for path in untracked:
        untracked_archive = _archive_untracked_path(cwd=cwd, path=path)
        if untracked_archive is None:
            return None
        chunks.append(untracked_archive)
    archive_text = ""
    for chunk in chunks:
        if not chunk.strip():
            continue
        if archive_text and not archive_text.endswith("\n"):
            archive_text += "\n"
        if archive_text and not archive_text.endswith("\n\n"):
            archive_text += "\n"
        archive_text += chunk
        if not archive_text.endswith("\n"):
            archive_text += "\n"
    if not archive_text:
        return None
    package_dir.mkdir(parents=True, exist_ok=True)
    diff_path = package_dir / "dirty_diff.patch"
    diff_path.write_text(archive_text, encoding="utf-8")
    return str(diff_path)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    parse_selector_list(args.selectors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
