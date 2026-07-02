"""Run the hi-bench benchmark: invoke Harbor across the model list, then ingest.

This shells out to the ``harbor`` CLI (rather than driving its Python API) because
the CLI is Harbor's stable, documented surface. Harbor permutes over every ``-m``
model and runs ``-k`` attempts each, producing one trial per (model, attempt).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from hi_bench import db, ingest
from hi_bench.config import load_config

AGENT_IMPORT_PATH = "hi_bench.agent:HiAgent"
TASKS_DIR = Path("tasks")


def discover_tasks(tasks_dir: Path = TASKS_DIR) -> list[Path]:
    """Every subdirectory of ``tasks/`` that is a Harbor task (has task.toml)."""
    return sorted(p.parent for p in tasks_dir.glob("*/task.toml"))


def build_command(
    task_path: Path,
    models: list[str],
    attempts: int,
    concurrency: int,
    job_name: str,
    jobs_dir: Path,
    env_file: Path | None,
) -> list[str]:
    cmd = [
        "harbor", "run",
        "-p", str(task_path),
        "-a", AGENT_IMPORT_PATH,
        "-k", str(attempts),
        "-n", str(concurrency),
        "--disable-verification",   # nothing to grade; we only measure
        "-o", str(jobs_dir),
        "--job-name", job_name,
        "-y",
    ]
    for model in models:
        cmd += ["-m", model]
    if env_file is not None and env_file.exists():
        cmd += ["--env-file", str(env_file)]
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the hi-bench benchmark.")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--mock", action="store_true", help="Use mock models (offline).")
    parser.add_argument("--attempts", type=int, default=None, help="Override runs/model.")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--jobs-dir", type=Path, default=Path("jobs"))
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--job-name", type=str, default=None)
    parser.add_argument(
        "--task", type=Path, action="append", default=None,
        help="Task dir to run (repeatable). Default: every task under tasks/.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    models = cfg.mock_models if args.mock else cfg.models
    if not models:
        which = "mock_list" if args.mock else "list"
        sys.exit(f"No models configured under [models].{which} in {args.config}")

    tasks = args.task if args.task else discover_tasks()
    if not tasks:
        sys.exit(f"No tasks found under {TASKS_DIR}/ (need a <name>/task.toml).")

    attempts = args.attempts if args.attempts is not None else cfg.attempts
    concurrency = args.concurrency if args.concurrency is not None else cfg.concurrency

    prefix = "mock" if args.mock else "bench"
    base = args.job_name or f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"Models: {', '.join(models)}")
    print(f"Tasks: {', '.join(t.name for t in tasks)}")
    print(f"Attempts/model: {attempts}  Concurrency: {concurrency}")

    # One Harbor job per task (each writes its own job dir); ingest them all. The
    # report groups by task_name, so separate jobs still produce one graph per task.
    total = 0
    for task_path in tasks:
        job_name = f"{base}__{task_path.name}"
        cmd = build_command(
            task_path=task_path,
            models=models,
            attempts=attempts,
            concurrency=concurrency,
            job_name=job_name,
            jobs_dir=args.jobs_dir,
            env_file=None if args.mock else args.env_file,
        )
        print(f"\n=== task {task_path.name} -> job {job_name} ===")
        print("+ " + " ".join(cmd))
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"harbor run exited with {proc.returncode}; ingesting whatever completed.")

        job_dir = args.jobs_dir / job_name
        if job_dir.exists():
            total += ingest.ingest_job(job_dir, args.db)
        else:
            print(f"Job directory {job_dir} not found; nothing to ingest for this task.")

    print(f"\nIngested {total} trial(s) into {args.db}. Run `make report` to see results.")


if __name__ == "__main__":
    main()
