# hi-bench — LLM cost/latency benchmark on the Harbor framework.
# Run `make help` for the target list.

VENV       := .venv
PYTHON_VER := 3.12
BIN        := $(CURDIR)/$(VENV)/bin
PY         := $(BIN)/python
# Ensure the venv's `harbor` CLI (shelled out to by hi_bench.run) is on PATH.
export PATH := $(BIN):$(PATH)

.DEFAULT_GOAL := help

.PHONY: help setup smoke bench ingest report report-file report-html clean distclean check-docker

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

$(VENV): pyproject.toml ## Create the virtualenv (uv, pinned Python)
	uv venv --python $(PYTHON_VER) $(VENV)

setup: $(VENV) ## Create venv and install harbor + this package (editable)
	uv pip install --python $(PY) -e .
	@echo "Setup complete. Copy .env.example to .env and add API keys for real runs."

smoke: ## End-to-end run with mock models — no API keys, no network (needs Docker)
	$(PY) -m hi_bench.run --mock
	@$(MAKE) --no-print-directory report

bench: check-docker ## Run the real model list from config.toml, ingest into sqlite
	$(PY) -m hi_bench.run
	@$(MAKE) --no-print-directory report

ingest: ## Ingest a specific job dir: make ingest JOB=jobs/<name>
	@test -n "$(JOB)" || (echo "Usage: make ingest JOB=jobs/<name>" && exit 1)
	$(PY) -m hi_bench.ingest $(JOB)

report: ## Print the cost/latency report from sqlite
	$(PY) -m hi_bench.report

report-file: ## Write the report to report.txt (and print it)
	$(PY) -m hi_bench.report --out report.txt

report-html: ## Write an HTML report with a stacked bar chart to report.html
	$(PY) -m hi_bench.report --html report.html
	@echo "Open report.html in a browser."

check-docker: ## Verify Docker is available (Harbor builds a container per trial)
	@docker info >/dev/null 2>&1 || (echo "Docker is not running. Start Docker and retry." && exit 1)

clean: ## Remove Harbor job outputs and the sqlite database
	rm -rf jobs data/hi_bench.db

distclean: clean ## Also remove the virtualenv
	rm -rf $(VENV)
