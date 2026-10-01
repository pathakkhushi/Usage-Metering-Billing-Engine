import pytest

from app.services.pricing import compute_cost, fmt_usd


def test_cached_input_is_cheaper_and_reasoning_bills_as_output():
    # input_tokens=5000 INCLUDES 4000 cached -> fresh = 1000
    c = compute_cost(api_calls=0, input_tokens=5000, cached_input_tokens=4000, output_tokens=500, reasoning_tokens=1500)
    assert c.fresh_input_micro == 1_000      # 1000 * $1.00/M
    assert c.cached_input_micro == 1_000     # 4000 * $0.25/M
    assert c.output_micro == 2_000           # 500  * $4.00/M
    assert c.reasoning_micro == 6_000        # 1500 * $4.00/M (== output rate)
    assert c.total_micro == 10_000 == c.output_micro + c.reasoning_micro + 2_000
    assert fmt_usd(c.total_micro) == "$0.010000"


def test_categories_are_not_naively_added():
    naive = compute_cost(0, 5000, 0, 0, 0).total_micro            # all 5000 as fresh input
    correct = compute_cost(0, 5000, 4000, 0, 0).total_micro       # 4000 of them cached
    assert correct < naive and (naive, correct) == (5_000, 2_000)


def test_api_call_price_and_rounding_up():
    assert compute_cost(10, 0, 0, 0, 0).api_calls_micro == 20_000
    assert compute_cost(0, 1, 0, 0, 0).fresh_input_micro == 1     # 1 token = 1 micro-USD
    assert compute_cost(0, 1, 1, 0, 0).cached_input_micro == 1    # 0.25 micro rounds UP, never undercharges


def test_invalid_usage_rejected():
    with pytest.raises(ValueError):
        compute_cost(1, 10, 11, 0, 0)
