import importlib.util
import subprocess
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


def test_collect_environment_contains_required_keys(monkeypatch):
    monkeypatch.setattr(_MODULE.platform, "platform", lambda: "Linux-test")

    environment = _MODULE.collect_environment(cwd=Path("/repo"))

    assert environment["platform"] == "Linux-test"
    assert environment["working_directory"] == "/repo"
    assert "python_version" in environment
