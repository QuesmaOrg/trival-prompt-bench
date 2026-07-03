"""Load and expose trivial-prompt-bench configuration from config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")


@dataclass(frozen=True)
class Config:
    attempts: int
    concurrency: int
    models: list[str]
    mock_models: list[str]
    annual_salary_usd: float
    work_hours_per_year: float

    @property
    def salary_usd_per_second(self) -> float:
        return self.annual_salary_usd / (self.work_hours_per_year * 3600.0)


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Config:
    data = tomllib.loads(Path(path).read_text())
    bench = data.get("bench", {})
    models = data.get("models", {})
    cost = data.get("cost", {})
    return Config(
        attempts=int(bench.get("attempts", 5)),
        concurrency=int(bench.get("concurrency", 4)),
        models=list(models.get("list", [])),
        mock_models=list(models.get("mock_list", [])),
        annual_salary_usd=float(cost.get("annual_salary_usd", 120000)),
        work_hours_per_year=float(cost.get("work_hours_per_year", 2080)),
    )
