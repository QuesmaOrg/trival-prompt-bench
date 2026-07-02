FROM ubuntu:24.04

# This task's agent (hi_bench.agent:HiAgent) runs in the Harbor host process and
# talks to the model over the network via LiteLLM, so it needs nothing from this
# container. Harbor still builds and starts an environment per trial; we keep it
# minimal. Build time here is infra overhead and is NOT counted as user waiting time.

WORKDIR /app
