"""Anthropic API wrapper. Model name is configurable via CLAIMTRACE_MODEL.

Returns text plus a usage dict so every call's token counts land on the
node span that made it.
"""

from __future__ import annotations

import time

from . import config


def complete(system: str, user: str, max_tokens: int = 4096) -> tuple[str, dict]:
    import anthropic

    client = anthropic.Anthropic()
    started = time.monotonic()
    response = client.messages.create(
        model=config.model_name(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = "".join(block.text for block in response.content if block.type == "text")
    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "llm_ms": elapsed_ms,
    }
    return text, usage
