"""Money math. Integers only (micro-USD); never floats.

Token categories (provider-style semantics):
  input_tokens         total prompt tokens, INCLUDING the cached ones
  cached_input_tokens  subset of input_tokens served from cache -> cheaper
  output_tokens        generated tokens
  reasoning_tokens     hidden 'thinking' tokens -> billed AS OUTPUT

So categories are NOT summed blindly:
  fresh_input = input - cached          (cached must not be billed twice)
  billable_output = output + reasoning
Rounding: each category is rounded UP to the next micro-USD once, on the totals
(monthly totals are priced once; we never sum per-event rounded values).
"""
from dataclasses import dataclass

from app.config import MICRO, PRICING


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def fmt_usd(micro: int) -> str:
    sign = "-" if micro < 0 else ""
    micro = abs(micro)
    return f"{sign}${micro // MICRO}.{micro % MICRO:06d}"


def total_tokens(input_tokens: int, output_tokens: int, reasoning_tokens: int) -> int:
    """Quota unit 'AI tokens' = everything processed. Cached is already inside input."""
    return input_tokens + output_tokens + reasoning_tokens


@dataclass(frozen=True)
class CostBreakdown:
    api_calls_micro: int
    fresh_input_micro: int
    cached_input_micro: int
    output_micro: int
    reasoning_micro: int

    @property
    def tokens_micro(self) -> int:
        return self.fresh_input_micro + self.cached_input_micro + self.output_micro + self.reasoning_micro

    @property
    def total_micro(self) -> int:
        return self.api_calls_micro + self.tokens_micro

    def as_dict(self) -> dict:
        return {
            "api_calls_micro_usd": self.api_calls_micro,
            "fresh_input_micro_usd": self.fresh_input_micro,
            "cached_input_micro_usd": self.cached_input_micro,
            "output_micro_usd": self.output_micro,
            "reasoning_micro_usd": self.reasoning_micro,
            "tokens_micro_usd": self.tokens_micro,
            "total_usage_micro_usd": self.total_micro,
            "total_usage_display": fmt_usd(self.total_micro),
        }


def compute_cost(api_calls: int, input_tokens: int, cached_input_tokens: int,
                 output_tokens: int, reasoning_tokens: int) -> CostBreakdown:
    if cached_input_tokens > input_tokens:
        raise ValueError("cached_input_tokens cannot exceed input_tokens")
    if min(api_calls, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens) < 0:
        raise ValueError("negative usage")
    fresh = input_tokens - cached_input_tokens
    p = PRICING
    return CostBreakdown(
        api_calls_micro=api_calls * p.api_call_micro,
        fresh_input_micro=ceil_div(fresh * p.input_per_mtok, MICRO),
        cached_input_micro=ceil_div(cached_input_tokens * p.cached_input_per_mtok, MICRO),
        output_micro=ceil_div(output_tokens * p.output_per_mtok, MICRO),
        reasoning_micro=ceil_div(reasoning_tokens * p.reasoning_per_mtok, MICRO),
    )
