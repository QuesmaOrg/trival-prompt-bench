# CLAUDE.md

Guidance for working in this repository.

## What this is

`hi-bench` is a tiny LLM cost/latency benchmark built on the **Harbor** framework
(`harbor-framework`, `pip install harbor` — the eval harness from the creators of
Terminal-Bench). It measures how much a task costs and how long it takes across a
list of models.

Every task runs through the **Terminus** agent (Harbor's built-in terminal agent),
so the numbers include real **agent overhead** — terminal setup + system prompt +
the agent loop — even for a one-word prompt. That's deliberate: we want to see what
wrapping a request in an agent actually costs. (`HiAgent`, the single-LLM-call agent,
still exists but is used only for offline `--mock` pipeline tests.)

We record, per run:

- token usage (input / cache / output)
- **model cost** (USD, computed by LiteLLM's pricing tables)
- **latency** (seconds the user waits for the answer)

and then compute a **total cost** per run:

```
total_cost = model_cost_usd + (latency_seconds * dev_salary_usd_per_second)
```

The second term ("user waiting cost") prices the developer's time spent waiting for
the response, using an average US developer salary. See `config.toml`.

## Architecture

```
tasks/<name>/             Harbor task. instruction.md is the prompt. task.toml [metadata]
                          may set `agent` and `verify` (see below). Trivial prompt tasks:
                          hi, thank-you, wtf. Agentic task: commit (git repo + verifier).
hi_bench/agent.py         HiAgent — single-LLM-call agent, used ONLY for --mock runs
hi_bench/run.py           Runs every task (via its chosen agent) across the models, ingests
hi_bench/ingest.py        Parses jobs/<job>/<trial>/result.json -> sqlite
hi_bench/db.py            sqlite schema + connection helpers
hi_bench/report.py        Reads sqlite, prints/writes the cost/latency report
hi_bench/report_html.py   Self-contained HTML report (one stacked bar graph per task)
config.toml               Model list (the "test suite") + salary assumptions
jobs/                     Harbor's raw job output (git-ignored)
data/hi_bench.db          sqlite database (git-ignored)
report.html / report.txt  Generated report artifacts (git-ignored)
```

### Agents (per-task) — measuring agent overhead

The default agent is **`terminus-2`** (Harbor's built-in terminal agent): the runner
passes `-a terminus-2` for every task, so cost/latency include the full agent loop
(tmux setup + system prompt + turns), not just the model's reply. That's the point —
we're measuring what an agent *wrapper* costs, even on a one-word prompt.

Per-task overrides live in each `task.toml` `[metadata]`:

- `agent = "..."` — Harbor agent name or import path (default `terminus-2`).
- `verify = true|false` — run the task's verifier (default `false`; trivial tasks have
  nothing to grade, so the runner passes `--disable-verification` for them).

`commit` sets `agent = "terminus-2"` and `verify = true`. `--mock` runs override the
agent to `HiAgent` (mock models short-circuit its single call; a real agent loop can't
use them). Task images that Terminus drives must have **tmux** (baked into every task's
Dockerfile so Terminus doesn't apt-install it per trial).

### The report is per-task

The report groups runs by **task**, then by model within each task. Both the text
and HTML reports render **one section per task**; the HTML report draws **one data
graph per task** — a horizontal stacked bar per model with two components that sum to
the average total cost per run:

- **Token wasted** (blue) — the LLM API cost
- **Time wasted** (orange) — `latency x salary/second`

The chart **defaults to LLM cost only** (blue bars); a checkbox toggle ("Show time
wasted") stacks the orange waiting-cost segment on top so each bar becomes total cost.
The toggle rescales the axis and re-renders all task charts (`renderAll(showTime)`).

Each task's graph is scaled to its own max, has its own insight line and data table,
and the footer aggregates spend across all tasks. So when hi-bench grows beyond the
single `Hi` task (add more dirs under `tasks/`, run them, ingest), the report
automatically gains a graph per task with no code changes — the per-task loop is
driven by the `task_name` column in sqlite. The task's prompt shown in each section
comes from the `prompt` column, filled at ingest time from the task's `instruction.md`
(authoritative and agent-independent). Verified tasks also show a `pass` (avg reward)
column, from the `reward` column populated from `TrialResult.verifier_result.rewards`.
The `tool calls` column is `n_tool_calls`: at ingest we read the agent's ATIF
transcript (`<trial>/agent/trajectory.json`) and sum `tool_calls` across its steps
(includes real commands like `bash_command` plus the `mark_task_complete` marker;
NULL for HiAgent/mock runs, which write no trajectory).

Generate it with `make report-html` (writes `report.html`). Chart colors are the
dataviz reference palette's slots 1 (blue) and 8 (orange), validated for CVD and
contrast in light and dark modes; keep the legend, the 2px segment gap, and the data
table if you touch `report_html.py` (they are the accessibility "relief" channels).

### How the pieces fit (important facts discovered from the Harbor source)

- Harbor runs, per trial, an **agent** inside an **environment** (a Docker container)
  against a **task**, then optionally a **verifier** (enabled only for tasks with
  `verify = true`; otherwise the runner passes `--disable-verification`).
- `terminus-2` runs the agent loop against the container; token/cost/latency land in
  the trial result the same way regardless of which agent ran.
- `HiAgent` (mock only) subclasses `harbor.agents.base.BaseAgent`; custom agents load by
  **import path** (`-a hi_bench.agent:HiAgent`). The repo must be importable (installed
  with `uv pip install -e .`, which the Makefile does). Its `run()` executes in the
  **Harbor host process** and populates `AgentContext`
  (`n_input_tokens`, `n_cache_tokens`, `n_output_tokens`, `cost_usd`).
- Harbor writes each trial's result to `jobs/<job-name>/<trial-name>/result.json`,
  a serialized `harbor.models.trial.result.TrialResult`. We parse it with that same
  pydantic model (do NOT hand-roll JSON parsing — use the model).
  - Tokens + cost: `TrialResult.compute_token_cost_totals()`
    -> `(n_input, n_cache, n_output, cost_usd)`.
  - **Latency** (what the user waits, and what waiting-cost bills against): the
    **transcript** value — Terminus records each LLM call's wall time in
    `agent_result.metadata["api_request_times_msec"]`; we sum it into `llm_seconds`
    (and count calls in `n_llm_calls`). This is the real time spent on the model,
    excluding tmux polling and the model-chosen per-command waits that inflate the
    span. When absent (HiAgent/mock), we fall back to the `agent_execution` span
    (`agent_seconds`). The report's latency + waiting-cost use
    `COALESCE(llm_seconds, agent_seconds)`.
  - Also stored for reference: `agent_seconds` (agent_execution span),
    `total_seconds` (whole trial), `env_setup_seconds`.
  - Model identity: `agent_info.model_info.{name,provider}`.

### Mock models (test without API keys)

`HiAgent` special-cases any model named `mock/<something>`: it returns a canned answer
with synthetic tokens/cost and a small sleep, making **zero** network calls. Use these
to validate the entire Harbor -> sqlite -> report pipeline offline (`make smoke`).
Real models require API keys (see below).

## Commands

Everything is a Makefile target. Common ones:

```
make setup        # create .venv (uv, Python 3.12) and install harbor + this package
make smoke        # end-to-end run with mock/* models — no API keys needed
make bench        # run the real model list from config.toml, ingest into sqlite
make report       # print the cost/latency report from sqlite
make report-html  # write report.html — one stacked-bar data graph per task
make report-file  # write report.txt
make clean        # remove jobs/ and the sqlite db
```

Direct Harbor invocation (one of what `make bench` runs per task under the hood):

```
# trivial task (no grading):
harbor run -p tasks/hi -a terminus-2 \
  -m anthropic/claude-haiku-4-5-20251001 -m anthropic/claude-sonnet-5 \
  -k 5 --disable-verification -o jobs --job-name <base>__hi --env-file .env -y

# agentic task (graded): omit --disable-verification (task.toml sets verify=true)
harbor run -p tasks/commit -a terminus-2 -m ... -k 5 -o jobs --job-name <base>__commit \
  --env-file .env -y
```

## API keys

LiteLLM reads provider keys from the environment. Put them in `.env` (git-ignored;
copy from `.env.example`). Harbor loads it via `--env-file .env`. Typical keys:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`. Model names are LiteLLM
identifiers, e.g. `anthropic/claude-...`, `openai/gpt-...`, `gemini/gemini-...`.

## Conventions / gotchas

- Requires **Docker** running (Harbor builds a container per trial).
- Python is pinned to **3.12** via uv; 3.14 lacks wheels for some Harbor deps.
- The default model in this project when building AI features is the latest Claude
  (e.g. `anthropic/claude-opus-4-8`, `anthropic/claude-haiku-4-5-20251001`).
- Latency for the waiting-cost is the **agent_execution** duration, not total wall
  time — keep that distinction if you touch `ingest.py`/`report.py`.
- Ingest is **idempotent**: it upserts on the trial UUID, so re-ingesting a job is safe.
- Salary assumptions live in `config.toml [cost]`; changing them changes the report
  only (raw per-run data in sqlite is assumption-free).
