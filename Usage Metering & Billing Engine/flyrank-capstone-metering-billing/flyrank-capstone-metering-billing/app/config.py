"""Settings (from env / .env) and PINNED pricing constants.

Pricing is deliberately NOT env-overridable: money rules live in code, are reviewed
in git, and EVIDENCE.md proves their totals.
All money is integer: micro-USD (1 USD = 1_000_000 micro-USD) or cents.
"""
from dataclasses import dataclass
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./metering.db"
    admin_token: str = "change-me-admin-token"

    stripe_secret_key: str = ""  # empty => offline mock checkout
    stripe_webhook_secret: str = "whsec_replace_me"
    stripe_price_id_pro: str = ""
    checkout_success_url: str = "http://localhost:8000/health"
    checkout_cancel_url: str = "http://localhost:8000/health"
    webhook_tolerance_seconds: int = 300

    run_worker: bool = True
    job_poll_seconds: float = 1.0
    job_max_attempts: int = 5
    job_backoff_seconds: int = 2
    alert_webhook_url: str = ""

    @property
    def stripe_mock(self) -> bool:
        return not self.stripe_secret_key


@lru_cache
def get_settings() -> Settings:
    return Settings()


MICRO = 1_000_000


@dataclass(frozen=True)
class Pricing:
    # micro-USD per 1,000,000 tokens  (e.g. 1_000_000 == $1.00 / 1M tokens)
    input_per_mtok: int = 1_000_000          # fresh (non-cached) input  $1.00 / 1M
    cached_input_per_mtok: int = 250_000     # cached input              $0.25 / 1M (75% cheaper)
    output_per_mtok: int = 4_000_000         # output                    $4.00 / 1M
    reasoning_per_mtok: int = 4_000_000      # reasoning billed AS OUTPUT (must equal output)
    api_call_micro: int = 2_000              # $0.002 list price per API call


PRICING = Pricing()
assert PRICING.reasoning_per_mtok == PRICING.output_per_mtok, "reasoning tokens bill as output"
