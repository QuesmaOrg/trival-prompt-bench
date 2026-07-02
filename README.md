# hi-bench — what a trivial LLM request really costs

A tiny cost/latency benchmark on the [Harbor](https://github.com/harbor-framework/harbor)
framework. It sends a trivial prompt to a set of models and prices each run as:

```
total_cost = LLM API cost + user waiting cost
user waiting cost = latency_seconds × dev_salary_per_second
```

The waiting term charges the developer's time spent watching the spinner (average US
developer salary, configurable). The point it makes: **for cheap requests, your time
is the cost, not the tokens.**

## 📊 Results

**Open [`report.html`](report.html)** for the full interactive report — one stacked-bar
graph per task (🟧 time wasted + 🟦 token cost), per-model tables, and hover details.

Latest run — 4 Anthropic models, 5 runs each, 2 tasks (40 trials, 0 errors),
salary $120k/yr = $0.016026/sec:

### Task "Hi"
| Model | avg latency | LLM $/run | waiting $/run | **total $/run** |
|---|--:|--:|--:|--:|
| claude-haiku-4-5 | 1.01s | $0.000087 | $0.016251 | **$0.016338** |
| claude-opus-4-8  | 1.51s | $0.000395 | $0.024265 | **$0.024660** |
| claude-sonnet-5  | 2.63s | $0.000225 | $0.042164 | **$0.042389** |
| claude-fable-5   | 4.84s | $0.001630 | $0.077609 | **$0.079239** |

### Task "Thank you"
| Model | avg latency | LLM $/run | waiting $/run | **total $/run** |
|---|--:|--:|--:|--:|
| claude-haiku-4-5 | 1.07s | $0.000114 | $0.017118 | **$0.017232** |
| claude-sonnet-5  | 1.52s | $0.000396 | $0.024282 | **$0.024678** |
| claude-opus-4-8  | 2.93s | $0.000645 | $0.046893 | **$0.047538** |
| claude-fable-5   | 5.19s | $0.004390 | $0.083140 | **$0.087530** |

**Overall spend across all runs: $1.70**

### What the numbers say
- **Latency dominates, not tokens.** Waiting cost is **150–187×** the API cost. The
  total-cost ranking is essentially a latency ranking.
- **Haiku wins both tasks** by being the fastest — cheapest total cost by a wide margin.
- **Ranking flips between tasks.** Opus beat Sonnet on "Hi" but lost on "Thank you"
  (its p95 spiked to 7.84s). Same models, different order — which is exactly why the
  report draws a separate graph per task.
- **Fable 5 is the outlier** — slowest and priciest, and it wrote the longest replies
  (85.8 output tokens on "Thank you" vs ~15–25 for the others).

> Numbers are a point-in-time latency sample; network/load shifts them run to run.
> The salary assumption drives the totals — retune `config.toml [cost]` and re-run
> `make report-html` (no re-benchmarking needed; raw sqlite data is assumption-free).

## Run it yourself

```bash
make setup                     # .venv (uv, Python 3.12) + harbor
cp .env.example .env           # add ANTHROPIC_API_KEY (+ others)
$EDITOR config.toml            # pick models (the "test suite") + salary
make bench                     # run every task × every model, store to sqlite
make report-html               # regenerate report.html
```

**Requirements:** Docker running (Harbor builds a container per trial) and API keys
for the providers you benchmark. No keys? `make smoke` runs the whole pipeline offline
with `mock/*` models.

## How it works

```
tasks/<name>/          Harbor task — instruction.md is the prompt, verifier disabled
hi_bench/agent.py      HiAgent: a custom Harbor agent making ONE LiteLLM call
hi_bench/run.py        Runs every task under tasks/ across the model list, then ingests
hi_bench/ingest.py     Parses jobs/<job>/<trial>/result.json → sqlite
hi_bench/report.py     Text report + writes report.html / report.txt
hi_bench/report_html.py Self-contained HTML: one stacked-bar graph per task
data/hi_bench.db       sqlite results (git-ignored)
report.html            Committed report artifact
```

Add a task by copying a `tasks/<name>/` dir and changing `instruction.md` — the runner
discovers it automatically and the report gains a graph for it, no code changes.

## Make targets

| Target | Does |
|---|---|
| `make setup` | Create the venv and install harbor + this package |
| `make smoke` | End-to-end run with `mock/*` models — offline, no keys |
| `make bench` | Run every task × every model, ingest, and report |
| `make report` | Print the text report from sqlite |
| `make report-html` | Write `report.html` (one graph per task) |
| `make report-file` | Write `report.txt` |
| `make clean` | Remove `jobs/` and the sqlite database |

## Notes

- Model names are LiteLLM identifiers (`provider/model`). If a model is missing from
  LiteLLM's pricing table the run still records latency/tokens, but `LLM $/run` is blank.
- Python is pinned to 3.12; some Harbor dependencies lack 3.14 wheels.
- See [`CLAUDE.md`](CLAUDE.md) for implementation notes.
