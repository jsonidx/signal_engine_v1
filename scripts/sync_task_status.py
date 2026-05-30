#!/usr/bin/env python3
"""Move task files into the correct folder and validate workflow metadata."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "docs" / "tasks"
NEW_DIR = TASKS_DIR / "new"
IN_PROGRESS_DIR = TASKS_DIR / "in-progress"
FINISHED_DIR = TASKS_DIR / "finished"
TEMPLATE_PATH = TASKS_DIR / "TEMPLATE.md"

STATUS_PATTERN = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE)
STAGE_PATTERN = re.compile(r"^Stage:\s*(.+?)\s*$", re.MULTILINE)

STATUS_TO_FOLDER = {
    "proposed": NEW_DIR,
    "new": NEW_DIR,
    "not started": NEW_DIR,
    "todo": NEW_DIR,
    "in progress": IN_PROGRESS_DIR,
    "in-progress": IN_PROGRESS_DIR,
    "started": IN_PROGRESS_DIR,
    "doing": IN_PROGRESS_DIR,
    "implemented": IN_PROGRESS_DIR,
    "qa": IN_PROGRESS_DIR,
    "done": FINISHED_DIR,
    "completed": FINISHED_DIR,
    "finished": FINISHED_DIR,
    "closed": FINISHED_DIR,
}

STATUS_TO_ALLOWED_STAGES = {
    "proposed": {"discovery", "ready"},
    "new": {"discovery", "ready"},
    "not started": {"discovery", "ready"},
    "todo": {"discovery", "ready"},
    "in progress": {"in progress", "qa", "blocked"},
    "in-progress": {"in progress", "qa", "blocked"},
    "started": {"in progress", "qa", "blocked"},
    "doing": {"in progress", "qa", "blocked"},
    "implemented": {"in progress", "qa"},
    "qa": {"qa"},
    "done": {"done"},
    "completed": {"done"},
    "finished": {"done"},
    "closed": {"done"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync docs/tasks ticket files into new, in-progress, or finished folders."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional task files to sync. Defaults to all task markdown files.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report misplaced files without moving them.",
    )
    return parser.parse_args()


def iter_task_files(explicit_paths: list[str]) -> list[Path]:
    if explicit_paths:
        files = [Path(p).resolve() for p in explicit_paths]
    else:
        files = sorted(TASKS_DIR.glob("**/*.md"))

    task_files: list[Path] = []
    for path in files:
        if not path.exists():
            raise FileNotFoundError(f"Task file does not exist: {path}")
        if path == TEMPLATE_PATH:
            continue
        if path.suffix != ".md":
            continue
        task_files.append(path)
    return task_files


def read_status(path: Path) -> str:
    match = STATUS_PATTERN.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"Missing Status field in {path}")
    status = match.group(1).strip().lower()
    if status not in STATUS_TO_FOLDER:
        allowed = ", ".join(sorted(STATUS_TO_FOLDER))
        raise ValueError(f"Unsupported Status '{status}' in {path}. Allowed: {allowed}")
    return status


def read_stage(path: Path) -> str:
    match = STAGE_PATTERN.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"Missing Stage field in {path}")
    return match.group(1).strip().lower()


def validate_stage(path: Path, status: str) -> str:
    stage = read_stage(path)
    allowed_stages = STATUS_TO_ALLOWED_STAGES[status]
    if stage not in allowed_stages:
        allowed = ", ".join(sorted(allowed_stages))
        raise ValueError(
            f"Invalid Stage '{stage}' for Status '{status}' in {path}. Allowed: {allowed}"
        )
    return stage


def desired_path(path: Path, status: str) -> Path:
    return STATUS_TO_FOLDER[status] / path.name


def sync_file(path: Path, check_only: bool) -> tuple[bool, str]:
    status = read_status(path)
    stage = validate_stage(path, status)
    target = desired_path(path, status)
    if path.resolve() == target.resolve():
        return False, f"OK   {path.relative_to(REPO_ROOT)} [{status}, {stage}]"

    if check_only:
        return True, (
            f"MISS {path.relative_to(REPO_ROOT)} -> "
            f"{target.relative_to(REPO_ROOT)} [{status}, {stage}]"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))
    return True, (
        f"MOVE {path.relative_to(REPO_ROOT)} -> "
        f"{target.relative_to(REPO_ROOT)} [{status}, {stage}]"
    )


def main() -> int:
    args = parse_args()

    try:
        task_files = iter_task_files(args.paths)
        changed = False
        for directory in (NEW_DIR, IN_PROGRESS_DIR, FINISHED_DIR):
            directory.mkdir(parents=True, exist_ok=True)
        for path in task_files:
            moved, message = sync_file(path, args.check)
            changed = changed or moved
            print(message)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    if args.check and changed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
