"""List prices for model calls: the cost table of the ledger (T009, FR-084, NFR-03).

Meta Model API responses carry no cost, so the ledger prices each live call from the list
prices on Meta's pricing page (https://dev.meta.ai/docs/pricing-rate-limits, standard mode,
read 2026-10-28). ``MODEL_PRICES`` replaces the table; a model it does not list costs 0,
never a guess. Input the provider served from its prompt cache (``CallMeta.tokens_cached``,
Meta's ``usage.prompt_tokens_details.cached_tokens``) bills at the cached rate.
"""

from pydantic import BaseModel, ConfigDict, Field


class ModelPrice(BaseModel):
    """USD list prices for one model ID; each unit the model is not billed by stays 0."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_mtok: float = Field(default=0.0, ge=0)
    # Input served from the prompt cache; None bills it as ordinary input.
    cached_input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float = Field(default=0.0, ge=0)
    per_image: float = Field(default=0.0, ge=0)
    per_audio_hour: float = Field(default=0.0, ge=0)

    def cost(
        self,
        tokens_in: int,
        tokens_out: int,
        *,
        tokens_cached: int = 0,
        images: int = 0,
        audio_s: float | None = None,
    ) -> float:
        cached = min(max(tokens_cached, 0), tokens_in)
        cached_rate = (
            self.input_per_mtok
            if self.cached_input_per_mtok is None
            else self.cached_input_per_mtok
        )
        tokens = (
            (tokens_in - cached) * self.input_per_mtok
            + cached * cached_rate
            + tokens_out * self.output_per_mtok
        ) / 1_000_000
        # Audio is metered in whole seconds, rounded down.
        audio = int(audio_s or 0) * self.per_audio_hour / 3600
        return tokens + images * self.per_image + audio


# Standard mode only: the data-sharing "-contributor" variants are never called (Art. V.2).
META_LIST_PRICES: dict[str, ModelPrice] = {
    "muse-spark-1.3": ModelPrice(
        input_per_mtok=1.25, cached_input_per_mtok=0.15, output_per_mtok=4.25
    ),
    "muse-spark-1.2": ModelPrice(
        input_per_mtok=1.25, cached_input_per_mtok=0.15, output_per_mtok=4.25
    ),
    "muse-spark-1.1": ModelPrice(
        input_per_mtok=1.25, cached_input_per_mtok=0.15, output_per_mtok=4.25
    ),
    "muse-wonder-1.0": ModelPrice(
        input_per_mtok=4.00, cached_input_per_mtok=0.20, output_per_mtok=20.00
    ),
    # Billed per image returned, not per token.
    "muse-image-1.0": ModelPrice(per_image=0.01),
    # $2.50 per 1,000 images.
    "sam-3.1": ModelPrice(per_image=0.0025),
    "muse-voice-transcribe-1.0": ModelPrice(per_audio_hour=0.18),
}
