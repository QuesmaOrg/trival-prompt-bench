# trivial-prompt-bench — what a trivial LLM request really costs

A tiny cost/latency benchmark on the [Harbor](https://github.com/harbor-framework/harbor)
framework. It runs a trivial prompt through a terminal agent (Terminus) across many
models (routed via OpenRouter) and prices each run as:

```
total_cost = LLM API cost + user waiting cost
user waiting cost = latency_seconds × dev_salary_per_second
```

The waiting term charges the developer's time spent watching the spinner (average US
developer salary, configurable). The point it makes: **for cheap requests, your time
is the cost, not the tokens.**

## 📊 Results

The full interactive report is [`report.html`](report.html) — a self-contained page
committed in the repo (one bar graph per task, LLM cost by default with a toggle to add
🟧 time wasted, per-model tables, and hover details).

> GitHub shows `.html` files as raw source, and external preview services
> (githack, htmlpreview) can't read this **private** repo. To view it **rendered**,
> open it locally after cloning:
>
> ```bash
> open report.html          # macOS   (xdg-open on Linux)
> ```
>
> Or regenerate it fresh from the data with `make report-html`.

Latest run — **14 models across 6 providers** (Anthropic, OpenAI, Google, xAI, and the
Chinese labs DeepSeek/Alibaba/Moonshot/Zhipu/MiniMax), all via OpenRouter, 5 runs each
on 4 tasks = **280 agentic trials** through the Terminus agent in an identical git-repo
environment. Salary $120k/yr = $0.016026/sec. `err` = failed runs (excluded from
averages); `total $/run` = LLM cost + waiting cost; latency is the transcript LLM time.

### Model leaderboard (averaged across the 4 tasks, cheapest first)
| Model | err/20 | avg latency | avg total $/run |
|---|--:|--:|--:|
| openai/gpt-5.4-mini | 0 | 9.3s | **$0.155** |
| openai/gpt-5.5 | 0 | 11.8s | **$0.230** |
| x-ai/grok-4.3 | 0 | 17.6s | **$0.288** |
| google/gemini-3.1-pro-preview | 5 | 19.5s | **$0.332** |
| anthropic/claude-fable-5 | 5 | 20.8s | **$0.383** |
| anthropic/claude-haiku-4.5 | 4 | 23.5s | **$0.405** |
| minimax/minimax-m3 | 6 | 26.7s | **$0.431** |
| google/gemini-3.5-flash | 1 | 25.8s | **$0.506** |
| deepseek/deepseek-v4-pro | 4 | 31.2s | **$0.507** |
| z-ai/glm-5.2 | 1 | 36.5s | **$0.594** |
| anthropic/claude-opus-4.8 | 0 | 32.1s | **$0.594** |
| moonshotai/kimi-k2.6 | 4 | 37.6s | **$0.619** |
| anthropic/claude-sonnet-5 | 1 | 49.6s | **$0.854** |
| qwen/qwen3.7-max | 1 | 54.7s | **$0.897** |

### Per-task spend & reliability
| Task | prompt | spend | errors | cheapest | priciest |
|---|---|--:|--:|---|---|
| `commit` | "commit" (graded, 100% pass) | $22.22 | 0 | gpt-5.4-mini $0.126 | fable-5 $0.673 |
| `hi` | "Hi" | $24.21 | 8 | gpt-5.4-mini $0.076 | sonnet-5 $0.841 |
| `thank-you` | "Thank you" | $26.79 | 3 | gpt-5.4-mini $0.053 | qwen3.7-max $1.221 |
| `wtf` | "WTF" | $42.15 | 21 | grok-4.3 $0.338 | qwen3.7-max $1.388 |

**Overall spend across all 280 runs: $115.37**

### What the numbers say
- **GPT-5.4-mini wins outright** — cheapest and fastest overall (9.3s), zero errors,
  and the cheapest model on *every* task. GPT-5.5 and Grok-4.3 follow.
- **Latency dominates the bill** (waiting cost ≫ token cost), so the ranking is
  essentially a speed ranking: the slow frontier models (qwen3.7-max 55s, sonnet-5 50s)
  are the most expensive despite reasonable token prices.
- **Prompt clarity drives cost and reliability.** `commit` (a clear instruction) had
  **0 errors** across all 14 models and was the cheapest task; `wtf` (a bare
  interjection) had **21/70 failures** and cost nearly 2× as much — models thrash or
  time out when there's nothing concrete to do.
- **Reliability varies widely.** gpt-5.4-mini, gpt-5.5, grok-4.3, and opus-4.8 had zero
  errors; minimax-m3 (6), gemini-3.1-pro (5), and fable-5 (5) were the most failure-prone.

> Numbers are a point-in-time sample; network/load and agent nondeterminism shift them
> run to run (n=5 per task, errored runs excluded from averages). The salary assumption
> drives the totals — retune `config.toml [cost]` and re-run `make report-html` (no
> re-benchmarking needed; raw sqlite data is assumption-free). `report.html` also shows,
> per task, **what each model actually did** with its tools and **why runs failed**.

## Run it yourself

```bash
make setup                     # .venv (uv, Python 3.12) + harbor
cp .env.example .env           # add OPENROUTER_API_KEY
$EDITOR config.toml            # pick models (the "test suite"), tasks + salary
make all                       # bench (run + ingest + failure analysis) + report.html
```

**Requirements:** Docker running (Harbor builds a container per trial) and an
`OPENROUTER_API_KEY` in `.env` (models are `openrouter/…` slugs). No key? `make smoke`
runs the whole pipeline offline with `mock/*` models.

## How it works

```
tasks/<name>/          Harbor task — instruction.md is the prompt; all tasks share the
                       same git-repo environment, so only the prompt differs
trivial_prompt_bench/run.py         Runs the configured tasks across the models, then ingests + analyzes
trivial_prompt_bench/ingest.py      Parses jobs/<job>/<trial>/result.json → sqlite
trivial_prompt_bench/analyze.py     Per-(task,model) tool-usage + failure analysis (one Claude call each)
trivial_prompt_bench/report.py      Text report + writes report.html / report.txt
trivial_prompt_bench/report_html.py Self-contained HTML: one graph per task
trivial_prompt_bench/agent.py       HiAgent — single-LLM-call agent, used only for `make smoke`
data/trivial_prompt_bench.db        sqlite results (committed)
report.html            Committed report artifact
```

Every task runs through the **Terminus** agent, so the measured cost/latency includes
the whole agent loop (agent overhead), even for a one-word prompt. Which tasks run by
default is set in `config.toml [bench].tasks`; the rest stay in the repo and run on
demand (`python -m trivial_prompt_bench.run --task tasks/<name>`).

## Make targets

| Target | Does |
|---|---|
| `make setup` | Create the venv and install harbor + this package |
| `make all` | Full pipeline: bench (run + ingest + failure analysis) + `report.html` |
| `make smoke` | End-to-end run with `mock/*` models — offline, no keys |
| `make bench` | Run the configured tasks × models, ingest, analyze, and report |
| `make analyze` | (Re)generate the per-(task,model) analysis from existing results |
| `make report` | Print the text report from sqlite |
| `make report-html` | Write `report.html` |
| `make clean` | Remove `jobs/` and the sqlite database |

## Notes

- Models are `openrouter/<slug>` identifiers; token cost comes from OpenRouter's
  returned `response_cost`, so it works even for models LiteLLM's price map doesn't know.
- Python is pinned to 3.12; some Harbor dependencies lack 3.14 wheels.
- See [`CLAUDE.md`](CLAUDE.md) for implementation notes.
