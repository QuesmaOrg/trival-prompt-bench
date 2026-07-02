# CLAUDE.md

Guidance for working in this repository.

## What this is

`hi-bench` is a tiny LLM cost/latency benchmark built on the **Harbor** framework
(`harbor-framework`, `pip install harbor` — the eval harness from the creators of
Terminal-Bench). It measures how much a *trivial* task costs and how long it takes
across a list of models.

The task is deliberately minimal: **send the prompt `Hi` to a model and get one
answer.** We record, per run:

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
tasks/hi/                 Harbor task: instruction.md == "Hi", verification disabled
hi_bench/agent.py         HiAgent — a custom Harbor agent making ONE LiteLLM call
hi_bench/run.py           Orchestrates `harbor run` across the model list, then ingests
hi_bench/ingest.py        Parses jobs/<job>/<trial>/result.json -> sqlite
hi_bench/db.py            sqlite schema + connection helpers
hi_bench/report.py        Reads sqlite, prints/writes the cost/latency report
hi_bench/report_html.py   Self-contained HTML report (one stacked bar graph per task)
config.toml               Model list (the "test suite") + salary assumptions
jobs/                     Harbor's raw job output (git-ignored)
data/hi_bench.db          sqlite database (git-ignored)
report.html / report.txt  Generated report artifacts (git-ignored)
```

### The report is per-task

The report groups runs by **task**, then by model within each task. Both the text
and HTML reports render **one section per task**; the HTML report draws **one data
graph per task** — a horizontal stacked bar per model with two components that sum to
the average total cost per run:

- **Time wasted** (orange) — `latency x salary/second`
- **Token wasted** (blue) — the LLM API cost

Each task's graph is scaled to its own max, has its own insight line and data table,
and the footer aggregates spend across all tasks. So when hi-bench grows beyond the
single `Hi` task (add more dirs under `tasks/`, run them, ingest), the report
automatically gains a graph per task with no code changes — the per-task loop is
driven by the `task_name` column in sqlite. The task's prompt shown in each section
comes from the `prompt` column (recorded by `HiAgent` into the agent metadata).

Generate it with `make report-html` (writes `report.html`). Chart colors are the
dataviz reference palette's slots 1 (blue) and 8 (orange), validated for CVD and
contrast in light and dark modes; keep the legend, the 2px segment gap, and the data
table if you touch `report_html.py` (they are the accessibility "relief" channels).

### How the pieces fit (important facts discovered from the Harbor source)

- Harbor runs, per trial, an **agent** inside an **environment** (a Docker container)
  against a **task**, then optionally a **verifier**. We disable the verifier
  (`--disable-verification`) because there is nothing to grade — we only measure.
- Our `HiAgent` subclasses `harbor.agents.base.BaseAgent`. Custom agents are loaded by
  **import path**: `-a hi_bench.agent:HiAgent`. The repo must be importable
  (installed with `uv pip install -e .`, which the Makefile does).
- A custom `BaseAgent.run()` executes in the **Harbor host process** (not inside the
  container), so it can call LiteLLM directly over the network. The container is still
  built (from `tasks/hi/environment/Dockerfile`, `ubuntu:24.04`) but our agent ignores
  it. Environment build time is infra overhead, NOT counted as user waiting time.
- The agent must populate the passed-in `AgentContext` with
  `n_input_tokens`, `n_cache_tokens`, `n_output_tokens`, `cost_usd`.
- Harbor writes each trial's result to `jobs/<job-name>/<trial-name>/result.json`,
  a serialized `harbor.models.trial.result.TrialResult`. We parse it with that same
  pydantic model (do NOT hand-roll JSON parsing — use the model).
  - Tokens + cost: `TrialResult.compute_token_cost_totals()`
    -> `(n_input, n_cache, n_output, cost_usd)`.
  - **Latency** (what the user waits): `agent_execution` `TimingInfo`
    (`finished_at - started_at`). This is the model call duration and is what we bill
    the developer's waiting time against.
  - Total wall time: top-level `started_at`/`finished_at`.
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

Direct Harbor invocation (what `make bench` runs under the hood):

```
harbor run -p tasks/hi -a hi_bench.agent:HiAgent \
  -m anthropic/claude-haiku-4-5-20251001 -m openai/gpt-... \
  -k 5 --disable-verification -o jobs --job-name <name> --env-file .env -y
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
