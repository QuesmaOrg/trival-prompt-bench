"""Ingest Harbor trial results into sqlite.

Walks a job directory for ``result.json`` files, parses each with Harbor's own
``TrialResult`` pydantic model (so we track the schema, not a fragile hand-rolled
parse), extracts tokens/cost/timings, and upserts one row per trial.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from harbor.models.trial.result import TimingInfo, TrialResult

from hi_bench import db


def _duration_seconds(start, finish) -> float | None:
    if start is None or finish is None:
        return None
    return (finish - start).total_seconds()


def _timing_duration(timing: TimingInfo | None) -> float | None:
    if timing is None:
        return None
    return _duration_seconds(timing.started_at, timing.finished_at)


def trial_to_row(result: TrialResult, job_name: str) -> dict:
    n_input, n_cache, n_output, cost = result.compute_token_cost_totals()

    model_info = result.agent_info.model_info
    provider = model_info.provider if model_info else None
    model_name = model_info.name if model_info else None
    full_model = (
        f"{provider}/{model_name}" if provider and model_name else (model_name or None)
    )

    error = None
    if result.exception_info is not None:
        error = (
            f"{result.exception_info.exception_type}: "
            f"{result.exception_info.exception_message}"
        )

    response_text = None
    prompt = None
    if result.agent_result is not None and result.agent_result.metadata:
        response_text = result.agent_result.metadata.get("response_text")
        prompt = result.agent_result.metadata.get("prompt")

    return {
        "trial_id": str(result.id),
        "job_name": job_name,
        "task_name": result.task_name,
        "prompt": prompt,
        "trial_name": result.trial_name,
        "agent_name": result.agent_info.name,
        "agent_version": result.agent_info.version,
        "model": full_model,
        "provider": provider,
        "model_name": model_name,
        "n_input_tokens": n_input,
        "n_cache_tokens": n_cache,
        "n_output_tokens": n_output,
        "model_cost_usd": cost,
        "agent_seconds": _timing_duration(result.agent_execution),
        "total_seconds": _duration_seconds(result.started_at, result.finished_at),
        "env_setup_seconds": _timing_duration(result.environment_setup),
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
        "error": error,
        "response_text": response_text,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }


def ingest_job(job_dir: Path, db_path: Path) -> int:
    """Ingest every result.json under *job_dir*. Returns the number of rows written."""
    job_dir = Path(job_dir)
    job_name = job_dir.name
    result_files = sorted(job_dir.rglob("result.json"))
    # Exclude the job-level result.json (aggregate); we only want per-trial ones,
    # which live one directory deeper. The job aggregate fails TrialResult parsing
    # and is skipped anyway, but filtering keeps logs clean.
    result_files = [p for p in result_files if p.parent != job_dir]

    conn = db.connect(db_path)
    written = 0
    try:
        for path in result_files:
            try:
                result = TrialResult.model_validate_json(path.read_text())
            except Exception as exc:
                print(f"  skip {path}: {exc}")
                continue
            db.upsert_run(conn, trial_to_row(result, job_name))
            written += 1
        conn.commit()
    finally:
        conn.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a Harbor job into sqlite.")
    parser.add_argument("job_dir", type=Path, help="Path to jobs/<job-name>")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    args = parser.parse_args()

    n = ingest_job(args.job_dir, args.db)
    print(f"Ingested {n} trial(s) from {args.job_dir} into {args.db}")


if __name__ == "__main__":
    main()
