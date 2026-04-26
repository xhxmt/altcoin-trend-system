#!/usr/bin/env python3

import argparse
import difflib
import os
import platform
import subprocess
import sys
from collections.abc import Mapping
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


def dirty_paths(*, cwd: Path) -> list[str]:
    output = git_output(["git", "status", "--porcelain=v1", "-z"], cwd=cwd)
    entries = [entry for entry in output.split("\0") if entry]
    paths: list[str] = []
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
            index += 2
            continue
        paths.append(path)
        index += 1
    return paths


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
        return f"{header}# content: empty text file\n"
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
