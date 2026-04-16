#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_KEEP_RUNS = 30
GROUP_MARKER_EXPRESSIONS = {
    "unit": "not integration and not cli_smoke and not provider_smoke",
    "integration": "integration and not provider_smoke",
    "cli-smoke": "cli_smoke and not provider_smoke",
    "provider-smoke": "provider_smoke",
    "all": "not provider_smoke",
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run btwin test groups with shared reporting.")
    parser.add_argument(
        "group",
        choices=tuple(GROUP_MARKER_EXPRESSIONS),
        help="Test group to run.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(".test-artifacts"),
        help="Directory used to store per-run test artifacts.",
    )
    parser.add_argument(
        "--keep-runs",
        type=int,
        default=None,
        help="How many recent test runs to retain.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Additional argument forwarded to pytest. May be repeated.",
    )
    return parser.parse_args()


def _resolve_keep_runs(cli_value: int | None) -> int:
    if cli_value is not None:
        return cli_value
    env_value = os.environ.get("BTWIN_TEST_KEEP_RUNS")
    if env_value:
        try:
            return max(1, int(env_value))
        except ValueError:
            return DEFAULT_KEEP_RUNS
    return DEFAULT_KEEP_RUNS


def _create_run_directory(artifact_root: Path, group: str) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = artifact_root / f"{timestamp}-{group}"
    suffix = 1
    while run_dir.exists():
        suffix += 1
        run_dir = artifact_root / f"{timestamp}-{group}-{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _update_latest_link(artifact_root: Path, run_dir: Path) -> None:
    latest_path = artifact_root / "latest"
    if latest_path.is_symlink() or latest_path.exists():
        if latest_path.is_dir() and not latest_path.is_symlink():
            shutil.rmtree(latest_path)
        else:
            latest_path.unlink()
    latest_path.symlink_to(run_dir.name)


def _prune_old_runs(artifact_root: Path, keep_runs: int) -> None:
    run_dirs = sorted(
        path
        for path in artifact_root.iterdir()
        if path.name != "latest" and path.is_dir()
    )
    while len(run_dirs) > keep_runs:
        oldest = run_dirs.pop(0)
        shutil.rmtree(oldest)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _provider_preflight() -> tuple[bool, str | None]:
    if shutil.which("codex") is None:
        return False, "codex CLI not found on PATH"
    return True, None


def _build_pytest_command(args: argparse.Namespace, run_dir: Path) -> list[str]:
    report_path = run_dir / "report.html"
    log_path = run_dir / "pytest.log"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-m",
        GROUP_MARKER_EXPRESSIONS[args.group],
        f"--html={report_path}",
        "--self-contained-html",
        f"--log-file={log_path}",
    ]
    command.extend(args.pytest_arg)
    return command


def main() -> int:
    args = _parse_args()
    artifact_root = args.artifact_root.resolve()
    keep_runs = _resolve_keep_runs(args.keep_runs)
    run_dir = _create_run_directory(artifact_root, args.group)

    metadata: dict[str, object] = {
        "group": args.group,
        "keep_runs": keep_runs,
        "provider_smoke_included": args.group == "provider-smoke",
        "provider_surface": "app-server" if args.group == "provider-smoke" else None,
        "provider_continuity": "long-term" if args.group == "provider-smoke" else None,
        "provider_model": "gpt-5.4-mini" if args.group == "provider-smoke" else None,
        "status": "pending",
        "skip_reason": None,
    }

    if args.group == "provider-smoke":
        ok, reason = _provider_preflight()
        if not ok:
            metadata["status"] = "skipped"
            metadata["skip_reason"] = reason
            (run_dir / "report.html").write_text(
                "<html><body><h1>provider-smoke skipped</h1>"
                f"<p>{reason}</p></body></html>\n",
                encoding="utf-8",
            )
            _write_json(run_dir / "metadata.json", metadata)
            _write_json(run_dir / "summary.json", {"ok": True, "skipped": True, "reason": reason})
            _update_latest_link(artifact_root, run_dir)
            _prune_old_runs(artifact_root, keep_runs)
            print(f"provider-smoke skipped: {reason}")
            return 0

    command = _build_pytest_command(args, run_dir)
    metadata["pytest_command"] = command
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "BTWIN_TEST_RUN_DIR": str(run_dir),
            "BTWIN_TEST_GROUP": args.group,
            "BTWIN_PROVIDER_SURFACE": "app-server",
            "BTWIN_PROVIDER_CONTINUITY": "long-term",
            "BTWIN_PROVIDER_MODEL": "gpt-5.4-mini",
        },
        check=False,
    )
    (run_dir / "stdout.txt").write_text(result.stdout, encoding="utf-8")
    (run_dir / "stderr.txt").write_text(result.stderr, encoding="utf-8")
    metadata["status"] = "passed" if result.returncode == 0 else "failed"
    metadata["returncode"] = result.returncode
    _write_json(run_dir / "metadata.json", metadata)
    _write_json(
        run_dir / "summary.json",
        {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "group": args.group,
        },
    )
    _update_latest_link(artifact_root, run_dir)
    _prune_old_runs(artifact_root, keep_runs)

    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
