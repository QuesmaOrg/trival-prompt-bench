"""Self-contained HTML report for trivial-prompt-bench.

Renders one horizontal **stacked bar per model**, split into two components that
sum to the average total cost per run:

    - user waiting cost  (latency x dev-salary/second)   -> orange
    - LLM cost           (API tokens)                     -> blue

The page is fully self-contained (inline CSS/JS/SVG, no network) so it opens
offline. Colors are the dataviz reference palette's categorical slots 1 (blue)
and 8 (orange); the pair was validated for CVD/contrast in light and dark modes.
Design notes: legend always present (2 series), 2px surface gap between the
stacked segments, 4px rounded outer end, per-segment hover tooltip, and a full
data table (the "relief" channel so the tiny LLM sliver is always legible).
"""

from __future__ import annotations

import json

# Template uses __TOKEN__ placeholders (str.replace) to avoid brace-escaping the CSS/JS.
_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>trivial-prompt-bench report</title>
<style>
  :root {
    --page: #f9f9f7; --surface: #fcfcfb;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --border: rgba(11,11,11,0.10);
    --series-wait: #eb6834; --series-llm: #2a78d6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --page: #0d0d0d; --surface: #1a1a19;
      --text-primary: #fff; --text-secondary: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --baseline: #383835; --border: rgba(255,255,255,0.10);
      --series-wait: #d95926; --series-llm: #3987e5;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--page); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    line-height: 1.5; padding: 32px 20px;
  }
  .wrap { max-width: 900px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--text-secondary); font-size: 14px; margin: 0 0 2px; }
  .assume { color: var(--muted); font-size: 13px; margin: 0 0 20px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 22px 22px 10px; margin-bottom: 22px;
  }
  .legend { display: flex; gap: 24px; margin: 0 0 18px; font-size: 14px; color: var(--text-secondary); }
  .legend span { display: inline-flex; align-items: center; gap: 7px; }
  .legend b { font-weight: 800; color: var(--text-primary); }
  .legend .note { color: var(--muted); font-weight: 400; font-size: 12px; }
  .swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
  .sw-wait { background: var(--series-wait); }
  .sw-llm { background: var(--series-llm); }

  .row { display: grid; grid-template-columns: 210px 1fr 96px; align-items: center; gap: 12px; margin: 14px 0; }
  .row .name { font-size: 13px; color: var(--text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .track { position: relative; height: 26px; }
  .bar { display: flex; height: 100%; align-items: stretch; }
  .seg { height: 100%; cursor: pointer; transition: filter .12s; }
  .seg:hover { filter: brightness(1.08); }
  .seg-wait { background: var(--series-wait); border-radius: 4px 0 0 4px; }
  .seg-llm  { background: var(--series-llm);  border-radius: 0 4px 4px 0; margin-left: 2px; min-width: 3px; }
  .row .total { font-size: 13px; text-align: right; font-variant-numeric: tabular-nums; color: var(--text-primary); }
  .axis { grid-column: 2 / 3; border-top: 1px solid var(--baseline); height: 1px; margin-top: 2px; }
  .ticks { grid-column: 2 / 3; display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; margin-top: 3px; font-variant-numeric: tabular-nums; }

  table { border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 6px; }
  th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--grid); font-variant-numeric: tabular-nums; }
  th:first-child, td:first-child { text-align: left; font-variant-numeric: normal; }
  th { color: var(--muted); font-weight: 600; font-size: 12px; }
  tfoot td { font-weight: 700; border-top: 2px solid var(--baseline); border-bottom: none; }

  #tip {
    position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s;
    background: var(--text-primary); color: var(--surface); padding: 8px 10px;
    border-radius: 8px; font-size: 12px; line-height: 1.45; max-width: 260px;
    box-shadow: 0 4px 16px rgba(0,0,0,.25); z-index: 10;
  }
  #tip b { font-weight: 700; }
  .insight { font-size: 13px; color: var(--text-secondary); margin: 4px 0 0; }
  .foot { color: var(--muted); font-size: 12px; margin-top: 24px; }
  .task-title { font-size: 16px; margin: 30px 0 10px; }
  .task-title .prompt { color: var(--text-secondary); font-weight: 400; font-size: 14px; }
  .controls { display: flex; align-items: center; gap: 24px; margin: 0 0 18px; flex-wrap: wrap; }
  .toggle { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px;
            color: var(--text-primary); user-select: none; }
  .toggle input { width: 16px; height: 16px; accent-color: var(--series-wait); cursor: pointer; }
  .legend .dim { opacity: .4; }
  .failure { background: var(--surface); border: 1px solid var(--border);
             border-left: 3px solid var(--series-wait); border-radius: 10px;
             padding: 12px 16px; margin: -8px 0 22px; font-size: 13px;
             color: var(--text-secondary); line-height: 1.5; }
  .failure b { color: var(--text-primary); }
</style>
</head>
<body>
<div class="wrap">
  <h1>trivial-prompt-bench &mdash; cost per model, per task</h1>
  <p class="sub" id="sub"></p>
  <p class="assume" id="assume"></p>

  <div class="controls">
    <label class="toggle"><input type="checkbox" id="toggle-time"> Show <b>time wasted</b> (waiting cost)</label>
    <div class="legend">
      <span><i class="swatch sw-llm"></i> <b>Token wasted</b> <span class="note">LLM API cost</span></span>
      <span id="lg-wait"><i class="swatch sw-wait"></i> <b>Time wasted</b> <span class="note">latency &times; salary</span></span>
    </div>
  </div>

  <div id="tasks"></div>
  <p class="foot" id="foot"></p>
</div>
<div id="tip"></div>
<script>
const DATA = __DATA__;
const usd = x => x == null ? "–" : "$" + x.toLocaleString(undefined, {minimumFractionDigits: 6, maximumFractionDigits: 6});
const secs = x => x == null ? "–" : x.toFixed(2) + "s";
const num = x => x == null ? "–" : x.toLocaleString(undefined, {maximumFractionDigits: 1});
const pct = x => x == null ? "–" : Math.round(x * 100) + "%";

document.getElementById("assume").textContent =
  "Salary assumption: $" + DATA.meta.annual_salary_usd.toLocaleString() + "/yr over " +
  DATA.meta.work_hours_per_year.toLocaleString() + " work-h/yr = $" +
  DATA.meta.salary_per_second.toFixed(6) + " per second of waiting.";

const tip = document.getElementById("tip");
function showTip(e, html) { tip.innerHTML = html; tip.style.opacity = 1;
  tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 270) + "px";
  tip.style.top = (e.clientY + 14) + "px"; }
function hideTip() { tip.style.opacity = 0; }
function el(tag, cls) { const d = document.createElement(tag); if (cls) d.className = cls; return d; }

// showTime=false (default): bars show LLM cost only. showTime=true: stack the
// "time wasted" (waiting cost) segment on top so the bar equals total cost per run.
function renderTask(container, task, showTime) {
  const rows = task.models;
  // The bar metric is LLM-only by default, or the full total when time is shown.
  const metric = r => showTime ? (r.total || 0) : (r.llm || 0);
  const maxVal = Math.max(...rows.map(metric), 1e-9);

  const title = el("h2", "task-title");
  title.innerHTML = task.task + " <span class='prompt'>&middot; prompt &ldquo;" + task.prompt + "&rdquo;</span>";
  container.appendChild(title);

  // --- chart card ---
  const chartCard = el("div", "card");
  const chart = el("div");
  for (const r of rows) {
    const wait = r.waiting || 0, llm = r.llm || 0, total = r.total || 0;
    const row = el("div", "row");
    const name = el("div", "name"); name.textContent = r.model; name.title = r.model;
    const track = el("div", "track");
    const bar = el("div", "bar"); bar.style.width = (100 * metric(r) / maxVal) + "%";

    const lSeg = el("div", "seg seg-llm");
    lSeg.onmousemove = e => showTip(e, "<b>Token wasted</b><br>" + usd(llm) +
      (showTime && total > 0 ? " &middot; " + (100 * llm / total).toFixed(1) + "% of total" : ""));
    lSeg.onmouseleave = hideTip;

    if (showTime) {
      // stacked: [time wasted | llm] == total
      const wSeg = el("div", "seg seg-wait");
      wSeg.style.flexBasis = (100 * wait / total) + "%";
      wSeg.onmousemove = e => showTip(e, "<b>Time wasted</b><br>" + usd(wait) + " &middot; " +
        (total > 0 ? (100 * wait / total).toFixed(1) + "% of total" : "–") + "<br>latency " + secs(r.avg_latency));
      wSeg.onmouseleave = hideTip;
      lSeg.style.flexBasis = (100 * llm / total) + "%";
      bar.appendChild(wSeg); bar.appendChild(lSeg);
    } else {
      // LLM cost only: single segment, both ends rounded, no gap
      lSeg.style.flexBasis = "100%";
      lSeg.style.borderRadius = "4px";
      lSeg.style.marginLeft = "0";
      bar.appendChild(lSeg);
    }
    track.appendChild(bar);
    const tot = el("div", "total"); tot.textContent = usd(metric(r));
    row.appendChild(name); row.appendChild(track); row.appendChild(tot);
    chart.appendChild(row);
  }
  const axis = el("div", "axis");
  const ticks = el("div", "ticks");
  ticks.innerHTML = "<span>$0</span><span>" + usd(maxVal / 2) + "</span><span>" + usd(maxVal) + "</span>";
  chart.appendChild(axis); chart.appendChild(ticks);
  chartCard.appendChild(chart);

  const cheapest = rows[0];
  if (showTime && cheapest && cheapest.llm && cheapest.waiting) {
    const mult = Math.round(cheapest.waiting / cheapest.llm);
    const insight = el("p", "insight");
    insight.textContent = "Waiting cost dwarfs API cost — about " + mult +
      "× larger on " + cheapest.model + ". Latency, not token price, drives total cost.";
    chartCard.appendChild(insight);
  }
  container.appendChild(chartCard);

  // --- table card (unchanged by the toggle; always shows every column) ---
  const tableCard = el("div", "card");
  const tbl = el("table");
  tbl.innerHTML = "<thead><tr><th>model</th><th>runs</th><th>err</th><th>pass</th><th>tool calls</th><th>avg latency</th>" +
    "<th>p95</th><th>avg tokens out</th><th>LLM $/run</th><th>waiting $/run</th><th>total $/run</th></tr></thead>";
  const tbody = el("tbody");
  for (const r of rows) {
    const tr = el("tr");
    tr.innerHTML = "<td>" + r.model + "</td><td>" + r.n_runs + "</td><td>" + r.n_errors +
      "</td><td>" + pct(r.reward) + "</td><td>" + num(r.tool_calls) + "</td><td>" + secs(r.avg_latency) + "</td><td>" + secs(r.p95_latency) +
      "</td><td>" + num(r.avg_tokens_out) + "</td><td>" + usd(r.llm) +
      "</td><td>" + usd(r.waiting) + "</td><td>" + usd(r.total) + "</td>";
    tbody.appendChild(tr);
  }
  tbl.appendChild(tbody);
  const taskSpend = rows.reduce((a, r) => a + (r.sum_total || 0), 0);
  const tfoot = el("tfoot");
  tfoot.innerHTML = "<tr><td>task spend (all runs)</td><td colspan='9'></td><td>" + usd(taskSpend) + "</td></tr>";
  tbl.appendChild(tfoot);
  tableCard.appendChild(tbl);
  container.appendChild(tableCard);

  // --- failure analysis (below the table, only if this task had failures) ---
  if (task.analysis) {
    const fa = el("div", "failure");
    fa.innerHTML = "<b>Failure analysis</b> <span class='note'>" + task.n_failures +
      " failed</span><br>" + task.analysis;
    container.appendChild(fa);
  }
}

const tasksEl = document.getElementById("tasks");
const subEl = document.getElementById("sub");
const waitLegend = document.getElementById("lg-wait");

function renderAll(showTime) {
  subEl.textContent = showTime
    ? "Average total cost per run (LLM cost + time wasted) · one graph per task"
    : "Average LLM API cost per run · one graph per task · toggle to add waiting-time cost";
  waitLegend.classList.toggle("dim", !showTime);
  tasksEl.innerHTML = "";
  for (const task of DATA.tasks) renderTask(tasksEl, task, showTime);
}

const toggle = document.getElementById("toggle-time");
toggle.addEventListener("change", () => renderAll(toggle.checked));
renderAll(false);  // default: LLM cost only

const grand = DATA.tasks.reduce((a, t) => a + t.models.reduce((b, r) => b + (r.sum_total || 0), 0), 0);
document.getElementById("foot").textContent = "Generated " + DATA.meta.generated_at + " from " +
  DATA.meta.total_runs + " runs across " + DATA.tasks.length + " task(s). " +
  "OVERALL spend (all tasks, all runs): " + usd(grand) + ". total $/run = LLM cost + waiting cost.";
</script>
</body>
</html>
"""


def _model_dict(s) -> dict:
    return {
        "model": s.model,
        "n_runs": s.n_runs,
        "n_errors": s.n_errors,
        "avg_latency": s.avg_latency,
        "p95_latency": s.p95_latency,
        "avg_tokens_out": s.avg_tokens_out,
        "llm": s.avg_model_cost,
        "waiting": s.avg_waiting_cost,
        "total": s.avg_total_cost,
        "sum_total": s.sum_total_cost,
        "reward": s.avg_reward,
        "tool_calls": s.avg_tool_calls,
    }


def render_html(tasks, cfg, generated_at: str, analyses: dict | None = None) -> str:
    """Render the report. ``tasks`` is a list of report.TaskStats (one per task).

    ``analyses`` maps task_name -> {analysis, n_failures, ...} (from the DB); the
    matching note is rendered below each task's table.
    """
    analyses = analyses or {}
    tasks_data = [
        {
            "task": t.task,
            "prompt": t.prompt or "",
            "models": [_model_dict(s) for s in t.models],
            "analysis": (analyses.get(t.task) or {}).get("analysis"),
            "n_failures": (analyses.get(t.task) or {}).get("n_failures"),
        }
        for t in tasks
    ]
    total_runs = sum(s.n_runs for t in tasks for s in t.models)
    data = {
        "meta": {
            "annual_salary_usd": cfg.annual_salary_usd,
            "work_hours_per_year": cfg.work_hours_per_year,
            "salary_per_second": cfg.salary_usd_per_second,
            "generated_at": generated_at,
            "total_runs": total_runs,
        },
        "tasks": tasks_data,
    }
    return _TEMPLATE.replace("__DATA__", json.dumps(data))
