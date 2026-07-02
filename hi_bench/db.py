"""sqlite storage for hi-bench runs.

One row per Harbor trial (i.e. per model attempt). All numbers stored here are raw
measurements — no salary/cost assumptions are baked in, so the report can be
re-computed with different assumptions without re-running the benchmark.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/hi_bench.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    trial_id        TEXT PRIMARY KEY,   -- Harbor TrialResult.id (UUID)
    job_name        TEXT,
    task_name       TEXT,
    prompt          TEXT,               -- the instruction sent to the model
    trial_name      TEXT,
    agent_name      TEXT,
    agent_version   TEXT,
    model           TEXT,               -- full LiteLLM id, e.g. anthropic/claude-...
    provider        TEXT,
    model_name      TEXT,
    n_input_tokens  INTEGER,
    n_cache_tokens  INTEGER,
    n_output_tokens INTEGER,
    model_cost_usd  REAL,               -- cost of the model call (LiteLLM pricing)
    agent_seconds   REAL,               -- model-call latency == user waiting time
    total_seconds   REAL,               -- full trial wall time (incl. env build)
    env_setup_seconds REAL,
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT,               -- exception type/message if the trial failed
    response_text   TEXT,
    ingested_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_model ON runs(model);
CREATE INDEX IF NOT EXISTS idx_runs_job ON runs(job_name);
"""

RUN_COLUMNS = [
    "trial_id", "job_name", "task_name", "prompt", "trial_name",
    "agent_name", "agent_version", "model", "provider", "model_name",
    "n_input_tokens", "n_cache_tokens", "n_output_tokens", "model_cost_usd",
    "agent_seconds", "total_seconds", "env_setup_seconds",
    "started_at", "finished_at", "error", "response_text", "ingested_at",
]


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating parent dirs + schema) a sqlite connection with row access."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a DB was first created (idempotent)."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(runs)")}
    for col in RUN_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} TEXT")
    conn.commit()


def upsert_run(conn: sqlite3.Connection, row: dict) -> None:
    """Insert or replace a run keyed by trial_id (idempotent re-ingest)."""
    values = [row.get(col) for col in RUN_COLUMNS]
    placeholders = ", ".join(["?"] * len(RUN_COLUMNS))
    cols = ", ".join(RUN_COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO runs ({cols}) VALUES ({placeholders})",
        values,
    )
