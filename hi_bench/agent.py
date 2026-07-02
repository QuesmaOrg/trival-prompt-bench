"""HiAgent — a minimal Harbor agent that answers a single prompt with one LLM call.

This is the whole "task" from the model's point of view: take the instruction
(``Hi``) and produce one completion. We deliberately do NOT run a tool-use loop or
touch the environment container — we want to measure the cost and latency of the
*simplest possible* request.

Harbor wraps ``run()`` in an ``agent_execution`` timing span and persists the
populated :class:`AgentContext` (tokens + cost) into the trial's ``result.json``,
which ``hi_bench.ingest`` later reads.

Mock models
-----------
Any model named ``mock/<name>`` short-circuits the network entirely and returns a
canned answer with synthetic tokens/cost. This lets the full
Harbor -> sqlite -> report pipeline be exercised offline, with no API keys.
"""

from __future__ import annotations

import asyncio
from typing import override

import litellm
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext


class HiAgent(BaseAgent):
    """Sends the task instruction to ``self.model_name`` once and records usage."""

    # We never touch the container, so we are trivially OS-agnostic.
    SUPPORTS_WINDOWS: bool = True

    @staticmethod
    @override
    def name() -> str:
        return "hi-agent"

    @override
    def version(self) -> str:
        return "1.0.0"

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        # Nothing to install: the agent runs in the Harbor host process and calls
        # the model directly over the network via LiteLLM.
        return None

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError(
                "HiAgent requires a model (-m). Example: -m anthropic/claude-haiku-4-5-20251001"
            )

        prompt = instruction.strip() or "Hi"
        self.logger.info("HiAgent prompting %s with %r", self.model_name, prompt)

        if self.model_name.startswith("mock/"):
            await self._run_mock(prompt, context)
            return

        await self._run_real(prompt, context)

    async def _run_real(self, prompt: str, context: AgentContext) -> None:
        response = await litellm.acompletion(
            model=self.model_name,
            messages=[{"role": "user", "content": prompt}],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            # LiteLLM normalizes to OpenAI-style usage. prompt_tokens is the total
            # input (including any cached tokens), matching AgentContext semantics.
            context.n_input_tokens = getattr(usage, "prompt_tokens", None)
            context.n_output_tokens = getattr(usage, "completion_tokens", None)
            context.n_cache_tokens = _extract_cache_tokens(usage)

        try:
            context.cost_usd = litellm.completion_cost(completion_response=response)
        except Exception as exc:  # pricing table may lack an entry for the model
            self.logger.warning("Could not compute cost for %s: %s", self.model_name, exc)
            context.cost_usd = None

        answer = _first_message_content(response)
        context.metadata = {"prompt": prompt, "response_text": answer}
        self.logger.info("HiAgent got %d chars back", len(answer or ""))

    async def _run_mock(self, prompt: str, context: AgentContext) -> None:
        # Deterministic, offline stand-in so the pipeline is testable without keys.
        # A tiny sleep gives a non-zero, realistic-ish latency to bill against.
        await asyncio.sleep(0.15)
        answer = "Hello! How can I help you today?"
        context.n_input_tokens = 1
        context.n_cache_tokens = 0
        context.n_output_tokens = 9
        # Pretend this mock costs a hair — enough to exercise the cost columns.
        context.cost_usd = 0.0000123
        context.metadata = {"prompt": prompt, "response_text": answer, "mock": True}


def _extract_cache_tokens(usage: object) -> int | None:
    """Best-effort read of cached input tokens across provider shapes."""
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached is not None:
            return cached
    # Some providers expose it flat.
    return getattr(usage, "cache_read_input_tokens", None)


def _first_message_content(response: object) -> str | None:
    try:
        return response.choices[0].message.content  # type: ignore[attr-defined]
    except Exception:
        return None
