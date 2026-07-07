"""Per-(task, model) analysis via a Claude call, stored in sqlite.

For every model that ran a task we ask the model to describe **what the agent actually
did with its tools** (the commands it issued), and — when that model had failed runs —
the **failure mode** as a short summary plus folded details. Results go into the
``analysis`` table. This is the only place that calls an LLM to interpret results; the
report just fetches and formats.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import litellm

from trivial_prompt_bench import db
from trivial_prompt_bench.config import load_config

_MAX_RUNS_SHOWN = 3      # distinct command-sequences to show the analyst per (task, model)
_MAX_CMDS_PER_RUN = 20   # cap commands listed per run


def _load_env(env_file: Path = Path(".env")) -> None:
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def _fmt_run_commands(tool_calls_json: str | None) -> str:
    """Render one run's tool calls as a compact command list."""
    if not tool_calls_json:
        return "(no tool calls / no trajectory)"
    try:
        calls = json.loads(tool_calls_json)
    except Exception:
        return "(unparseable)"
    if not calls:
        return "(no tool calls — the agent never issued a command)"
    lines = []
    for c in calls[:_MAX_CMDS_PER_RUN]:
        fn, cmd = c.get("fn"), (c.get("cmd") or "").replace("\n", " ⏎ ")
        lines.append(f"$ {cmd[:140]}" if fn == "bash_command" and cmd else f"[{fn}]")
    if len(calls) > _MAX_CMDS_PER_RUN:
        lines.append(f"... (+{len(calls) - _MAX_CMDS_PER_RUN} more)")
    return "\n".join(lines)


def _build_prompt(task, prompt, model, runs) -> tuple[str, bool]:
    n = len(runs)
    fails = [r for r in runs if r["error"]]
    # Distinct command sequences (dedup identical runs), most-detailed first.
    seen, blocks = {}, []
    for r in runs:
        key = r["tool_calls_json"] or ""
        seen[key] = seen.get(key, 0) + 1
    for key, count in sorted(seen.items(), key=lambda kv: -len(kv[0]))[:_MAX_RUNS_SHOWN]:
        blocks.append(f"--- a run (x{count}) ---\n{_fmt_run_commands(key)}")
    cmds = "\n\n".join(blocks)

    fail_line = ""
    fail_keys = ""
    if fails:
        errs = "; ".join(sorted({r["error"].split(":")[0] for r in fails}))
        fail_line = f"\n{len(fails)} of {n} runs FAILED ({errs})."
        fail_keys = (
            '  "failure_summary": "one sentence: the failure mode / root cause",\n'
            '  "failure_details": "2-4 sentences with specifics — what it kept doing, why it errored/timed out",\n'
        )
    prompt_text = f"""\
You are analyzing an LLM agent benchmark. A model was given a short prompt and driven \
through the Terminus terminal agent inside a small git repo (a Python todo app with one \
uncommitted change to app.py).

Task prompt: "{prompt}"
Model: {model}   (ran {n} times){fail_line}

What the agent did — shell commands it issued, per distinct run:
{cmds}

Respond with ONLY a JSON object:
{{
  "tool_usage": "2-3 sentences, concrete: what the agent actually did in the terminal (the commands/pattern) and whether that fit the prompt",
{fail_keys}}}"""
    return prompt_text, bool(fails)


def _parse(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)  # grab the outermost object
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {"tool_usage": text[:800]}


def generate_analyses(db_path: Path, model: str, env_file: Path = Path(".env")) -> int:
    _load_env(env_file)
    conn = db.connect(db_path)
    written = 0
    try:
        rows = conn.execute(
            "SELECT task_name, prompt, model, error, tool_calls_json, n_tool_calls, "
            "agent_seconds FROM runs ORDER BY task_name, model"
        ).fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["task_name"], r["model"]), []).append(r)

        conn.execute("DELETE FROM analysis")
        for (task, mdl), runs in groups.items():
            prompt_text, has_fail = _build_prompt(task, runs[0]["prompt"] or "", mdl, runs)
            try:
                kwargs = dict(model=model,
                              messages=[{"role": "user", "content": prompt_text}],
                              max_tokens=900)
                try:
                    resp = litellm.completion(response_format={"type": "json_object"}, **kwargs)
                except Exception:
                    resp = litellm.completion(**kwargs)  # model may not support json mode
                data = _parse(resp.choices[0].message.content or "")
            except Exception as exc:
                print(f"  analysis failed for {task} / {mdl}: {exc}")
                continue
            n_fail = sum(1 for r in runs if r["error"])
            conn.execute(
                "INSERT OR REPLACE INTO analysis (task_name, model, n_runs, n_failures, "
                "tool_usage, failure_summary, failure_details, gen_model, generated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (task, mdl, len(runs), n_fail,
                 data.get("tool_usage"),
                 data.get("failure_summary") if has_fail else None,
                 data.get("failure_details") if has_fail else None,
                 model, datetime.now(timezone.utc).isoformat()),
            )
            written += 1
            print(f"  analyzed {task.split('/')[-1]:14} / {mdl.split('/')[-1]:24}"
                  f"{'  (' + str(n_fail) + ' failed)' if n_fail else ''}")
        conn.commit()
    finally:
        conn.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate per-(task,model) analysis into sqlite.")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--model", type=str, default=None, help="Override the analysis model.")
    args = parser.parse_args()
    cfg = load_config(args.config)
    n = generate_analyses(args.db, args.model or cfg.analysis_model, args.env_file)
    print(f"Wrote {n} analysis row(s) to {args.db}." if n else "Nothing to analyze.")


if __name__ == "__main__":
    main()
