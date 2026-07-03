"""Report cost and latency per model from the sqlite database.

Total cost of a run has two parts:

    total_cost = model_cost_usd + user_waiting_cost
    user_waiting_cost = agent_seconds * salary_usd_per_second

where ``salary_usd_per_second`` comes from config.toml's [cost] assumptions. The
"user waiting cost" prices the developer's time spent waiting for the answer.
"""

from __future__ import annotations

import argparse
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trivial_prompt_bench import db
from trivial_prompt_bench.config import load_config
from trivial_prompt_bench.report_html import render_html


@dataclass
class ModelStats:
    model: str
    n_runs: int
    n_errors: int
    avg_latency: float | None
    p95_latency: float | None
    avg_model_cost: float | None
    avg_tokens_out: float | None
    avg_waiting_cost: float | None
    avg_total_cost: float | None
    sum_total_cost: float
    avg_reward: float | None  # verifier pass rate (None if the task isn't graded)
    avg_tool_calls: float | None  # avg tool calls per run (None for non-agent runs)


@dataclass
class TaskStats:
    task: str
    prompt: str | None
    models: list[ModelStats]


def _f(x) -> float | None:
    """Coerce a possibly-string / possibly-None numeric cell to float or None."""
    if x is None or x == "":
        return None
    return float(x)


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def _p95(xs: list[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    idx = min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))
    return xs[idx]


def _aggregate_models(rows: list, salary_per_second: float) -> list[ModelStats]:
    """Group a set of run rows by model and compute per-model stats."""
    by_model: dict[str, list] = {}
    for r in rows:
        by_model.setdefault(r["model"] or "(unknown)", []).append(r)

    stats: list[ModelStats] = []
    for model, rs in by_model.items():
        ok = [r for r in rs if not r["error"]]
        # Latency = the transcript's summed LLM-call time when available (Terminus),
        # falling back to the agent_execution span (e.g. HiAgent/mock runs). Coerce to
        # float: columns added by an ALTER-TABLE migration have TEXT affinity, so SQLite
        # may return these numbers as strings.
        def _latency(r):
            v = r["llm_seconds"] if r["llm_seconds"] is not None else r["agent_seconds"]
            return _f(v)

        latencies = [_latency(r) for r in ok]
        model_costs = [_f(r["model_cost_usd"]) for r in ok]
        tokens_out = [_f(r["n_output_tokens"]) for r in ok]
        rewards = [_f(r["reward"]) for r in ok if r["reward"] is not None]
        tool_calls = [_f(r["n_tool_calls"]) for r in ok if r["n_tool_calls"] is not None]

        waiting_costs = [
            (_latency(r) * salary_per_second) for r in ok if _latency(r) is not None
        ]
        total_costs = [
            (r["model_cost_usd"] or 0.0) + (_latency(r) or 0.0) * salary_per_second
            for r in ok
        ]

        stats.append(
            ModelStats(
                model=model,
                n_runs=len(rs),
                n_errors=len(rs) - len(ok),
                avg_latency=_mean(latencies),
                p95_latency=_p95(latencies),
                avg_model_cost=_mean(model_costs),
                avg_tokens_out=_mean(tokens_out),
                avg_waiting_cost=_mean(waiting_costs),
                avg_total_cost=_mean(total_costs),
                sum_total_cost=sum(total_costs),
                avg_reward=_mean(rewards) if rewards else None,
                avg_tool_calls=_mean(tool_calls) if tool_calls else None,
            )
        )
    stats.sort(key=lambda s: (s.avg_total_cost is None, s.avg_total_cost or 0.0))
    return stats


def compute_stats_by_task(conn, salary_per_second: float) -> list[TaskStats]:
    """Group runs by task, then by model within each task."""
    rows = conn.execute(
        "SELECT task_name, prompt, model, model_cost_usd, llm_seconds, agent_seconds, "
        "n_output_tokens, n_tool_calls, error, reward FROM runs"
    ).fetchall()

    by_task: dict[str, list] = {}
    for r in rows:
        by_task.setdefault(r["task_name"] or "(unknown task)", []).append(r)

    tasks = []
    for task, rs in by_task.items():
        prompt = next((r["prompt"] for r in rs if r["prompt"]), None)
        tasks.append(
            TaskStats(
                task=task,
                prompt=prompt,
                models=_aggregate_models(rs, salary_per_second),
            )
        )
    tasks.sort(key=lambda t: t.task)
    return tasks


def _fmt_usd(x: float | None) -> str:
    return "-" if x is None else f"${x:,.6f}"


def _fmt_s(x: float | None) -> str:
    return "-" if x is None else f"{x:,.2f}s"


def _fmt_pass(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


def _fmt_num(x: float | None) -> str:
    return "-" if x is None else f"{x:.1f}"


def render(tasks: list[TaskStats], cfg, analyses: dict | None = None) -> str:
    analyses = analyses or {}
    lines: list[str] = []
    lines.append("trivial-prompt-bench report")
    lines.append("=" * 96)
    lines.append(
        f"Salary assumption: ${cfg.annual_salary_usd:,.0f}/yr over "
        f"{cfg.work_hours_per_year:,.0f} work-h/yr "
        f"= ${cfg.salary_usd_per_second:.6f}/second of waiting."
    )
    header = (
        f"{'model':<40} {'runs':>5} {'err':>4} {'pass':>5} {'tools':>6} {'avg lat':>9} {'p95 lat':>9} "
        f"{'avg model$':>12} {'avg wait$':>12} {'avg total$':>13}"
    )

    grand = 0.0
    for t in tasks:
        lines.append("")
        lines.append(f"TASK: {t.task}")
        lines.append(header)
        lines.append("-" * len(header))
        for s in t.models:
            lines.append(
                f"{s.model:<40} {s.n_runs:>5} {s.n_errors:>4} {_fmt_pass(s.avg_reward):>5} "
                f"{_fmt_num(s.avg_tool_calls):>6} "
                f"{_fmt_s(s.avg_latency):>9} {_fmt_s(s.p95_latency):>9} "
                f"{_fmt_usd(s.avg_model_cost):>12} {_fmt_usd(s.avg_waiting_cost):>12} "
                f"{_fmt_usd(s.avg_total_cost):>13}"
            )
        task_total = sum(s.sum_total_cost for s in t.models)
        grand += task_total
        lines.append(f"{'  task total spend (all runs)':<71}{_fmt_usd(task_total):>44}")
        fa = analyses.get(t.task)
        if fa:
            lines.append(f"  Failure analysis ({fa['n_failures']} failed): {fa['analysis']}")

    lines.append("")
    lines.append("=" * 96)
    lines.append(f"{'OVERALL spend (all tasks, all runs)':<52}{_fmt_usd(grand):>44}")
    lines.append("")
    lines.append(
        "avg total$ = avg model cost + avg waiting cost (latency * salary/second). "
        "Cheapest model first within each task."
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report trivial-prompt-bench results.")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Also write the text report to this file (e.g. report.txt).",
    )
    parser.add_argument(
        "--html", type=Path, default=None,
        help="Write a self-contained HTML report (stacked bar chart) to this file.",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    conn = db.connect(args.db)
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM runs").fetchone()["c"]
        if n == 0:
            print("No runs in the database yet. Run `make smoke` or `make bench` first.")
            return
        tasks = compute_stats_by_task(conn, cfg.salary_usd_per_second)
        analyses = {
            r["task_name"]: dict(r)
            for r in conn.execute(
                "SELECT task_name, n_failures, analysis, model, generated_at FROM failure_analysis"
            )
        }
    finally:
        conn.close()

    text = render(tasks, cfg, analyses)
    print(text)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
        print(f"\nWrote text report to {args.out}")
    if args.html is not None:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        args.html.write_text(render_html(tasks, cfg, generated_at, analyses))
        print(f"Wrote HTML report to {args.html}")


if __name__ == "__main__":
    main()
