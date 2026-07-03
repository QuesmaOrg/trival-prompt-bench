# trivial-prompt-bench — what a trivial LLM request really costs

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

Latest run — 4 Anthropic models, 5 runs each, 4 tasks = **80 agentic trials** via the
Terminus agent, all in the same git-repo environment, salary $120k/yr = $0.016026/sec.
Columns: `err` = failed runs (excluded from averages), `tools` = avg tool calls,
`lat` = transcript LLM latency, `total` = LLM + waiting cost per run.

### `commit` — prompt "commit" (graded: **100% pass, all models**)
| Model | err | tools | avg lat | total $/run |
|---|--:|--:|--:|--:|
| claude-haiku-4-5 | 0 | 4.6 | 6.8s | **$0.1150** |
| claude-sonnet-5  | 0 | 9.6 | 15.3s | **$0.2796** |
| claude-opus-4-8  | 0 | 8.6 | 21.6s | **$0.4070** |
| claude-fable-5   | 0 | 7.2 | 31.1s | **$0.5856** |

### `hi` — prompt "Hi"
| Model | err | tools | avg lat | total $/run |
|---|--:|--:|--:|--:|
| claude-fable-5   | 0 | 2.8 | 10.7s | **$0.2037** |
| claude-opus-4-8  | 0 | 10.2 | 23.4s | **$0.4369** |
| claude-sonnet-5  | 0 | 32.2 | 45.5s | **$0.8300** |
| claude-haiku-4-5 | 4 | 22.0 | 48.4s | **$0.8344** |

### `thank-you` — prompt "Thank you"
| Model | err | tools | avg lat | total $/run |
|---|--:|--:|--:|--:|
| claude-haiku-4-5 | 2 | 5.7 | 12.4s | **$0.2119** |
| claude-fable-5   | 0 | 2.0 | 12.3s | **$0.2252** |
| claude-opus-4-8  | 0 | 9.6 | 26.5s | **$0.4925** |
| claude-sonnet-5  | 0 | 15.6 | 33.7s | **$0.6089** |

### `wtf` — prompt "WTF"
| Model | err | tools | avg lat | total $/run |
|---|--:|--:|--:|--:|
| claude-haiku-4-5 | 0 | 18.0 | 21.2s | **$0.3659** |
| claude-sonnet-5  | 0 | 33.4 | 54.2s | **$0.9843** |
| claude-opus-4-8  | 0 | 23.0 | 55.0s | **$1.0255** |
| claude-fable-5   | 5 | — | — | **failed 5/5** |

**Overall spend across all 80 runs: $34.27**

### What the numbers say
- **The prompt is the cost driver.** Same environment, same models — only the prompt
  differs, yet total cost per run swings **~10×** (from `commit` at $0.12 to `wtf` at
  $1.03). A clear instruction (`commit`) is cheap and 100% reliable; a bare
  interjection (`WTF`) makes the agent thrash.
- **Under-specified prompts blow up.** `hi`/`wtf` drop the agent into a repo with a
  pending change and no clear task, so it explores: Sonnet spent **32 tool calls / 45s**
  on "Hi". More tool calls → more latency → more cost.
- **Latency still dominates** the bill (waiting cost ≫ token cost), so the ranking is
  essentially a latency/tool-call ranking.
- **Trivial prompts are flaky as agent tasks.** Haiku errored 4/5 on "Hi" and 2/5 on
  "Thank you"; **Fable failed all 5 "WTF" runs**. These are agent failures, not bench
  bugs (see the `err` column).

> Numbers are a point-in-time sample; network/load and agent nondeterminism shift them
> run to run (small n=5, and errored runs are excluded from averages). The salary
> assumption drives the totals — retune `config.toml [cost]` and re-run `make
> report-html` (no re-benchmarking needed; raw sqlite data is assumption-free).

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
trivial_prompt_bench/agent.py      HiAgent: a custom Harbor agent making ONE LiteLLM call
trivial_prompt_bench/run.py        Runs every task under tasks/ across the model list, then ingests
trivial_prompt_bench/ingest.py     Parses jobs/<job>/<trial>/result.json → sqlite
trivial_prompt_bench/report.py     Text report + writes report.html / report.txt
trivial_prompt_bench/report_html.py Self-contained HTML: one stacked-bar graph per task
data/trivial_prompt_bench.db       sqlite results (git-ignored)
report.html            Committed report artifact
```

## Contributing a task

Tasks are the unit of contribution. **Every task shares the same environment and
differs only in its prompt** — that's the whole design: the prompt is the one variable,
so differences in cost/latency are attributable to the prompt (and the model), not the
setup.

### Task structure

```
tasks/<name>/
├── task.toml              # name = "trivial-prompt-bench/<name>" (+ optional [metadata])
├── instruction.md         # THE PROMPT — the only file you change for a new task
├── environment/
│   ├── Dockerfile         # identical across tasks — do not edit (keeps tasks comparable)
│   └── build_repo.sh      # identical — builds the shared git project the agent runs in
├── tests/test.sh          # verifier; trivial tasks use the always-pass one
└── solution/solve.sh      # reference ("oracle") solution
```

Every task's container is the same: a small git repo with history and one uncommitted
change. The agent (Terminus by default) is handed your prompt inside that repo. The
runner **auto-discovers** any `tasks/*/task.toml`, so no code changes are needed.

### Add a task (clone the `hi` task, change the prompt, open a PR)

```bash
# 1. Clone the simplest task
cp -r tasks/hi tasks/goodbye

# 2. Change the prompt — this is the only content that differs between trivial tasks
echo "Goodbye" > tasks/goodbye/instruction.md

# 3. Rename the task so it's unique
#    edit tasks/goodbye/task.toml -> name = "trivial-prompt-bench/goodbye"

# 4. Leave environment/ and tests/ untouched (shared setup, always-pass verifier)

# 5. Run it and eyeball the report
make bench          # discovers goodbye automatically, runs it across all models
make report-html    # regenerate report.html — a new graph appears for your task

# 6. Commit and open a PR
git checkout -b task/goodbye
git add tasks/goodbye report.html
git commit -m "Add 'goodbye' task"
git push -u origin task/goodbye
gh pr create --fill
```

That's it for a trivial-prompt task. If your task needs the agent to *do* something
(like `commit` does), set `agent`/`verify` in `task.toml [metadata]` and write a real
`tests/test.sh` — see `tasks/commit/` as the worked example.

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
