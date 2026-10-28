"""List prices for model calls: the cost table of the ledger (T009, FR-084, NFR-03).

Meta Model API responses carry no cost, so the ledger prices each live call from the list
prices on Meta's pricing page (https://dev.meta.ai/docs/pricing-rate-limits, standard mode,
read 2026-10-28). ``MODEL_PRICES`` replaces the table; a model it does not list costs 0,
never a guess. Cached input is billed lower, but responses do not report it apart, so all
input is priced at the full rate: estimates err high, which suits a spend alert.
"""

from pydantic import BaseModel, ConfigDict, Field


class ModelPrice(BaseModel):
    """USD list prices for one model ID; each unit the model is not billed by stays 0."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_per_mtok: float = Field(default=0.0, ge=0)
    output_per_mtok: float = Field(default=0.0, ge=0)
    per_image: float = Field(default=0.0, ge=0)
    per_audio_hour: float = Field(default=0.0, ge=0)

    def cost(
        self, tokens_in: int, tokens_out: int, *, images: int = 0, audio_s: float | None = None
    ) -> float:
        tokens = (tokens_in * self.input_per_mtok + tokens_out * self.output_per_mtok) / 1_000_000
        # Audio is metered in whole seconds, rounded down.
        audio = int(audio_s or 0) * self.per_audio_hour / 3600
        return tokens + images * self.per_image + audio


# Standard mode only: the data-sharing "-contributor" variants are never called (Art. V.2).
META_LIST_PRICES: dict[str, ModelPrice] = {
    "muse-spark-1.3": ModelPrice(input_per_mtok=1.25, output_per_mtok=4.25),
    "muse-spark-1.2": ModelPrice(input_per_mtok=1.25, output_per_mtok=4.25),
    "muse-spark-1.1": ModelPrice(input_per_mtok=1.25, output_per_mtok=4.25),
    "muse-wonder-1.0": ModelPrice(input_per_mtok=4.00, output_per_mtok=20.00),
    # Billed per image returned, not per token.
    "muse-image-1.0": ModelPrice(per_image=0.01),
    # $2.50 per 1,000 images.
    "sam-3.1": ModelPrice(per_image=0.0025),
    "muse-voice-transcribe-1.0": ModelPrice(per_audio_hour=0.18),
}
