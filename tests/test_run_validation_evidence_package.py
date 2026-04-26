import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_validation_evidence_package.py"
_SPEC = importlib.util.spec_from_file_location("run_validation_evidence_package", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _run_git(cwd: Path, *args: str) -> str:
    completed = _MODULE.subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _init_git_repo(path: Path) -> None:
    path.mkdir()
    _run_git(path, "init")
    _run_git(path, "config", "user.email", "test@example.com")
    _run_git(path, "config", "user.name", "Test User")
    _run_git(path, "config", "commit.gpgsign", "false")


def _write_selector_artifact(
    artifact: Path,
    *,
    metadata_overrides: dict[str, object] | None = None,
    summary_overrides: dict[str, object] | None = None,
) -> None:
    artifact.mkdir()
    metadata: dict[str, object] = {
        "coverage_status": "trusted",
        "rule_version": "rule-1",
        "feature_preparation_version": "feature-1",
        "market_1m_timestamp_semantics": "minute_open_utc",
        "forward_scan_start_policy": "signal_available_at_inclusive",
    }
    summary: dict[str, object] = {
        "signal_count": 12,
        "primary_label_complete_count": 10,
        "incomplete_label_count": 2,
        "precision_before_dd8": 0.5,
        "avg_abs_mae_24h_pct": 6.0,
    }
    metadata.update(metadata_overrides or {})
    summary.update(summary_overrides or {})
    (artifact / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (artifact / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (artifact / "signals.csv").write_text("symbol\n", encoding="utf-8")
    (artifact / "README.md").write_text("# run\n", encoding="utf-8")


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


def test_run_selector_validation_raises_when_validator_fails(tmp_path, monkeypatch):
    def fake_run_command(**kwargs):
        return {"name": kwargs["name"], "exit_code": 1, "classification": "failed"}

    monkeypatch.setattr(_MODULE, "run_command", fake_run_command)

    with pytest.raises(RuntimeError, match="validator failed for selector=ignition_A"):
        _MODULE.run_selector_validation(
            selector="ignition_A",
            exchange="binance",
            window_days=30,
            end_at="2026-04-24T10:00:00+00:00",
            package_dir=tmp_path,
            cwd=Path.cwd(),
        )


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


def test_classify_pytest_junit_sums_testsuites_root(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        (
            '<testsuites>'
            '<testsuite tests="2" failures="1" errors="0" skipped="0" />'
            '<testsuite tests="3" failures="0" errors="1" skipped="1" />'
            "</testsuites>"
        ),
        encoding="utf-8",
    )

    result = _MODULE.classify_pytest_junit(junit)

    assert result == {
        "passed_count": 2,
        "skipped_count": 1,
        "failed_count": 2,
        "classification": "failed",
    }


def test_classify_pytest_junit_rejects_zero_test_junit(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="0" failures="0" errors="0" skipped="0"></testsuite>',
        encoding="utf-8",
    )

    result = _MODULE.classify_pytest_junit(junit)

    assert result == {
        "passed_count": 0,
        "skipped_count": 0,
        "failed_count": 1,
        "classification": "failed",
    }


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


def test_discover_single_artifact_directory_fails_for_missing_required_file(tmp_path):
    artifact = tmp_path / "generated"
    artifact.mkdir()
    (artifact / "summary.json").write_text("{}", encoding="utf-8")
    (artifact / "metadata.json").write_text("{}", encoding="utf-8")
    (artifact / "signals.csv").write_text("symbol\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="artifact directory missing required files"):
        _MODULE.discover_single_artifact_directory(tmp_path)


def test_place_artifact_directory_accepts_positional_args_and_replaces_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "summary.json").write_text('{"source": true}\n', encoding="utf-8")
    nested = source / "nested"
    nested.mkdir()
    (nested / "artifact.txt").write_text("copied\n", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.mkdir()
    (destination / "stale.txt").write_text("old\n", encoding="utf-8")

    result = _MODULE.place_artifact_directory(source, destination)

    assert result == destination
    assert not (destination / "stale.txt").exists()
    assert (destination / "summary.json").read_text(encoding="utf-8") == '{"source": true}\n'
    assert (destination / "nested" / "artifact.txt").read_text(encoding="utf-8") == "copied\n"


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


@pytest.mark.parametrize(
    ("summary_overrides", "message"),
    [
        ({"signal_count": 12.5}, "invalid count required field"),
        ({"primary_label_complete_count": 10.0}, "invalid count required field"),
        ({"incomplete_label_count": -1}, "invalid count required field"),
        ({"precision_before_dd8": 1.5}, "invalid precision_before_dd8"),
        ({"avg_abs_mae_24h_pct": -0.1}, "invalid avg_abs_mae_24h_pct"),
        ({"primary_label_complete_count": 13}, "inconsistent selector counts"),
        ({"incomplete_label_count": 13}, "inconsistent selector counts"),
        ({"primary_label_complete_count": 8, "incomplete_label_count": 5}, "inconsistent selector counts"),
    ],
)
def test_extract_selector_artifact_rejects_invalid_numeric_domains(tmp_path, summary_overrides, message):
    artifact = tmp_path / "selector"
    _write_selector_artifact(artifact, summary_overrides=summary_overrides)

    with pytest.raises(ValueError, match=message):
        _MODULE.extract_selector_artifact(selector="ignition", artifact_dir=artifact)


def test_extract_selector_artifact_records_metadata_canonical_conflict(tmp_path):
    artifact = tmp_path / "selector"
    _write_selector_artifact(artifact, summary_overrides={"coverage_status": "material_gaps"})

    extracted = _MODULE.extract_selector_artifact(selector="ignition", artifact_dir=artifact)

    assert extracted["coverage_status"] == "trusted"
    assert "coverage_status" in extracted["field_conflicts"]


def test_relevant_dirty_paths_are_limited_to_validation_relevant_areas():
    dirty = [
        "scripts/run_validation_evidence_package.py",
        "src/altcoin_trend/signals/v2.py",
        "tests/test_signal_v2.py",
        "docs/superpowers/specs/2026-04-25-validation-evidence-package-design.md",
        "docs/superpowers/plans/2026-04-25-validation-evidence-package.md",
        "README.md",
    ]

    assert _MODULE.relevant_dirty_paths(dirty) == [
        "scripts/run_validation_evidence_package.py",
        "src/altcoin_trend/signals/v2.py",
        "tests/test_signal_v2.py",
        "docs/superpowers/specs/2026-04-25-validation-evidence-package-design.md",
        "docs/superpowers/plans/2026-04-25-validation-evidence-package.md",
    ]


def test_dirty_worktree_policy_disables_threshold_claims_for_relevant_paths():
    assert _MODULE.dirty_worktree_policy([]) == "clean"
    assert _MODULE.dirty_worktree_policy(["scripts/run_validation_evidence_package.py"]) == "threshold_claims_disabled"


def test_git_output_raises_by_default_and_allows_explicit_failure(monkeypatch):
    class FakeCompleted:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository\n"

    monkeypatch.setattr(_MODULE.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    with pytest.raises(RuntimeError, match="git command failed"):
        _MODULE.git_output(["git", "status", "--short"], cwd=Path.cwd())

    assert _MODULE.git_output(["git", "status", "--short"], cwd=Path.cwd(), allow_failure=True) == ""


def test_dirty_paths_raises_when_git_status_fails(monkeypatch):
    class FakeCompleted:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository\n"

    monkeypatch.setattr(_MODULE.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    with pytest.raises(RuntimeError, match="git command failed"):
        _MODULE.dirty_paths(cwd=Path.cwd())


def test_dirty_paths_parses_normal_rename_and_untracked_paths(monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = (
            " M scripts/run_validation_evidence_package.py\0"
            "R  docs/superpowers/plans/new-plan.md\0docs/old-plan.md\0"
            "?? tests/test_new_validation.py\0"
        )
        stderr = ""

    monkeypatch.setattr(_MODULE.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    assert _MODULE.dirty_paths(cwd=Path.cwd()) == [
        "scripts/run_validation_evidence_package.py",
        "docs/old-plan.md",
        "docs/superpowers/plans/new-plan.md",
        "tests/test_new_validation.py",
    ]


def test_dirty_paths_preserves_relevant_source_path_for_rename(monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = "R  README.md\0scripts/foo.py\0"
        stderr = ""

    monkeypatch.setattr(_MODULE.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    paths = _MODULE.dirty_paths(cwd=Path.cwd())

    assert paths == ["scripts/foo.py", "README.md"]
    assert _MODULE.relevant_dirty_paths(paths) == ["scripts/foo.py", "README.md"]


def test_dirty_paths_preserves_relevant_source_path_with_spaces_in_real_repo(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    script_path = repo / "scripts" / "foo bar.py"
    script_path.parent.mkdir()
    script_path.write_text("print('base')\n", encoding="utf-8")
    _run_git(repo, "add", "scripts/foo bar.py")
    _run_git(repo, "commit", "-m", "initial")

    _run_git(repo, "mv", "scripts/foo bar.py", "README.md")

    paths = _MODULE.dirty_paths(cwd=repo)

    assert paths == ["scripts/foo bar.py", "README.md"]
    assert _MODULE.relevant_dirty_paths(paths) == ["scripts/foo bar.py", "README.md"]


def test_dirty_paths_discovers_nested_untracked_files_as_relevant(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "initial")
    plan_path = repo / "docs" / "superpowers" / "plans" / "new-plan.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("plan\n", encoding="utf-8")

    paths = _MODULE.dirty_paths(cwd=repo)

    assert "docs/superpowers/plans/new-plan.md" in paths
    assert _MODULE.relevant_dirty_paths(paths) == ["docs/superpowers/plans/new-plan.md"]


def test_archive_dirty_diff_replays_relevant_rename_destination(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    script_path = repo / "scripts" / "foo bar.py"
    script_path.parent.mkdir()
    script_path.write_text("print('base')\n", encoding="utf-8")
    _run_git(repo, "add", "scripts/foo bar.py")
    _run_git(repo, "commit", "-m", "initial")

    _run_git(repo, "mv", "scripts/foo bar.py", "README.md")
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    relevant_paths = _MODULE.relevant_dirty_paths(_MODULE.dirty_paths(cwd=repo))

    result = _MODULE.archive_dirty_diff(cwd=repo, package_dir=package_dir, paths=relevant_paths)
    assert result is not None
    replay = tmp_path / "replay-rename"
    _run_git(tmp_path, "clone", str(repo), str(replay))
    _run_git(replay, "apply", str(result))

    assert (replay / "README.md").read_text(encoding="utf-8") == "print('base')\n"
    assert not (replay / "scripts" / "foo bar.py").exists()


def test_archive_dirty_diff_replays_empty_untracked_file(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-m", "initial")
    empty_path = repo / "docs" / "superpowers" / "plans" / "empty-plan.md"
    empty_path.parent.mkdir(parents=True)
    empty_path.write_text("", encoding="utf-8")
    package_dir = tmp_path / "package"
    package_dir.mkdir()
    relevant_paths = _MODULE.relevant_dirty_paths(_MODULE.dirty_paths(cwd=repo))

    result = _MODULE.archive_dirty_diff(cwd=repo, package_dir=package_dir, paths=relevant_paths)
    assert result is not None
    replay = tmp_path / "replay-empty"
    _run_git(tmp_path, "clone", str(repo), str(replay))
    _run_git(replay, "apply", str(result))

    replayed_empty_path = replay / "docs" / "superpowers" / "plans" / "empty-plan.md"
    assert replayed_empty_path.exists()
    assert replayed_empty_path.read_text(encoding="utf-8") == ""


def test_archive_dirty_diff_captures_unstaged_staged_and_untracked_text(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "staged.txt").write_text("base staged\n", encoding="utf-8")
    (repo / "unstaged.txt").write_text("base unstaged\n", encoding="utf-8")
    _run_git(repo, "add", "staged.txt", "unstaged.txt")
    _run_git(repo, "commit", "-m", "initial")

    (repo / "staged.txt").write_text("staged change\n", encoding="utf-8")
    _run_git(repo, "add", "staged.txt")
    (repo / "unstaged.txt").write_text("unstaged change\n", encoding="utf-8")
    untracked_path = repo / "docs" / "superpowers" / "plans" / "2026-04-25-validation-evidence-package.md"
    untracked_path.parent.mkdir(parents=True)
    untracked_path.write_text("plan text\n", encoding="utf-8")
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    result = _MODULE.archive_dirty_diff(
        cwd=repo,
        package_dir=package_dir,
        paths=[
            "staged.txt",
            "unstaged.txt",
            "docs/superpowers/plans/2026-04-25-validation-evidence-package.md",
        ],
    )

    assert result is not None
    patch_text = Path(result).read_text(encoding="utf-8")
    assert "+staged change\n" in patch_text
    assert "+unstaged change\n" in patch_text
    assert "# Untracked file: docs/superpowers/plans/2026-04-25-validation-evidence-package.md" in patch_text
    assert "+plan text\n" in patch_text


def test_archive_dirty_diff_captures_staged_binary_patch(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    binary_path = repo / "scripts" / "fixture.bin"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"\x00base-binary\n")
    _run_git(repo, "add", "scripts/fixture.bin")
    _run_git(repo, "commit", "-m", "initial")

    binary_path.write_bytes(b"\x00changed-binary\n")
    _run_git(repo, "add", "scripts/fixture.bin")
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    result = _MODULE.archive_dirty_diff(cwd=repo, package_dir=package_dir, paths=["scripts/fixture.bin"])

    assert result is not None
    patch_text = Path(result).read_text(encoding="utf-8")
    assert "GIT binary patch" in patch_text


def test_archive_dirty_diff_replays_staged_then_unstaged_binary_changes(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    binary_path = repo / "scripts" / "fixture.bin"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"\x00base-binary\n")
    _run_git(repo, "add", "scripts/fixture.bin")
    _run_git(repo, "commit", "-m", "initial")

    binary_path.write_bytes(b"\x00staged-binary\n")
    _run_git(repo, "add", "scripts/fixture.bin")
    final_bytes = b"\x00final-binary\n"
    binary_path.write_bytes(final_bytes)
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    result = _MODULE.archive_dirty_diff(cwd=repo, package_dir=package_dir, paths=["scripts/fixture.bin"])
    assert result is not None
    replay = tmp_path / "replay"
    _run_git(tmp_path, "clone", str(repo), str(replay))
    _run_git(replay, "apply", str(result))

    assert (replay / "scripts" / "fixture.bin").read_bytes() == final_bytes


def test_archive_dirty_diff_returns_none_for_untracked_binary_only(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    binary_path = repo / "scripts" / "untracked.bin"
    binary_path.parent.mkdir()
    binary_path.write_bytes(b"\x00\xffbinary\n")
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    result = _MODULE.archive_dirty_diff(cwd=repo, package_dir=package_dir, paths=["scripts/untracked.bin"])

    assert result is None
    assert not (package_dir / "dirty_diff.patch").exists()


def test_archive_dirty_diff_returns_none_for_empty_archive(tmp_path):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    (repo / "tracked.txt").write_text("clean\n", encoding="utf-8")
    _run_git(repo, "add", "tracked.txt")
    _run_git(repo, "commit", "-m", "initial")
    package_dir = tmp_path / "package"
    package_dir.mkdir()

    result = _MODULE.archive_dirty_diff(cwd=repo, package_dir=package_dir, paths=["tracked.txt"])

    assert result is None
    assert not (package_dir / "dirty_diff.patch").exists()


def test_collect_environment_contains_required_keys(monkeypatch):
    monkeypatch.setattr(_MODULE.platform, "platform", lambda: "Linux-test")

    environment = _MODULE.collect_environment(cwd=Path("/repo"))

    assert environment["platform"] == "Linux-test"
    assert environment["working_directory"] == "/repo"
    assert "python_version" in environment


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


def test_query_latest_market_ts_normalizes_database_timestamp(monkeypatch):
    latest = datetime(2026, 4, 25, 10, 55)

    class FakeResult:
        def scalar(self):
            return latest

    class FakeConnection:
        def execute(self, statement, params):
            assert "SELECT max(ts)" in str(statement)
            assert params == {"exchange": "binance"}
            return FakeResult()

    class FakeEngine:
        def begin(self):
            return self

        def __enter__(self):
            return FakeConnection()

        def __exit__(self, exc_type, exc, tb):
            return False

    settings = object()
    monkeypatch.setattr(_MODULE, "load_settings", lambda: settings)
    monkeypatch.setattr(_MODULE, "build_engine", lambda received: FakeEngine())

    assert _MODULE.query_latest_market_ts(exchange="binance") == latest.replace(tzinfo=timezone.utc)
