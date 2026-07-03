"""Generate a short, per-task failure analysis with a Claude call and store it in sqlite.

This is the ONLY place that calls an LLM to interpret results. It reads the failed
runs from the ``runs`` table, asks the model for a 2-3 sentence root cause per task,
and writes the text to the ``failure_analysis`` table. The report never calls an LLM —
it just fetches and formats what this wrote.
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import litellm

from trivial_prompt_bench import db
from trivial_prompt_bench.config import load_config

PROMPT_TEMPLATE = """\
You are analyzing failures from an LLM agent benchmark (trivial-prompt-bench). Each run \
hands a model a short prompt inside a git repo and drives it through the Terminus \
terminal agent; a run "fails" when the agent errors or times out.

Task: {task}  (the prompt given to the agent was: "{prompt}")
{n_fail} of {n_total} runs failed. The failed runs:
{rows}

In AT MOST 3 sentences, give the most likely root cause and what it reveals about the \
model/prompt. Be concrete and technical. No preamble, no bullet points, no restating \
the numbers.
"""


def _load_env(env_file: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from .env into os.environ (without overriding existing)."""
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


def _failures_by_task(conn) -> dict[str, list]:
    rows = conn.execute(
        "SELECT task_name, prompt, model_name, error, n_tool_calls, agent_seconds "
        "FROM runs WHERE error IS NOT NULL AND error != '' ORDER BY task_name"
    ).fetchall()
    by_task: dict[str, list] = {}
    for r in rows:
        by_task.setdefault(r["task_name"], []).append(r)
    return by_task


def _task_totals(conn) -> dict[str, int]:
    return {
        r["task_name"]: r["n"]
        for r in conn.execute("SELECT task_name, COUNT(*) n FROM runs GROUP BY task_name")
    }


def generate_failure_analyses(db_path: Path, model: str, env_file: Path = Path(".env")) -> int:
    """Analyze every task that has failed runs; store one note each. Returns count."""
    _load_env(env_file)
    conn = db.connect(db_path)
    written = 0
    try:
        by_task = _failures_by_task(conn)
        totals = _task_totals(conn)
        # Regenerate from scratch so notes always match the current data.
        conn.execute("DELETE FROM failure_analysis")
        for task, fails in by_task.items():
            rows_txt = "\n".join(
                f"- {r['model_name']}: {r['error']} "
                f"(tool_calls={r['n_tool_calls']}, agent_seconds="
                f"{r['agent_seconds']:.0f})" if r["agent_seconds"] is not None
                else f"- {r['model_name']}: {r['error']}"
                for r in fails
            )
            prompt = PROMPT_TEMPLATE.format(
                task=task, prompt=fails[0]["prompt"] or "", n_fail=len(fails),
                n_total=totals.get(task, len(fails)), rows=rows_txt,
            )
            try:
                resp = litellm.completion(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=250,
                )
                analysis = (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                print(f"  analysis failed for {task}: {exc}")
                continue
            conn.execute(
                "INSERT OR REPLACE INTO failure_analysis "
                "(task_name, n_failures, analysis, model, generated_at) VALUES (?,?,?,?,?)",
                (task, len(fails), analysis, model, datetime.now(timezone.utc).isoformat()),
            )
            written += 1
            print(f"  analyzed {task} ({len(fails)} failures)")
        conn.commit()
    finally:
        conn.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-task failure analysis into sqlite.")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--model", type=str, default=None, help="Override the analysis model.")
    args = parser.parse_args()

    cfg = load_config(args.config)
    model = args.model or cfg.analysis_model
    n = generate_failure_analyses(args.db, model, args.env_file)
    if n == 0:
        print("No failed runs to analyze.")
    else:
        print(f"Wrote {n} failure analysis note(s) to {args.db}.")


if __name__ == "__main__":
    main()
