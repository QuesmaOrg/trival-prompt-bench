"""Run the trivial-prompt-bench benchmark: invoke Harbor across the model list, then ingest.

This shells out to the ``harbor`` CLI (rather than driving its Python API) because
the CLI is Harbor's stable, documented surface. Harbor permutes over every ``-m``
model and runs ``-k`` attempts each, producing one trial per (model, attempt).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path

from trivial_prompt_bench import db, ingest
from trivial_prompt_bench.config import load_config

# Every task runs through the Terminus agent so we measure real agent overhead
# (terminal setup + system prompt + agent loop), even for trivial prompts.
DEFAULT_AGENT = "terminus-2"
# Single-call chat agent, used only for offline --mock pipeline tests (mock models
# short-circuit its one LLM call; a real agent loop can't use them).
MOCK_AGENT = "trivial_prompt_bench.agent:HiAgent"
TASKS_DIR = Path("tasks")


def discover_tasks(tasks_dir: Path = TASKS_DIR) -> list[Path]:
    """Every subdirectory of ``tasks/`` that is a Harbor task (has task.toml)."""
    return sorted(p.parent for p in tasks_dir.glob("*/task.toml"))


def task_settings(task_path: Path) -> tuple[str, bool]:
    """Read a task's per-task trivial-prompt-bench settings from its task.toml [metadata].

    Returns ``(agent, verify)``. ``agent`` is a Harbor agent name (e.g.
    ``terminus-2``) or an import path; defaults to ``HiAgent``. ``verify`` toggles
    whether the task's verifier runs (default off — trivial tasks have nothing to
    grade). Agentic tasks like ``commit`` set both.
    """
    meta = {}
    toml_path = task_path / "task.toml"
    if toml_path.exists():
        meta = tomllib.loads(toml_path.read_text()).get("metadata", {}) or {}
    agent = meta.get("agent") or DEFAULT_AGENT
    verify = bool(meta.get("verify", False))
    return agent, verify


def build_command(
    task_path: Path,
    agent: str,
    verify: bool,
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
        "-a", agent,
        "-k", str(attempts),
        "-n", str(concurrency),
        "-o", str(jobs_dir),
        "--job-name", job_name,
        "-y",
    ]
    if not verify:
        cmd.append("--disable-verification")   # nothing to grade; we only measure
    for model in models:
        cmd += ["-m", model]
    if env_file is not None and env_file.exists():
        cmd += ["--env-file", str(env_file)]
    return cmd


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the trivial-prompt-bench benchmark.")
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

    # Task selection: explicit --task wins; else the configured [bench].tasks set;
    # an empty configured list means "every task under tasks/".
    if args.task:
        tasks = args.task
    elif cfg.run_tasks:
        tasks = [TASKS_DIR / name for name in cfg.run_tasks]
        missing = [str(t) for t in tasks if not (t / "task.toml").exists()]
        if missing:
            sys.exit(f"Configured [bench].tasks not found: {', '.join(missing)}")
    else:
        tasks = discover_tasks()
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
        if args.mock:
            # Offline pipeline test: force the single-call chat agent + mock models.
            agent, verify = MOCK_AGENT, False
        else:
            agent, verify = task_settings(task_path)
        job_name = f"{base}__{task_path.name}"
        cmd = build_command(
            task_path=task_path,
            agent=agent,
            verify=verify,
            models=models,
            attempts=attempts,
            concurrency=concurrency,
            job_name=job_name,
            jobs_dir=args.jobs_dir,
            env_file=None if args.mock else args.env_file,
        )
        print(f"\n=== task {task_path.name} -> job {job_name}  (agent={agent}, verify={verify}) ===")
        print("+ " + " ".join(cmd))
        proc = subprocess.run(cmd)
        if proc.returncode != 0:
            print(f"harbor run exited with {proc.returncode}; ingesting whatever completed.")

        job_dir = args.jobs_dir / job_name
        if job_dir.exists():
            instruction_file = task_path / "instruction.md"
            prompt = instruction_file.read_text().strip() if instruction_file.exists() else None
            total += ingest.ingest_job(job_dir, args.db, prompt_override=prompt)
        else:
            print(f"Job directory {job_dir} not found; nothing to ingest for this task.")

    print(f"\nIngested {total} trial(s) into {args.db}.")

    # Generate the per-task failure analysis (Claude call -> db). Best-effort: a
    # missing API key or provider hiccup shouldn't fail the whole run. Skipped for
    # --mock (no real failures worth analyzing, and mocks never error).
    if not args.mock:
        try:
            from trivial_prompt_bench.analyze import generate_analyses
            n = generate_analyses(args.db, cfg.analysis_model, args.env_file)
            print(f"Analysis: wrote {n} per-(task,model) note(s).")
        except Exception as exc:
            print(f"Analysis skipped: {exc}")

    print("Run `make report` (or `make report-html`) to see results.")


if __name__ == "__main__":
    main()
