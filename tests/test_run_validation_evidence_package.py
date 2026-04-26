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
