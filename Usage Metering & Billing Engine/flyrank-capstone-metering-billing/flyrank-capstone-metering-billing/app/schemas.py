from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TOKENS = 10_000_000


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = Field(0, ge=0, le=MAX_TOKENS, description="Total prompt tokens (includes cached)")
    cached_input_tokens: int = Field(0, ge=0, le=MAX_TOKENS, description="Subset of input_tokens served from cache")
    output_tokens: int = Field(0, ge=0, le=MAX_TOKENS)
    reasoning_tokens: int = Field(0, ge=0, le=MAX_TOKENS, description="Billed as output")

    @model_validator(mode="after")
    def _cached_within_input(self):
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        return self


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field("", max_length=10_000)
    usage: TokenUsage | None = Field(None, description="Simulated token counts; derived from the prompt if omitted")


class TenantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    plan: str = Field("free", max_length=32)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: str = Field("pro", max_length=32)
