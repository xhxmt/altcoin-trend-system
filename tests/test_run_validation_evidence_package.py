import importlib.util
from datetime import datetime, timezone
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
    assert _MODULE.relevant_dirty_paths(paths) == ["scripts/foo.py"]


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
    assert _MODULE.relevant_dirty_paths(paths) == ["scripts/foo bar.py"]


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
