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
ALLOWED_COMPARISON_STATUSES = {
    "evidence_backed",
    "not_supported",
    "experimental_only",
    "insufficient",
}
EVIDENCE_BACKED_REQUIRED_COMPARISON_FIELDS = (
    "comparison_window_start",
    "comparison_window_end",
    "baseline_primary_label_complete_count",
    "candidate_primary_label_complete_count",
    "baseline_precision_before_dd8",
    "candidate_precision_before_dd8",
    "baseline_avg_abs_mae_24h_pct",
    "candidate_avg_abs_mae_24h_pct",
    "baseline_avg_mae_24h_pct",
    "candidate_avg_mae_24h_pct",
    "mae_path_risk_policy",
    "path_risk_pass",
    "requires_90d",
)
EVIDENCE_BACKED_REQUIRED_90D_COMPARISON_FIELDS = (
    "baseline_90d_primary_label_complete_count",
    "candidate_90d_primary_label_complete_count",
    "baseline_90d_precision_before_dd8",
    "candidate_90d_precision_before_dd8",
    "baseline_90d_avg_abs_mae_24h_pct",
    "candidate_90d_avg_abs_mae_24h_pct",
    "baseline_90d_avg_mae_24h_pct",
    "candidate_90d_avg_mae_24h_pct",
    "metrics_pass_90d",
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
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=merged_env,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        finished_at = _iso_now()
        stdout_log.write_text("", encoding="utf-8")
        stderr_log.write_text(str(exc), encoding="utf-8")
        return {
            "name": name,
            "argv": list(argv),
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": -1,
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "junit_xml": str(junit_xml) if junit_xml is not None else None,
            "classification": "failed",
            "reason": "command_launch_failed",
            "error": str(exc),
        }
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
    if (baseline_90d_config is None) != (candidate_90d_config is None):
        raise ValueError("both 90d comparison configs are required when either is provided")
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


def _require_non_empty_string(config_path: Path, value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"comparison config {config_path} field {field} must be a non-empty string")
    return value


def _require_file(config_path: Path, value: Any, *, field: str) -> str:
    value = _require_non_empty_string(config_path, value, field=field)
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
    selector = validate_selector_name(_require_non_empty_string(config_path, config.get("selector"), field="selector"))
    comparison_type = _require_non_empty_string(config_path, config.get("comparison_type"), field="comparison_type")
    change_id = _require_non_empty_string(config_path, config.get("change_id"), field="change_id")
    normalized = {
        "config_path": str(config_path),
        "selector": selector,
        "comparison_type": comparison_type,
        "change_id": change_id,
        "change_classification": change_classification,
        "baseline": {
            "summary_path": _require_file(
                config_path,
                baseline.get("summary_path"),
                field="baseline.summary_path",
            ),
            "metadata_path": _require_file(
                config_path,
                baseline.get("metadata_path"),
                field="baseline.metadata_path",
            ),
        },
        "candidate": {
            "summary_path": _require_file(
                config_path,
                candidate.get("summary_path"),
                field="candidate.summary_path",
            ),
            "metadata_path": _require_file(
                config_path,
                candidate.get("metadata_path"),
                field="candidate.metadata_path",
            ),
        },
        "ninety_day": None,
    }
    ninety_day = config.get("ninety_day")
    if ninety_day is not None and not isinstance(ninety_day, dict):
        raise ValueError(f"comparison config {config_path} field ninety_day must be an object")
    if isinstance(ninety_day, dict) and "required" in ninety_day and not isinstance(ninety_day["required"], bool):
        raise ValueError(f"comparison config {config_path} field ninety_day.required must be a boolean")
    if isinstance(ninety_day, dict) and ninety_day.get("required", False):
        baseline_90d = ninety_day.get("baseline")
        candidate_90d = ninety_day.get("candidate")
        if not isinstance(baseline_90d, dict) or not isinstance(candidate_90d, dict):
            raise ValueError(f"comparison config {config_path} requires ninety_day baseline and candidate objects")
        normalized["ninety_day"] = {
            "required": True,
            "baseline": {
                "summary_path": _require_file(
                    config_path,
                    baseline_90d.get("summary_path"),
                    field="ninety_day.baseline.summary_path",
                ),
                "metadata_path": _require_file(
                    config_path,
                    baseline_90d.get("metadata_path"),
                    field="ninety_day.baseline.metadata_path",
                ),
            },
            "candidate": {
                "summary_path": _require_file(
                    config_path,
                    candidate_90d.get("summary_path"),
                    field="ninety_day.candidate.summary_path",
                ),
                "metadata_path": _require_file(
                    config_path,
                    candidate_90d.get("metadata_path"),
                    field="ninety_day.candidate.metadata_path",
                ),
            },
        }
    return normalized


def load_traceable_comparison_configs(root: Path | None) -> list[dict[str, Any]]:
    if root is None or not root.exists():
        return []
    return [normalize_traceable_comparison_config(path) for path in sorted(root.glob("*.json"))]


def comparison_not_run(reason: str) -> dict[str, str]:
    return {
        "comparison_status": "comparison_not_run",
        "reason": reason,
        "threshold_decision_status": "no_decision",
    }


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


def discover_comparison_result(
    output_root: Path,
    *,
    stdout_log: Path | None,
    comparison_dir: Path,
) -> tuple[Path, Path | None]:
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


def _require_comparison_int(result: dict[str, Any], field: str) -> int:
    value = result.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"evidence_backed comparison result field {field} must be a non-negative integer")
    return value


def _require_comparison_number(result: dict[str, Any], field: str, *, non_negative: bool = False) -> int | float:
    value = numeric_value(result.get(field), field=field)
    if non_negative and value < 0:
        raise ValueError(f"evidence_backed comparison result field {field} must be non-negative")
    return value


def _require_comparison_rate(result: dict[str, Any], field: str) -> int | float:
    value = _require_comparison_number(result, field)
    if value < 0 or value > 1:
        raise ValueError(f"evidence_backed comparison result field {field} must be in [0, 1]")
    return value


def _require_comparison_bool(result: dict[str, Any], field: str, *, must_be_true: bool = False) -> bool:
    value = result.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"evidence_backed comparison result field {field} must be a boolean")
    if must_be_true and value is not True:
        raise ValueError(f"evidence_backed comparison result field {field} must be true")
    return value


def _require_comparison_string(result: dict[str, Any], field: str) -> str:
    value = result.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"evidence_backed comparison result field {field} must be a non-empty string")
    return value


def _require_comparison_timestamp(result: dict[str, Any], field: str) -> datetime:
    value = _require_comparison_string(result, field)
    try:
        return parse_iso_datetime(value)
    except ValueError as exc:
        raise ValueError(f"evidence_backed comparison result field {field} must be an ISO datetime") from exc


def _validate_evidence_backed_comparison_fields(result: dict[str, Any]) -> None:
    missing = [field for field in EVIDENCE_BACKED_REQUIRED_COMPARISON_FIELDS if field not in result]
    if missing:
        raise ValueError(f"evidence_backed comparison result missing required fields: {missing}")

    _require_comparison_timestamp(result, "comparison_window_start")
    _require_comparison_timestamp(result, "comparison_window_end")
    for field in (
        "baseline_primary_label_complete_count",
        "candidate_primary_label_complete_count",
    ):
        _require_comparison_int(result, field)
    for field in (
        "baseline_precision_before_dd8",
        "candidate_precision_before_dd8",
    ):
        _require_comparison_rate(result, field)
    for field in (
        "baseline_avg_abs_mae_24h_pct",
        "candidate_avg_abs_mae_24h_pct",
    ):
        _require_comparison_number(result, field, non_negative=True)
    for field in (
        "baseline_avg_mae_24h_pct",
        "candidate_avg_mae_24h_pct",
    ):
        _require_comparison_number(result, field)
    _require_comparison_string(result, "mae_path_risk_policy")
    _require_comparison_bool(result, "path_risk_pass", must_be_true=True)
    requires_90d = _require_comparison_bool(result, "requires_90d")

    if not requires_90d:
        return

    missing_90d = [field for field in EVIDENCE_BACKED_REQUIRED_90D_COMPARISON_FIELDS if field not in result]
    if missing_90d:
        raise ValueError(f"evidence_backed 90d comparison result missing required fields: {missing_90d}")
    for field in (
        "baseline_90d_primary_label_complete_count",
        "candidate_90d_primary_label_complete_count",
    ):
        _require_comparison_int(result, field)
    for field in (
        "baseline_90d_precision_before_dd8",
        "candidate_90d_precision_before_dd8",
    ):
        _require_comparison_rate(result, field)
    for field in (
        "baseline_90d_avg_abs_mae_24h_pct",
        "candidate_90d_avg_abs_mae_24h_pct",
    ):
        _require_comparison_number(result, field, non_negative=True)
    for field in (
        "baseline_90d_avg_mae_24h_pct",
        "candidate_90d_avg_mae_24h_pct",
    ):
        _require_comparison_number(result, field)
    _require_comparison_bool(result, "metrics_pass_90d", must_be_true=True)


def validate_comparison_result(result: dict[str, Any], *, comparison_path: Path) -> dict[str, Any]:
    status = result.get("status")
    reason = result.get("reason")
    if not isinstance(status, str) or status not in ALLOWED_COMPARISON_STATUSES:
        raise ValueError("comparison result status is missing or invalid")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("comparison result reason is missing or invalid")
    if status == "evidence_backed":
        if not comparison_path.is_file():
            raise ValueError("comparison result path is missing")
        _validate_evidence_backed_comparison_fields(result)
    return result


def malformed_comparison_result(
    *,
    error: Exception,
    command: dict[str, Any],
    config: dict[str, Any],
    selector: str,
) -> dict[str, Any]:
    return {
        "comparison_status": "insufficient",
        "reason": "malformed_comparison_result",
        "threshold_decision_status": "no_decision",
        "error": str(error),
        "command": command,
        "change_id": config.get("change_id"),
        "selector": selector,
    }


def run_comparison_config(*, config: dict[str, Any], package_dir: Path, cwd: Path) -> dict[str, Any]:
    selector = validate_selector_name(str(config["selector"]))
    comparison_dir = package_dir / "comparisons" / selector
    generated_config_dir = comparison_dir / "configs"
    output_root = comparison_dir / "output"
    baseline_config = write_validator_comparison_side_config(
        generated_config_dir / "baseline.json",
        config["baseline"],
    )
    candidate_config = write_validator_comparison_side_config(
        generated_config_dir / "candidate.json",
        config["candidate"],
    )
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
            "selector": selector,
        }
    try:
        comparison_path, readme_path = discover_comparison_result(
            output_root,
            stdout_log=Path(str(record.get("stdout_log"))) if record.get("stdout_log") else None,
            comparison_dir=comparison_dir,
        )
        result = validate_comparison_result(read_json_object(comparison_path), comparison_path=comparison_path)
    except Exception as exc:
        return malformed_comparison_result(command=record, config=config, selector=selector, error=exc)
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
    selector_missing = not selector_artifacts
    selector_failed = any(
        item.get("selector_evidence_status") == "gate_failed" for item in selector_artifacts.values()
    )
    unsafe_end = end_at_safety_status != "safe"
    dirty_present = bool(relevant_dirty_paths)
    dirty_blocks_gate = dirty_present
    dirty_diff_missing = dirty_present and dirty_diff_path is None
    gate_failed = (
        command_failed
        or smoke_failed
        or selector_missing
        or selector_failed
        or tests_skipped_by_user
        or unsafe_end
        or dirty_blocks_gate
    )
    diagnostic = (
        comparison.get("comparison_status") == "comparison_not_run"
        or any(item.get("selector_evidence_status") == "diagnostic_only" for item in selector_artifacts.values())
        or dirty_present
        or tests_skipped_by_user
        or unsafe_end
    )
    if gate_failed and (command_failed or smoke_failed or selector_missing or selector_failed or dirty_diff_missing):
        overall_status = "failed"
    elif diagnostic:
        overall_status = "passed_with_diagnostics"
    else:
        overall_status = "passed"
    comparison_status = comparison.get("comparison_status")
    threshold_decision_status = "no_decision"
    if not gate_failed and not diagnostic:
        if comparison.get("threshold_decision_status") == "no_decision":
            threshold_decision_status = "no_decision"
        elif comparison_status == "evidence_backed":
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
        return (
            "No threshold change is supported by this package because comparison was not run: "
            f"{comparison.get('reason')}."
        )
    if comparison_status == "evidence_backed" and manifest.get("threshold_decision_status") == "supported":
        return (
            f"This package supports retaining candidate threshold change {comparison.get('change_id')} "
            f"because comparison artifact {comparison.get('comparison_path')} reports evidence_backed "
            "with trusted 30d evidence and trusted required 90d review."
        )
    return (
        "No threshold change is supported by this package because comparison result is "
        f"{comparison_status}: {comparison.get('reason')}."
    )


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
        caveats.append(
            "- Relevant dirty paths were present; production-ready threshold claims require human review of the archived diff."
        )
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


def resolve_path_against_cwd(path: str | Path, *, cwd: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return cwd / candidate


def run_evidence_package(argv: list[str] | None = None, *, cwd: Path | None = None) -> int:
    cwd = cwd or Path.cwd()
    args = parse_args(argv)
    selectors = parse_selector_list(args.selectors)
    git_sha = current_git_sha(cwd=cwd)
    now = utc_now()
    identity = build_run_identity(now=now, git_sha=git_sha)
    package_dir = resolve_package_dir(
        output_root=resolve_path_against_cwd(args.output_root, cwd=cwd),
        package_date=identity["package_date"],
        run_id=identity["run_id"],
        overwrite=bool(args.overwrite),
    )
    if package_dir.exists() and args.overwrite:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "package_date": identity["package_date"],
        "run_id": identity["run_id"],
        "package_dir": str(package_dir),
        "run_started_at": now.isoformat(),
        "run_finished_at": None,
        "git_sha": git_sha,
        "git_sha7": identity["git_sha7"],
        "worktree_dirty": False,
        "dirty_paths": [],
        "relevant_dirty_paths": [],
        "dirty_diff_path": None,
        "dirty_worktree_policy": "unknown",
        "exchange_universe": [args.exchange],
        "window_days": int(args.window_days),
        "selectors": list(selectors),
        "commands": [],
        "db_smoke": {},
        "selector_artifacts": {},
        "comparison": {},
        "environment": {},
    }
    try:
        dirty = dirty_paths(cwd=cwd)
        relevant_dirty = relevant_dirty_paths(dirty)
        dirty_policy = dirty_worktree_policy(relevant_dirty)
        manifest.update(
            {
                "worktree_dirty": bool(dirty),
                "dirty_paths": dirty,
                "relevant_dirty_paths": relevant_dirty,
                "dirty_worktree_policy": dirty_policy,
            }
        )
        dirty_diff = archive_dirty_diff(cwd=cwd, package_dir=package_dir, paths=relevant_dirty)
        manifest["dirty_diff_path"] = dirty_diff
        manifest["environment"] = collect_environment(cwd=cwd)
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
        manifest["db_smoke"] = {"command": db_command_record}
        try:
            smoke = classify_pytest_junit(junit_xml)
        except Exception as exc:
            manifest["db_smoke"].update(
                {
                    "passed_count": 0,
                    "skipped_count": 0,
                    "failed_count": 1,
                    "classification": "failed",
                    "reason": "junit_parse_failed",
                    "error": str(exc),
                }
            )
        else:
            smoke["command"] = db_command_record
            manifest["db_smoke"] = smoke
        for selector in selectors:
            try:
                manifest["selector_artifacts"][selector] = run_selector_validation(
                    selector=selector,
                    exchange=args.exchange,
                    window_days=int(args.window_days),
                    end_at=str(manifest["resolved_end_at"]),
                    package_dir=package_dir,
                    cwd=cwd,
                )
            except SelectorValidationError as exc:
                manifest["selector_artifacts"][selector] = {
                    "selector": exc.selector,
                    "artifact_status": "failed",
                    "selector_evidence_status": "gate_failed",
                    "reason": exc.reason,
                    "error": str(exc),
                    "command": exc.command,
                }
        comparison_root = (
            resolve_path_against_cwd(args.comparison_root, cwd=cwd) if args.comparison_root else None
        )
        configs = load_traceable_comparison_configs(comparison_root)
        if not configs:
            manifest["comparison"] = comparison_not_run("missing_traceable_baseline_candidate_config")
        elif len(configs) == 1:
            manifest["comparison"] = run_comparison_config(config=configs[0], package_dir=package_dir, cwd=cwd)
        else:
            manifest["comparison"] = comparison_not_run("multiple_traceable_comparison_configs_not_supported_in_p0")
            manifest["comparison"]["config_count"] = len(configs)
        manifest.update(
            calculate_package_status(
                commands=list(manifest["commands"]),
                db_smoke=dict(manifest["db_smoke"]),
                selector_artifacts=dict(manifest["selector_artifacts"]),
                comparison=dict(manifest["comparison"]),
                tests_skipped_by_user=bool(args.skip_tests),
                end_at_safety_status=str(manifest["end_at_safety_status"]),
                relevant_dirty_paths=list(manifest["relevant_dirty_paths"]),
                dirty_diff_path=manifest["dirty_diff_path"],
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


if __name__ == "__main__":
    raise SystemExit(main())
